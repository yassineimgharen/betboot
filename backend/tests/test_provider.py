from datetime import timedelta
from unittest.mock import AsyncMock

import httpx
import pytest

from betboot.providers.base import ProviderError
from betboot.providers.football_data import FootballDataProvider


def payload(now):
    return {"matches": [{"id": 1, "competition": {"code": "PL"},
                         "season": {"startDate": f"{now.year}-08-01"},
                         "utcDate": (now-timedelta(days=2)).isoformat(), "status": "FINISHED",
                         "homeTeam": {"id": 1, "name": "Synthetic A"},
                         "awayTeam": {"id": 2, "name": "Synthetic B"},
                         "score": {"duration": "REGULAR", "fullTime": {"home": 0, "away": 2}},
                         "lastUpdated": (now-timedelta(days=1)).isoformat()}]}


async def test_normalizes_real_zero_and_provenance(now):
    def handler(request):
        assert request.headers["X-Auth-Token"] == "test-key"
        assert "test-key" not in str(request.url)
        return httpx.Response(200, json=payload(now))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        rows = await FootballDataProvider(client, "test-key").matches("PL", now.year)
    assert rows[0].home_goals == 0
    assert rows[0].source == "football-data.org"
    assert rows[0].source_updated_at < rows[0].collected_at


async def test_missing_score_rejects_batch(now):
    data = payload(now)
    data["matches"][0]["score"]["fullTime"]["home"] = None
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=data))) as client:
        with pytest.raises(ProviderError, match="validation"):
            await FootballDataProvider(client, "test-key").matches("PL", now.year)


async def test_rate_limit_retries_then_success(now, monkeypatch):
    sleep = AsyncMock()
    monkeypatch.setattr("betboot.providers.football_data.asyncio.sleep", sleep)
    responses = iter([httpx.Response(429, headers={"Retry-After": "1"}),
                      httpx.Response(503), httpx.Response(200, json=payload(now))])
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: next(responses))) as client:
        rows = await FootballDataProvider(client, "test-key").matches("PL", now.year)
    assert len(rows) == 1
    assert sleep.await_count >= 5


async def test_long_retry_after_does_not_retry_early(now):
    async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda _: httpx.Response(429, headers={"Retry-After": "120"}))) as client:
        with pytest.raises(ProviderError, match="later collection"):
            await FootballDataProvider(client, "test-key").matches("PL", now.year)


async def test_unauthorized_is_sanitized(now):
    async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda _: httpx.Response(403, text="secret-key"))) as client:
        with pytest.raises(ProviderError) as error:
            await FootballDataProvider(client, "secret-key").matches("PL", now.year)
    assert str(error.value) == "Provider rejected request (HTTP 403)"


async def test_token_required():
    async with httpx.AsyncClient() as client:
        with pytest.raises(ProviderError, match="TOKEN"):
            FootballDataProvider(client, "")
