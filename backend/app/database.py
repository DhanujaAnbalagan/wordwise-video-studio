"""Database connection utilities for VideoFlow AI backend.
We use a SQLite file at the path defined by the DATABASE_URL environment variable
(defaults to ./videoflow.db in the backend directory)."""

import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///videoflow.db")

engine = create_engine(
    DATABASE_URL, connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Import models so that Base.metadata.create_all can find them.
from . import models  # noqa: F401

def init_db() -> None:
    """Create database tables if they do not exist."""
    models.Base.metadata.create_all(bind=engine)
