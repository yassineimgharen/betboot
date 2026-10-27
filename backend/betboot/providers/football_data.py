import asyncio
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from time import monotonic

import httpx
from pydantic import ValidationError

from betboot.domain import LEAGUES, League, MatchObservation
from betboot.providers.base import ProviderError


class FootballDataProvider:
    """football-data.org v4; token in a header, not URLs or logs.

    One collector process per account. A shared limiter is required before scaling workers.
    Never scrapes football-data.co.uk (an unrelated service).
    """

    source = "football-data.org"

    def __init__(self, client: httpx.AsyncClient, token: str, interval: float = 6.1):
        if not token:
            raise ProviderError("FOOTBALL_DATA_TOKEN is required for collection")
        self.client = client
        self.token = token
        self.interval = max(6.1, interval)
        self._last_request = float("-inf")
        self._lock = asyncio.Lock()

    async def _get(self, path: str, params: dict) -> dict:
        for attempt in range(3):
            async with self._lock:
                await asyncio.sleep(max(0, self.interval - (monotonic() - self._last_request)))
                self._last_request = monotonic()
                try:
                    response = await self.client.get(
                        f"https://api.football-data.org/v4/{path}",
                        params=params,
                        headers={"X-Auth-Token": self.token},
                        timeout=30,
                    )
                except httpx.TransportError:
                    if attempt == 2:
                        raise ProviderError("Provider transport failed after 3 attempts") from None
                    continue
            if response.status_code == 429 or response.status_code >= 500:
                if attempt == 2:
                    raise ProviderError(f"Provider unavailable (HTTP {response.status_code})")
                delay = 2 ** attempt
                retry = response.headers.get("Retry-After")
                if retry:
                    try:
                        delay = float(retry)
                    except ValueError:
                        try:
                            delay = (parsedate_to_datetime(retry) - datetime.now(UTC)).total_seconds()
                        except (TypeError, ValueError):
                            delay = 2 ** attempt
                # Do not retry before a provider's long embargo: fail and let a later job retry.
                if delay > 60:
                    raise ProviderError("Provider rate limit requires a later collection run")
                await asyncio.sleep(max(0, delay))
                continue
            if response.status_code != 200:
                raise ProviderError(f"Provider rejected request (HTTP {response.status_code})")
            try:
                payload = response.json()
            except ValueError:
                raise ProviderError("Provider returned invalid JSON") from None
            if not isinstance(payload, dict):
                raise ProviderError("Provider returned an unexpected response shape")
            return payload
        raise ProviderError("Provider retries exhausted")

    async def matches(self, league: League, season: int) -> list[MatchObservation]:
        if league not in LEAGUES or not 1900 <= season <= 2200:
            raise ValueError("Unsupported league or season")
        data = await self._get(f"competitions/{league}/matches", {"season": season})
        now = datetime.now(UTC)
        try:
            rows = data["matches"]
            if not isinstance(rows, list):
                raise ValueError("Expected matches list")
            result = []
            for row in rows:
                for identifier in (row["id"], row["homeTeam"]["id"], row["awayTeam"]["id"]):
                    if type(identifier) is not int or identifier <= 0:
                        raise ValueError("Missing or invalid provider identity")
                if row["competition"]["code"] != league:
                    raise ValueError("Unexpected competition")
                row_season = datetime.fromisoformat(row["season"]["startDate"]).year
                if row_season != season:
                    raise ValueError("Unexpected season")
                finished = row["status"] == "FINISHED"
                if finished and row["score"].get("duration") != "REGULAR":
                    raise ValueError("Unsupported score duration")
                score = row["score"].get("fullTime", {}) if finished else {}
                result.append(MatchObservation(
                    source=self.source, external_id=str(row["id"]), league=league,
                    season=season, kickoff=row["utcDate"], status=row["status"],
                    home_id=str(row["homeTeam"]["id"]), away_id=str(row["awayTeam"]["id"]),
                    home_name=row["homeTeam"]["name"], away_name=row["awayTeam"]["name"],
                    home_short_name=row["homeTeam"].get("shortName"), away_short_name=row["awayTeam"].get("shortName"),
                    home_goals=score.get("home"), away_goals=score.get("away"),
                    source_updated_at=row.get("lastUpdated"), collected_at=now,
                ))
            return result
        except (KeyError, TypeError, ValueError, ValidationError):
            # Atomic batch rejection: missing scores must never quietly become zero.
            raise ProviderError("Provider batch failed schema/consistency validation") from None
