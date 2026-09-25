from __future__ import annotations

from sqlalchemy import delete, select

from app.db.models import Favorite


async def is_favorite(session, *, user_id, title_id) -> bool:
    row = await session.scalar(
        select(Favorite.id)
        .where(Favorite.user_id == user_id, Favorite.title_id == title_id)
        .limit(1)
    )
    return row is not None


async def toggle_favorite(session, *, user_id, title_id) -> bool:
    existing = await session.scalar(
        select(Favorite).where(Favorite.user_id == user_id, Favorite.title_id == title_id)
    )
    if existing is not None:
        await session.delete(existing)
        return False
    session.add(Favorite(user_id=user_id, title_id=title_id))
    await session.flush()
    return True
