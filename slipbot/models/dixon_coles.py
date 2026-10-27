"""Time-weighted Dixon-Coles model (Dixon & Coles 1997) with parameter uncertainty.

log(lambda_home) = mu + home + attack[h] + defence[a]
log(lambda_away) = mu        + attack[a] + defence[h]
Fitted by Newton iterations on the weighted Poisson likelihood with a small ridge penalty
(which also resolves the attack/defence identifiability). The low-score correlation rho is
then fitted by one-dimensional maximum likelihood. The inverse Hessian approximates the
parameter covariance, which drives the prediction-uncertainty estimate.
"""
from dataclasses import dataclass

import numpy as np
from scipy.optimize import minimize_scalar
from scipy.stats import poisson

MAX_GOALS = 10


@dataclass
class DixonColes:
    teams: dict           # team id -> index
    theta: np.ndarray     # [mu, home, attack..., defence...]
    cov: np.ndarray
    rho: float
    recent: dict          # team id -> matches in the fit data from the last 365 days

    def rates(self, home_id, away_id, theta=None):
        t = self.theta if theta is None else theta
        n = len(self.teams)
        h, a = self.teams[home_id], self.teams[away_id]
        lam_home = np.exp(t[..., 0] + t[..., 1] + t[..., 2 + h] + t[..., 2 + n + a])
        lam_away = np.exp(t[..., 0] + t[..., 2 + a] + t[..., 2 + n + h])
        return lam_home, lam_away

    def score_matrix(self, home_id, away_id):
        return score_matrix(*self.rates(home_id, away_id), self.rho)

    def sampled_rates(self, home_id, away_id, draws=400, seed=0):
        """Rates under parameter uncertainty: draws from N(theta, cov)."""
        rng = np.random.default_rng(seed)
        thetas = rng.multivariate_normal(self.theta, self.cov, size=draws, method='cholesky')
        return self.rates(home_id, away_id, thetas)


def tau(x, y, lam, mu, rho):
    """Dixon-Coles adjustment for the 0-0, 1-0, 0-1 and 1-1 scorelines."""
    out = np.ones_like(lam, dtype=float)
    out = np.where((x == 0) & (y == 0), 1 - lam * mu * rho, out)
    out = np.where((x == 0) & (y == 1), 1 + lam * rho, out)
    out = np.where((x == 1) & (y == 0), 1 + mu * rho, out)
    return np.where((x == 1) & (y == 1), 1 - rho, out)


def score_matrix(lam_home, lam_away, rho):
    """P(home goals = i, away goals = j); accepts scalars or equal-length arrays of rates
    (returning shape (..., MAX_GOALS + 1, MAX_GOALS + 1))."""
    lam_home, lam_away = np.asarray(lam_home, float), np.asarray(lam_away, float)
    goals = np.arange(MAX_GOALS + 1)
    home = poisson.pmf(goals, lam_home[..., None])
    away = poisson.pmf(goals, lam_away[..., None])
    matrix = home[..., :, None] * away[..., None, :]
    matrix[..., 0, 0] *= 1 - lam_home * lam_away * rho
    matrix[..., 0, 1] *= 1 + lam_home * rho
    matrix[..., 1, 0] *= 1 + lam_away * rho
    matrix[..., 1, 1] *= 1 - rho
    matrix = np.clip(matrix, 0, None)
    return matrix / matrix.sum(axis=(-2, -1), keepdims=True)


def fit(matches, now, half_life_days=240, ridge=1.0, iterations=50):
    """Fit on finished matches (objects with home_id, away_id, home_goals, away_goals, kickoff)."""
    teams = {}
    for m in matches:
        teams.setdefault(m.home_id, len(teams))
        teams.setdefault(m.away_id, len(teams))
    n, rows = len(teams), len(matches)
    p = 2 + 2 * n
    age = np.array([(now - m.kickoff).total_seconds() / 86400 for m in matches])
    weight = 0.5 ** (age / half_life_days)
    hi = np.array([teams[m.home_id] for m in matches])
    ai = np.array([teams[m.away_id] for m in matches])
    hg = np.array([m.home_goals for m in matches], dtype=float)
    ag = np.array([m.away_goals for m in matches], dtype=float)

    X = np.zeros((2 * rows, p))
    r = np.arange(rows)
    X[r, 0] = 1
    X[r, 1] = 1
    X[r, 2 + hi] = 1
    X[r, 2 + n + ai] = 1
    X[rows + r, 0] = 1
    X[rows + r, 2 + ai] = 1
    X[rows + r, 2 + n + hi] = 1
    y = np.concatenate([hg, ag])
    w = np.concatenate([weight, weight])
    penalty = np.full(p, ridge)
    penalty[:2] = 0

    theta = np.zeros(p)
    theta[0] = np.log(max(y.mean(), 0.1))
    for _ in range(iterations):
        lam = np.exp(X @ theta)
        grad = X.T @ (w * (y - lam)) - penalty * theta
        hess = (X * (w * lam)[:, None]).T @ X + np.diag(penalty)
        step = np.linalg.solve(hess, grad)
        theta += step
        if np.max(np.abs(step)) < 1e-8:
            break
    lam = np.exp(X @ theta)
    hess = (X * (w * lam)[:, None]).T @ X + np.diag(penalty)
    cov = np.linalg.inv(hess)
    cov = (cov + cov.T) / 2

    lam_h, lam_a = lam[:rows], lam[rows:]

    def neg_tau_likelihood(rho):
        t = tau(hg, ag, lam_h, lam_a, rho)
        return np.inf if np.any(t <= 0) else -np.sum(weight * np.log(t))

    rho = minimize_scalar(neg_tau_likelihood, bounds=(-0.25, 0.25), method='bounded').x
    recent = age <= 365
    counts = {team: int(np.sum(recent & ((hi == i) | (ai == i)))) for team, i in teams.items()}
    return DixonColes(teams=teams, theta=theta, cov=cov, rho=float(rho), recent=counts)
