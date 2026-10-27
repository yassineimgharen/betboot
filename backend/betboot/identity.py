"""Explicit, versioned provider aliases; never fuzzy name matching.

Football-data.org IDs and canonical names are checked together. Alias matching is
scoped to a provider and league, and must also pass the independent kickoff test.
"""
PL_TEAMS = {
    '57': ('Arsenal FC', ('Arsenal',)),
    '58': ('Aston Villa FC', ('Aston Villa',)),
    '61': ('Chelsea FC', ('Chelsea',)),
    '62': ('Everton FC', ('Everton',)),
    '63': ('Fulham FC', ('Fulham',)),
    '64': ('Liverpool FC', ('Liverpool',)),
    '65': ('Manchester City FC', ('Manchester City',)),
    '66': ('Manchester United FC', ('Manchester United',)),
    '67': ('Newcastle United FC', ('Newcastle United',)),
    '71': ('Sunderland AFC', ('Sunderland',)),
    '73': ('Tottenham Hotspur FC', ('Tottenham Hotspur',)),
    '1044': ('AFC Bournemouth', ('Bournemouth', 'AFC Bournemouth')),
    '1076': ('Coventry City FC', ('Coventry City',)),
    '322': ('Hull City AFC', ('Hull City',)),
    '341': ('Leeds United FC', ('Leeds United',)),
    '349': ('Ipswich Town FC', ('Ipswich Town',)),
    '351': ('Nottingham Forest FC', ('Nottingham Forest',)),
    '354': ('Crystal Palace FC', ('Crystal Palace',)),
    '397': ('Brighton & Hove Albion FC', ('Brighton and Hove Albion', 'Brighton & Hove Albion')),
    '402': ('Brentford FC', ('Brentford',)),
}


# Reviewed exact aliases from the football-data canonical IDs and iSports fixture feed.
ISPORTS_TEAMS = {
    ('FL1','524'): ('Paris Saint-Germain FC', ('Paris Saint Germain (PSG)',)),
    ('FL1','523'): ('Olympique Lyonnais', ('Lyon',)),
    ('PD','559'): ('Sevilla FC', ('Sevilla',)),
}


def normalize(name):
    return ' '.join(name.casefold().split())


def team_matches(name, fixture, side, provider):
    canonical=getattr(fixture, side+'_name')
    if normalize(name)==normalize(canonical):
        return True
    short=getattr(fixture,side+'_short_name',None)
    if fixture.source=='football-data.org' and provider in ('the-odds-api','isports') and short and normalize(name)==normalize(short):
        return True
    if fixture.source=='football-data.org' and provider=='isports':
        reviewed=ISPORTS_TEAMS.get((fixture.league,getattr(fixture,side+'_id')))
        if reviewed and canonical==reviewed[0] and normalize(name) in {normalize(n) for n in reviewed[1]}:return True
    if fixture.source!='football-data.org' or fixture.league!='PL' or provider not in ('the-odds-api','isports'):
        return False
    entry=PL_TEAMS.get(getattr(fixture, side+'_id'))
    return bool(entry and canonical==entry[0] and normalize(name) in {normalize(n) for n in entry[1]})
