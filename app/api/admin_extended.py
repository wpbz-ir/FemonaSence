from __future__ import annotations

from datetime import datetime, timedelta, timezone
import re
import uuid

from fastapi import Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import selectinload

from app.api.admin import admin_gate, router, _request_meta
from app.core.config import settings
from app.db.models import (
    AdminActionLog,
    AuditLog,
    Collection,
    Country,
    DownloadHistory,
    Favorite,
    Genre,
    NotificationJob,
    Order,
    Payment,
    Person,
    Plan,
    Release,
    Season,
    Series,
    StorageFile,
    Subscription,
    Title,
    title_people,
    User,
    Wallet,
    WalletLedgerEntry,
    WatchHistory,
    Episode,
)
from app.runtime.db import session_scope
from app.services.audit import record_admin_action
from app.services.content_pipeline import PipelineError, build_quality_matrix
from app.services.runtime_admin_config import load_module_settings, save_module_settings
from app.services.runtime_bot_menu_config import load_bot_menu_settings, save_bot_menu_settings
from app.services.runtime_payment_config import load_payment_config, public_payment_config, save_payment_config
from app.services.text_normalization import repair_mojibake


class UserStatusUpdate(BaseModel):
    status: str = Field(pattern="^(ACTIVE|DISABLED|BLOCKED|DELETED)$")


class UserSubscriptionUpdate(BaseModel):
    plan_id: uuid.UUID
    duration_days: int | None = Field(default=None, ge=1, le=3650)


class GenreUpdate(BaseModel):
    name_fa: str | None = Field(default=None, min_length=1, max_length=100)
    name_en: str | None = Field(default=None, min_length=1, max_length=100)
    slug: str | None = Field(default=None, max_length=120)
    active: bool | None = None


class PersonUpdate(BaseModel):
    name_fa: str | None = Field(default=None, max_length=160)
    name_en: str | None = Field(default=None, max_length=160)
    slug: str | None = Field(default=None, max_length=180)
    biography: str | None = None
    profile_url: str | None = None


class CollectionCreate(BaseModel):
    name_fa: str = Field(min_length=1, max_length=160)
    name_en: str | None = None
    parent_id: uuid.UUID | None = None
    description: str | None = None
    poster_url: str | None = None
    sort_order: int = 0
    active: bool = True


class CollectionUpdate(BaseModel):
    name_fa: str | None = Field(default=None, min_length=1, max_length=160)
    name_en: str | None = None
    description: str | None = None
    poster_url: str | None = None
    parent_id: uuid.UUID | None = None
    sort_order: int | None = None
    active: bool | None = None


class CountryCreate(BaseModel):
    name_fa: str = Field(min_length=1, max_length=100)
    name_en: str = Field(min_length=1, max_length=100)
    code: str = Field(min_length=2, max_length=8)


class CountryUpdate(BaseModel):
    name_fa: str | None = Field(default=None, min_length=1, max_length=100)
    name_en: str | None = Field(default=None, min_length=1, max_length=100)
    code: str | None = Field(default=None, min_length=2, max_length=8)


class SeriesCreate(BaseModel):
    title_id: uuid.UUID
    total_seasons: int = Field(default=0, ge=0, le=1000)
    ongoing: bool = False


class SeriesUpdate(BaseModel):
    total_seasons: int | None = Field(default=None, ge=0, le=1000)
    ongoing: bool | None = None


class SeasonCreate(BaseModel):
    series_id: uuid.UUID
    season_number: int = Field(ge=1, le=10000)
    title: str | None = None
    synopsis: str | None = None


class SeasonUpdate(BaseModel):
    season_number: int | None = Field(default=None, ge=1, le=10000)
    title: str | None = None
    synopsis: str | None = None


class EpisodeCreate(BaseModel):
    season_id: uuid.UUID
    episode_number: int = Field(ge=1, le=100000)
    title: str | None = None
    synopsis: str | None = None
    runtime_minutes: int | None = Field(default=None, ge=0, le=10000)
    air_date: datetime | None = None


class EpisodeUpdate(BaseModel):
    episode_number: int | None = Field(default=None, ge=1, le=100000)
    title: str | None = None
    synopsis: str | None = None
    runtime_minutes: int | None = Field(default=None, ge=0, le=10000)
    air_date: datetime | None = None


class PaymentGatewayUpdate(BaseModel):
    enabled: bool = False
    sandbox: bool = True
    merchant_id: str | None = None
    base_url: str = "https://winapay.io/webservice/rest"
    timeout_seconds: int = Field(default=30, ge=5, le=120)


class ModuleSettingsUpdate(BaseModel):
    settings: dict


class TitleRelationsUpdate(BaseModel):
    genre_ids: list[uuid.UUID] = Field(default_factory=list)
    country_ids: list[uuid.UUID] = Field(default_factory=list)
    person_ids: list[uuid.UUID] = Field(default_factory=list)
    collection_ids: list[uuid.UUID] = Field(default_factory=list)
    runtime_minutes: int | None = Field(default=None, ge=0, le=10000)
    user_rating: float | None = Field(default=None, ge=0, le=10)
    trailer_url: str | None = None


