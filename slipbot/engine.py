"""Per-fixture predictions: calibrated probabilities, uncertainty and candidate selections."""
from collections import Counter
from dataclasses import dataclass, field
import json
import math
import pickle

import numpy as np

from slipbot.config import ROOT
from slipbot import markets, quotes as q
from slipbot.leagues import LEAGUES
from slipbot.teams import display_name
from slipbot.models import dixon_coles, elo

MIN_LEAGUE_MATCHES = 300     # finished league matches available for fitting
MIN_TEAM_RECENT = 8          # league matches per team in the last 365 days
MIN_BOOKS = 3                # bookmakers needed for a market consensus
# Ranking penalties (standard-deviation terms) instead of hard rejections:
UNVALIDATED_SD = 0.05        # market whose probabilities failed or lack out-of-sample validation
MARKET_ONLY_SD = 0.02        # no statistical model to cross-check the bookmaker consensus
OTHER_COMPETITION_SD = 0.03  # competition outside the named list (consensus checked mainly on major leagues)
EV_WEIGHT = 0.5              # EV only nudges the ranking; probability dominates
HIGH_BAND = 0.65             # probabilities at or above this are checked for overconfidence
MIN_BAND_SAMPLE = 30
MODEL_VERSION = 2            # bump to invalidate cached fits
# Regularisation chosen on Aug-Dec 2025 and confirmed on Jan-Jun 2026 (docs/VALIDATION.md).
# Goals markets need far stronger shrinkage than 1X2 to beat base rates out of sample.
RESULT_FIT = {'ridge': 20.0, 'half_life_days': 500}
GOALS_FIT = {'ridge': 60.0, 'half_life_days': 500}
CALIBRATION_FILE = ROOT / 'slipbot' / 'calibration.json'
# Used only until `python -m slipbot.validate` has written fitted values. A model weight
# of 0 means the market consensus alone is trusted; no market counts as validated.
DEFAULT_CALIBRATION = {'dc_weight': 0.5, 'model_weight': 0.0, 'goals_model_weight': 0.0,
                       'markets': {}, 'source': 'defaults (not validated)'}
RESULT_MARKETS = ('1X2', 'DC')


def calibration():
    try:
        return {**DEFAULT_CALIBRATION, **json.loads(CALIBRATION_FILE.read_text())}
    except (OSError, ValueError):
        return dict(DEFAULT_CALIBRATION)


def overconfidence(band):
    """How far predictions in the high-probability band exceeded the observed hit rate,
    out of sample (0 when the sample is small or predictions were not too high)."""
    if not band or band.get('n', 0) < MIN_BAND_SAMPLE:
        return 0.0
    return max(0.0, band['predicted'] - band['observed'])


@dataclass
class LeagueModel:
    dc: dixon_coles.DixonColes         # tuned for match results
    goals: dixon_coles.DixonColes      # tuned for goal totals
    elo: elo.EloModel
    size: int


def fit_league(history, now):
    if len(history) < MIN_LEAGUE_MATCHES:
        return None
    return LeagueModel(dc=dixon_coles.fit(history, now, **RESULT_FIT),
                       goals=dixon_coles.fit(history, now, **GOALS_FIT),
                       elo=elo.fit(history), size=len(history))


def cached_league(code, history, now, cache_dir):
    """The fitted model for this exact history; refitted only when new results arrive."""
    if len(history) < MIN_LEAGUE_MATCHES:
        return None
    path = cache_dir / f'model-{code}.pkl'
    key = (MODEL_VERSION, len(history), history[-1].id, history[-1].kickoff.isoformat())
    try:
        stored_key, model = pickle.loads(path.read_bytes())
        if stored_key == key:
            return model
    except (OSError, pickle.UnpicklingError, EOFError, ValueError, AttributeError):
        pass
    model = fit_league(history, now)
    try:
        path.write_bytes(pickle.dumps((key, model)))
    except OSError:
        pass
    return model


def pool(model, market, weight):
    """Logarithmic opinion pool: p ∝ model^w · market^(1-w), over a partition of outcomes
    (1X2, over/under, BTTS; double chance is pooled through its 1X2 components)."""
    raw = {s: model[s] ** weight * market[s] ** (1 - weight) for s in model}
    norm = sum(raw.values())
    return {s: v / norm for s, v in raw.items()}


