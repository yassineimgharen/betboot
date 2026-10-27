import hashlib
import json
import math
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta

from betboot.domain import MatchObservation, require_aware

VERSION = "poisson-shrinkage-v1"


class InsufficientData(ValueError):
    pass


@dataclass(frozen=True)
class ModelConfig:
    min_league_matches: int = 50
    min_venue_matches: int = 5
    prior_matches: float = 8.0
    half_life_days: float = 180.0
    lookback_days: int = 730
    max_fixture_age_hours: float = 24.0
    max_result_age_days: float = 60.0

    def __post_init__(self):
        if any(not math.isfinite(v) or v <= 0 for v in asdict(self).values()):
            raise ValueError("Model configuration values must be positive and finite")


def poisson_mass(rate: float) -> list[float]:
    if not math.isfinite(rate) or not 0 < rate <= 20:
        raise ValueError("Goal rate is outside the supported range")
    mass = [math.exp(-rate)]
    # Adaptive support: total omitted marginal mass < 1e-12.
    while 1 - sum(mass) > 1e-12:
        mass.append(mass[-1] * rate / len(mass))
        if len(mass) > 200:
            raise ValueError("Poisson convergence failed")
    total = sum(mass)
    return [p / total for p in mass]


def markets(home_rate: float, away_rate: float) -> dict[str, float]:
    h, a = poisson_mass(home_rate), poisson_mass(away_rate)
    result = {"home": 0.0, "draw": 0.0, "away": 0.0, "btts_yes": 0.0,
              "over_1.5": 0.0, "over_2.5": 0.0, "over_3.5": 0.0}
    for i, ph in enumerate(h):
        for j, pa in enumerate(a):
            p = ph * pa
            result["home" if i > j else "away" if j > i else "draw"] += p
            if i > 0 and j > 0:
                result["btts_yes"] += p
            for line in (1.5, 2.5, 3.5):
                if i + j > line:
                    result[f"over_{line}"] += p
    for line in (1.5, 2.5, 3.5):
        result[f"under_{line}"] = 1 - result[f"over_{line}"]
    result["btts_no"] = 1 - result["btts_yes"]
    result.update({"1X": result["home"] + result["draw"],
                   "X2": result["draw"] + result["away"],
                   "12": result["home"] + result["away"]})
    return result


def predict(fixture: MatchObservation, history: list[MatchObservation], as_of: datetime,
            config: ModelConfig | None = None) -> dict:
    config = config or ModelConfig()
    require_aware(as_of)
    if fixture.status != "TIMED" or fixture.kickoff <= as_of:
        raise InsufficientData("A verified, timed, pre-match fixture is required")
    if not fixture.collected_at <= as_of <= fixture.collected_at + timedelta(
            hours=config.max_fixture_age_hours):
        raise InsufficientData("Fixture is stale or not known at the prediction cutoff")
    # Resolve revisions before filtering status: a later retraction of a finished
    # result must remove it from training, including for direct engine callers.
    unique = {}
    for m in sorted(history, key=lambda m: m.collected_at):
        if m.source == fixture.source and m.collected_at <= as_of:
            unique[m.external_id] = m
    eligible = [m for m in unique.values() if m.league == fixture.league
                and m.status == "FINISHED" and m.kickoff < as_of
                and m.external_id != fixture.external_id
                and m.kickoff >= as_of - timedelta(days=config.lookback_days)]
    rows = sorted(eligible, key=lambda m: (m.kickoff, m.external_id))
    home_rows = [m for m in rows if m.home_id == fixture.home_id]
    away_rows = [m for m in rows if m.away_id == fixture.away_id]
    if len(rows) < config.min_league_matches or min(len(home_rows), len(away_rows)) < config.min_venue_matches:
        raise InsufficientData("Insufficient league or team venue history")
    for team in (fixture.home_id, fixture.away_id):
        latest = max(m.kickoff for m in rows if team in (m.home_id, m.away_id))
        if as_of - latest > timedelta(days=config.max_result_age_days):
            raise InsufficientData("Team result history is too old")

    def weight(m):
        return 0.5 ** ((as_of - m.kickoff).total_seconds() / 86400 / config.half_life_days)

    total = sum(weight(m) for m in rows)
    home_mean = sum(weight(m) * m.home_goals for m in rows) / total
    away_mean = sum(weight(m) * m.away_goals for m in rows) / total
    if min(home_mean, away_mean) <= 0:
        raise InsufficientData("Degenerate league scoring distribution")

    def shrunk(team_rows, field, mean):
        return (sum(weight(m) * getattr(m, field) for m in team_rows)
                + config.prior_matches * mean) / (
                    sum(weight(m) for m in team_rows) + config.prior_matches)

    home_rate = (shrunk(home_rows, "home_goals", home_mean)
                 * shrunk(away_rows, "home_goals", home_mean) / home_mean)
    away_rate = (shrunk(away_rows, "away_goals", away_mean)
                 * shrunk(home_rows, "away_goals", away_mean) / away_mean)
    if max(home_rate, away_rate) > 20:
        raise InsufficientData("Estimated scoring rates are implausible")
    probs = markets(home_rate, away_rate)
    evidence = [m.model_dump(mode="json") for m in rows]
    fingerprint = hashlib.sha256(json.dumps(evidence, sort_keys=True).encode()).hexdigest()
    return {
        "model_version": VERSION, "config": asdict(config), "as_of": as_of.isoformat(),
        "fixture": fixture.model_dump(mode="json"), "training_fingerprint": fingerprint,
        "training_count": len(rows), "training_match_ids": [m.external_id for m in rows],
        "training_start": rows[0].kickoff.isoformat(), "training_end": rows[-1].kickoff.isoformat(),
        "home_goal_rate": home_rate, "away_goal_rate": away_rate,
        "markets": {market: {"probability": p, "fair_odds": 1 / p if p > 0 else None,
                              "bookmaker_odds": None, "expected_value": None,
                              "uncertainty_interval": None} for market, p in probs.items()},
        "calibrated": False, "recommendation_eligible": False, "mode": "paper",
        "data_quality": {
            "score": None, "reason": "A quality scoring rubric has not yet been validated",
            "league_matches": len(rows), "home_venue_matches": len(home_rows),
            "away_venue_matches": len(away_rows),
            "fixture_age_hours": (as_of - fixture.collected_at).total_seconds() / 3600,
            "missing": ["xg", "injuries", "lineups", "odds", "tactical_context"],
        },
        "reasons": ["Venue-specific scoring and conceding rates relative to league averages",
                    f"Team rates shrunk with {config.prior_matches:g} league-average prior matches",
                    f"Results exponentially weighted with {config.half_life_days:g}-day half-life"],
        "warnings": ["Uncalibrated research baseline; not a betting recommendation",
                     "No validated uncertainty interval or out-of-sample performance yet",
                     "Independent goals assumption; no opponent-strength or lineup adjustment"],
    }