class MatrixRequest(BaseModel):
    source_release_id: uuid.UUID
    qualities: list[str] = Field(default_factory=lambda: ["480", "720", "1080"], min_length=1, max_length=3)
    requires_subscription: bool = False
    minimum_plan_rank: int = Field(default=0, ge=0, le=1000)
    priority: int = Field(default=50, ge=-1000, le=1000)
    description: str | None = Field(default=None, max_length=2000)
    target_language: str = Field(default="ORIGINAL", max_length=32)
    target_subtitle_type: str = Field(default="NONE", max_length=32)
    target_codec_video: str = Field(default="H.264", max_length=32)
    target_codec_audio: str = Field(default="AAC", max_length=32)
    target_container: str = Field(default="MP4", max_length=16)
    auto_publish: bool = False


def _fa_status(value: str | None) -> str:
    return {
        "ACTIVE": "فعال", "DISABLED": "غیرفعال", "BLOCKED": "مسدود", "DELETED": "حذف‌شده",
        "DRAFT": "پیش‌نویس", "PUBLISHED": "منتشرشده", "ARCHIVED": "بایگانی‌شده",
        "READY": "آماده", "QUEUED": "در صف", "RUNNING": "در حال اجرا", "SUCCEEDED": "موفق",
        "FAILED": "ناموفق", "CANCELLED": "لغوشده", "RETRY": "تلاش مجدد", "PAID": "پرداخت‌شده",
        "PENDING": "در انتظار", "CREATED": "ایجادشده", "RECEIVED": "دریافت‌شده",
        "PROCESSED": "پردازش‌شده", "IGNORED": "نادیده‌گرفته‌شده",
    }.get(str(value or ""), str(value or "—"))


