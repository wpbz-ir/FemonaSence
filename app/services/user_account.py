from __future__ import annotations

from datetime import datetime, timezone

from aiogram.types import User as TgUser
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError

from app.db.models import Role, User, Wallet, user_roles
from app.runtime.model_introspection import first_attr, model_values


_SEEN_REFRESH_SECONDS = 300


async def ensure_user(session, tg_user: TgUser):
    """Fetch (or create) the user with ONE round-trip on the hot path.

    The wallet is loaded through an outer join and last_seen_at is refreshed at most
    every few minutes, so a plain button press no longer issues an UPDATE on Neon.
    """
    tg_id_col = first_attr(User, ("telegram_user_id", "tg_user_id", "telegram_id"))
    row = (
        await session.execute(
            select(User, Wallet.id).outerjoin(Wallet, Wallet.user_id == User.id).where(tg_id_col == tg_user.id)
        )
    ).first()
    now = datetime.now(timezone.utc)

    if row is None:
        values = model_values(
            User,
            telegram_user_id=int(tg_user.id),
            tg_user_id=int(tg_user.id),
            telegram_id=int(tg_user.id),
            username=tg_user.username,
            first_name=tg_user.first_name,
            last_name=tg_user.last_name,
            language_code=tg_user.language_code,
            status="ACTIVE",
            last_seen_at=now,
        )
        user = User(**values)
        session.add(user)
        try:
            # savepoint: رقابت get-or-create روی users.telegram_user_id UNIQUE —
            # در صورت برخورد، savepoint بازگردانی می‌شود و ردیف برنده دوباره خوانده می‌شود.
            async with session.begin_nested():
                await session.flush()
        except IntegrityError:
            user = await session.scalar(select(User).where(tg_id_col == tg_user.id))
            if user is None:
                raise
            wallet_id = await session.scalar(select(Wallet.id).where(Wallet.user_id == user.id))
            row = (user, wallet_id)  # ادامه از مسیر مشترک تازه‌سازی پایین
        else:
            await ensure_wallet(session, user)
            return user

    user, wallet_id = row
    columns = User.__table__.columns
    for name, value in (
        ("username", tg_user.username),
        ("first_name", tg_user.first_name),
        ("last_name", tg_user.last_name),
        ("language_code", tg_user.language_code),
    ):
        if name in columns and getattr(user, name, None) != value:
            setattr(user, name, value)

    last_seen = getattr(user, "last_seen_at", None)
    if "last_seen_at" in columns and (
        last_seen is None
        or (now - (last_seen if last_seen.tzinfo else last_seen.replace(tzinfo=timezone.utc))).total_seconds()
        > _SEEN_REFRESH_SECONDS
    ):
        user.last_seen_at = now


    if wallet_id is None:
        await ensure_wallet(session, user)
    return user


async def ensure_wallet(session, user):
    wallet = await session.scalar(select(Wallet).where(Wallet.user_id == user.id))
    if wallet is None:
        values = model_values(
            Wallet,
            user_id=user.id,
            balance_irr=0,
            version=1,
        )
        wallet = Wallet(**values)
        session.add(wallet)
        try:
            # savepoint: رقابت get-or-create روی wallets.user_id UNIQUE —
            # ردیف برنده از تراکنش هم‌زمان دوباره خوانده می‌شود.
            async with session.begin_nested():
                await session.flush()
        except IntegrityError:
            wallet = await session.scalar(select(Wallet).where(Wallet.user_id == user.id))
            if wallet is None:
                raise
    return wallet


async def ensure_super_admin(session, user) -> bool:
    role = await session.scalar(select(Role).where(Role.name == "SUPER_ADMIN"))
    if role is None:
        return False

    # [FIX-E] RACE (user_roles PK): check-then-insert در بوت اولیه‌ی چندنمونه‌ای
    # (ربات + ورکر + پنل هم‌زمان) هر دو «نبودن» را می‌بینند و تراکنش دوم با
    # IntegrityError می‌میرد. INSERT ... ON CONFLICT DO NOTHING (بدون target —
    # PK ترکیبی user_id/role_id کافی است) اتمی است؛ ردیف سپس دوباره خوانده
    # می‌شود تا موفقیت واقعی گزارش شود.
    await session.execute(
        pg_insert(user_roles)
        .values(
            user_id=user.id,
            role_id=role.id,
        )
        .on_conflict_do_nothing()
    )
    assigned = await session.scalar(
        select(user_roles.c.user_id).where(
            user_roles.c.user_id == user.id,
            user_roles.c.role_id == role.id,
        )
    )
    return assigned is not None
