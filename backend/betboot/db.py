import hashlib
import json
from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, UniqueConstraint, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from betboot.domain import MatchObservation, require_aware


class Base(DeclarativeBase):
    pass


class IngestRun(Base):
    __tablename__ = "ingest_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    source: Mapped[str] = mapped_column(String(100))
    league: Mapped[str] = mapped_column(String(10))
    season: Mapped[int] = mapped_column(Integer)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20))
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(String(500))


class Observation(Base):
    __tablename__ = "match_observations"
    __table_args__ = (UniqueConstraint("fingerprint"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("ingest_runs.id"))
    source: Mapped[str] = mapped_column(String(100), index=True)
    external_id: Mapped[str] = mapped_column(String(100), index=True)
    league: Mapped[str] = mapped_column(String(10), index=True)
    kickoff: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    fingerprint: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSON)


class Prediction(Base):
    __tablename__ = "predictions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    source: Mapped[str] = mapped_column(String(100))
    external_id: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    model_version: Mapped[str] = mapped_column(String(100))
    payload: Mapped[dict] = mapped_column(JSON)


def engine_for(url: str):
    return create_engine(url, pool_pre_ping=True)


def save_observations(session: Session, run_id: str, matches: list[MatchObservation]) -> int:
    inserted = 0
    for match in matches:
        payload = match.model_dump(mode="json")
        fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        if session.query(Observation).filter_by(fingerprint=fingerprint).first() is None:
            session.add(Observation(
                run_id=run_id, source=match.source, external_id=match.external_id,
                league=match.league, kickoff=match.kickoff, collected_at=match.collected_at,
                fingerprint=fingerprint, payload=payload,
            ))
            session.flush()
            inserted += 1
    return inserted


def as_of_matches(session: Session, cutoff: datetime, league: str | None = None):
    """Most recently observed version per source ID, using only records known by cutoff.

    A later correction cannot change a historical as-of view. Provider clocks never
    substitute for collection time. Repeated polls are retained as freshness evidence.
    """
    require_aware(cutoff)
    query = session.query(Observation).filter(Observation.collected_at <= cutoff.astimezone(UTC))
    if league:
        query = query.filter(Observation.league == league)
    rows = query.order_by(Observation.collected_at, Observation.id).all()
    latest = {}
    for row in rows:
        latest[(row.source, row.external_id)] = MatchObservation.model_validate(row.payload)
    return list(latest.values())
