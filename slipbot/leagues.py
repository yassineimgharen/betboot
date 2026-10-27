"""Supported competitions and their identifiers at each provider.

`fd` competitions are verified and modelled with football-data.org (free tier) fixtures and
history. The others are discovered through the iSports schedule and priced from the
bookmaker consensus, which is validated out of sample (docs/VALIDATION.md); they have no
statistical model of their own.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class League:
    code: str
    name: str
    country: str
    isports_name: str    # leagueName in iSports feeds
    odds_api_key: str    # sport key in The Odds API
    aliases: tuple[str, ...]
    fd: bool = False     # football-data.org fixtures + history (Dixon-Coles / Elo models)
    core: bool = False   # searched first


LEAGUES = {
    l.code: l for l in (
        League('PL', 'Premier League', 'England', 'English Premier League', 'soccer_epl',
               ('premier league', 'epl', 'england', 'english', 'angleterre', 'pl'), fd=True, core=True),
        League('PD', 'La Liga', 'Spain', 'Spanish La Liga', 'soccer_spain_la_liga',
               ('la liga', 'laliga', 'spain', 'spanish', 'espagne', 'sbanya', 'spania'), fd=True, core=True),
        League('SA', 'Serie A', 'Italy', 'Italy Serie A', 'soccer_italy_serie_a',
               ('serie a', 'italy', 'italian', 'italie', 'calcio', 'italia'), fd=True, core=True),
        League('BL1', 'Bundesliga', 'Germany', 'German Bundesliga', 'soccer_germany_bundesliga',
               ('bundesliga', 'germany', 'german', 'allemagne', 'almania', 'lalmania'), fd=True, core=True),
        League('FL1', 'Ligue 1', 'France', 'France Ligue 1', 'soccer_france_ligue_one',
               ('ligue 1', 'ligue1', 'france', 'french', 'fransa'), fd=True, core=True),
        League('PPL', 'Primeira Liga', 'Portugal', 'Liga Portugal 1', 'soccer_portugal_primeira_liga',
               ('portugal', 'portuguese', 'primeira', 'liga portugal', 'bortugal'), fd=True),
        League('DED', 'Eredivisie', 'Netherlands', 'Holland Eredivisie', 'soccer_netherlands_eredivisie',
               ('eredivisie', 'netherlands', 'holland', 'dutch', 'hollanda'), fd=True),
        League('ELC', 'Championship', 'England', 'England Championship', 'soccer_efl_champ',
               ('championship', 'efl'), fd=True),
        League('BE1', 'Pro League', 'Belgium', 'Belgian Pro League', 'soccer_belgium_first_div',
               ('belgium', 'belgian', 'belgique', 'jupiler', 'pro league', 'beljika')),
        League('TR1', 'Süper Lig', 'Turkey', 'Turkey Super Lig', 'soccer_turkey_super_league',
               ('turkey', 'turkish', 'turquie', 'super lig', 'turkia')),
        League('CL', 'Champions League', 'Europe', 'UEFA Champions League', 'soccer_uefa_champs_league',
               ('champions league', 'ucl', 'ldc', 'ligue des champions')),
        League('EL', 'Europa League', 'Europe', 'UEFA Europa League', 'soccer_uefa_europa_league',
               ('europa league', 'uel')),
        League('ECL', 'Conference League', 'Europe', 'UEFA Conference League',
               'soccer_uefa_europa_conference_league', ('conference league', 'uecl')),
        League('SC1', 'Premiership', 'Scotland', 'Scottish Premiership', 'soccer_spl',
               ('scotland', 'scottish', 'ecosse')),
        League('AT1', 'Bundesliga', 'Austria', 'Austrian Bundesliga', 'soccer_austria_bundesliga',
               ('austria', 'austrian', 'autriche')),
        League('CH1', 'Super League', 'Switzerland', 'Switzerland Super League', 'soccer_switzerland_superleague',
               ('switzerland', 'swiss', 'suisse')),
        League('GR1', 'Super League', 'Greece', 'Greece Super League A', 'soccer_greece_super_league',
               ('greece', 'greek', 'grece')),
        League('DK1', 'Superliga', 'Denmark', 'Denmark Superliga', 'soccer_denmark_superliga',
               ('denmark', 'danish', 'danemark')),
    )
}
CORE = tuple(code for code, l in LEAGUES.items() if l.core)
FD_CODES = tuple(code for code, l in LEAGUES.items() if l.fd)
BY_ISPORTS_NAME = {l.isports_name: code for code, l in LEAGUES.items()}
# Any other competition iSports lists with odds (cups, second tiers, other countries): priced
# from the bookmaker consensus with an extra uncertainty penalty. Matches keep their iSports
# league name as `league`.
ANY = '*'
ALL_COMPETITIONS = tuple(LEAGUES) + (ANY,)
