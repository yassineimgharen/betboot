"""Elo ratings (goal-difference weighted) mapped to 1X2 with an ordered-logit link."""
from dataclasses import dataclass

import numpy as np
from scipy.optimize import minimize

K = 20.0
HOME_ADVANTAGE = 60.0
NEWCOMER_OFFSET = -60.0    # promoted sides start below the league average


def _margin_multiplier(goal_diff):
    d = abs(goal_diff)
    return 1.0 if d <= 1 else 1.5 if d == 2 else (11 + d) / 8


def run(matches):
    """Chronological ratings. Returns (ratings after all matches, pre-match diffs per match)."""
    ratings, diffs = {}, []
    for m in matches:
        mean = np.mean(list(ratings.values())) if ratings else 1500.0
        rh = ratings.setdefault(m.home_id, mean + NEWCOMER_OFFSET if ratings else 1500.0)
        ra = ratings.setdefault(m.away_id, mean + NEWCOMER_OFFSET if ratings else 1500.0)
        diff = rh + HOME_ADVANTAGE - ra
        diffs.append(diff)
        expected = 1 / (1 + 10 ** (-diff / 400))
        result = 1.0 if m.home_goals > m.away_goals else 0.5 if m.home_goals == m.away_goals else 0.0
        change = K * _margin_multiplier(m.home_goals - m.away_goals) * (result - expected)
        ratings[m.home_id] = rh + change
        ratings[m.away_id] = ra - change
    return ratings, np.array(diffs)


def _sigmoid(x):
    return 1 / (1 + np.exp(-x))


@dataclass
class EloModel:
    ratings: dict
    cut_away: float
    cut_draw: float
    slope: float

    def diff(self, home_id, away_id):
        return self.ratings[home_id] + HOME_ADVANTAGE - self.ratings[away_id]

    def probabilities(self, home_id, away_id):
        return outcome_probabilities(self.diff(home_id, away_id), self.cut_away, self.cut_draw, self.slope)


def outcome_probabilities(diff, cut_away, cut_draw, slope):
    """Ordered logit: P(away) = s(c1 - b d), P(away or draw) = s(c2 - b d)."""
    p_away = _sigmoid(cut_away - slope * diff)
    p_not_home = _sigmoid(cut_draw - slope * diff)
    return {'home': 1 - p_not_home, 'draw': p_not_home - p_away, 'away': p_away}


def fit_link(diffs, outcomes):
    """Fit ordered-logit cut points and slope. outcomes: 0 away, 1 draw, 2 home."""
    diffs, outcomes = np.asarray(diffs, float), np.asarray(outcomes)

    def nll(params):
        c1, gap, b = params
        p = outcome_probabilities(diffs, c1, c1 + abs(gap), b)
        chosen = np.select([outcomes == 0, outcomes == 1], [p['away'], p['draw']], p['home'])
        return -np.sum(np.log(np.clip(chosen, 1e-12, None)))

    result = minimize(nll, x0=[-0.6, 1.2, 0.004], method='Nelder-Mead', options={'maxiter': 4000})
    c1, gap, b = result.x
    return c1, c1 + abs(gap), b


def fit(matches, burn_in=120):
    """Ratings over all matches; link fitted on matches after a burn-in period."""
    ratings, diffs = run(matches)
    outcomes = [2 if m.home_goals > m.away_goals else 1 if m.home_goals == m.away_goals else 0 for m in matches]
    start = min(burn_in, max(len(matches) - 50, 0))
    c1, c2, b = fit_link(diffs[start:], outcomes[start:])
    return EloModel(ratings=ratings, cut_away=c1, cut_draw=c2, slope=b)
