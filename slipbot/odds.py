"""Fixture discovery across providers, and verified quotes for each fixture."""
from dataclasses import replace
from datetime import UTC, datetime

from slipbot.http import SourceError
from slipbot.leagues import ANY, BY_ISPORTS_NAME, LEAGUES
from slipbot.sources.football_data import Match
from slipbot.sources.odds_api import OddsApi, parse_event
from slipbot.teams import best_match, normalise

KICKOFF_TOLERANCE = 300      # seconds; a different kickoff means a different or moved fixture


def _names(match, side):
    return (getattr(match, side), getattr(match, side + '_short'))


def _kickoff(row):
    return datetime.fromtimestamp(float(row['matchTime']), UTC)


def discover_isports(rows, codes, start, end, main):
    """Not-started fixtures in [start, end) of every competition in `codes` that football-data.org
    does not cover (ANY admits competitions outside the named list). Only fixtures with open
    pre-match quotes in the odds feed count: the schedule may be up to 12 hours old, the odds
    feed is minutes old."""
    found = []
    for r in rows:
        name = r.get('leagueName') or ''
        code = BY_ISPORTS_NAME.get(name)
        if code is None:
            if ANY not in codes or not name:
                continue
            code = name
        elif code not in codes or LEAGUES[code].fd:
            continue
        if r.get('status') != 0:
            continue
        kickoff = _kickoff(r)
        if start <= kickoff < end and str(r['matchId']) in main:
            found.append(Match(id=f'is:{r["matchId"]}', league=code, kickoff=kickoff, status='TIMED',
                               home_id=normalise(r['homeName']), away_id=normalise(r['awayName']),
                               home=r['homeName'], away=r['awayName'], home_short=r['homeName'],
                               away_short=r['awayName'], isports_id=str(r['matchId'])))
    return found


def link_isports(fixtures, rows):
    """football-data.org fixtures with their iSports matchId (league, kickoff and both names
    must agree uniquely); fixtures without a unique match are returned unchanged."""
    linked = []
    for match in fixtures:
        league = LEAGUES[match.league].isports_name
        candidates = [(r['homeName'], r['awayName'], r) for r in rows
                      if r.get('leagueName') == league and r.get('status') == 0
                      and abs(float(r['matchTime']) - match.kickoff.timestamp()) <= KICKOFF_TOLERANCE]
        row = best_match(_names(match, 'home'), _names(match, 'away'), candidates)
        linked.append(replace(match, isports_id=str(row['matchId'])) if row else match)
    return linked


def odds_api_quotes(fixtures, fetcher, settings, notes):
    """{match id: [Quote]} from The Odds API, including additional markets for a bounded
    number of events (they are billed per event)."""
    if not settings.odds_api_key or not fixtures:
        return {}
    source = OddsApi(fetcher, settings.odds_api_key)
    found = {}
    extra_budget = settings.extra_market_events
    for code in sorted({m.league for m in fixtures if m.league in LEAGUES}):
        try:
            events = source.league_events(LEAGUES[code].odds_api_key)
        except SourceError as exc:
            hint = ' (invalid key or monthly credits used up)' if 'HTTP 401' in str(exc) else ''
            notes.append(f'{exc}{hint}')
            return found     # quota or key problems affect every league alike
        for match in (m for m in fixtures if m.league == code):
            candidates = []
            for e in events:
                start = datetime.fromisoformat(e['commence_time'].replace('Z', '+00:00'))
                if abs((start - match.kickoff).total_seconds()) <= KICKOFF_TOLERANCE:
                    candidates.append((e['home_team'], e['away_team'], e))
            event = best_match(_names(match, 'home'), _names(match, 'away'), candidates)
            if not event:
                continue
            found[match.id] = parse_event(event)
            if extra_budget > 0:
                extra_budget -= 1
                try:
                    detail = source.event_markets(LEAGUES[code].odds_api_key, event['id'])
                    found[match.id] += parse_event({**detail, 'home_team': event['home_team'],
                                                    'away_team': event['away_team']})
                except SourceError as exc:
                    notes.append(str(exc))
                    extra_budget = 0
    return found


def merge(*sources):
    """Combine {match id: [Quote]} maps. When two providers carry the same bookmaker and
    market, the more recently updated provider's set of quotes is kept whole."""
    merged = {}
    for source in sources:
        for match_id, quotes in source.items():
            groups = merged.setdefault(match_id, {})
            incoming = {}
            for quote in quotes:
                incoming.setdefault((quote.bookmaker.casefold(), quote.market), []).append(quote)
            for key, group in incoming.items():
                newest = max(x.updated for x in group)
                if key not in groups or newest > max(x.updated for x in groups[key]):
                    groups[key] = group
    return {m: [x for group in g.values() for x in group] for m, g in merged.items()}
