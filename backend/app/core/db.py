import asyncio
from collections.abc import AsyncGenerator, Generator

from sqlalchemy import create_engine, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.models import User
from app.schemas import UserCreate


def _create_engine():
    """Sync engine — used by Alembic, prestart checks, and one-off scripts only."""
    if settings.CLOUD_SQL_INSTANCE:
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
    if settings.CLOUD_SQL_INSTANCE:
        from google.cloud.sql.connector import Connector, IPTypes

        connector: "Connector | None" = None

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
        _session_local = sessionmaker(bind=_get_engine(), autocommit=False, autoflush=False)
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
AsyncSessionLocal = async_sessionmaker(bind=async_engine, autoflush=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    db = _get_session_local()()
    try:
        yield db
    finally:
        db.close()


async def get_async_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session


def init_db(session: Session) -> None:
    # crud.create_user is async (used by the app's AsyncSession routes), so this
    # one-off sync script path builds the superuser directly instead.
    from app.core.security import get_password_hash

    user = session.execute(
        select(User).where(User.email == settings.FIRST_SUPERUSER)
    ).scalar_one_or_none()
    if not user:
        user_in = UserCreate(
            email=settings.FIRST_SUPERUSER,
            password=settings.FIRST_SUPERUSER_PASSWORD,
            is_superuser=True,
        )
        db_obj = User(
            email=user_in.email,
            full_name=user_in.full_name,
            is_active=user_in.is_active,
            is_superuser=user_in.is_superuser,
            hashed_password=get_password_hash(user_in.password),
        )
        session.add(db_obj)
        session.commit()
