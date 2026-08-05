import asyncio
import logging
import re
from collections.abc import AsyncGenerator, Generator

from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings

logger = logging.getLogger(__name__)


class DatabaseConfigError(RuntimeError):
    """The database connection settings are ambiguous or incomplete."""


def _redact(url: str) -> str:
    """Strip credentials from a connection URL so it can be logged."""
    return re.sub(r"//[^/@]*@", "//***@", url)


def _resolve_db_target() -> str:
    """Decide how to reach the database, and say so out loud.

    Returns "cloud_sql" or "url". Both engine factories go through here so the
    decision is made the same way for each, is logged whenever it is made, and
    cannot silently differ between the sync and async engines.

    Refusing when both are set is the point. CLOUD_SQL_INSTANCE used to win
    unconditionally, so exporting POSTGRES_DB_URL to aim a script at a scratch
    database did nothing at all and the script wrote to Cloud SQL instead —
    with no error and no log line saying which target it picked. That is a bad
    way to find out, and every environment here sets exactly one of the two:
    GKE and docker-compose.dev pass POSTGRES_DB_URL only, a bare-metal local
    checkout has CLOUD_SQL_INSTANCE only. Two contradictory instructions is a
    mistake, not a preference to resolve silently.

    DB_TARGET is the way to resolve it deliberately. It is needed because
    Settings sets env_ignore_empty=True, so blanking one of the two on the
    command line does nothing — the .env value survives, and the obvious
    workaround silently fails.
    """
    instance = settings.CLOUD_SQL_INSTANCE
    url = settings.SQLALCHEMY_DATABASE_URI
    target = settings.DB_TARGET

    if target == "cloud_sql":
        if not instance:
            raise DatabaseConfigError(
                "DB_TARGET=cloud_sql but CLOUD_SQL_INSTANCE is not set."
            )
        logger.info("database target: Cloud SQL connector (%s) [DB_TARGET]", instance)
        return "cloud_sql"

    if target == "url":
        if not url:
            raise DatabaseConfigError("DB_TARGET=url but POSTGRES_DB_URL is not set.")
        logger.info("database target: direct URL (%s) [DB_TARGET]", _redact(url))
        return "url"

    if instance and url:
        raise DatabaseConfigError(
            "CLOUD_SQL_INSTANCE and POSTGRES_DB_URL are both set, which "
            "specifies two different databases:\n"
            f"  CLOUD_SQL_INSTANCE = {instance}\n"
            f"  POSTGRES_DB_URL    = {_redact(url)}\n"
            "Unset whichever one you did not mean, or say which to use. "
            "Blanking one on the command line will NOT work — Settings uses "
            "env_ignore_empty, so the .env value survives. To aim one command "
            "at the database in POSTGRES_DB_URL:\n"
            '  DB_TARGET=url POSTGRES_DB_URL="postgresql://..." <command>'
        )

    if instance:
        logger.info("database target: Cloud SQL connector (%s)", instance)
        return "cloud_sql"

    if url:
        logger.info("database target: direct URL (%s)", _redact(url))
        return "url"

    raise DatabaseConfigError(
        "No database configured — set CLOUD_SQL_INSTANCE (to use the Cloud SQL "
        "connector) or POSTGRES_DB_URL (to connect directly), but not both."
    )


def _create_engine():
    """Sync engine — used by Alembic, prestart checks, and one-off scripts only."""
    if _resolve_db_target() == "cloud_sql":
        from google.cloud.sql.connector import Connector, IPTypes

        connector = Connector()

        def getconn():
            return connector.connect(
                settings.CLOUD_SQL_INSTANCE,
                "pg8000",
                user=settings.POSTGRES_USER,
                password=settings.POSTGRES_PASSWORD,
                db=settings.POSTGRES_DB,
                ip_type=IPTypes.PUBLIC,
            )

        return create_engine(
            "postgresql+pg8000://",
            creator=getconn,
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=10,
        )

    return create_engine(
        str(settings.SQLALCHEMY_DATABASE_URI),
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=10,
    )


def _create_async_engine():
    """
    Async engine — used by the running app so request handlers can await DB
    I/O instead of blocking the event loop. This is what lets a single Cloud
    Run instance actually serve concurrent requests off one process/thread.
    """
    if _resolve_db_target() == "cloud_sql":
        from google.cloud.sql.connector import Connector, IPTypes

        connector: Connector | None = None

        async def async_creator():
            nonlocal connector
            if connector is None:
                # Must bind to the loop handling requests — Connector() with no
                # `loop` spins up its own background loop, and connect_async()
                # requires the currently running loop to match.
                connector = Connector(loop=asyncio.get_running_loop())
            return await connector.connect_async(
                settings.CLOUD_SQL_INSTANCE,
                "asyncpg",
                user=settings.POSTGRES_USER,
                password=settings.POSTGRES_PASSWORD,
                db=settings.POSTGRES_DB,
                ip_type=IPTypes.PUBLIC,
            )

        return create_async_engine(
            "postgresql+asyncpg://",
            async_creator=async_creator,
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=10,
        )

    async_url = str(settings.SQLALCHEMY_DATABASE_URI).replace(
        "postgresql+psycopg://", "postgresql+asyncpg://"
    )
    return create_async_engine(
        async_url,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=10,
    )


# Sync engine/session — Alembic, backend_pre_start/tests_pre_start, initial_data,
# seeds. Built lazily (see __getattr__ below): with CLOUD_SQL_INSTANCE set, building
# this eagerly constructs a google.cloud.sql.connector.Connector() at import time,
# which is pure cold-start cost for the FastAPI app process — the app never uses the
# sync engine at request time (it uses async_engine/AsyncSessionLocal below).
_engine = None
_session_local = None


def _get_engine():
    global _engine
    if _engine is None:
        _engine = _create_engine()
    return _engine


def _get_session_local():
    global _session_local
    if _session_local is None:
        _session_local = sessionmaker(
            bind=_get_engine(), autocommit=False, autoflush=False
        )
    return _session_local


def __getattr__(name: str):
    """PEP 562 lazy module attributes, so `from app.core.db import engine` /
    `SessionLocal` (Alembic, backend_pre_start.py, initial_data.py, tests, ...)
    keeps working unchanged, while the underlying engine/Connector is only
    built the first time it's actually accessed."""
    if name == "engine":
        return _get_engine()
    if name == "SessionLocal":
        return _get_session_local()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# Async engine/session — used by the FastAPI app at request time.
async_engine = _create_async_engine()
AsyncSessionLocal = async_sessionmaker(
    bind=async_engine, autoflush=False, expire_on_commit=False
)


def get_db() -> Generator[Session, None, None]:
    db = _get_session_local()()
    try:
        yield db
    finally:
        db.close()


async def get_async_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session
