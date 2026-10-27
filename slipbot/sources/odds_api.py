"""The Odds API v4 (the-odds-api.com): 1X2 and totals per league, plus double chance,
both teams to score and alternate totals per event (the "additional markets").
Optional: used when ODDS_API_KEY is set and the monthly credits are not exhausted."""
from datetime import datetime

from slipbot.http import SourceError
from slipbot.market_names import normalize
from slipbot.markets import selections
from slipbot.quotes import FAIR_TOTAL, Quote, valid_book as _valid

BASE = 'https://api.the-odds-api.com/v4/sports/{sport}/odds'
EVENT = 'https://api.the-odds-api.com/v4/sports/{sport}/events/{event}/odds'
EXTRA_MARKETS = 'double_chance,btts,alternate_totals,alternate_spreads'


def _events(payload):
    if not isinstance(payload, list):
        message = payload.get('message') if isinstance(payload, dict) else None
        raise SourceError(f'The Odds API: {message or "unexpected response"}')
    return payload


def double_chance_selection(name, home, away):
    """'Arsenal or Draw', 'Draw/Chelsea', 'Home/Away'... -> '1X' / 'X2' / '12', or None."""
    mapped = normalize('double chance', name, home=home, away=away)
    return mapped[1] if mapped else None


def parse_event(event, unmapped=None):
    """One event -> [Quote]. Every outcome goes through the shared name normaliser; outcomes
    it cannot map are counted in `unmapped` (a Counter), never guessed. A bookmaker's market
    is used only when all of its selections are present and form a plausible book."""
    quotes = []
    home, away = event['home_team'], event['away_team']
    for book in event.get('bookmakers', []):
        for market in book.get('markets', []):
            updated = datetime.fromisoformat(market['last_update'].replace('Z', '+00:00'))
            groups = {}
            for o in market.get('outcomes', []):
                mapped = normalize(market['key'], o.get('name'), o.get('point'), home, away)
                if mapped is None:
                    if unmapped is not None:
                        unmapped[f'{market["key"]}: {o.get("name")}'] += 1
                    continue
                groups.setdefault(mapped[0], {})[mapped[1]] = float(o['price'])
            for key, prices in groups.items():
                if set(prices) == set(selections(key)) and _valid(list(prices.values()),
                                                                  fair_total=FAIR_TOTAL.get(key, 1.0)):
                    quotes += [Quote(key, s, p, book['title'], updated) for s, p in prices.items()]
    return quotes


class OddsApi:
    def __init__(self, fetcher, key):
        if not key:
            raise SourceError('ODDS_API_KEY is not set')
        self.fetcher = fetcher
        self.key = key

    def league_events(self, sport_key):
        """Upcoming events with quotes for one league (3 credits; cached 10 minutes)."""
        return self.fetcher.get_cached(
            BASE.format(sport=sport_key),
            params={'apiKey': self.key, 'regions': 'eu', 'markets': 'h2h,totals,spreads', 'oddsFormat': 'decimal'},
            slot='the-odds-api', spacing=1, cache_key=f'oa:{sport_key}', max_age=600, validate=_events)

    def event_markets(self, sport_key, event_id):
        """Double chance, BTTS and alternate totals for one event (about 3 credits; 10 minutes)."""
        def single(payload):
            if not isinstance(payload, dict) or 'bookmakers' not in payload:
                raise SourceError(f'The Odds API: {payload.get("message") if isinstance(payload, dict) else "unexpected response"}')
            return payload
        return self.fetcher.get_cached(
            EVENT.format(sport=sport_key, event=event_id),
            params={'apiKey': self.key, 'regions': 'eu', 'markets': EXTRA_MARKETS, 'oddsFormat': 'decimal'},
            slot='the-odds-api', spacing=1, cache_key=f'oa:event:{event_id}', max_age=600, validate=single)
