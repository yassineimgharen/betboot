"""Normalise provider market and outcome names to internal market keys (see slipbot.markets).

Examples: "Both Teams Score"/"GG-NG" + "GG" -> ('BTTS', 'yes'); "Double Chance" + "Home or Draw"
-> ('DC', '1X'); "Total Goals" + "Over" at 1.5 (or the outcome "Over 1.5") -> ('OU1.5', 'over');
"Asian Handicap" + "<away team>" at +0.5 -> ('AH-0.50', 'away'). Lines are kept exactly; a name
that cannot be mapped unambiguously returns None (the caller counts it, never guesses).
"""
import re

from slipbot.markets import AH_LINES, GOAL_LINES, ah_market
from slipbot.teams import similarity

FAMILIES = {
    '1X2': ('h2h', 'h2h_3_way', '1x2', 'match result', 'match winner', 'full time result', 'fulltime result',
            'three way', '3way', 'moneyline 3-way'),
    'DC': ('double chance', 'double_chance', 'dc'),
    'BTTS': ('btts', 'both teams to score', 'both teams score', 'both to score', 'gg/ng', 'gg-ng', 'gg ng',
             'goal goal / no goal'),
    'OU': ('totals', 'alternate_totals', 'total goals', 'total', 'over/under', 'goals over/under', 'over under',
           'total goals over/under', 'goal line'),
    'AH': ('spreads', 'alternate_spreads', 'asian handicap', 'handicap', 'asian lines', 'ah'),
}


def _clean(text):
    return re.sub(r'\s+', ' ', str(text or '').strip().casefold())


def family(market_name):
    name = _clean(market_name)
    for fam, names in FAMILIES.items():
        if name in names:
            return fam
    # Names carrying a line or selection, e.g. "Total Goals Over 1.5", "Double Chance 1X".
    for fam, names in FAMILIES.items():
        if any(name.startswith(n + ' ') for n in names if len(n) > 2):
            return fam
    return None


def _side(text, home, away):
    t = _clean(text)
    if t in ('1', 'home', 'w1'):
        return '1'
    if t in ('2', 'away', 'w2'):
        return '2'
    if t in ('x', 'draw', 'tie'):
        return 'X'
    h, a = (similarity(t, home) if home else 0), (similarity(t, away) if away else 0)
    if h >= 0.8 and h > a:
        return '1'
    if a >= 0.8 and a > h:
        return '2'
    return None


def _number(text):
    m = re.search(r'([+-]?\d+(?:\.\d+)?)\s*$', _clean(text))
    return float(m.group(1)) if m else None


def normalize(market_name, outcome_name, point=None, home=None, away=None):
    """-> (market key, selection) or None."""
    fam = family(market_name)
    out = _clean(outcome_name)
    tail = _clean(market_name)
    if fam == '1X2':
        side = _side(out, home, away)
        return ('1X2', {'1': 'home', 'X': 'draw', '2': 'away'}[side]) if side else None
    if fam == 'BTTS':
        if out in ('yes', 'gg', 'y', 'both score'):
            return ('BTTS', 'yes')
        if out in ('no', 'ng', 'n'):
            return ('BTTS', 'no')
        return None
    if fam == 'DC':
        compact = out.replace(' ', '').upper()
        if compact in ('1X', 'X1', 'X2', '2X', '12', '21'):
            sides = set(compact)
        else:
            parts = re.split(r'\s+or\s+|/|\s+-\s+', out)
            sides = {_side(p, home, away) for p in parts}
            if len(parts) != 2 or None in sides:
                return None
        key = ''.join(sorted(sides, key='1X2'.index))
        return ('DC', key) if key in ('1X', 'X2', '12') else None
    if fam == 'OU':
        side = 'over' if out.startswith('over') or out == 'o' else 'under' if out.startswith('under') or out == 'u' else None
        if side is None and (tail.endswith(' over') or tail.endswith(' under')):
            side = tail.rsplit(' ', 1)[1]
        line = point if point is not None else (_number(out) if _number(out) is not None else _number(tail))
        if side is None or line is None or float(line) not in GOAL_LINES:
            return None
        return (f'OU{float(line)}', side)
    if fam == 'AH':
        words = re.sub(r'\s*[+-]?\d+(?:\.\d+)?\s*$', '', out)
        side = _side(words, home, away)
        line = point if point is not None else _number(out)
        if side not in ('1', '2') or line is None:
            return None
        home_handicap = float(line) if side == '1' else -float(line)
        if home_handicap not in AH_LINES:
            return None
        return (ah_market(home_handicap), 'home' if side == '1' else 'away')
    return None
