from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

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
        await session.flush()
        return False
    # SELECT-then-INSERT race on uq_favorites_user_title: two concurrent
    # toggles can both observe "no row" above. INSERT ... ON CONFLICT DO
    # NOTHING lets exactly one row win (waiting on the unique index if the
    # rival txn is still open), then a re-SELECT decides the returned state,
    # so neither caller crashes with an uncaught IntegrityError and the
    # dedupe invariant (one favorite per user+title) holds.
    await session.execute(
        pg_insert(Favorite)
        .values(user_id=user_id, title_id=title_id)
        .on_conflict_do_nothing(constraint="uq_favorites_user_title")
    )
    row = await session.scalar(
        select(Favorite.id)
        .where(Favorite.user_id == user_id, Favorite.title_id == title_id)
        .limit(1)
    )
    return row is not None
