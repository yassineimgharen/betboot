from datetime import datetime

from betboot.domain import MatchObservation, require_aware


def team_form(history: list[MatchObservation], source: str, team_id: str, as_of: datetime):
    """Descriptive form only. No imputation of xG or unavailable context."""
    require_aware(as_of)
    unique = {}
    for match in sorted(history, key=lambda m: m.collected_at):
        if match.source == source and match.collected_at <= as_of:
            unique[match.external_id] = match
    rows = sorted((m for m in unique.values() if m.status == "FINISHED"
                   and m.kickoff < as_of and team_id in (m.home_id, m.away_id)),
                  key=lambda m: m.kickoff, reverse=True)
    result = {}
    for venue in ("all", "home", "away"):
        venue_rows = [m for m in rows if venue == "all" or
                      (m.home_id if venue == "home" else m.away_id) == team_id]
        result[venue] = {}
        for n in (5, 10, 20):
            subset = venue_rows[:n]
            gf = [m.home_goals if m.home_id == team_id else m.away_goals for m in subset]
            ga = [m.away_goals if m.home_id == team_id else m.home_goals for m in subset]
            result[venue][str(n)] = {
                "requested_matches": n, "observed_matches": len(subset),
                "complete_window": len(subset) == n,
                "goals_for": sum(gf) if subset else None,
                "goals_against": sum(ga) if subset else None,
                "points": sum(3 if f > a else 1 if f == a else 0 for f, a in zip(gf, ga))
                if subset else None,
                "xg": None, "xga": None,
            }
    return result
