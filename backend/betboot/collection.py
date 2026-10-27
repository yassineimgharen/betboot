import logging
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy.orm import Session

from betboot.db import IngestRun, save_observations
from betboot.domain import League
from betboot.providers.base import MatchProvider, ProviderError

logger = logging.getLogger(__name__)


async def collect(engine, provider: MatchProvider, league: League, season: int) -> dict:
    run_id = str(uuid4())
    with Session(engine) as session:
        session.add(IngestRun(id=run_id, source=provider.source, league=league, season=season,
                              started_at=datetime.now(UTC), status="running", row_count=0))
        session.commit()
    try:
        matches = await provider.matches(league, season)
        if any(m.source != provider.source or m.league != league or m.season != season
               for m in matches):
            raise ProviderError("Provider batch identity mismatch")
        ids = [(m.source, m.external_id) for m in matches]
        if len(set(ids)) != len(ids):
            raise ProviderError("Provider batch contains duplicate match identities")
        with Session(engine) as session, session.begin():
            count = save_observations(session, run_id, matches)
            run = session.get(IngestRun, run_id)
            run.status, run.row_count, run.finished_at = "success", count, datetime.now(UTC)
        logger.info("collection_complete run=%s league=%s rows=%d", run_id, league, count)
        return {"run_id": run_id, "status": "success", "inserted": count}
    except Exception as exc:
        # Never log HTTP request objects or arbitrary exception messages with credentials.
        message = str(exc) if isinstance(exc, ProviderError) else "Collection failed internally"
        with Session(engine) as session, session.begin():
            run = session.get(IngestRun, run_id)
            run.status, run.error, run.finished_at = "failed", message, datetime.now(UTC)
        logger.error("collection_failed run=%s league=%s type=%s", run_id, league, type(exc).__name__)
        raise ProviderError(message) from None
