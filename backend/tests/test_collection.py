from datetime import timedelta

import pytest
from sqlalchemy.orm import Session

from betboot.collection import collect
from betboot.db import IngestRun, Observation, as_of_matches
from betboot.providers.base import ProviderError


class StubProvider:
    source = "synthetic-test-only"

    def __init__(self, rows):
        self.rows = rows

    async def matches(self, league, season):
        return self.rows


async def test_ingestion_replay_and_as_of_corrections(engine, fixture, now):
    provider = StubProvider([fixture])
    first = await collect(engine, provider, "PL", fixture.season)
    second = await collect(engine, provider, "PL", fixture.season)
    assert first["inserted"] == 1
    assert second["inserted"] == 0
    corrected = fixture.model_copy(update={"kickoff": fixture.kickoff + timedelta(days=1),
                                           "collected_at": now + timedelta(minutes=1)})
    await collect(engine, StubProvider([corrected]), "PL", fixture.season)
    with Session(engine) as db:
        assert as_of_matches(db, now)[0].kickoff == fixture.kickoff
        assert as_of_matches(db, now + timedelta(minutes=2))[0].kickoff == corrected.kickoff
        assert db.query(Observation).count() == 2


async def test_failed_batch_is_atomic_and_audited(engine, fixture):
    with pytest.raises(ProviderError, match="duplicate"):
        await collect(engine, StubProvider([fixture, fixture]), "PL", fixture.season)
    with Session(engine) as db:
        assert db.query(Observation).count() == 0
        assert db.query(IngestRun).one().status == "failed"


async def test_cross_league_batch_rejected(engine, fixture):
    with pytest.raises(ProviderError, match="identity mismatch"):
        await collect(engine, StubProvider([fixture]), "SA", fixture.season)


async def test_arbitrary_errors_are_sanitized(engine, fixture):
    class Broken:
        source = "synthetic-test-only"

        async def matches(self, league, season):
            raise RuntimeError("secret-token")

    with pytest.raises(ProviderError, match="internally"):
        await collect(engine, Broken(), "PL", fixture.season)
    with Session(engine) as db:
        assert "secret-token" not in db.query(IngestRun).one().error
