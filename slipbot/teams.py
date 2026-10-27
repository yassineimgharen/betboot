"""Cross-provider team identity: football-data.org names vs. bookmaker feed names."""
from difflib import SequenceMatcher
import re
import unicodedata

NOISE = {'fc', 'cf', 'ac', 'as', 'ss', 'us', 'ssc', 'sc', 'afc', 'cfc', 'calcio', 'club', 'de', 'la',
         'sad', 'rcd', 'ud', 'cd', 'sd', 'rc', 'og', 'sv', 'vfb', 'vfl', 'tsg', 'bc', 'ogc', 'stade',
         'olympique', 'hsc', 'aj', 'losc', 'ca', 'balompie', 'futbol', 'and', 'hove', 'albion'}
# Names that share almost no characters across providers.
ALIASES = {
    'internazionale milano': 'inter', 'inter milan': 'inter', 'wolverhampton wanderers': 'wolves',
    'paris saint germain': 'psg', 'paris sg': 'psg', 'bayern munchen': 'bayern', 'bayern munich': 'bayern',
    'borussia monchengladbach': 'gladbach', 'monchengladbach': 'gladbach', 'b monchengladbach': 'gladbach',
    'athletic bilbao': 'athletic', 'athletic': 'athletic', 'atletico madrid': 'atletico',
    'atletico de madrid': 'atletico', 'manchester united': 'man united', 'manchester city': 'man city',
    'man utd': 'man united', 'nottingham forest': 'nottingham', "nott'm forest": 'nottingham',
    'tottenham hotspur': 'tottenham', 'spurs': 'tottenham', 'newcastle united': 'newcastle',
    'west ham united': 'west ham', 'brighton hove albion': 'brighton', 'leeds united': 'leeds',
    'koln': 'cologne', '1 fc koln': 'cologne', 'mainz 05': 'mainz', '1 fsv mainz 05': 'mainz',
    'bayer 04 leverkusen': 'leverkusen', 'bayer leverkusen': 'leverkusen', 'deportivo alaves': 'alaves',
    'real betis balompie': 'betis', 'real betis': 'betis', 'celta de vigo': 'celta', 'celta vigo': 'celta',
    'hellas verona': 'verona', 'as roma': 'roma', 'ss lazio': 'lazio', 'olympique lyonnais': 'lyon',
    'olympique de marseille': 'marseille', 'stade rennais': 'rennes', 'rennais': 'rennes',
    'racing club de lens': 'lens', 'rc strasbourg alsace': 'strasbourg', 'strasbourg alsace': 'strasbourg',
    'paris fc': 'paris fc', 'st pauli': 'st pauli', 'fc st pauli 1910': 'st pauli',
}


def normalise(name):
    text = unicodedata.normalize('NFKD', name).encode('ascii', 'ignore').decode().casefold()
    text = re.sub(r'[^a-z0-9\' ]+', ' ', text).strip()
    text = re.sub(r'\s+', ' ', text)
    if text in ALIASES:
        return ALIASES[text]
    tokens = [t for t in text.split() if t not in NOISE and not re.fullmatch(r'\d+', t)]
    core = ' '.join(tokens) or text
    return ALIASES.get(core, core)


def similarity(a, b):
    a, b = normalise(a), normalise(b)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    ta, tb = a.split(), b.split()
    # "Inter" vs "Inter Milan", "Wolfsburg" vs "VfL Wolfsburg".
    if set(ta) <= set(tb) or set(tb) <= set(ta):
        return 0.9
    return SequenceMatcher(None, a, b).ratio()


def best_match(home_names, away_names, candidates, minimum=0.55, margin=0.15):
    """Pick the unique candidate (home, away, payload) agreeing with both team names.
    Returns None when nothing agrees well enough or two candidates are too close."""
    scored = []
    for home, away, payload in candidates:
        h = max(similarity(n, home) for n in home_names)
        a = max(similarity(n, away) for n in away_names)
        if h >= minimum and a >= minimum:
            scored.append((h + a, payload))
    scored.sort(key=lambda s: -s[0])
    if not scored or (len(scored) > 1 and scored[0][0] - scored[1][0] < margin):
        return None
    return scored[0][1]


def display_name(name):
    """'Venezia FC' -> 'Venezia'; leaves names like 'AC Milan' or 'Paris FC' alone."""
    stripped = re.sub(r'^(?:FC|AFC|CF) |\s(?:FC|AFC|CF)$', '', name).strip()
    return name if stripped in ('Paris', '') else stripped
