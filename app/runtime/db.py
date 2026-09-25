from __future__ import annotations

from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import SessionLocal, close_engine, engine, get_session


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Open a short-lived application transaction with automatic commit/rollback."""
    async with SessionLocal() as session:
        try:
            yield session
            await session.commit()
        except BaseException:
            await session.rollback()
            raise


__all__ = [
    "engine",
    "SessionLocal",
    "session_scope",
    "get_session",
    "close_engine",
]
