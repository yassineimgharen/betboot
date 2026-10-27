"""Settings from .env. Only data-provider keys; never bookmaker-account credentials."""
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import os

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent


def _float(name, default):
    try:
        return float(os.getenv(name, default))
    except ValueError:
        return float(default)


def _zone(name):
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo('Africa/Casablanca')


@dataclass(frozen=True)
class Settings:
    football_data_token: str
    isports_key: str
    odds_api_key: str
    min_odds: float          # strict: selected odds must be greater than this
    extra_market_events: int  # events per run for which The Odds API additional markets are bought
    max_odds_age: float      # seconds since the bookmaker last changed the price (feeds are current snapshots)
    max_days: float          # days ahead the background warm-up keeps schedules for
    timezone: ZoneInfo       # decides which calendar day a kickoff belongs to, and how times are shown
    time_budget: float       # seconds a run may spend waiting on provider rate limits
    preferred_bookmaker: str
    cache_dir: Path


def load_settings():
    load_dotenv(ROOT / '.env')
    return Settings(
        football_data_token=os.getenv('FOOTBALL_DATA_TOKEN', '').strip(),
        isports_key=os.getenv('ISPORTS_API_KEY', '').strip(),
        odds_api_key=os.getenv('ODDS_API_KEY', '').strip(),
        min_odds=_float('SLIP_MIN_ODDS', 1.30),
        extra_market_events=int(_float('SLIP_EXTRA_MARKET_EVENTS', 10)),
        max_odds_age=_float('SLIP_MAX_ODDS_AGE_HOURS', 48) * 3600,
        max_days=min(_float('SLIP_MAX_DAYS', 7), 9),     # football-data.org allows 10-day ranges
        time_budget=_float('SLIP_TIME_BUDGET', 45),
        timezone=_zone(os.getenv('SLIP_TIMEZONE', 'Africa/Casablanca')),
        preferred_bookmaker=os.getenv('SLIP_BOOKMAKER', '1xBet').strip(),
        cache_dir=ROOT / '.cache',
    )
