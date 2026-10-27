"""Background cache warm-up: python -m slipbot.prefetch

Started by the bot when a run had to skip provider requests because of rate limits (iSports
allows one schedule request per minute). It fetches the schedules for the search window and
the football-data.org histories, and fits the league models, so the next run is fast. It
only reads public data into .cache/; one instance runs at a time.
"""
from datetime import timedelta
import fcntl

from slipbot import engine
from slipbot.app import utc_days
from slipbot.config import load_settings
from slipbot.http import Fetcher, SourceError
from slipbot.leagues import FD_CODES
from slipbot.sources.football_data import FootballData, utcnow
from slipbot.sources.isports import ISports


def main():
    settings = load_settings()
    settings.cache_dir.mkdir(exist_ok=True)
    with open(settings.cache_dir / 'prefetch.lock', 'w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return 0        # another warm-up is already running
        fetcher = Fetcher(settings.cache_dir)
        now = utcnow()
        if settings.isports_key:
            isports = ISports(fetcher, settings.isports_key)
            for day in utc_days(now, now + timedelta(days=settings.max_days)):
                try:
                    isports.schedule(day)
                except SourceError:
                    pass
        if settings.football_data_token:
            fd = FootballData(fetcher, settings.football_data_token)
            for code in FD_CODES:
                try:
                    engine.cached_league(code, fd.history(code, now), now, settings.cache_dir)
                except SourceError:
                    pass
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
