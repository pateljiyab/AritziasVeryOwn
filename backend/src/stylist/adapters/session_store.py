"""Save sessions and every turn to SQLite (NFR-6), in data/sessions.db.

Kept apart from catalog.db so re-running ingest can't touch them. A session row holds the
latest SessionState as JSON; each turn row records what came in, how it was parsed, what was
shown, and how long it took, so G2 (no constraint violations) can be checked by scanning
the turns table.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, ForeignKey, String, create_engine, func, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from stylist.core.feedback import SessionState
from stylist.jobs.ingest import ROOT

SESSIONS_DB_URL = f"sqlite:///{ROOT / 'data' / 'sessions.db'}"


class Base(DeclarativeBase):
    pass


class SessionRow(Base):
    __tablename__ = "sessions"

    session_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    state: Mapped[dict | None] = mapped_column(JSON)  # SessionState, None before the first outfit
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class TurnRow(Base):
    __tablename__ = "turns"

    turn_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.session_id"), index=True)
    kind: Mapped[str] = mapped_column(String(10))  # "message" or "action"
    input: Mapped[dict] = mapped_column(JSON)  # {"text": ...} or the FeedbackIntent
    parsed: Mapped[dict | None] = mapped_column(JSON)  # ParsedRequest or FeedbackIntent
    outfit: Mapped[dict | None] = mapped_column(JSON)  # slot -> item_id of the outfit shown
    locked: Mapped[dict] = mapped_column(JSON)  # slot -> item_id locked after this turn
    rejected: Mapped[list] = mapped_column(JSON)  # product keys rejected after this turn
    score: Mapped[float | None]
    breakdown: Mapped[dict | None] = mapped_column(JSON)
    notes: Mapped[list] = mapped_column(JSON)
    error: Mapped[str | None]
    latency_ms: Mapped[float]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class SessionStore:
    def __init__(self, db_url: str = SESSIONS_DB_URL) -> None:
        self.engine = create_engine(db_url)
        Base.metadata.create_all(self.engine)

    def create(self) -> str:
        session_id = str(uuid.uuid4())
        now = datetime.now(UTC)
        with Session(self.engine) as db, db.begin():
            db.add(SessionRow(session_id=session_id, state=None, created_at=now, updated_at=now))
        return session_id

    def exists(self, session_id: str) -> bool:
        with Session(self.engine) as db:
            return db.get(SessionRow, session_id) is not None

    def load(self, session_id: str) -> SessionState | None:
        """The session's state, or None if it has no outfit yet. KeyError if it doesn't exist."""
        with Session(self.engine) as db:
            row = db.get(SessionRow, session_id)
        if row is None:
            raise KeyError(session_id)
        return SessionState.model_validate(row.state) if row.state else None

    def record(self, session_id: str, state: SessionState | None, turn: dict) -> None:
        """Save the new state and the turn together, so they can't disagree."""
        now = datetime.now(UTC)
        with Session(self.engine) as db, db.begin():
            row = db.get(SessionRow, session_id)
            row.state = state.model_dump(mode="json") if state else None
            row.updated_at = now
            db.add(TurnRow(session_id=session_id, created_at=now, **turn))

    def turns(self, session_id: str) -> list[TurnRow]:
        with Session(self.engine) as db:
            return list(db.scalars(select(TurnRow).where(TurnRow.session_id == session_id).order_by(TurnRow.turn_id)))

    def count(self) -> int:
        with Session(self.engine) as db:
            return db.scalar(select(func.count()).select_from(SessionRow)) or 0
