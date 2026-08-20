#!/usr/bin/env python3
"""apps/control_api/database.py — SQLAlchemy 2.0 async engine & connection pool."""

from __future__ import annotations

from sqlalchemy import MetaData
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.orm import declarative_base

from config import config

# Naming convention for constraints to ensure deterministic names in migrations.
naming_convention = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s"
}

metadata = MetaData(naming_convention=naming_convention)
Base = declarative_base(metadata=metadata)

engine: AsyncEngine | None = None


def get_engine() -> AsyncEngine:
    global engine
    if engine is None:
        engine = create_async_engine(config.database_url, echo=False, future=True)
    return engine