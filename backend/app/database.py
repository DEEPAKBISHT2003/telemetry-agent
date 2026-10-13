"""Asynchronous Database Infrastructure using SQLAlchemy 2.0 and asyncpg."""

import asyncio
import logging
from typing import AsyncGenerator, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.config import Settings, get_settings

logger = logging.getLogger("telemetry.backend.database")

# Global engine and sessionmaker singletons (initialized lazily)
_engine: Optional[AsyncEngine] = None
_session_factory: Optional[async_sessionmaker[AsyncSession]] = None


def get_engine(settings: Optional[Settings] = None) -> Optional[AsyncEngine]:
    """Retrieve or lazily initialize the asynchronous SQLAlchemy engine."""
    global _engine, _session_factory
    if _engine is not None:
        return _engine

    cfg = settings or get_settings()
    if not cfg.database_url:
        return None

    # Normalise postgresql:// to postgresql+asyncpg:// if needed
    db_url = cfg.database_url
    if db_url.startswith("postgresql://"):
        db_url = db_url.replace("postgresql://", "postgresql+asyncpg://", 1)

    # Engine arguments
    engine_kwargs = {
        "echo": cfg.debug,
    }

    if "sqlite" in db_url:
        # SQLite async testing support
        engine_kwargs["poolclass"] = NullPool
    else:
        # PostgreSQL asyncpg connection pool tuning
        engine_kwargs["pool_size"] = cfg.db_pool_size
        engine_kwargs["max_overflow"] = cfg.db_max_overflow
        engine_kwargs["pool_timeout"] = cfg.db_pool_timeout
        engine_kwargs["pool_recycle"] = cfg.db_pool_recycle
        engine_kwargs["pool_pre_ping"] = cfg.db_pool_pre_ping

    logger.info(
        "Initializing async database engine",
        extra={"database_url": cfg.get_masked_database_url(), "pool_size": cfg.db_pool_size},
    )

    _engine = create_async_engine(db_url, **engine_kwargs)
    _session_factory = async_sessionmaker(
        bind=_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )
    return _engine


def get_session_factory(settings: Optional[Settings] = None) -> Optional[async_sessionmaker[AsyncSession]]:
    """Retrieve or lazily initialize the async sessionmaker factory."""
    global _session_factory
    if _session_factory is not None:
        return _session_factory
    get_engine(settings)
    return _session_factory


async def dispose_engine() -> None:
    """Cleanly dispose of database engine connection pools during application shutdown."""
    global _engine, _session_factory
    if _engine is not None:
        logger.info("Disposing database connection pool...")
        await _engine.dispose()
        _engine = None
        _session_factory = None
        logger.info("Database connection pool disposed.")


async def check_database_connectivity(timeout: float = 3.0) -> bool:
    """Execute a lightweight non-blocking query to verify database connectivity.

    Catches all connection failures, avoids logging credentials or raw SQL,
    and returns True only if the database responds within the timeout.
    """
    engine = get_engine()
    if engine is None:
        return False

    try:
        async with asyncio.timeout(timeout):
            async with engine.connect() as conn:
                result = await conn.execute(text("SELECT 1"))
                row = result.scalar()
                return bool(row == 1)
    except TimeoutError:
        logger.warning("Database connectivity check timed out after %.1f seconds", timeout)
        return False
    except Exception as exc:
        # Distinguish connection error without leaking raw passwords or hostnames
        error_type = type(exc).__name__
        logger.warning(
            "Database connectivity check failed: %s",
            error_type,
            extra={"error_type": error_type},
        )
        return False
