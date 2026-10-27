"""Deterministic model comparison. No model output is treated as verified fact."""
import math
from collections import defaultdict

import numpy as np
from scipy.optimize import minimize_scalar

from betboot.models.poisson import poisson_mass

VERSION = 'research-ensemble-v3-sklearn'


def score_grid(home, away, rho=0):
    grid = np.outer(poisson_mass(home), poisson_mass(away))
    grid[0, 0] *= 1-home*away*rho
    grid[0, 1] *= 1+home*rho
    grid[1, 0] *= 1+away*rho
    grid[1, 1] *= 1-rho
    if np.min(grid) < 0:
        raise ValueError('Invalid Dixon-Coles correction')
    return grid / grid.sum()


def grid_markets(grid):
    i, j = np.indices(grid.shape)
    p = {'home': float(grid[i>j].sum()), 'draw': float(grid[i==j].sum()),
         'away': float(grid[i<j].sum()), 'btts_yes': float(grid[(i>0)&(j>0)].sum())}
    p['btts_no'] = 1-p['btts_yes']
    for line in (1.5, 2.5, 3.5):
        p[f'over_{line}'] = float(grid[i+j>line].sum())
        p[f'under_{line}'] = 1-p[f'over_{line}']
    p.update({'1X': p['home']+p['draw'], 'X2': p['draw']+p['away'], '12': 1-p['draw']})
    return p


def handicap_distribution(grid, line, side='home'):
    """Return full/half wins and losses and pushes; quarter-lines split into two bets."""
    if not math.isfinite(line) or abs(line*4-round(line*4)) > 1e-8 or abs(line)>10:
        raise ValueError('Handicap must be a quarter-goal line between -10 and 10')
    if side not in ('home', 'away'):
        raise ValueError('Invalid handicap side')
    lines = [line] if line*2 == round(line*2) else [line-.25, line+.25]
    out = {str(k): 0.0 for k in (-1., -.5, 0., .5, 1.)}
    for i, row in enumerate(grid):
        for j, p in enumerate(row):
            margin = i-j if side == 'home' else j-i
            settlement = sum(float(np.sign(margin+x)) for x in lines)/len(lines)
            out[str(settlement)] += float(p)
    return out


def handicap_value(distribution, odds):
    win = distribution['1.0'] + distribution['0.5']/2
    loss = distribution['-1.0'] + distribution['-0.5']/2
    return {'fair_odds': 1+loss/win if win else None,
            'expected_value': win*(odds-1)-loss, 'settlement_probabilities': distribution}


def advanced_models(history, fixture, baseline, as_of):
    # Use the exact deduplicated input IDs from the baseline and latest known revisions.
    latest = {}
    for m in sorted(history, key=lambda m: m.collected_at):
        if m.source == fixture.source and m.collected_at <= as_of:
            latest[m.external_id] = m
    rows = [latest[k] for k in baseline['training_match_ids']]
    ratings = defaultdict(lambda: 1500.)
    form = defaultdict(list)
    x, y, rate_history = [], [], []
    for m in rows:
        def features(h, a):
            hr, ar = form[h][-20:], form[a][-20:]
            if len(hr)<5 or len(ar)<5:
                return None
            return [ratings[h]-ratings[a], np.mean([r[0] for r in hr]),
                    np.mean([r[1] for r in hr]), np.mean([r[0] for r in ar]),
                    np.mean([r[1] for r in ar])]
        f = features(m.home_id, m.away_id)
        if f is not None:
            x.append(f); y.append(0 if m.home_goals>m.away_goals else 1 if m.home_goals==m.away_goals else 2)
            rate_history.append((max(.1, (f[1]+f[4])/2), max(.1, (f[3]+f[2])/2), m.home_goals, m.away_goals))
        expected = 1/(1+10**(-(ratings[m.home_id]+65-ratings[m.away_id])/400))
        actual = 1 if m.home_goals>m.away_goals else .5 if m.home_goals==m.away_goals else 0
        delta = 20*(actual-expected)
        ratings[m.home_id] += delta; ratings[m.away_id] -= delta
        form[m.home_id].append((m.home_goals, m.away_goals))
        form[m.away_id].append((m.away_goals, m.home_goals))
    hr, ar = form[fixture.home_id][-20:], form[fixture.away_id][-20:]
    f = [ratings[fixture.home_id]-ratings[fixture.away_id], np.mean([r[0] for r in hr]),
         np.mean([r[1] for r in hr]), np.mean([r[0] for r in ar]), np.mean([r[1] for r in ar])]
    expected = 1/(1+10**(-(f[0]+65)/400))
    draw = (sum(m.home_goals==m.away_goals for m in rows)+2)/(len(rows)+8)
    elo = {'home': (1-draw)*expected, 'draw': draw, 'away': (1-draw)*(1-expected)}
    # Fit low-score dependence on historical one-step rates, never on the target result.
    def loss(rho):
        total = 0.
        for h,a,hg,ag in rate_history:
            tau = (1-h*a*rho if (hg,ag)==(0,0) else 1+h*rho if (hg,ag)==(0,1)
                   else 1+a*rho if (hg,ag)==(1,0) else 1-rho if (hg,ag)==(1,1) else 1.)
            if tau <= 0:
                return 1e12
            total -= math.log(tau)
        return total
    h, a = baseline['home_goal_rate'], baseline['away_goal_rate']
    bound = min(.15, .95/max(h*a, h, a, 1))
    rho = float(minimize_scalar(loss, bounds=(-bound, bound), method='bounded').x) if len(rate_history)>=30 else 0.
    dc = grid_markets(score_grid(h, a, rho))
    models = {'poisson': {k:v['probability'] for k,v in baseline['markets'].items()},
              'dixon_coles': dc, 'elo': elo}
    unavailable = {}
    if len(x)>=120 and len(set(y))==3:
        from sklearn.ensemble import GradientBoostingClassifier
        model = GradientBoostingClassifier(n_estimators=80, max_depth=2, learning_rate=.04, random_state=17)
        model.fit(np.asarray(x), np.asarray(y))
        probs = model.predict_proba(np.asarray([f]))[0]
        models['gradient_boosting'] = dict(zip(('home','draw','away'), map(float, probs)))
    else:
        unavailable['gradient_boosting'] = 'Requires 120 chronological training examples and all outcomes'
    unavailable['xg'] = 'No licensed historical shot-based xG observations available'
    # Fixed equal weights avoid tuning on a holdout. Ensemble itself still requires calibration.
    ensemble = {k: float(np.mean([v[k] for v in models.values() if k in v])) for k in dc}
    ensemble.update({'1X':ensemble['home']+ensemble['draw'], 'X2':ensemble['draw']+ensemble['away'], '12':1-ensemble['draw']})
    return {'version': VERSION, 'models': models, 'ensemble': ensemble, 'rho':rho,
            'unavailable': unavailable, 'calibrated':False,
            'warning':'Model comparison is research output, not validated accuracy'}


def outcome(market, home, away):
    if market in ('home','draw','away','1X','X2','12','btts_yes','btts_no'):
        return {'home':home>away,'draw':home==away,'away':home<away,'1X':home>=away,
                'X2':home<=away,'12':home!=away,'btts_yes':home>0 and away>0,
                'btts_no':home==0 or away==0}[market]
    if market.startswith(('over_', 'under_')):
        line = float(market.split('_')[1])
        return home+away>line if market.startswith('over') else home+away<line
    raise ValueError('Unsupported binary market')
