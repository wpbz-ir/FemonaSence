from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import settings
from app.core.database import async_database_url


_DB_URL = async_database_url(settings.database_url)
_connect_args: dict = {"timeout": 20}
if "-pooler" in _DB_URL:
    # Neon's PgBouncer (transaction mode) cannot serve asyncpg prepared statements.
    _connect_args["statement_cache_size"] = 0

engine: AsyncEngine = create_async_engine(
    _DB_URL,
    pool_pre_ping=True,
    pool_recycle=900,
    pool_size=8,
    max_overflow=8,
    pool_timeout=20,
    connect_args=_connect_args,
    echo=settings.db_echo,
)

SessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session


async def close_engine() -> None:
    await engine.dispose()
