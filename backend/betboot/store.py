from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import JSON, DateTime, String
from sqlalchemy.orm import Mapped, Session, mapped_column

from betboot.db import Base


class Artifact(Base):
    __tablename__='research_artifacts'
    id: Mapped[str]=mapped_column(String(36),primary_key=True)
    kind: Mapped[str]=mapped_column(String(30),index=True)
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),index=True)
    payload: Mapped[dict]=mapped_column(JSON)


def save(engine,kind,payload):
    identifier=str(uuid4())
    with Session(engine) as db, db.begin():
        db.add(Artifact(id=identifier,kind=kind,created_at=datetime.now(UTC),payload=payload))
    return identifier


def read(engine,kind,limit=100):
    with Session(engine) as db:
        return [{'id':r.id,**r.payload} for r in db.query(Artifact).filter_by(kind=kind).order_by(Artifact.created_at.desc()).limit(limit)]
