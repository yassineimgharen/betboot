import argparse
import asyncio
import json
import logging

import httpx

from betboot.collection import collect
from betboot.config import Settings
from betboot.db import engine_for
from betboot.domain import LEAGUES
from betboot.providers.base import ProviderError
from betboot.providers.football_data import FootballDataProvider


async def run_collection(args):
    settings = Settings()
    engine = engine_for(settings.database_url)
    failed = False
    try:
        async with httpx.AsyncClient(follow_redirects=False) as client:
            provider = FootballDataProvider(client, settings.football_data_token.get_secret_value(),
                                            settings.provider_request_interval)
            for league in (LEAGUES if args.league == "all" else [args.league]):
                try:
                    print(json.dumps(await collect(engine, provider, league, args.season)))
                except ProviderError as exc:
                    failed = True
                    print(json.dumps({"league": league, "status": "failed", "error": str(exc)}))
    finally:
        engine.dispose()
    return 1 if failed else 0


def main():
    parser = argparse.ArgumentParser(description="Betboot observation collector")
    sub = parser.add_subparsers(dest="command", required=True)
    sync = sub.add_parser("collect", help="Collect fixtures and final scores for a season")
    sync.add_argument("--league", choices=[*LEAGUES, "all"], default="all")
    sync.add_argument("--season", type=int, required=True, help="Season start year, e.g. 2026")
    args = parser.parse_args()
    if not 1900 <= args.season <= 2200:
        parser.error("season must be between 1900 and 2200")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    # Third-party request logs are not needed; future odds providers may use query-string secrets.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try:
        raise SystemExit(asyncio.run(run_collection(args)))
    except ProviderError as exc:
        parser.exit(1, f"{exc}\n")
