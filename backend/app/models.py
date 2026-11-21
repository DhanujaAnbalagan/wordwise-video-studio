"""Persistence model.

* ``Project`` – one uploaded source video, its transcript and the undo/redo stacks.
* ``RevisionRow`` – immutable timeline snapshots (``r0``, ``r1`` … per project).
* ``InstructionRow`` – the audit log of every instruction and its outcome.
* ``Job`` – background work (media processing, rendering) with progress 0–100.
* ``IdempotencyRecord`` – stored responses for ``Idempotency-Key`` replays.

The original upload is never modified: it is stored once under ``media.storageKey``
with its SHA-256, and renders are written to separate keys.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(32), default="created")  # created|processing|ready|failed
    error: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    media: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    transcript: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    silences: Mapped[list | None] = mapped_column(JSON, nullable=True)
    undo_stack: Mapped[list] = mapped_column(JSON, default=list)
    redo_stack: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class RevisionRow(Base):
    __tablename__ = "revisions"
    __table_args__ = (UniqueConstraint("project_id", "rev_id", name="uq_project_revision"),)

    pk: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    rev_id: Mapped[str] = mapped_column(String(32))
    index: Mapped[int] = mapped_column(Integer)
    parent_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    kind: Mapped[str] = mapped_column(String(16))
    label: Mapped[str] = mapped_column(Text)
    ops: Mapped[list] = mapped_column(JSON, default=list)
    state: Mapped[dict] = mapped_column(JSON)
    hash: Mapped[str] = mapped_column(String(64))
    created_ms: Mapped[int] = mapped_column(Integer, default=0)


class InstructionRow(Base):
    __tablename__ = "instructions"
    __table_args__ = (UniqueConstraint("project_id", "result_id", name="uq_project_instruction"),)

    pk: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    result_id: Mapped[str] = mapped_column(String(32))
    seq: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32))
    result: Mapped[dict] = mapped_column(JSON)


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(16))  # process | render
    status: Mapped[str] = mapped_column(String(16), default="queued")  # queued|running|succeeded|failed
    progress: Mapped[int] = mapped_column(Integer, default=0)
    stage: Mapped[str | None] = mapped_column(String(64), nullable=True)
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    lease_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_worker_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    payload_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_keys"
    __table_args__ = (UniqueConstraint("key", "method", "path", name="uq_idempotency"),)

    pk: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(255))
    method: Mapped[str] = mapped_column(String(8))
    path: Mapped[str] = mapped_column(String(512))
    request_hash: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(16), default="pending")  # pending | done
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    response_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# Aliases for backwards compatibility and clarity
Revision = RevisionRow
Instruction = InstructionRow