def double_chance(p):
    return {'1X': p['home'] + p['draw'], 'X2': p['draw'] + p['away'], '12': p['home'] + p['away']}


@dataclass
class Candidate:
    match: object
    market: str
    selection: str
    quote: q.Quote
    probability: float                # calibrated estimate (shown on the slip)
    model_probability: float | None   # calibrated model estimate; None for market-only fixtures
    market_probability: float         # margin-free bookmaker consensus
    uncertainty: float                # standard deviation incl. disagreement and validation penalties
    books: int
    odds_age_hours: float
    reliability: dict = field(default_factory=dict)   # out-of-sample record of this market
    settled_share: float = 1.0        # expected stake fraction won or lost (< 1 when it can push)

    @property
    def ev(self):
        """Expected profit per unit staked; returned stakes (handicap pushes) earn nothing."""
        return self.settled_share * (self.probability * self.quote.price - 1)

    @property
    def fair_odds(self):
        return 1 / self.probability

    @property
    def implied_probability(self):
        return 1 / self.quote.price

    @property
    def validated(self):
        return bool(self.reliability.get('validated'))

    @property
    def disagreement(self):
        return 0.0 if self.model_probability is None else abs(self.model_probability - self.market_probability)

    @property
    def conservative_probability(self):
        """The estimate less one standard deviation of everything that makes it uncertain."""
        return self.probability - self.uncertainty

    @property
    def score(self):
        """Ranking quality: conservative probability, nudged by expected value. A 75% pick
        at a fair price outranks a 55% pick with a large EV; long shots rank last."""
        return self.conservative_probability + EV_WEIGHT * max(-0.15, min(0.15, self.ev))

    @property
    def tier(self):
        """Internal confidence class, for the details view and validation."""
        p = self.conservative_probability
        return 'high' if p >= 0.70 else 'medium' if p >= 0.55 else 'speculative'

    @property
    def label(self):
        return markets.label(self.market, self.selection, display_name(self.match.home_short),
                             display_name(self.match.away_short))


@dataclass
class Assessment:
    match: object
    candidates: list = field(default_factory=list)
    problem: str | None = None
    rejected: Counter = field(default_factory=Counter)    # selection-level reasons, for --debug
    quoted_markets: int = 0


def model_markets(league_model, match, cal, wanted):
    """{market: ({selection: p}, {selection: sd}, {selection: settled share})} from the fitted
    models, uncalibrated, for the markets in `wanted`. Handicap probabilities are conditional
    on the stake not being returned; the settled share is the expected fraction of the stake
    that is won or lost (below 1 on whole and quarter lines, which can push)."""
    point, sampled = {}, {}
    for name, dc in (('result', league_model.dc), ('goals', league_model.goals)):
        point[name] = dc.score_matrix(match.home_id, match.away_id)
        lam_h, lam_a = dc.sampled_rates(match.home_id, match.away_id)
        sampled[name] = dixon_coles.score_matrix(lam_h, lam_a, dc.rho)
    flat = {name: markets.probabilities(m) for name, m in point.items()}
    flat_sampled = {name: markets.probabilities(m) for name, m in sampled.items()}
    elo_p = league_model.elo.probabilities(match.home_id, match.away_id)
    w = cal['dc_weight']
    out = {}
    for market in wanted:
        sels = markets.selections(market)
        share = {s: 1.0 for s in sels}
        if market.startswith('AH'):
            h = markets.ah_line(market)
            exact = markets.handicap(point['result'], h)
            draws = markets.handicap(sampled['result'], h)
            p = {s: float(markets.decided_probability(*exact[s])) for s in sels}
            sd = {s: float(np.std(markets.decided_probability(*draws[s]))) for s in sels}
            share = {s: float(exact[s][0] + exact[s][1]) for s in sels}
        else:
            which = 'result' if market in RESULT_MARKETS else 'goals'
            p = {s: float(flat[which][(market, s)]) for s in sels}
            sd = {s: float(np.std(flat_sampled[which][(market, s)])) for s in sels}
            if market in RESULT_MARKETS:
                # Elo uncertainty is not modelled separately, so only the Dixon-Coles share counts.
                sd = {s: w * v for s, v in sd.items()}
                blend = {s: w * flat['result'][('1X2', s)] + (1 - w) * elo_p[s] for s in ('home', 'draw', 'away')}
                p = blend if market == '1X2' else double_chance(blend)
        out[market] = (p, sd, share)
    return out


