from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db.models import Favorite


# [FIX-E] سقف نرم علاقه‌مندی برای هر کاربر — بدون سقف، دکمه‌ی ★ در طول زمان
# می‌تواند میلیون‌ها ردیف برای یک کاربر بسازد (لیست/پنل غیرقابل استفاده).
FAVORITES_PER_USER_LIMIT = 1000
FAVORITES_LIMIT_MESSAGE = "سقف تعداد علاقه‌مندی‌ها پر شده است."


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
    # [FIX-E] سقف ۱۰۰۰ علاقه‌مندی: قبل از insert شمرده می‌شود؛ عبور از سقف با
    # ValueError فارسی رد می‌شود (هندلر آن را به show_alert تبدیل می‌کند).
    count = int(
        await session.scalar(
            select(func.count()).select_from(Favorite).where(Favorite.user_id == user_id)
        )
        or 0
    )
    if count >= FAVORITES_PER_USER_LIMIT:
        raise ValueError(FAVORITES_LIMIT_MESSAGE)
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