def _fa_digits(value) -> str:
    return str(value).translate(str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹"))


async def _admin_actor_id(session) -> uuid.UUID | None:
    if settings.admin_user_id is None:
        return None
    return await session.scalar(select(User.id).where(User.telegram_user_id == settings.admin_user_id))


@router.get("/users", dependencies=[Depends(admin_gate)])
async def admin_users(offset: int = 0, limit: int = 50, q: str | None = None, status: str | None = None):
    offset = max(0, min(offset, 100000))
    limit = max(1, min(limit, 100))
    async with session_scope() as session:
        stmt = select(User).order_by(User.created_at.desc()).offset(offset).limit(limit)
        if status:
            stmt = stmt.where(User.status == status.upper())
        if q:
            term = f"%{q.strip()}%"
            conditions = [User.username.ilike(term), User.first_name.ilike(term), User.last_name.ilike(term)]
            if q.strip().isdigit():
                conditions.append(User.telegram_user_id == int(q.strip()))
            stmt = stmt.where(or_(*conditions))
        rows = (await session.scalars(stmt)).all()
        items = []
        for user in rows:
            sub = await session.scalar(select(Subscription).where(Subscription.user_id == user.id, Subscription.status == "ACTIVE").order_by(Subscription.expires_at.desc()))
            plan = await session.get(Plan, sub.plan_id) if sub else None
            items.append({
                "id": str(user.id), "telegram_user_id": user.telegram_user_id, "username": user.username,
                "first_name": repair_mojibake(user.first_name), "last_name": repair_mojibake(user.last_name),
                "language_code": user.language_code, "status": user.status, "status_fa": _fa_status(user.status),
                "last_seen_at": user.last_seen_at.isoformat() if user.last_seen_at else None,
                "subscription": {"name_fa": repair_mojibake(plan.name_fa) if plan else None, "expires_at": sub.expires_at.isoformat() if sub else None},
                "roles": [role.name for role in user.roles],
            })
        total_stmt = select(func.count()).select_from(User)
        if status:
            total_stmt = total_stmt.where(User.status == status.upper())
        if q:
            term = f"%{q.strip()}%"
            cond = [User.username.ilike(term), User.first_name.ilike(term), User.last_name.ilike(term)]
            if q.strip().isdigit():
                cond.append(User.telegram_user_id == int(q.strip()))
            total_stmt = total_stmt.where(or_(*cond))
        total = await session.scalar(total_stmt)
        return {"items": items, "total": int(total or 0), "offset": offset, "limit": limit}


@router.get("/users/{user_id}", dependencies=[Depends(admin_gate)])
async def admin_user_detail(user_id: uuid.UUID):
    async with session_scope() as session:
        user = await session.get(User, user_id)
        if user is None:
            raise HTTPException(status_code=404, detail="کاربر پیدا نشد.")
        subscriptions = (await session.execute(select(Subscription, Plan).join(Plan, Plan.id == Subscription.plan_id).where(Subscription.user_id == user.id).order_by(Subscription.starts_at.desc()).limit(100))).all()
        orders = (await session.scalars(select(Order).where(Order.user_id == user.id).order_by(Order.created_at.desc()).limit(100))).all()
        payments = (await session.execute(select(Payment, Order.order_number).join(Order, Order.id == Payment.order_id).where(Order.user_id == user.id).order_by(Payment.created_at.desc()).limit(100))).all()
        wallet = await session.scalar(select(Wallet).where(Wallet.user_id == user.id))
        ledger = (await session.scalars(select(WalletLedgerEntry).where(WalletLedgerEntry.wallet_id == wallet.id).order_by(WalletLedgerEntry.created_at.desc()).limit(100))).all() if wallet else []
        favorites = await session.scalar(select(func.count()).select_from(Favorite).where(Favorite.user_id == user.id))
        watched = await session.scalar(select(func.count()).select_from(WatchHistory).where(WatchHistory.user_id == user.id))
        downloads = await session.scalar(select(func.count()).select_from(DownloadHistory).where(DownloadHistory.user_id == user.id))
        notifications = await session.scalar(select(func.count()).select_from(NotificationJob).where(NotificationJob.user_id == user.id))
        audit = (await session.scalars(select(AuditLog).where(AuditLog.entity_id == user.id).order_by(AuditLog.created_at.desc()).limit(100))).all()
        admin_audit = (await session.scalars(select(AdminActionLog).where(AdminActionLog.entity_id == user.id).order_by(AdminActionLog.created_at.desc()).limit(100))).all()
        combined_audit = [
            {"action": x.action, "entity_type": x.entity_type, "created_at": x.created_at.isoformat(), "old": x.old_value, "new": x.new_value}
            for x in audit
        ] + [
            {"action": x.action, "entity_type": x.entity_type, "created_at": x.created_at.isoformat(), "details": x.details, "success": x.success}
            for x in admin_audit
        ]
        combined_audit.sort(key=lambda item: item.get("created_at") or "", reverse=True)
        return {
            "user": {"id": str(user.id), "telegram_user_id": user.telegram_user_id, "username": user.username, "first_name": repair_mojibake(user.first_name), "last_name": repair_mojibake(user.last_name), "language_code": user.language_code, "status": user.status, "status_fa": _fa_status(user.status), "created_at": user.created_at.isoformat(), "last_seen_at": user.last_seen_at.isoformat() if user.last_seen_at else None, "roles": [role.name for role in user.roles]},
            "subscriptions": [{"id": str(sub.id), "plan_id": str(plan.id), "plan_name_fa": repair_mojibake(plan.name_fa), "starts_at": sub.starts_at.isoformat(), "expires_at": sub.expires_at.isoformat(), "status": sub.status, "status_fa": _fa_status(sub.status), "auto_renew": sub.auto_renew, "metadata": sub.extra_data} for sub, plan in subscriptions],
            "orders": [{"id": str(o.id), "order_number": o.order_number, "plan_id": str(o.plan_id) if o.plan_id else None, "amount_toman": str(o.amount_toman) if o.amount_toman is not None else None, "amount_irr": str(o.amount_irr), "currency": o.currency, "status": o.status, "status_fa": _fa_status(o.status), "created_at": o.created_at.isoformat()} for o in orders],
            "payments": [{"id": str(p.id), "order_number": n, "provider": p.provider, "provider_reference": p.provider_reference, "amount_toman": str(p.amount_toman) if p.amount_toman is not None else None, "status": p.status, "status_fa": _fa_status(p.status), "paid_at": p.paid_at.isoformat() if p.paid_at else None} for p, n in payments],
            "wallet": {"balance_irr": str(wallet.balance_irr) if wallet else "0", "version": wallet.version if wallet else 0, "ledger": [{"amount_irr": str(x.amount_irr), "balance_after_irr": str(x.balance_after_irr), "entry_type": x.entry_type, "description": repair_mojibake(x.description), "created_at": x.created_at.isoformat()} for x in ledger]},
            "activity": {"favorites": int(favorites or 0), "watch_history": int(watched or 0), "downloads": int(downloads or 0), "notifications": int(notifications or 0)},
            "audit": combined_audit[:200],
        }


@router.get("/users/{user_id}/360", dependencies=[Depends(admin_gate)])
async def admin_user_360(user_id: uuid.UUID):
    return await admin_user_detail(user_id)


@router.patch("/users/{user_id}/status", dependencies=[Depends(admin_gate)])
async def admin_user_status(user_id: uuid.UUID, payload: UserStatusUpdate, request: Request):
    async with session_scope() as session:
        user = await session.get(User, user_id)
        if user is None:
            raise HTTPException(status_code=404, detail="کاربر پیدا نشد.")
        if settings.admin_user_id is not None and user.telegram_user_id == settings.admin_user_id and payload.status != "ACTIVE":
            raise HTTPException(status_code=403, detail="حساب مدیر اصلی قابل غیرفعال‌سازی یا مسدودسازی نیست.")
        old = user.status
        user.status = payload.status
        await record_admin_action(session, action="CHANGE_USER_STATUS", entity_type="user", entity_id=user.id, actor_user_id=await _admin_actor_id(session), details={"old_status": old, "new_status": user.status}, **_request_meta(request))
        return {"ok": True, "status": user.status, "status_fa": _fa_status(user.status)}


@router.post("/users/{user_id}/subscription", dependencies=[Depends(admin_gate)])
async def admin_user_subscription(user_id: uuid.UUID, payload: UserSubscriptionUpdate, request: Request):
    async with session_scope() as session:
        user = await session.get(User, user_id)
        plan = await session.get(Plan, payload.plan_id)
        if user is None:
            raise HTTPException(status_code=404, detail="کاربر پیدا نشد.")
        if plan is None or not plan.active:
            raise HTTPException(status_code=404, detail="اشتراک انتخاب‌شده پیدا نشد یا غیرفعال است.")
        if user.status == "DELETED":
            raise HTTPException(status_code=409, detail="حساب حذف‌شده قابل ارتقای اشتراک نیست.")
        duration = payload.duration_days or plan.duration_days
        now = datetime.now(timezone.utc)
        active = await session.scalar(select(Subscription).where(Subscription.user_id == user.id, Subscription.status == "ACTIVE", Subscription.expires_at >= now).order_by(Subscription.expires_at.desc()).with_for_update())
        starts_at = active.expires_at if active else now
        expires_at = starts_at + timedelta(days=duration)
        old_plan = active.plan_id if active else None
        if active:
            active.plan_id = plan.id
            active.expires_at = expires_at
            sub = active
        else:
            sub = Subscription(user_id=user.id, plan_id=plan.id, starts_at=starts_at, expires_at=expires_at, status="ACTIVE", auto_renew=False, extra_data={"source": "ADMIN_MANUAL"})
            session.add(sub)
            await session.flush()
        await record_admin_action(session, action="MANUAL_SUBSCRIPTION_UPGRADE", entity_type="user_subscription", entity_id=sub.id, actor_user_id=await _admin_actor_id(session), details={"user_id": str(user.id), "plan_id": str(plan.id), "old_plan_id": str(old_plan) if old_plan else None, "duration_days": duration}, **_request_meta(request))
        return {"ok": True, "subscription_id": str(sub.id), "expires_at": expires_at.isoformat(), "plan_name_fa": repair_mojibake(plan.name_fa)}


@router.patch("/genres/{genre_id}", dependencies=[Depends(admin_gate)])
async def genre_update(genre_id: uuid.UUID, payload: GenreUpdate, request: Request):
    async with session_scope() as session:
        row = await session.get(Genre, genre_id)
        if row is None:
            raise HTTPException(status_code=404, detail="ژانر پیدا نشد.")
        values = payload.model_dump(exclude_unset=True)
        old = {k: getattr(row, k) for k in values}
        for k, v in values.items(): setattr(row, k, v)
        await record_admin_action(session, action="UPDATE_GENRE", entity_type="genre", entity_id=row.id, details={"old": old, "new": values}, **_request_meta(request))
        return {"ok": True}


@router.get("/people/{person_id}", dependencies=[Depends(admin_gate)])
async def person_detail(person_id: uuid.UUID):
    async with session_scope() as session:
        row = await session.get(Person, person_id)
        if row is None:
            raise HTTPException(status_code=404, detail="فرد پیدا نشد.")
        return {"id": str(row.id), "name_fa": repair_mojibake(row.name_fa), "name_en": row.name_en, "slug": row.slug, "biography": repair_mojibake(row.biography), "profile_url": row.profile_url}


@router.patch("/people/{person_id}", dependencies=[Depends(admin_gate)])
async def person_update(person_id: uuid.UUID, payload: PersonUpdate, request: Request):
    async with session_scope() as session:
        row = await session.get(Person, person_id)
        if row is None:
            raise HTTPException(status_code=404, detail="فرد پیدا نشد.")
        values = payload.model_dump(exclude_unset=True)
        old = {k: getattr(row, k) for k in values}
        for k, v in values.items(): setattr(row, k, v)
        await record_admin_action(session, action="UPDATE_PERSON", entity_type="person", entity_id=row.id, details={"old": old, "new": values}, **_request_meta(request))
        return {"ok": True}


@router.get("/collections", dependencies=[Depends(admin_gate)])
async def collections(offset: int = 0, limit: int = 100):
    async with session_scope() as session:
        rows = (await session.scalars(select(Collection).order_by(Collection.sort_order.asc(), Collection.name_fa.asc()).offset(max(0, offset)).limit(min(100, max(1, limit))))).all()
        return [{"id": str(x.id), "name_fa": repair_mojibake(x.name_fa), "name_en": x.name_en, "slug": x.slug, "description": repair_mojibake(x.description), "parent_id": str(x.parent_id) if x.parent_id else None, "sort_order": x.sort_order, "active": x.active} for x in rows]


@router.post("/collections", dependencies=[Depends(admin_gate)])
async def collection_create(payload: CollectionCreate, request: Request):
    async with session_scope() as session:
        if payload.parent_id and await session.get(Collection, payload.parent_id) is None:
            raise HTTPException(status_code=422, detail="مجموعه والد پیدا نشد.")
        slug = re.sub(r"[^\w\u0600-\u06ff]+", "-", (payload.name_en or payload.name_fa).strip().lower()).strip("-") or uuid.uuid4().hex
        base_slug = slug
        suffix = 2
        while await session.scalar(select(Collection.id).where(Collection.slug == slug)):
            slug = f"{base_slug}-{suffix}"
            suffix += 1
        row = Collection(**payload.model_dump(), slug=slug)
        session.add(row)
        await session.flush()
        await record_admin_action(session, action="CREATE_COLLECTION", entity_type="collection", entity_id=row.id, **_request_meta(request))
        return {"id": str(row.id), "slug": row.slug}


@router.patch("/collections/{collection_id}", dependencies=[Depends(admin_gate)])
async def collection_update(collection_id: uuid.UUID, payload: CollectionUpdate, request: Request):
    async with session_scope() as session:
        row = await session.get(Collection, collection_id)
        if row is None:
            raise HTTPException(status_code=404, detail="مجموعه پیدا نشد.")
        values = payload.model_dump(exclude_unset=True)
        if values.get("parent_id") == collection_id:
            raise HTTPException(status_code=422, detail="یک مجموعه نمی‌تواند والد خودش باشد.")
        if values.get("parent_id") and await session.get(Collection, values["parent_id"]) is None:
            raise HTTPException(status_code=422, detail="مجموعه والد پیدا نشد.")
        old = {k: getattr(row, k) for k in values}
        for k, v in values.items(): setattr(row, k, v)
        await record_admin_action(session, action="UPDATE_COLLECTION", entity_type="collection", entity_id=row.id, details={"old": old, "new": values}, **_request_meta(request))
        return {"ok": True}


@router.get("/countries", dependencies=[Depends(admin_gate)])
async def countries():
    async with session_scope() as session:
        rows = (await session.scalars(select(Country).order_by(Country.name_fa.asc()))).all()
        return [{"id": str(x.id), "name_fa": repair_mojibake(x.name_fa), "name_en": x.name_en, "code": x.code} for x in rows]


@router.post("/countries", dependencies=[Depends(admin_gate)])
async def country_create(payload: CountryCreate, request: Request):
    async with session_scope() as session:
        code = payload.code.strip().upper()
        if await session.scalar(select(Country).where(Country.code == code)):
            raise HTTPException(status_code=409, detail="کد کشور تکراری است.")
        row = Country(name_fa=payload.name_fa, name_en=payload.name_en, code=code)
        session.add(row)
        await session.flush()
        await record_admin_action(session, action="CREATE_COUNTRY", entity_type="country", entity_id=row.id, **_request_meta(request))
        return {"id": str(row.id)}


@router.patch("/countries/{country_id}", dependencies=[Depends(admin_gate)])
async def country_update(country_id: uuid.UUID, payload: CountryUpdate, request: Request):
    async with session_scope() as session:
        row = await session.get(Country, country_id)
        if row is None:
            raise HTTPException(status_code=404, detail="کشور پیدا نشد.")
        values = payload.model_dump(exclude_unset=True)
        if "code" in values: values["code"] = values["code"].strip().upper()
        old = {k: getattr(row, k) for k in values}
        for k, v in values.items(): setattr(row, k, v)
        await record_admin_action(session, action="UPDATE_COUNTRY", entity_type="country", entity_id=row.id, details={"old": old, "new": values}, **_request_meta(request))
        return {"ok": True}


@router.get("/years", dependencies=[Depends(admin_gate)])
async def title_years():
    async with session_scope() as session:
        rows = (await session.execute(select(Title.release_year, func.count(Title.id)).where(Title.release_year.is_not(None)).group_by(Title.release_year).order_by(Title.release_year.desc()))).all()
        return [{"year": int(y), "count": int(c)} for y, c in rows]


@router.get("/series", dependencies=[Depends(admin_gate)])
async def series_list():
    async with session_scope() as session:
        rows = (await session.execute(select(Series, Title).join(Title, Title.id == Series.title_id).order_by(Title.title_fa.asc()))).all()
        return [{"id": str(s.id), "title_id": str(t.id), "title_fa": repair_mojibake(t.title_fa), "total_seasons": s.total_seasons, "ongoing": s.ongoing} for s, t in rows]


@router.post("/series", dependencies=[Depends(admin_gate)])
async def series_create(payload: SeriesCreate, request: Request):
    async with session_scope() as session:
        title = await session.get(Title, payload.title_id)
        if title is None or title.kind not in {"SERIES", "ANIMATION"}:
            raise HTTPException(status_code=422, detail="عنوان انتخاب‌شده یک سریال معتبر نیست.")
        if await session.scalar(select(Series).where(Series.title_id == payload.title_id)):
            raise HTTPException(status_code=409, detail="ساختار سریال قبلاً ثبت شده است.")
        row = Series(**payload.model_dump())
        session.add(row)
        await session.flush()
        await record_admin_action(session, action="CREATE_SERIES", entity_type="series", entity_id=row.id, **_request_meta(request))
        return {"id": str(row.id)}


@router.patch("/series/{series_id}", dependencies=[Depends(admin_gate)])
async def series_update(series_id: uuid.UUID, payload: SeriesUpdate, request: Request):
    async with session_scope() as session:
        row = await session.get(Series, series_id)
        if row is None: raise HTTPException(status_code=404, detail="سریال پیدا نشد.")
        values = payload.model_dump(exclude_unset=True)
        for k, v in values.items(): setattr(row, k, v)
        await record_admin_action(session, action="UPDATE_SERIES", entity_type="series", entity_id=row.id, details={"new": values}, **_request_meta(request))
        return {"ok": True}


@router.get("/seasons", dependencies=[Depends(admin_gate)])
async def seasons(series_id: uuid.UUID | None = None):
    async with session_scope() as session:
        stmt = select(Season).order_by(Season.season_number.asc())
        if series_id: stmt = stmt.where(Season.series_id == series_id)
        rows = (await session.scalars(stmt)).all()
        return [{"id": str(x.id), "series_id": str(x.series_id), "season_number": x.season_number, "title": repair_mojibake(x.title), "synopsis": repair_mojibake(x.synopsis)} for x in rows]


@router.post("/seasons", dependencies=[Depends(admin_gate)])
async def season_create(payload: SeasonCreate, request: Request):
    async with session_scope() as session:
        if await session.get(Series, payload.series_id) is None: raise HTTPException(status_code=404, detail="سریال پیدا نشد.")
        if await session.scalar(select(Season).where(Season.series_id == payload.series_id, Season.season_number == payload.season_number)):
            raise HTTPException(status_code=409, detail="این شماره فصل قبلاً ثبت شده است.")
        row = Season(**payload.model_dump()); session.add(row); await session.flush()
        await record_admin_action(session, action="CREATE_SEASON", entity_type="season", entity_id=row.id, **_request_meta(request))
        return {"id": str(row.id)}


@router.patch("/seasons/{season_id}", dependencies=[Depends(admin_gate)])
async def season_update(season_id: uuid.UUID, payload: SeasonUpdate, request: Request):
    async with session_scope() as session:
        row = await session.get(Season, season_id)
        if row is None: raise HTTPException(status_code=404, detail="فصل پیدا نشد.")
        values = payload.model_dump(exclude_unset=True)
        if "season_number" in values and await session.scalar(select(Season).where(Season.series_id == row.series_id, Season.season_number == values["season_number"], Season.id != row.id)):
            raise HTTPException(status_code=409, detail="این شماره فصل قبلاً ثبت شده است.")
        for k, v in values.items(): setattr(row, k, v)
        await record_admin_action(session, action="UPDATE_SEASON", entity_type="season", entity_id=row.id, details={"new": values}, **_request_meta(request))
        return {"ok": True}


@router.get("/episodes", dependencies=[Depends(admin_gate)])
async def episodes(season_id: uuid.UUID | None = None):
    async with session_scope() as session:
        stmt = select(Episode).order_by(Episode.episode_number.asc())
        if season_id: stmt = stmt.where(Episode.season_id == season_id)
        rows = (await session.scalars(stmt)).all()
        return [{"id": str(x.id), "season_id": str(x.season_id), "episode_number": x.episode_number, "title": repair_mojibake(x.title), "synopsis": repair_mojibake(x.synopsis), "runtime_minutes": x.runtime_minutes, "air_date": x.air_date.isoformat() if x.air_date else None} for x in rows]


@router.post("/episodes", dependencies=[Depends(admin_gate)])
async def episode_create(payload: EpisodeCreate, request: Request):
    async with session_scope() as session:
        if await session.get(Season, payload.season_id) is None: raise HTTPException(status_code=404, detail="فصل پیدا نشد.")
        if await session.scalar(select(Episode).where(Episode.season_id == payload.season_id, Episode.episode_number == payload.episode_number)):
            raise HTTPException(status_code=409, detail="این شماره قسمت قبلاً ثبت شده است.")
        row = Episode(**payload.model_dump()); session.add(row); await session.flush()
        await record_admin_action(session, action="CREATE_EPISODE", entity_type="episode", entity_id=row.id, **_request_meta(request))
        return {"id": str(row.id)}


@router.patch("/episodes/{episode_id}", dependencies=[Depends(admin_gate)])
async def episode_update(episode_id: uuid.UUID, payload: EpisodeUpdate, request: Request):
    async with session_scope() as session:
        row = await session.get(Episode, episode_id)
        if row is None: raise HTTPException(status_code=404, detail="قسمت پیدا نشد.")
        values = payload.model_dump(exclude_unset=True)
        if "episode_number" in values and await session.scalar(select(Episode).where(Episode.season_id == row.season_id, Episode.episode_number == values["episode_number"], Episode.id != row.id)):
            raise HTTPException(status_code=409, detail="این شماره قسمت قبلاً ثبت شده است.")
        for k, v in values.items(): setattr(row, k, v)
        await record_admin_action(session, action="UPDATE_EPISODE", entity_type="episode", entity_id=row.id, details={"new": values}, **_request_meta(request))
        return {"ok": True}


@router.patch("/titles/{title_id}/relations", dependencies=[Depends(admin_gate)])
async def title_relations_update(title_id: uuid.UUID, payload: TitleRelationsUpdate, request: Request):
    async with session_scope() as session:
        title = await session.get(Title, title_id, options=[selectinload(Title.genres), selectinload(Title.countries), selectinload(Title.people), selectinload(Title.collections)])
        if title is None: raise HTTPException(status_code=404, detail="عنوان پیدا نشد.")
        genre_rows = (await session.scalars(select(Genre).where(Genre.id.in_(payload.genre_ids)))).all() if payload.genre_ids else []
        country_rows = (await session.scalars(select(Country).where(Country.id.in_(payload.country_ids)))).all() if payload.country_ids else []
        person_rows = (await session.scalars(select(Person).where(Person.id.in_(payload.person_ids)))).all() if payload.person_ids else []
        collection_rows = (await session.scalars(select(Collection).where(Collection.id.in_(payload.collection_ids)))).all() if payload.collection_ids else []
        if len(genre_rows) != len(set(payload.genre_ids)): raise HTTPException(status_code=422, detail="یکی از ژانرهای انتخاب‌شده پیدا نشد.")
        if len(country_rows) != len(set(payload.country_ids)): raise HTTPException(status_code=422, detail="یکی از کشورهای انتخاب‌شده پیدا نشد.")
        if len(person_rows) != len(set(payload.person_ids)): raise HTTPException(status_code=422, detail="یکی از افراد انتخاب‌شده پیدا نشد.")
        if len(collection_rows) != len(set(payload.collection_ids)): raise HTTPException(status_code=422, detail="یکی از مجموعه‌های انتخاب‌شده پیدا نشد.")
        title.genres = list(genre_rows)
        title.countries = list(country_rows)
        title.collections = list(collection_rows)

        desired_people = {person.id for person in person_rows}
        if desired_people:
            existing_people = (
                await session.execute(
                    select(title_people.c.person_id, title_people.c.role).where(
                        title_people.c.title_id == title.id
                    )
                )
            ).all()
            existing_ids = {row.person_id for row in existing_people}
            await session.execute(
                delete(title_people).where(
                    title_people.c.title_id == title.id,
                    ~title_people.c.person_id.in_(desired_people),
                )
            )
            for person_id in desired_people - existing_ids:
                await session.execute(
                    title_people.insert().values(
                        title_id=title.id, person_id=person_id, role="ACTOR"
                    )
                )
        else:
            await session.execute(
                delete(title_people).where(title_people.c.title_id == title.id)
            )
        if payload.runtime_minutes is not None: title.runtime_minutes = payload.runtime_minutes
        title.user_rating = payload.user_rating; title.trailer_url = payload.trailer_url
        await record_admin_action(session, action="UPDATE_TITLE_RELATIONS", entity_type="title", entity_id=title.id, details={"genre_ids": [str(x) for x in payload.genre_ids], "country_ids": [str(x) for x in payload.country_ids], "person_ids": [str(x) for x in payload.person_ids], "collection_ids": [str(x) for x in payload.collection_ids]}, **_request_meta(request))
        return {"ok": True}


@router.get("/titles/{title_id}/relations", dependencies=[Depends(admin_gate)])
async def title_relations(title_id: uuid.UUID):
    async with session_scope() as session:
        title = await session.get(Title, title_id, options=[selectinload(Title.genres), selectinload(Title.countries), selectinload(Title.people), selectinload(Title.collections)])
        if title is None: raise HTTPException(status_code=404, detail="عنوان پیدا نشد.")
        return {"genres": [{"id": str(x.id), "name_fa": repair_mojibake(x.name_fa)} for x in title.genres], "countries": [{"id": str(x.id), "name_fa": repair_mojibake(x.name_fa)} for x in title.countries], "people": [{"id": str(x.id), "name_fa": repair_mojibake(x.name_fa), "name_en": x.name_en} for x in title.people], "collections": [{"id": str(x.id), "name_fa": repair_mojibake(x.name_fa)} for x in title.collections]}


@router.get("/releases", dependencies=[Depends(admin_gate)])
async def admin_releases(offset: int = 0, limit: int = 100, status: str | None = None):
    async with session_scope() as session:
        stmt = select(Release).order_by(Release.created_at.desc()).offset(max(0, offset)).limit(min(100, max(1, limit)))
        if status: stmt = stmt.where(Release.status == status.upper())
        rows = (await session.scalars(stmt)).all()
        return [{"id": str(x.id), "title_id": str(x.title_id) if x.title_id else None, "episode_id": str(x.episode_id) if x.episode_id else None, "quality": x.quality, "language": x.language, "subtitle_type": x.subtitle_type, "label": repair_mojibake(x.label), "height": x.height, "codec_video": x.codec_video, "codec_audio": x.codec_audio, "container": x.container, "size_bytes": x.size_bytes, "status": x.status, "status_fa": _fa_status(x.status), "priority": x.priority, "pipeline_key": x.pipeline_key} for x in rows]


@router.get("/storage", dependencies=[Depends(admin_gate)])
async def admin_storage(offset: int = 0, limit: int = 100):
    async with session_scope() as session:
        rows = (await session.scalars(select(StorageFile).order_by(StorageFile.created_at.desc()).offset(max(0, offset)).limit(min(100, max(1, limit))))).all()
        return [{"id": str(x.id), "file_unique_key": x.file_unique_key, "filename": repair_mojibake(x.filename), "size_bytes": x.size_bytes, "status": x.status, "chat_id": x.chat_id, "message_id": x.message_id, "created_at": x.created_at.isoformat()} for x in rows]


@router.get("/payment-gateway", dependencies=[Depends(admin_gate)])
async def payment_gateway():
    value = public_payment_config()
    value["source_env_fallback"] = bool(settings.winapay_merchant_id)
    return value


@router.put("/payment-gateway", dependencies=[Depends(admin_gate)])
async def payment_gateway_update(payload: PaymentGatewayUpdate, request: Request):
    current = load_payment_config()
    data = payload.model_dump()
    if not data.get("merchant_id"):
        data["merchant_id"] = current.get("merchant_id", "")
    try:
        result = save_payment_config(data)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    async with session_scope() as session:
        await record_admin_action(session, action="UPDATE_PAYMENT_GATEWAY", entity_type="payment_gateway", entity_id=None, actor_user_id=await _admin_actor_id(session), details={"enabled": result["enabled"], "sandbox": result["sandbox"], "base_url": result["base_url"], "timeout_seconds": result["timeout_seconds"], "merchant_id_configured": result["merchant_id_configured"]}, **_request_meta(request))
    return result


@router.post("/payment-gateway/test", dependencies=[Depends(admin_gate)])
async def payment_gateway_test():
    config = load_payment_config()
    try:
        import httpx
        async with httpx.AsyncClient(timeout=float(config.get("timeout_seconds", 30)), follow_redirects=False) as client:
            response = await client.get(str(config.get("base_url")))
        return {"reachable": True, "status_code": response.status_code, "message": "دسترسی شبکه به نشانی درگاه برقرار است."}
    except Exception as exc:
        return {"reachable": False, "status_code": None, "message": "دسترسی شبکه به نشانی درگاه برقرار نیست.", "diagnostic": str(exc)[:400]}


@router.get("/settings/modules", dependencies=[Depends(admin_gate)])
async def module_settings():
    return load_module_settings()


@router.put("/settings/modules", dependencies=[Depends(admin_gate)])
async def module_settings_update(payload: ModuleSettingsUpdate, request: Request):
    result = save_module_settings(payload.settings)
    async with session_scope() as session:
        await record_admin_action(session, action="UPDATE_ADMIN_MODULE_SETTINGS", entity_type="admin_settings", entity_id=None, actor_user_id=await _admin_actor_id(session), details=result, **_request_meta(request))
    return result


@router.get("/settings/bot-menu", dependencies=[Depends(admin_gate)])
async def bot_menu_settings():
    return load_bot_menu_settings()


@router.put("/settings/bot-menu", dependencies=[Depends(admin_gate)])
async def bot_menu_settings_update(payload: ModuleSettingsUpdate, request: Request):
    result = save_bot_menu_settings(payload.settings)
    async with session_scope() as session:
        await record_admin_action(
            session,
            action="UPDATE_BOT_MENU_SETTINGS",
            entity_type="bot_menu",
            entity_id=None,
            actor_user_id=await _admin_actor_id(session),
            details=result,
            **_request_meta(request),
        )
    return result


@router.get("/audit", dependencies=[Depends(admin_gate)])
async def admin_audit(offset: int = 0, limit: int = 100):
    async with session_scope() as session:
        rows = (await session.scalars(select(AuditLog).order_by(AuditLog.created_at.desc()).offset(max(0, offset)).limit(min(100, max(1, limit))))).all()
        return [{"id": str(x.id), "action": x.action, "entity_type": x.entity_type, "entity_id": str(x.entity_id) if x.entity_id else None, "created_at": x.created_at.isoformat(), "old": x.old_value, "new": x.new_value} for x in rows]


@router.post("/encoding/repair", dependencies=[Depends(admin_gate)])
async def repair_encoding(request: Request, apply: bool = False):
    changed = 0; scanned = 0; samples = []
    async with session_scope() as session:
        targets = [(Title, "title_fa", "عنوان"), (Title, "synopsis", "خلاصه"), (Genre, "name_fa", "ژانر"), (Person, "name_fa", "فرد"), (Person, "biography", "زندگی‌نامه"), (Collection, "name_fa", "مجموعه"), (Collection, "description", "توضیحات مجموعه"), (Season, "title", "عنوان فصل"), (Season, "synopsis", "خلاصه فصل"), (Episode, "title", "عنوان قسمت"), (Episode, "synopsis", "خلاصه قسمت")]
        for model, field, label in targets:
            rows = (await session.scalars(select(model))).all()
            for row in rows:
                value = getattr(row, field, None); scanned += 1; fixed = repair_mojibake(value)
                if fixed != value:
                    changed += 1
                    if len(samples) < 20: samples.append({"field": label, "id": str(row.id), "before": value, "after": fixed})
                    if apply: setattr(row, field, fixed)
        if apply and changed:
            meta = _request_meta(request) if request else {}
            await record_admin_action(session, action="REPAIR_UTF8_MOJIBAKE", entity_type="content_text", entity_id=None, actor_user_id=await _admin_actor_id(session), details={"scanned": scanned, "changed": changed, "samples": samples[:10]}, **meta)
    return {"apply": apply, "scanned": scanned, "changed": changed, "samples": samples}


@router.post("/titles/{title_id}/pipeline-advanced", dependencies=[Depends(admin_gate)])
async def pipeline_advanced(title_id: uuid.UUID, payload: MatrixRequest, request: Request):
    async with session_scope() as session:
        title = await session.get(Title, title_id)
        source = await session.get(Release, payload.source_release_id)
        if title is None: raise HTTPException(status_code=404, detail="عنوان پیدا نشد.")
        if source is None: raise HTTPException(status_code=404, detail="نسخه مبنا پیدا نشد.")
        try:
            run = await build_quality_matrix(session, source_release_id=source.id, requested_qualities=payload.qualities, requires_subscription=payload.requires_subscription, minimum_plan_rank=payload.minimum_plan_rank, priority=payload.priority, description=payload.description, target_language=payload.target_language, target_subtitle_type=payload.target_subtitle_type, target_codec_video=payload.target_codec_video, target_codec_audio=payload.target_codec_audio, target_container=payload.target_container, auto_publish=payload.auto_publish)
        except PipelineError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        await record_admin_action(session, action="BUILD_ADVANCED_QUALITY_MATRIX", entity_type="pipeline", entity_id=run.id, actor_user_id=await _admin_actor_id(session), details=payload.model_dump(exclude={"source_release_id"}), **_request_meta(request))
        return {"run_id": str(run.id), "status": run.status, "settings": payload.model_dump(exclude={"source_release_id"})}
