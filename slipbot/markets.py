"""Betting-market probabilities from scoreline matrices, and how markets settle.

Market keys: '1X2', 'DC', 'BTTS', 'OU<line>' (half-goal totals 0.5-4.5) and 'AH<±h>'
(Asian handicap, h = goals added to the home side: 'AH-0.50' is home -0.5 / away +0.5).
Asian handicaps on whole lines can push (stake returned); quarter lines put half the stake
on each neighbouring line, so they can be half won or half lost.
"""
import numpy as np

GOAL_LINES = (0.5, 1.5, 2.5, 3.5, 4.5)
AH_LINES = tuple(q / 4 for q in range(-10, 11))       # -2.5 ... +2.5 in quarter goals

LABELS = {
    ('1X2', 'home'): '{home} Win (1)', ('1X2', 'draw'): 'Draw (X)', ('1X2', 'away'): '{away} Win (2)',
    ('DC', '1X'): 'Double Chance 1X ({home} or Draw)', ('DC', 'X2'): 'Double Chance X2 (Draw or {away})',
    ('DC', '12'): 'Double Chance 12 (No Draw)',
    ('BTTS', 'yes'): 'BTTS Yes', ('BTTS', 'no'): 'BTTS No',
}


def ah_market(home_handicap):
    return f'AH{home_handicap + 0.0:+.2f}'      # + 0.0 turns -0.0 into 0.0


def ah_line(market):
    return float(market[2:])


def family(market):
    """Validation record key: each goal line separately, all handicap lines together."""
    return 'AH' if market.startswith('AH') else market


def selections(market):
    if market == '1X2':
        return ('home', 'draw', 'away')
    if market == 'DC':
        return ('1X', 'X2', '12')
    if market == 'BTTS':
        return ('yes', 'no')
    if market.startswith('OU'):
        return ('over', 'under')
    if market.startswith('AH'):
        return ('home', 'away')
    raise ValueError(market)


def _signed(x):
    text = f'{x:+.2f}'.rstrip('0')
    return text + '0' if text.endswith('.') else text


def label(market, selection, home, away):
    if market.startswith('OU'):
        return f'{selection.capitalize()} {market[2:]} Goals'
    if market.startswith('AH'):
        h = ah_line(market)
        team, line = (home, h) if selection == 'home' else (away, -h)
        return f'Asian Handicap {team} ' + ('0 (Draw No Bet)' if line == 0 else _signed(line))
    return LABELS[(market, selection)].format(home=home, away=away)


def _grid(matrix):
    g = matrix.shape[-1]
    return np.meshgrid(np.arange(g), np.arange(g), indexing='ij')


def probabilities(matrix):
    """Score matrix (..., G, G) -> {(market, selection): probability (array if batched)}
    for 1X2, double chance, goal totals and BTTS."""
    i, j = _grid(matrix)

    def p(mask):
        return (matrix * mask).sum(axis=(-2, -1))

    home, draw, away = p(i > j), p(i == j), p(i < j)
    out = {('1X2', 'home'): home, ('1X2', 'draw'): draw, ('1X2', 'away'): away,
           ('DC', '1X'): home + draw, ('DC', 'X2'): draw + away, ('DC', '12'): home + away}
    for line in GOAL_LINES:
        over = p(i + j > line)
        out[(f'OU{line}', 'over')], out[(f'OU{line}', 'under')] = over, 1 - over
    btts = p((i > 0) & (j > 0))
    out[('BTTS', 'yes')], out[('BTTS', 'no')] = btts, 1 - btts
    return out


def _components(home_handicap):
    """A quarter line is two half-stakes on the neighbouring lines."""
    if (home_handicap * 4) % 2:
        return ((home_handicap - 0.25, 0.5), (home_handicap + 0.25, 0.5))
    return ((home_handicap, 1.0),)


def handicap(matrix, home_handicap):
    """{side: (expected stake fraction won, expected stake fraction lost)} for one Asian
    handicap line, from the joint score distribution. The rest of the stake is returned."""
    i, j = _grid(matrix)
    out = {}
    for side, margin in (('home', i - j), ('away', j - i)):
        won = lost = 0.0
        for line, weight in _components(home_handicap if side == 'home' else -home_handicap):
            won = won + weight * (matrix * (margin + line > 0)).sum(axis=(-2, -1))
            lost = lost + weight * (matrix * (margin + line < 0)).sum(axis=(-2, -1))
        out[side] = (won, lost)
    return out


def decided_probability(won, lost):
    """Probability of winning given the stake is not returned: what odds price, and the
    figure shown for handicap selections (for half lines it is simply P(win))."""
    return won / np.maximum(won + lost, 1e-12)


def settle(market, selection, home_goals, away_goals):
    """(stake fraction won, stake fraction lost) on the full-time score."""
    if market.startswith('AH'):
        h = ah_line(market)
        margin = home_goals - away_goals if selection == 'home' else away_goals - home_goals
        gained = forfeited = 0.0
        for line, weight in _components(h if selection == 'home' else -h):
            gained += weight * (margin + line > 0)
            forfeited += weight * (margin + line < 0)
        return gained, forfeited
    hit = won(market, selection, home_goals, away_goals)
    return (1.0, 0.0) if hit else (0.0, 1.0)


def won(market, selection, home_goals, away_goals):
    """Settle a non-handicap selection on the full-time score."""
    if market == '1X2':
        return selection == ('home' if home_goals > away_goals else 'draw' if home_goals == away_goals else 'away')
    if market == 'DC':
        result = '1' if home_goals > away_goals else 'X' if home_goals == away_goals else '2'
        return result in selection
    if market.startswith('OU'):
        over = home_goals + away_goals > float(market[2:])
        return over if selection == 'over' else not over
    if market == 'BTTS':
        both = home_goals > 0 and away_goals > 0
        return both if selection == 'yes' else not both
    raise ValueError(market)
