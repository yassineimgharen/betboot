import os
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from betboot.db import Base
from betboot.domain import MatchObservation


@pytest.fixture
def engine():
    # TEST_DATABASE_URL must name a disposable test database: metadata is reset.
    url = os.getenv("TEST_DATABASE_URL", "sqlite://")
    kwargs = {"connect_args": {"check_same_thread": False}, "poolclass": StaticPool} if url == "sqlite://" else {}
    engine = create_engine(url, **kwargs)
    Base.metadata.create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture
def now():
    return datetime.now(UTC)


@pytest.fixture
def fixture(now):
    return MatchObservation(source="synthetic-test-only", external_id="next", league="PL",
                            season=now.year, kickoff=now + timedelta(days=1), status="TIMED",
                            home_id="A", away_id="B", home_name="Test A", away_name="Test B",
                            collected_at=now - timedelta(minutes=5))


@pytest.fixture
def history(fixture, now):
    # Deterministic synthetic observations, exclusively for tests. Never seeded into the app.
    return [fixture.model_copy(update={
        "external_id": str(i), "kickoff": now - timedelta(days=100-i),
        "status": "FINISHED", "home_goals": i % 4, "away_goals": i % 3,
        "collected_at": now - timedelta(days=99-i),
    }) for i in range(80)]