def _calibrated(p, gap):
    return {s: max(v - gap, 1e-6) if v >= HIGH_BAND else v for s, v in p.items()}


def usable_model(league_model, match):
    """The league model if it covers both teams with enough recent matches, else None."""
    if league_model is None:
        return None
    dc = league_model.dc
    for team in (match.home_id, match.away_id):
        if (team not in dc.teams or team not in league_model.elo.ratings
                or dc.recent.get(team, 0) < MIN_TEAM_RECENT):
            return None
    return league_model


def assess(match, league_model, match_quotes, now, settings, cal):
    """Every priced selection of one fixture. Without a usable model the bookmaker consensus
    alone is the estimate (validated out of sample), with a larger uncertainty."""
    result = Assessment(match)
    if not match_quotes:
        result.problem = 'no verified bookmaker odds'
        return result
    league_model = usable_model(league_model, match)
    quoted = sorted({x.market for x in match_quotes})
    result.quoted_markets = len(quoted)
    stale = sum(1 for x in match_quotes if (now - x.updated).total_seconds() > settings.max_odds_age)
    if stale:
        result.rejected['quote unchanged for longer than the freshness limit'] += stale
    book_maps = {m: q.books(match_quotes, m, now, settings.max_odds_age) for m in quoted}
    book_maps.setdefault('1X2', {})
    priced = [m for m in quoted if len(book_maps[m]) >= MIN_BOOKS]
    for m in quoted:
        if m not in priced:
            result.rejected[f'market quoted by fewer than {MIN_BOOKS} bookmakers'] += len(markets.selections(m))
    modelled = model_markets(league_model, match, cal, set(priced) | {'1X2'}) if league_model else {}
    record = cal.get('markets', {})
    for market in priced:
        book_map = book_maps[market]
        reliability = record.get(markets.family(market), {})
        consensus = q.consensus(book_map, market)
        # Outside the named competitions the consensus is calibrated on its own record.
        band = reliability.get('market_band_other' if match.league not in LEAGUES else 'market_band')
        consensus_cal = _calibrated(consensus, overconfidence(band))
        spread = q.dispersion(book_map, market)
        model_p = model_sd = None
        share = {s: 1.0 for s in markets.selections(market)}
        weight = 0.0
        pooled = consensus_cal
        if market in modelled:
            model_p, model_sd, share = modelled[market]
            model_p = _calibrated(model_p, overconfidence(reliability.get('model_band')))
            if market == 'DC':
                weight = cal['model_weight']
                if weight > 0 and len(book_maps['1X2']) >= MIN_BOOKS:
                    pooled = double_chance(pool(modelled['1X2'][0], q.consensus(book_maps['1X2'], '1X2'), weight))
            else:
                weight = cal['model_weight'] if market == '1X2' else cal['goals_model_weight']
                pooled = pool(model_p, consensus_cal, weight)
        for selection in markets.selections(market):
            quote = q.price_to_show(book_map, selection, settings.preferred_bookmaker)
            if quote is None:
                result.rejected['no listed bookmaker within 15% of the median price'] += 1
                continue
            if not quote.price > settings.min_odds:
                result.rejected[f'odds not above {settings.min_odds:.2f}'] += 1
                continue
            p = pooled[selection]
            terms = [(1 - weight) * spread[selection]]
            if model_p is None:
                terms.append(MARKET_ONLY_SD)
            else:
                # Delta method for the model's share of the pooled estimate.
                m = min(max(model_p[selection], 1e-6), 1 - 1e-6)
                terms.append(weight * p * (1 - p) / (m * (1 - m)) * model_sd[selection])
                # Model/market disagreement means one of them lacks information.
                terms.append(abs(model_p[selection] - consensus[selection]) / 2)
            if not reliability.get('validated'):
                terms.append(UNVALIDATED_SD)
            if match.league not in LEAGUES:
                terms.append(OTHER_COMPETITION_SD)
            result.candidates.append(Candidate(
                match=match, market=market, selection=selection, quote=quote, probability=p,
                model_probability=None if model_p is None else model_p[selection],
                market_probability=consensus[selection], uncertainty=math.hypot(*terms),
                books=len(book_map), odds_age_hours=(now - quote.updated).total_seconds() / 3600,
                reliability=reliability, settled_share=share[selection]))
    if not result.candidates:
        result.problem = 'no market with enough verified odds above the minimum'
    return result
