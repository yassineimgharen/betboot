"""iSports API: schedules, current pre-match odds for every line (odds/all: 1X2, goal totals,
Asian handicap from 18 bookmakers) and the per-date European 1X2 archive (odds/european/all,
used for validation). Hong Kong prices exclude the stake: decimal = price + 1.
"""
from datetime import UTC, datetime, timedelta, timezone

from slipbot.http import SourceError
from slipbot.markets import AH_LINES, GOAL_LINES, ah_market
from slipbot.quotes import Quote, valid_book as _valid

BASE = 'https://api.isportsapi.com/sport/football/'
SPACING = 61                      # documented: 60 seconds per call, per endpoint
BEIJING = timezone(timedelta(hours=8))   # odds/european/all `date` is a UTC+8 calendar day
COMPANIES = {
    '1': 'Macauslot', '3': 'Crown', '4': 'Ladbrokes', '7': 'SNAI', '8': 'Bet365', '9': 'William Hill',
    '12': 'Easybets', '14': 'Vcbet', '17': 'Mansion88', '19': 'Interwetten', '22': '10BET',
    '24': '12bet', '31': 'Sbobet', '35': 'Wewbet', '42': '18bet', '47': 'Pinnacle',
    '48': 'HK Jockey Club', '49': 'Bwin', '50': '1xBet',
}


def _payload(response):
    if not isinstance(response, dict) or response.get('code') != 0:
        message = response.get('message') if isinstance(response, dict) else None
        raise SourceError(f'iSports: {(message or "unexpected response").strip()}')
    return response.get('data')


PRE_MATCH = ('1', '2')     # documented odds type: 1 early, 2 pre-match closing, 3 in-play


def _quote_rows(rows, width, line_at, prices_at, time_at, close_at, type_at, hong_kong, extra_ok=lambda f: True):
    for row in rows:
        f = row.split(',')
        if len(f) != width or f[1] not in COMPANIES or f[type_at] not in PRE_MATCH:
            continue
        if close_at is not None and f[close_at] != 'false':
            continue
        if not extra_ok(f):
            continue
        try:
            line = float(f[line_at]) if line_at is not None else None
            prices = [float(p) + (1 if hong_kong else 0) for p in f[prices_at[0]:prices_at[1]]]
            updated = datetime.fromtimestamp(float(f[time_at]), UTC)
        except ValueError:
            continue
        if _valid(prices):
            yield f[0], COMPANIES[f[1]], line, prices, updated


def parse_all(data):
    """odds/all payload -> {matchId: [Quote]}: every open pre-match line of every bookmaker.

    Documented layouts (docs id 71); prices for handicap and totals are Hong Kong style:
      europeOdds: matchId,company,openH,openD,openA,curH,curD,curA,index,changeTime,close,type
      overUnder:  matchId,company,openLine,openOver,openUnder,line,over,under,index,changeTime,close,type
      handicap:   matchId,company,openLine,openHome,openAway,line,home,away,maintenance,inPlay,
                  index,count,changeTime,close,type   (positive line: the home side gives goals)
    """
    quotes = {}
    for mid, book, _, prices, updated in _quote_rows(data.get('europeOdds', []), 12, None, (5, 8), 9, 10, 11, False):
        quotes.setdefault(mid, []).extend(
            Quote('1X2', s, p, book, updated) for s, p in zip(('home', 'draw', 'away'), prices))
    for mid, book, line, prices, updated in _quote_rows(data.get('overUnder', []), 12, 5, (6, 8), 9, 10, 11, True):
        if line in GOAL_LINES:              # quarter and whole goal lines are not offered
            quotes.setdefault(mid, []).extend(
                Quote(f'OU{line}', s, p, book, updated) for s, p in zip(('over', 'under'), prices))
    for mid, book, line, prices, updated in _quote_rows(
            data.get('handicap', []), 15, 5, (6, 8), 12, 13, 14, True, extra_ok=lambda f: f[8] == 'false'):
        home_handicap = -line
        if home_handicap in AH_LINES:
            quotes.setdefault(mid, []).extend(
                Quote(ah_market(home_handicap), s, p, book, updated) for s, p in zip(('home', 'away'), prices))
    return quotes


class ISports:
    def __init__(self, fetcher, key):
        if not key:
            raise SourceError('ISPORTS_API_KEY is missing from .env')
        self.fetcher = fetcher
        self.key = key

    def _get(self, endpoint, params, cache_key, max_age, stale_ok=False, spacing=SPACING):
        for attempt in range(2):
            try:
                return self.fetcher.get_cached(BASE + endpoint, params={**params, 'api_key': self.key},
                                               slot='isports:' + endpoint, spacing=spacing, stale_ok=stale_ok,
                                               cache_key=cache_key, max_age=max_age, validate=_payload)
            except SourceError as exc:
                # Another process (or a burst) used this endpoint's slot: wait one interval, retry once.
                if attempt or 'frequency' not in str(exc):
                    raise

    def schedule(self, day_utc):
        """Fixtures kicking off on one UTC day (unlike odds/european/all, which uses UTC+8):
        [{matchId, leagueName, matchTime, homeName, awayName, status}]. Refreshed every 12 hours
        (the provider's recommended frequency). A fixture is only offered while odds/all still
        carries open pre-match quotes for it, and football-data.org leagues are re-checked there."""
        return self._get('schedule/basic', {'date': day_utc.isoformat()},
                         f'is:schedule:{day_utc}', 12 * 3600, stale_ok=True) or []

    def past_schedule(self, day_utc, now):
        """Schedule with final scores for a finished UTC day (cached permanently)."""
        settled = day_utc < (now.astimezone(UTC).date() - timedelta(days=1))
        return self._get('schedule/basic', {'date': day_utc.isoformat()}, f'is:schedule:{day_utc}',
                         None if settled else 3600, stale_ok=not settled) or []

    def all_odds(self):
        """{matchId: [Quote]} for every line of every upcoming fixture (next 14 days) in one
        request (about 8 MB; documented limit 10 s per call; cached 5 minutes)."""
        data = self._get('odds/all', {}, 'is:all', 300, spacing=11)
        if isinstance(data, list) and len(data) == 1:
            data = data[0]
        if not isinstance(data, dict):
            raise SourceError('iSports: odds/all returned no odds')
        return parse_all(data)

    def european_day(self, day_utc8, now):
        """Raw odds/european/all rows for one UTC+8 day. Past days never change, so they
        are cached permanently; the provider keeps roughly one month of history."""
        past = day_utc8 < (now.astimezone(BEIJING).date() - timedelta(days=1))
        return self._get('odds/european/all', {'date': day_utc8.isoformat()},
                         f'is:eu:{day_utc8}', None if past else 600) or []


def parse_european(row):
    """odds/european/all row -> latest (pre-close) 1X2 quotes per bookmaker."""
    quotes = []
    for book in row.get('odds', []):
        try:
            f = book['oddsDetail'].split(',')
            prices = [float(p) for p in f[5:8]]
            if len(f) == 8 and _valid(prices):
                updated = datetime.fromtimestamp(float(book['changeTime']), UTC)
                quotes += [Quote('1X2', s, p, f[1], updated) for s, p in zip(('home', 'draw', 'away'), prices)]
        except (KeyError, ValueError, AttributeError):
            continue
    return quotes
