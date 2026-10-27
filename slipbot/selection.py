"""Choose one bet per match and rank across matches.

Hard rules (enforced here or earlier): a real fixture that has not started, verified odds
strictly above the minimum, enough data for a calibrated estimate, one selection per match,
no fixture or team twice. Everything else is ranking: `Candidate.score` is the conservative
probability (estimate less its uncertainty, which includes model/market disagreement and a
penalty for unvalidated markets), nudged by expected value. Ranking by raw EV was what used
to favour long shots, because EV multiplies any probability error by the odds.
"""
from collections import Counter
import math

from slipbot.teams import normalise


def rank_key(c):
    # Near-ties go to the shorter price (less variance).
    return (round(c.score, 4), -c.quote.price)


def best_per_match(assessment):
    return max(assessment.candidates, key=rank_key, default=None)


def choose(assessments, count, log=None):
    """The best selection of each match, best matches first. `log` (a Counter) receives the
    reason every other eligible selection was not used."""
    log = log if log is not None else Counter()
    picks = sorted(filter(None, map(best_per_match, assessments)), key=rank_key, reverse=True)
    log['other selections in a match whose best selection ranks higher'] += sum(
        max(len(a.candidates) - 1, 0) for a in assessments)
    chosen, fixtures, teams = [], set(), set()
    for pick in picks:
        m = pick.match
        # The same team twice in one slip (two fixtures, or one fixture from two providers).
        involved = {normalise(m.home), normalise(m.away)}
        key = (m.kickoff, *sorted(involved))
        if len(chosen) == count:
            log['match ranked below the requested count'] += 1
        elif m.id in fixtures or key in fixtures:
            log['duplicate fixture (same match from two providers)'] += 1
        elif involved & teams:
            log['team already on the slip'] += 1
        else:
            chosen.append(pick)
            fixtures |= {m.id, key}
            teams |= involved
    return chosen


def slip_probability(picks):
    """P(all selections win), assuming independence. Reasonable here because every pick is
    from a different match and no team appears twice; results in one league on one day are
    only weakly correlated. Returns (point, conservative)."""
    return (math.prod(p.probability for p in picks),
            math.prod(max(p.conservative_probability, 0) for p in picks))
