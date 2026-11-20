"""SQLAlchemy models for VideoFlow AI backend.
We deliberately avoid sqlmodel (unavailable) and use plain SQLAlchemy with
declarative base.  Types mirror the engine data structures needed for persistence.
"""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from sqlalchemy import Column, DateTime, Integer, String, Text, JSON, ForeignKey, UniqueConstraint
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()

class Project(Base):
    __tablename__ = "projects"
    id = Column(String, primary_key=True, index=True)  # user‑provided UUID or slug
    name = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    source_video_path = Column(String, nullable=False)  # immutable path on server
    source_hash = Column(String, nullable=False)  # SHA‑256 of original file
    transcript = Column(JSON, nullable=False)  # stores the Transcript JSON from engine

    # One‑to‑many relationship to revisions
    revisions = relationship("Revision", back_populates="project", cascade="all, delete-orphan")

class Revision(Base):
    __tablename__ = "revisions"
    id = Column(String, primary_key=True, index=True)  # revision hash (stableHash)
    project_id = Column(String, ForeignKey("projects.id"), nullable=False, index=True)
    parent_id = Column(String, ForeignKey("revisions.id"), nullable=True)
    kind = Column(String, nullable=False)  # "initial" | "edit" | "restore"
    label = Column(String, nullable=False)
    ops = Column(JSON, nullable=False)  # list of Op objects (as dicts)
    state = Column(JSON, nullable=False)  # TimelineState snapshot
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    project = relationship("Project", back_populates="revisions")
    parent = relationship("Revision", remote_side=[id])

    __table_args__ = (
        UniqueConstraint('project_id', 'id', name='uq_project_revision'),
    )

class ExportJob(Base):
    __tablename__ = "export_jobs"
    id = Column(String, primary_key=True, index=True)
    project_id = Column(String, ForeignKey("projects.id"), nullable=False)
    revision_id = Column(String, ForeignKey("revisions.id"), nullable=False)
    status = Column(String, nullable=False, default="queued")  # queued | processing | done | failed
    result_url = Column(String, nullable=True)  # MinIO public URL when done
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    project = relationship("Project")
    revision = relationship("Revision")
