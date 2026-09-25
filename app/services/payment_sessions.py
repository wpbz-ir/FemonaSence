from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.db.models import PaymentSession


SESSION_TTL_SECONDS = 15 * 60


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


async def create_payment_session(
    session,
    *,
    order_id,
    user_id,
    provider: str,
    purpose: str = "SUBSCRIPTION",
    ttl_seconds: int = SESSION_TTL_SECONDS,
) -> str:
    raw = secrets.token_urlsafe(36)
    row = PaymentSession(
        order_id=order_id,
        user_id=user_id,
        provider=provider,
        purpose=purpose,
        token_hash=_hash(raw),
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=max(60, min(ttl_seconds, 3600))),
        created_at=datetime.now(timezone.utc),
    )
    session.add(row)
    await session.flush()
    return raw


async def resolve_payment_session(session, raw_token: str, *, consume: bool = False):
    token_hash = _hash(raw_token)
    row = await session.scalar(
        select(PaymentSession)
        .where(
            PaymentSession.token_hash == token_hash,
            PaymentSession.expires_at > datetime.now(timezone.utc),
            PaymentSession.consumed_at.is_(None),
        )
    )
    if row is None:
        return None
    if consume:
        row.consumed_at = datetime.now(timezone.utc)
        await session.flush()
    return row
