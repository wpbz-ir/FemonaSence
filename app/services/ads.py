from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db.models import AdRequest, AdSetting, User
from app.services.text_normalization import clean_text


# ---------- Settings ----------

async def get_ad_settings(session) -> AdSetting | None:
    return await session.scalar(select(AdSetting).where(AdSetting.singleton.is_(True)))


async def get_or_create_ad_settings(session) -> AdSetting:
    row = await get_ad_settings(session)
    if row is None:
        # RACE (ad_settings.singleton UNIQUE): دو درخواست هم‌زمان می‌توانند به اینجا
        # برسند؛ ON CONFLICT DO NOTHING + re-select تضمین می‌کند دقیقاً یک ردیف وجود دارد.
        await session.execute(
            pg_insert(AdSetting)
            .values(singleton=True)
            .on_conflict_do_nothing(index_elements=[AdSetting.singleton])
        )
        row = await get_ad_settings(session)
        if row is None:  # عملاً دست‌نیافتنی — برندهٔ تراکنش هم‌زمان باید دیده شود
            raise RuntimeError("ad_settings row missing after upsert")
    return row


def serialize_ad_settings(row: AdSetting | None) -> dict:
    if row is None:
        return {
            "channel_chat_id": None,
            "channel_username": None,
            "rates_text": None,
            "instructions_text": None,
            "contact_text": None,
            "auto_channel_publish": False,
            "active": True,
        }
    return {
        "channel_chat_id": row.channel_chat_id,
        "channel_username": row.channel_username,
        "rates_text": row.rates_text,
        "instructions_text": row.instructions_text,
        "contact_text": row.contact_text,
        "auto_channel_publish": bool(row.auto_channel_publish),
        "active": bool(row.active),
    }


# ---------- Requests ----------

# Whitelist of legal ad-request statuses + allowed transitions.
# PENDING -> APPROVED | REJECTED; APPROVED -> PUBLISHED | REJECTED;
# REJECTED / PUBLISHED are terminal (immutable). Re-submitting the same
# status that is already on the row is an idempotent no-op.
AD_REQUEST_STATUSES = frozenset({"PENDING", "APPROVED", "REJECTED", "PUBLISHED"})

AD_REQUEST_TRANSITIONS: dict[str, frozenset[str]] = {
    "PENDING": frozenset({"APPROVED", "REJECTED"}),
    "APPROVED": frozenset({"PUBLISHED", "REJECTED"}),
    "REJECTED": frozenset(),  # terminal
    "PUBLISHED": frozenset(),  # terminal
}

async def create_ad_request(
    session,
    *,
    user_id,
    content_type: str,
    content_text: str | None,
    telegram_file_id: str | None,
    source_chat_id: int | None,
    source_message_id: int | None,
) -> AdRequest:
    row = AdRequest(
        user_id=user_id,
        content_type=content_type,
        content_text=clean_text(content_text) if content_text else None,
        telegram_file_id=telegram_file_id,
        source_chat_id=source_chat_id,
        source_message_id=source_message_id,
        status="PENDING",
    )
    session.add(row)
    await session.flush()
    return row


async def list_ad_requests(session, *, status: str | None = None, limit: int = 50) -> list[dict]:
    stmt = (
        select(
            AdRequest,
            User.telegram_user_id,
            User.username,
            User.first_name,
        )
        .join(User, User.id == AdRequest.user_id)
        .order_by(AdRequest.created_at.desc())
        .limit(min(200, max(1, limit)))
    )
    if status:
        stmt = stmt.where(AdRequest.status == status.upper())
    rows = (await session.execute(stmt)).all()
    result = []
    for request, tg_id, username, first_name in rows:
        result.append(
            {
                "id": str(request.id),
                "user": {"id": str(request.user_id), "telegram_user_id": tg_id, "username": username, "first_name": first_name},
                "content_type": request.content_type,
                "content_text": request.content_text,
                "has_file": bool(request.telegram_file_id),
                "status": request.status,
                "admin_note": request.admin_note,
                "published_chat_id": request.published_chat_id,
                "published_message_id": request.published_message_id,
                "created_at": request.created_at.isoformat() if request.created_at else None,
                "reviewed_at": request.reviewed_at.isoformat() if request.reviewed_at else None,
            }
        )
    return result


async def get_ad_request(session, request_id) -> AdRequest | None:
    return await session.get(AdRequest, request_id)


async def set_ad_request_status(session, request_id, *, status: str, admin_note: str | None, reviewer_user_id=None) -> AdRequest | None:
    """Transition an ad request through the validated status machine.

    Raises ValueError for an unknown status or an illegal transition
    (terminal rows are immutable). Callers that surface this to users must
    catch ValueError; unknown states fail closed instead of silently
    corrupting the workflow.
    """
    new_status = (status or "").strip().upper()
    if new_status not in AD_REQUEST_STATUSES:
        raise ValueError(f"invalid ad request status: {new_status!r}")

    row = await session.get(AdRequest, request_id)
    if row is None:
        return None

    current_status = (row.status or "").upper()
    if current_status == new_status:
        # Same-status re-submit: idempotent no-op, review metadata untouched.
        return row

    if new_status not in AD_REQUEST_TRANSITIONS.get(current_status, frozenset()):
        raise ValueError(
            f"invalid ad request status transition: {current_status} -> {new_status}"
        )

    row.status = new_status
    row.admin_note = clean_text(admin_note, 2000) if admin_note else None
    row.reviewed_at = datetime.now(timezone.utc)
    row.reviewed_by = reviewer_user_id
    await session.flush()
    return row


async def ad_request_counts(session) -> dict:
    rows = (
        await session.execute(
            select(AdRequest.status, func.count(AdRequest.id)).group_by(AdRequest.status)
        )
    ).all()
    counts = {status.upper(): count for status, count in rows}
    return {
        "pending": counts.get("PENDING", 0),
        "approved": counts.get("APPROVED", 0),
        "rejected": counts.get("REJECTED", 0),
        "published": counts.get("PUBLISHED", 0),
    }


async def super_admin_telegram_ids(session) -> list[int]:
    """شناسه‌های تلگرامی همه ادمین‌های ارشد — برای نوتیفیکیشن درخواست تبلیغ جدید."""
    from sqlalchemy import text

    rows = (
        await session.execute(
            text(
                """
                SELECT DISTINCT u.telegram_user_id
                FROM users u
                JOIN user_roles ur ON ur.user_id = u.id
                JOIN roles r ON r.id = ur.role_id
                WHERE r.name = 'SUPER_ADMIN' AND u.status = 'ACTIVE'
                """
            )
        )
    ).scalars().all()
    return [int(x) for x in rows if x is not None]
