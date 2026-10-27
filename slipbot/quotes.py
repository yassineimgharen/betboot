"""Bookmaker quotes: sanity checks, margin-free market consensus, and the price to show."""
from dataclasses import dataclass
from datetime import datetime
import math
import statistics

from slipbot.markets import selections

# Fair probabilities of a market's selections sum to this (each double-chance outcome
# covers two of the three results, so a fair double-chance book sums to 2).
FAIR_TOTAL = {'DC': 2.0}
# Shown when the preferred bookmaker has no quote; widely available European books first.
FALLBACK_BOOKS = ('Pinnacle', 'Bet365', 'Bwin', 'William Hill', 'Unibet', 'Betfair', 'Ladbrokes',
                  'Interwetten', '10BET', 'Marathon Bet', 'Betsson', 'SNAI')


@dataclass(frozen=True)
class Quote:
    market: str          # see slipbot.markets for the keys
    selection: str
    price: float         # decimal odds
    bookmaker: str
    updated: datetime


def valid_book(prices, low=0.97, high=1.25, fair_total=1.0):
    """Finite prices above 1 whose implied probabilities sum to a plausible book."""
    return (all(math.isfinite(p) and p > 1 for p in prices)
            and low * fair_total <= sum(1 / p for p in prices) <= high * fair_total)


def books(quotes, market, now, max_age):
    """{bookmaker: {selection: Quote}} for complete, recently updated books in one market."""
    wanted = selections(market)
    grouped = {}
    for q in quotes:
        if q.market == market and 0 <= (now - q.updated).total_seconds() <= max_age:
            grouped.setdefault(q.bookmaker, {})[q.selection] = q
    total = FAIR_TOTAL.get(market, 1.0)
    return {b: sel for b, sel in grouped.items()
            if set(sel) == set(wanted) and valid_book([sel[s].price for s in wanted], fair_total=total)}


def _fair_by_book(book_map, market):
    wanted = selections(market)
    total = FAIR_TOTAL.get(market, 1.0)
    rows = []
    for sel in book_map.values():
        implied = [1 / sel[s].price for s in wanted]
        rows.append([p * total / sum(implied) for p in implied])
    return wanted, total, rows


def consensus(book_map, market):
    """Median margin-free probabilities across bookmakers, renormalised; None if no books."""
    if not book_map:
        return None
    wanted, total, rows = _fair_by_book(book_map, market)
    medians = [statistics.median(col) for col in zip(*rows)]
    return {s: m * total / sum(medians) for s, m in zip(wanted, medians)}


def dispersion(book_map, market):
    """Standard deviation across bookmakers of each selection's margin-free probability:
    how much the market itself disagrees about the estimate."""
    wanted, _, rows = _fair_by_book(book_map, market)
    if len(rows) < 2:
        return {s: 0.0 for s in wanted}
    return {s: statistics.stdev(col) for s, col in zip(wanted, zip(*rows))}


def price_to_show(book_map, selection, preferred, tolerance=0.15):
    """The preferred bookmaker's quote, else the first fallback book. Quotes far from the
    median price (stale or erroneous lines) are never shown."""
    prices = [sel[selection].price for sel in book_map.values()]
    if not prices:
        return None
    median = statistics.median(prices)
    for book in (preferred, *FALLBACK_BOOKS):
        for name, sel in book_map.items():
            if name.casefold() == book.casefold():
                quote = sel[selection]
                if abs(quote.price / median - 1) <= tolerance:
                    return quote
    return None
