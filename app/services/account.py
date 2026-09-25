from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select

from app.db.models import Favorite, Referral, Subscription, Wallet, WatchHistory


def _money(wallet):
    if wallet is None:
        return 0
    return getattr(wallet, "balance_irr", 0)


async def build_account_snapshot(session, user):
    wallet = await session.scalar(select(Wallet).where(Wallet.user_id == user.id))

    stmt = select(Subscription).where(
        Subscription.user_id == user.id,
        Subscription.status == "ACTIVE",
        Subscription.expires_at >= datetime.now(timezone.utc),
    ).order_by(Subscription.expires_at.desc())

    subscription = await session.scalar(stmt)

    favorite_count = await session.scalar(
        select(func.count()).select_from(Favorite).where(Favorite.user_id == user.id)
    )
    history_count = await session.scalar(
        select(func.count()).select_from(WatchHistory).where(WatchHistory.user_id == user.id)
    )
    referral_count = await session.scalar(
        select(func.count()).select_from(Referral).where(Referral.referrer_user_id == user.id)
    )

    return {
        "wallet": _money(wallet),
        "subscription": subscription,
        "favorites": favorite_count or 0,
        "history": history_count or 0,
        "referrals": referral_count or 0,
    }
