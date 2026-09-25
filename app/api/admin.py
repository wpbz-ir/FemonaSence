from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select, text

from app.api.admin_auth import require_admin_token
from app.core.config import settings
from app.db.models import (
    AdminActionLog,
    Collection,
    Episode,
    Favorite,
    Genre,
    MediaJob,
    Order,
    Payment,
    PaymentAttempt,
    Person,
    Plan,
    Release,
    ReleaseFile,
    Role,
    Season,
    Series,
    ServiceHeartbeat,
    StorageFile,
    Subscription,
    Title,
    User,
    Wallet,
    WatchHistory,
    Coupon,
    CouponRedemption,
    CouponTargetUser,
    NotificationTemplate,
    user_roles,
)

from app.runtime.db import session_scope
from app.services.admin_ops import (
    cancel_job,
    get_job,
    job_stats,
    list_jobs,
    list_pipeline_runs,
    pipeline_stats,
    retry_job,
    set_job_priority,
)
from app.services.audit import record_admin_action
from app.services.text_normalization import repair_mojibake
from app.services.content_admin import (
    attach_storage_file,
    create_collection,
    create_episode,
    create_episode_release,
    create_genre,
    create_person,
    create_release,
    create_season,
    create_title,
    get_title,
    list_series_tree,
    list_storage_files,
    list_titles,
    register_telegram_storage_file,
)
from app.services.content_pipeline import PipelineError, build_quality_matrix, sync_pipeline_run
from app.services.heartbeats import service_instance_id
from app.services.rate_limit import RateLimitExceeded, RateLimitUnavailable, enforce

router = APIRouter(prefix="/api/admin", tags=["admin"])


class TitleCreate(BaseModel):
    kind: str = Field(pattern="^(MOVIE|SERIES|ANIMATION)$")
    title_fa: str = Field(min_length=1, max_length=255)
    title_en: str | None = None
    original_title: str | None = None
    synopsis: str | None = None
    release_year: int | None = Field(default=None, ge=1888, le=2200)
    imdb_id: str | None = None
    imdb_rating: float | None = Field(default=None, ge=0, le=10)
    imdb_votes: int | None = Field(default=None, ge=0)
    poster_url: str | None = None
    backdrop_url: str | None = None
    status: str = Field(default="DRAFT", pattern="^(DRAFT|PUBLISHED|ARCHIVED)$")
    rights_verified: bool = False
    rights_reference: str | None = None


class TitleUpdate(BaseModel):
    status: str | None = Field(default=None, pattern="^(DRAFT|PUBLISHED|ARCHIVED)$")
    synopsis: str | None = None
    poster_url: str | None = None
    backdrop_url: str | None = None
    trailer_url: str | None = None
    rights_verified: bool | None = None
    rights_reference: str | None = None


class ReleaseCreate(BaseModel):
    quality: str = Field(min_length=1, max_length=32)
    language: str = "ORIGINAL"
    subtitle_type: str = "NONE"
    label: str | None = None
    width: int | None = None
    height: int | None = None
    codec_video: str | None = None
    codec_audio: str | None = None
    container: str | None = None
    size_bytes: int | None = None
    status: str = Field(default="DRAFT", pattern="^(DRAFT|READY|PUBLISHED|DISABLED|ARCHIVED)$")


class ReleaseUpdate(BaseModel):
    status: str | None = Field(default=None, pattern="^(DRAFT|READY|PUBLISHED|DISABLED|ARCHIVED)$")
    label: str | None = None
    priority: int | None = Field(default=None, ge=-1000, le=1000)


class StorageRegister(BaseModel):
    chat_id: int
    message_id: int
    file_unique_key: str = Field(min_length=1, max_length=255)
    filename: str | None = None
    file_id: str | None = None
    size_bytes: int | None = None


class StorageAttach(BaseModel):
    storage_file_id: uuid.UUID
    primary: bool = True


class PipelineRequest(BaseModel):
    source_release_id: uuid.UUID
    qualities: list[str] = Field(default_factory=lambda: ["480", "720", "1080"], min_length=1, max_length=3)
    requires_subscription: bool = False
    minimum_plan_rank: int = Field(default=0, ge=0, le=1000)
    priority: int = Field(default=50, ge=-1000, le=1000)


class PlanCreate(BaseModel):
    code: str = Field(min_length=2, max_length=32, pattern=r"^[A-Za-z0-9_-]+$")
    name_fa: str = Field(min_length=1, max_length=120)
    name_en: str | None = None
    description: str | None = None
    price_toman: Decimal = Field(default=Decimal("0"), ge=0)
    price_irr: Decimal = Field(default=Decimal("0"), ge=0)
    duration_days: int = Field(ge=1, le=3650)
    rank: int = Field(default=0, ge=0, le=1000)
    active: bool = True
    sort_order: int = 0
    features: dict = Field(default_factory=dict)


class PlanUpdate(BaseModel):
    name_fa: str | None = None
    name_en: str | None = None
    description: str | None = None
    price_toman: Decimal | None = Field(default=None, ge=0)
    price_irr: Decimal | None = Field(default=None, ge=0)
    duration_days: int | None = Field(default=None, ge=1, le=3650)
    rank: int | None = Field(default=None, ge=0, le=1000)
    active: bool | None = None
    sort_order: int | None = None
    features: dict | None = None


class JobPriority(BaseModel):
    priority: int = Field(ge=-1000, le=1000)


async def admin_gate(request: Request):
    require_admin_token(request)
    try:
        await enforce("admin:" + (request.client.host if request.client else "unknown"), limit=240, window_seconds=60)
    except RateLimitExceeded as exc:
        raise HTTPException(status_code=429, detail="تعداد درخواست‌های پنل بیش از حد مجاز است.") from exc
    except RateLimitUnavailable as exc:
        raise HTTPException(status_code=503, detail="Rate limiter backend is unavailable.") from exc


def _request_meta(request: Request) -> dict:
    return {
        "request_id": getattr(request.state, "request_id", None),
        "method": request.method,
        "path": request.url.path,
        "ip_address": request.client.host if request.client else None,
    }


@router.get("/dashboard", dependencies=[Depends(admin_gate)])
async def dashboard(request: Request):
    async with session_scope() as session:
        from app.db.models import User
        users = await session.scalar(select(func.count()).select_from(User))
        titles_count = await session.scalar(select(func.count()).select_from(Title))
        releases = await session.scalar(select(func.count()).select_from(Release))
        storage = await session.scalar(select(func.count()).select_from(StorageFile))
        stats = await job_stats(session)
        pipeline = await pipeline_stats(session)
        heartbeats = (
            await session.scalars(
                select(ServiceHeartbeat)
                .order_by(ServiceHeartbeat.last_seen_at.desc())
                .limit(20)
            )
        ).all()

    return {
        "users": users or 0,
        "titles": titles_count or 0,
        "releases": releases or 0,
        "storage_files": storage or 0,
        "jobs": stats,
        "pipelines": pipeline,
        "services": [
            {
                "service": x.service_name,
                "instance": x.instance_id,
                "status": x.status,
                "last_seen_at": x.last_seen_at.isoformat(),
                "metadata": x.extra_data,
            }
            for x in heartbeats
        ],
    }


@router.get("/titles", dependencies=[Depends(admin_gate)])
async def titles(offset: int = 0, limit: int = 50):
    async with session_scope() as session:
        rows = await list_titles(session, limit=min(limit, 100), offset=max(offset, 0))
        return [
            {
                "id": str(row.id),
                "kind": row.kind,
                "title_fa": row.title_fa,
                "title_en": row.title_en,
                "year": row.release_year,
                "imdb": row.imdb_rating,
                "status": row.status,
            }
            for row in rows
        ]


@router.post("/titles", dependencies=[Depends(admin_gate)])
async def create_title_api(payload: TitleCreate, request: Request):
    async with session_scope() as session:
        try:
            title = await create_title(session, **payload.model_dump())
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        await record_admin_action(
            session,
            action="CREATE_TITLE",
            entity_type="title",
            entity_id=title.id,
            **_request_meta(request),
        )
        return {"id": str(title.id), "slug": title.slug}


@router.patch("/titles/{title_id}", dependencies=[Depends(admin_gate)])
async def update_title_api(title_id: uuid.UUID, payload: TitleUpdate, request: Request):
    async with session_scope() as session:
        title = await session.get(Title, title_id)
        if title is None:
            raise HTTPException(status_code=404, detail="Title not found")
        values = payload.model_dump(exclude_unset=True)
        if values.get("status") == "PUBLISHED" and settings.content_rights_required:
            if not values.get("rights_verified", title.rights_verified):
                raise HTTPException(status_code=422, detail="برای انتشار Title باید مجوز پخش تأیید شده باشد.")
        if values.get("rights_verified") is False and title.status == "PUBLISHED":
            raise HTTPException(status_code=422, detail="تا زمانی که Title منتشر است، نمی‌توان تأیید حقوق را غیرفعال کرد.")
        old = {key: getattr(title, key) for key in values}
        for key, value in values.items():
            setattr(title, key, value)
        if title.status == "PUBLISHED" and title.published_at is None:
                        title.published_at = datetime.now(timezone.utc)
        await record_admin_action(
            session,
            action="UPDATE_TITLE",
            entity_type="title",
            entity_id=title.id,
            details={"old": old, "new": values},
            **_request_meta(request),
        )
        return {"ok": True, "id": str(title.id)}


@router.get("/titles/{title_id}", dependencies=[Depends(admin_gate)])
async def title_api(title_id: uuid.UUID):
    async with session_scope() as session:
        title = await get_title(session, title_id)
        if title is None:
            raise HTTPException(status_code=404, detail="Title not found")

        releases = await session.scalars(
            select(Release)
            .where(Release.title_id == title.id)
            .order_by(Release.priority.desc(), Release.created_at.desc())
        )
        return {
            "id": str(title.id),
            "kind": title.kind,
            "title_fa": title.title_fa,
            "title_en": title.title_en,
            "original_title": title.original_title,
            "synopsis": title.synopsis,
            "release_year": title.release_year,
            "imdb_id": title.imdb_id,
            "imdb_rating": title.imdb_rating,
            "imdb_votes": title.imdb_votes,
            "poster_url": title.poster_url,
            "backdrop_url": title.backdrop_url,
            "trailer_url": title.trailer_url,
            "status": title.status,
            "rights_verified": title.rights_verified,
            "rights_reference": title.rights_reference,
            "releases": [
                {
                    "id": str(r.id),
                    "quality": r.quality,
                    "language": r.language,
                    "subtitle_type": r.subtitle_type,
                    "status": r.status,
                    "size_bytes": r.size_bytes,
                    "pipeline_key": r.pipeline_key,
                }
                for r in releases.all()
            ],
        }


@router.post("/titles/{title_id}/releases", dependencies=[Depends(admin_gate)])
async def release_api(title_id: uuid.UUID, payload: ReleaseCreate, request: Request):
    if payload.status == "PUBLISHED":
        raise HTTPException(status_code=422, detail="ابتدا Release را READY بسازید، فایل را متصل کنید و سپس منتشر کنید.")
    async with session_scope() as session:
        title = await session.get(Title, title_id)
        if title is None:
            raise HTTPException(status_code=404, detail="Title not found")
        row = await create_release(session, title_id=title_id, **payload.model_dump())
        await record_admin_action(
            session,
            action="CREATE_RELEASE",
            entity_type="release",
            entity_id=row.id,
            **_request_meta(request),
        )
        return {"id": str(row.id)}


@router.patch("/releases/{release_id}", dependencies=[Depends(admin_gate)])
async def release_update(release_id: uuid.UUID, payload: ReleaseUpdate, request: Request):
    async with session_scope() as session:
        release = await session.get(Release, release_id)
        if release is None:
            raise HTTPException(status_code=404, detail="Release not found")
        values = payload.model_dump(exclude_unset=True)
        if values.get("status") == "PUBLISHED":
            parent_title_id = release.title_id
            if parent_title_id is None and release.episode_id is not None:
                parent_title_id = await session.scalar(
                    select(Series.title_id)
                    .join(Season, Season.series_id == Series.id)
                    .join(Episode, Episode.season_id == Season.id)
                    .where(Episode.id == release.episode_id)
                )
            title = await session.get(Title, parent_title_id) if parent_title_id else None
            if title is None:
                raise HTTPException(status_code=422, detail="عنوان والد Release پیدا نشد.")
            if settings.content_rights_required and not title.rights_verified:
                raise HTTPException(status_code=422, detail="برای انتشار Release باید مجوز پخش Title تأیید شده باشد.")
            linked = await session.scalar(
                select(ReleaseFile.id).where(ReleaseFile.release_id == release.id, ReleaseFile.active.is_(True)).limit(1)
            )
            if linked is None:
                raise HTTPException(status_code=422, detail="برای انتشار Release حداقل یک فایل فعال باید متصل باشد.")
        old = {key: getattr(release, key) for key in values}
        for key, value in values.items():
            setattr(release, key, value)
        await record_admin_action(
            session,
            action="UPDATE_RELEASE",
            entity_type="release",
            entity_id=release.id,
            details={"old": old, "new": values},
            **_request_meta(request),
        )
        return {"ok": True}


@router.post("/titles/{title_id}/pipeline", dependencies=[Depends(admin_gate)])
async def pipeline_api(title_id: uuid.UUID, payload: PipelineRequest, request: Request):
    async with session_scope() as session:
        title = await session.get(Title, title_id)
        if title is None:
            raise HTTPException(status_code=404, detail="Title not found")
        source = await session.get(Release, payload.source_release_id)
        if source is None:
            raise HTTPException(status_code=404, detail="Source release not found")
        if source.title_id != title_id:
            # For episode releases the source belongs to the requested title through the series tree.
            if source.episode_id is None:
                raise HTTPException(status_code=409, detail="Source release is not owned by this title.")
        try:
            run = await build_quality_matrix(
                session,
                source_release_id=source.id,
                requested_qualities=payload.qualities,
                requires_subscription=payload.requires_subscription,
                minimum_plan_rank=payload.minimum_plan_rank,
                priority=payload.priority,
            )
        except PipelineError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if run.title_id != title_id:
            raise HTTPException(status_code=409, detail="Source release is not owned by the requested title.")

        await record_admin_action(
            session,
            action="BUILD_QUALITY_MATRIX",
            entity_type="pipeline",
            entity_id=run.id,
            details={
                "source_release_id": str(source.id),
                "qualities": payload.qualities,
            },
            **_request_meta(request),
        )
        return {
            "run_id": str(run.id),
            "status": run.status,
            "items": [
                {
                    "quality": item.quality,
                    "target_release_id": str(item.target_release_id) if item.target_release_id else None,
                    "media_job_id": str(item.media_job_id) if item.media_job_id else None,
                    "status": item.status,
                }
                for item in run.items
            ],
        }


@router.get("/pipelines", dependencies=[Depends(admin_gate)])
async def pipelines(status: str | None = None, offset: int = 0, limit: int = 50):
    async with session_scope() as session:
        runs = await list_pipeline_runs(
            session,
            status=status,
            offset=offset,
            limit=limit,
        )
        for run in runs:
            await sync_pipeline_run(session, run.id)
        return [
            {
                "id": str(run.id),
                "title_id": str(run.title_id),
                "source_release_id": str(run.source_release_id),
                "status": run.status,
                "requested_qualities": run.requested_qualities,
                "created_at": run.created_at.isoformat(),
                "items": [
                    {
                        "quality": item.quality,
                        "status": item.status,
                        "target_release_id": str(item.target_release_id) if item.target_release_id else None,
                        "media_job_id": str(item.media_job_id) if item.media_job_id else None,
                        "error": item.error_message,
                    }
                    for item in run.items
                ],
            }
            for run in runs
        ]


@router.get("/jobs", dependencies=[Depends(admin_gate)])
async def jobs(status: str | None = None, offset: int = 0, limit: int = 50):
    async with session_scope() as session:
        rows = await list_jobs(session, status=status, offset=offset, limit=limit)
        return [
            {
                "id": str(row.id),
                "job_type": row.job_type,
                "status": row.status,
                "priority": row.priority,
                "attempts": row.attempts,
                "max_attempts": row.max_attempts,
                "progress": float(row.progress_percent or 0),
                "target_quality": row.target_quality,
                "source_release_id": str(row.source_release_id),
                "target_release_id": str(row.target_release_id) if row.target_release_id else None,
                "error_code": row.error_code,
                "error_message": row.error_message,
                "cancel_requested": row.cancel_requested,
                "created_at": row.created_at.isoformat(),
                "updated_at": row.updated_at.isoformat(),
            }
            for row in rows
        ]


@router.get("/jobs/{job_id}", dependencies=[Depends(admin_gate)])
async def job_detail(job_id: uuid.UUID):
    async with session_scope() as session:
        result = await get_job(session, job_id)
        if result is None:
            raise HTTPException(status_code=404, detail="Job not found")
        job, events = result
        return {
            "job": {
                "id": str(job.id),
                "status": job.status,
                "progress": float(job.progress_percent or 0),
                "priority": job.priority,
                "attempts": job.attempts,
                "max_attempts": job.max_attempts,
                "error_code": job.error_code,
                "error_message": job.error_message,
                "metadata": job.extra_data,
            },
            "events": [
                {
                    "id": str(event.id),
                    "type": event.event_type,
                    "message": event.message,
                    "data": event.data,
                    "created_at": event.created_at.isoformat(),
                }
                for event in events
            ],
        }


@router.post("/jobs/{job_id}/retry", dependencies=[Depends(admin_gate)])
async def job_retry(job_id: uuid.UUID, request: Request):
    async with session_scope() as session:
        row_id = await retry_job(session, job_id)
        if row_id is None:
            raise HTTPException(status_code=409, detail="این Job قابل retry نیست.")
        await record_admin_action(
            session,
            action="RETRY_MEDIA_JOB",
            entity_type="media_job",
            entity_id=job_id,
            **_request_meta(request),
        )
        return {"ok": True, "id": str(row_id)}


@router.post("/jobs/{job_id}/cancel", dependencies=[Depends(admin_gate)])
async def job_cancel(job_id: uuid.UUID, request: Request):
    async with session_scope() as session:
        row = await cancel_job(session, job_id)
        if row is None:
            raise HTTPException(status_code=409, detail="این Job قابل لغو نیست.")
        await record_admin_action(
            session,
            action="CANCEL_MEDIA_JOB",
            entity_type="media_job",
            entity_id=job_id,
            **_request_meta(request),
        )
        return {"ok": True, "status": row["status"]}


@router.patch("/jobs/{job_id}/priority", dependencies=[Depends(admin_gate)])
async def job_priority(job_id: uuid.UUID, payload: JobPriority, request: Request):
    async with session_scope() as session:
        row = await set_job_priority(session, job_id, payload.priority)
        if row is None:
            raise HTTPException(status_code=409, detail="فقط Jobهای در صف قابل اولویت‌دهی هستند.")
        await record_admin_action(
            session,
            action="SET_JOB_PRIORITY",
            entity_type="media_job",
            entity_id=job_id,
            details={"priority": payload.priority},
            **_request_meta(request),
        )
        return {"ok": True, "priority": int(row["priority"])}


@router.post("/storage/register", dependencies=[Depends(admin_gate)])
async def storage_register(payload: StorageRegister, request: Request):
    async with session_scope() as session:
        row = await register_telegram_storage_file(session, **payload.model_dump())
        await record_admin_action(
            session,
            action="REGISTER_STORAGE_FILE",
            entity_type="storage_file",
            entity_id=row.id,
            **_request_meta(request),
        )
        return {"id": str(row.id)}


@router.post("/releases/{release_id}/attach-storage", dependencies=[Depends(admin_gate)])
async def storage_attach(release_id: uuid.UUID, payload: StorageAttach, request: Request):
    async with session_scope() as session:
        row = await attach_storage_file(
            session,
            release_id=release_id,
            storage_file_id=payload.storage_file_id,
            primary=payload.primary,
        )
        await record_admin_action(
            session,
            action="ATTACH_STORAGE_FILE",
            entity_type="release_file",
            entity_id=row.id,
            details={"release_id": str(release_id), "storage_file_id": str(payload.storage_file_id)},
            **_request_meta(request),
        )
        return {"id": str(row.id)}


@router.get("/plans", dependencies=[Depends(admin_gate)])
async def plans():
    async with session_scope() as session:
        rows = await session.scalars(select(Plan).order_by(Plan.sort_order.asc(), Plan.created_at.desc()).limit(500))
        return [
            {
                "id": str(x.id), "code": x.code, "name_fa": x.name_fa, "name_en": x.name_en,
                "price_toman": str(x.price_toman or 0), "price_irr": str(x.price_irr or 0),
                "duration_days": x.duration_days, "rank": x.rank, "active": x.active,
                "sort_order": x.sort_order, "features": x.features,
            }
            for x in rows.all()
        ]


@router.post("/plans", dependencies=[Depends(admin_gate)])
async def plan_create(payload: PlanCreate, request: Request):
    async with session_scope() as session:
        exists = await session.scalar(select(Plan).where(Plan.code == payload.code))
        if exists:
            raise HTTPException(status_code=409, detail="کد پلن تکراری است.")
        row = Plan(**payload.model_dump())
        session.add(row)
        await session.flush()
        await record_admin_action(session, action="CREATE_PLAN", entity_type="plan", entity_id=row.id, **_request_meta(request))
        return {"id": str(row.id)}


@router.patch("/plans/{plan_id}", dependencies=[Depends(admin_gate)])
async def plan_update(plan_id: uuid.UUID, payload: PlanUpdate, request: Request):
    async with session_scope() as session:
        row = await session.get(Plan, plan_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Plan not found")
        values = payload.model_dump(exclude_unset=True)
        old = {k: getattr(row, k) for k in values}
        for k, v in values.items():
            setattr(row, k, v)
        await record_admin_action(session, action="UPDATE_PLAN", entity_type="plan", entity_id=row.id, details={"old": old, "new": values}, **_request_meta(request))
        return {"ok": True}


@router.get("/genres", dependencies=[Depends(admin_gate)])
async def genres():
    async with session_scope() as session:
        rows = await session.scalars(select(Genre).order_by(Genre.name_en.asc()).limit(500))
        return [{"id": str(x.id), "fa": x.name_fa, "en": x.name_en, "slug": x.slug} for x in rows.all()]


class GenreCreate(BaseModel):
    name_fa: str = Field(min_length=1, max_length=100)
    name_en: str = Field(min_length=1, max_length=100)
    slug: str | None = None


class PersonCreate(BaseModel):
    name_en: str = Field(min_length=1, max_length=160)
    name_fa: str | None = None


class CollectionCreate(BaseModel):
    name_fa: str = Field(min_length=1, max_length=160)
    name_en: str | None = None
    description: str | None = None
    parent_id: uuid.UUID | None = None


class SeasonCreate(BaseModel):
    season_number: int = Field(ge=1, le=500)
    title: str | None = None
    synopsis: str | None = None


class EpisodeCreate(BaseModel):
    episode_number: int = Field(ge=1, le=10000)
    title: str | None = None
    synopsis: str | None = None
    runtime_minutes: int | None = Field(default=None, ge=1, le=100000)


@router.post("/genres", dependencies=[Depends(admin_gate)])
async def genre_create(payload: GenreCreate, request: Request):
    async with session_scope() as session:
        row = await create_genre(session, **payload.model_dump())
        await record_admin_action(session, action="CREATE_GENRE", entity_type="genre", entity_id=row.id, **_request_meta(request))
        return {"id": str(row.id), "slug": row.slug}


@router.get("/people", dependencies=[Depends(admin_gate)])
async def people():
    async with session_scope() as session:
        rows = await session.scalars(select(Person).order_by(Person.name_en.asc()).limit(500))
        return [{"id": str(x.id), "fa": x.name_fa, "en": x.name_en, "slug": x.slug} for x in rows.all()]


@router.post("/people", dependencies=[Depends(admin_gate)])
async def person_create(payload: PersonCreate, request: Request):
    async with session_scope() as session:
        row = await create_person(session, **payload.model_dump())
        await record_admin_action(session, action="CREATE_PERSON", entity_type="person", entity_id=row.id, **_request_meta(request))
        return {"id": str(row.id), "slug": row.slug}


@router.get("/collections", dependencies=[Depends(admin_gate)])
async def collections_list():
    async with session_scope() as session:
        rows = await session.scalars(select(Collection).order_by(Collection.sort_order.asc(), Collection.name_fa.asc()).limit(200))
        return [{"id": str(x.id), "fa": x.name_fa, "en": x.name_en, "slug": x.slug, "parent_id": str(x.parent_id) if x.parent_id else None} for x in rows.all()]


@router.post("/collections", dependencies=[Depends(admin_gate)])
async def collection_create(payload: CollectionCreate, request: Request):
    async with session_scope() as session:
        if payload.parent_id is not None:
            parent = await session.get(Collection, payload.parent_id)
            if parent is None:
                raise HTTPException(status_code=404, detail="مجموعه والد پیدا نشد.")
        row = await create_collection(session, **payload.model_dump())
        await record_admin_action(session, action="CREATE_COLLECTION", entity_type="collection", entity_id=row.id, **_request_meta(request))
        return {"id": str(row.id), "slug": row.slug}


@router.get("/users", dependencies=[Depends(admin_gate)])
async def users_list(offset: int = 0, limit: int = 50):
    async with session_scope() as session:
        rows = await session.scalars(
            select(User).order_by(User.created_at.desc()).offset(max(offset, 0)).limit(max(1, min(limit, 100)))
        )
        return [
            {
                "id": str(x.id),
                "telegram_user_id": x.telegram_user_id,
                "username": x.username,
                "name": " ".join(v for v in (x.first_name, x.last_name) if v) or None,
                "status": x.status,
                "last_seen_at": x.last_seen_at.isoformat() if x.last_seen_at else None,
            }
            for x in rows.all()
        ]


@router.get("/titles/{title_id}/seasons", dependencies=[Depends(admin_gate)])
async def seasons_tree(title_id: uuid.UUID):
    async with session_scope() as session:
        return await list_series_tree(session, title_id)


@router.post("/titles/{title_id}/seasons", dependencies=[Depends(admin_gate)])
async def season_create(title_id: uuid.UUID, payload: SeasonCreate, request: Request):
    async with session_scope() as session:
        try:
            row = await create_season(session, title_id=title_id, **payload.model_dump())
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        await record_admin_action(session, action="CREATE_SEASON", entity_type="season", entity_id=row.id, **_request_meta(request))
        return {"id": str(row.id)}


@router.post("/seasons/{season_id}/episodes", dependencies=[Depends(admin_gate)])
async def episode_create(season_id: uuid.UUID, payload: EpisodeCreate, request: Request):
    async with session_scope() as session:
        try:
            row = await create_episode(session, season_id=season_id, **payload.model_dump())
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        await record_admin_action(session, action="CREATE_EPISODE", entity_type="episode", entity_id=row.id, **_request_meta(request))
        return {"id": str(row.id)}


@router.post("/episodes/{episode_id}/releases", dependencies=[Depends(admin_gate)])
async def episode_release_create(episode_id: uuid.UUID, payload: ReleaseCreate, request: Request):
    async with session_scope() as session:
        if payload.status == "PUBLISHED":
            raise HTTPException(status_code=422, detail="Release را ابتدا READY ایجاد کنید و پس از اتصال فایل منتشر کنید.")
        try:
            row = await create_episode_release(session, episode_id=episode_id, **payload.model_dump())
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        await record_admin_action(session, action="CREATE_RELEASE", entity_type="release", entity_id=row.id, **_request_meta(request))
        return {"id": str(row.id)}


@router.get("/storage/files", dependencies=[Depends(admin_gate)])
async def storage_files(unattached: bool = True, offset: int = 0, limit: int = 50):
    async with session_scope() as session:
        rows = await list_storage_files(session, unattached_only=unattached, limit=max(1, min(limit, 100)), offset=max(offset, 0))
        return [
            {
                "id": str(x.id),
                "filename": x.filename,
                "size_bytes": x.size_bytes,
                "mime_type": x.mime_type,
                "status": x.status,
                "created_at": x.created_at.isoformat() if x.created_at else None,
                "width": (x.extra_data or {}).get("width"),
                "height": (x.extra_data or {}).get("height"),
                "duration": (x.extra_data or {}).get("duration"),
            }
            for x in rows
        ]


class UploadAttach(BaseModel):
    """Create a Release for a Movie title or a Series episode and link an ingested storage file in one transaction."""

    storage_file_id: uuid.UUID
    title_id: uuid.UUID | None = None
    episode_id: uuid.UUID | None = None
    quality: str = Field(min_length=1, max_length=32)
    language: str = Field(default="ORIGINAL", max_length=32)
    subtitle_type: str = Field(default="NONE", max_length=32)
    label: str | None = Field(default=None, max_length=255)
    status: str = Field(default="READY", pattern="^(DRAFT|READY|PUBLISHED|DISABLED|ARCHIVED)$")


async def _release_parent_title(session, release: Release) -> Title | None:
    if release.title_id:
        return await session.get(Title, release.title_id)
    if not release.episode_id:
        return None
    stmt = (select(Title)
            .join(Series, Series.title_id == Title.id)
            .join(Season, Season.series_id == Series.id)
            .join(Episode, Episode.season_id == Season.id)
            .where(Episode.id == release.episode_id))
    return await session.scalar(stmt)


@router.post("/upload/attach", dependencies=[Depends(admin_gate)])
async def upload_attach(payload: UploadAttach, request: Request):
    if bool(payload.title_id) == bool(payload.episode_id):
        raise HTTPException(status_code=422, detail="فقط یکی از «فیلم/عنوان» یا «قسمت» را مشخص کنید.")
    async with session_scope() as session:
        storage = await session.get(StorageFile, payload.storage_file_id)
        if storage is None:
            raise HTTPException(status_code=404, detail="فایل ذخیره‌سازی پیدا نشد.")
        if storage.status != "READY" or storage.storage_scope != "PRODUCTION" or not storage.file_id:
            raise HTTPException(status_code=422, detail="فایل Storage هنوز برای اتصال آماده نیست.")
        meta = storage.extra_data or {}
        desired_status = payload.status
        create_status = "READY" if desired_status == "PUBLISHED" else desired_status
        fields = dict(
            quality=payload.quality,
            language=payload.language,
            subtitle_type=payload.subtitle_type,
            label=payload.label,
            width=meta.get("width"),
            height=meta.get("height"),
            size_bytes=storage.size_bytes,
            status=create_status,
        )
        try:
            if payload.title_id:
                title = await session.get(Title, payload.title_id)
                if title is None:
                    raise HTTPException(status_code=404, detail="عنوان پیدا نشد.")
                if title.kind == "SERIES":
                    raise HTTPException(status_code=422, detail="برای سریال باید قسمت انتخاب شود.")
                release = await create_release(session, title_id=payload.title_id, **fields)
            else:
                release = await create_episode_release(session, episode_id=payload.episode_id, **fields)
            link = await attach_storage_file(session, release_id=release.id, storage_file_id=storage.id, primary=True)
            if desired_status == "PUBLISHED":
                title = await _release_parent_title(session, release)
                if title is None:
                    raise HTTPException(status_code=422, detail="عنوان والد نسخه پیدا نشد.")
                if settings.content_rights_required and not title.rights_verified:
                    raise HTTPException(status_code=422, detail="برای انتشار باید حقوق محتوا تأیید شده باشد.")
                release.status = "PUBLISHED"
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        await record_admin_action(
            session,
            action="UPLOAD_ATTACH",
            entity_type="release",
            entity_id=release.id,
            details={"storage_file_id": str(storage.id), "status": release.status},
            **_request_meta(request),
        )
        return {"release_id": str(release.id), "release_file_id": str(link.id), "status": release.status}


@router.get("/users/{user_id}/360", dependencies=[Depends(admin_gate)])
async def user_360(user_id: uuid.UUID):
    async with session_scope() as session:
        user = await session.get(User, user_id)
        if user is None:
            raise HTTPException(status_code=404, detail="کاربر پیدا نشد.")
        wallet = await session.scalar(select(Wallet).where(Wallet.user_id == user.id))
        roles = await session.scalars(
            select(Role.name).join(user_roles, user_roles.c.role_id == Role.id).where(user_roles.c.user_id == user.id)
        )
        subscriptions = (await session.execute(
            select(Subscription, Plan.name_fa, Plan.code)
            .join(Plan, Plan.id == Subscription.plan_id)
            .where(Subscription.user_id == user.id)
            .order_by(Subscription.expires_at.desc()).limit(20)
        )).all()
        orders = (await session.execute(
            select(Order, Plan.name_fa).outerjoin(Plan, Plan.id == Order.plan_id)
            .where(Order.user_id == user.id)
            .order_by(Order.created_at.desc()).limit(30)
        )).all()
        favorites = await session.scalar(select(func.count()).select_from(Favorite).where(Favorite.user_id == user.id))
        history = await session.scalar(select(func.count()).select_from(WatchHistory).where(WatchHistory.user_id == user.id))
        return {
            "user": {
                "id": str(user.id), "telegram_user_id": user.telegram_user_id,
                "username": user.username, "first_name": user.first_name, "last_name": user.last_name,
                "language_code": user.language_code, "status": user.status,
                "created_at": user.created_at.isoformat(),
                "last_seen_at": user.last_seen_at.isoformat() if user.last_seen_at else None,
            },
            "roles": roles.all(),
            "wallet_balance_irr": str(wallet.balance_irr if wallet else 0),
            "favorites_count": int(favorites or 0), "watch_history_count": int(history or 0),
            "subscriptions": [
                {"id": str(x.id), "plan": name, "code": code, "status": x.status,
                 "starts_at": x.starts_at.isoformat(), "expires_at": x.expires_at.isoformat()}
                for x, name, code in subscriptions
            ],
            "orders": [
                {"id": str(o.id), "order_number": o.order_number, "plan": name,
                 "amount_toman": str(o.amount_toman or 0), "amount_irr": str(o.amount_irr),
                 "status": o.status, "created_at": o.created_at.isoformat()}
                for o, name in orders
            ],
        }


class UserStatusUpdate(BaseModel):
    status: str = Field(pattern="^(ACTIVE|BLOCKED|DISABLED)$")


@router.patch("/users/{user_id}/status", dependencies=[Depends(admin_gate)])
async def user_status_update(user_id: uuid.UUID, payload: UserStatusUpdate, request: Request):
    async with session_scope() as session:
        user = await session.get(User, user_id)
        if user is None:
            raise HTTPException(status_code=404, detail="کاربر پیدا نشد.")
        old = user.status
        user.status = payload.status
        await record_admin_action(
            session, action="UPDATE_USER_STATUS", entity_type="user", entity_id=user.id,
            details={"old": old, "new": payload.status}, **_request_meta(request)
        )
        return {"ok": True, "status": user.status}


@router.get("/releases", dependencies=[Depends(admin_gate)])
async def releases_list(title_id: uuid.UUID | None = None, episode_id: uuid.UUID | None = None, limit: int = 200):
    async with session_scope() as session:
        stmt = (select(Release, Episode.episode_number, Season.season_number)
                .outerjoin(Episode, Release.episode_id == Episode.id)
                .outerjoin(Season, Episode.season_id == Season.id))
        if episode_id is not None:
            stmt = stmt.where(Release.episode_id == episode_id)
        elif title_id is not None:
            title = await session.get(Title, title_id)
            if title is None:
                raise HTTPException(status_code=404, detail="عنوان پیدا نشد.")
            if title.kind == "SERIES":
                episode_ids = (select(Episode.id).join(Season, Episode.season_id == Season.id)
                               .join(Series, Season.series_id == Series.id).where(Series.title_id == title_id))
                stmt = stmt.where(or_(Release.title_id == title_id, Release.episode_id.in_(episode_ids)))
            else:
                stmt = stmt.where(Release.title_id == title_id)
        rows = (await session.execute(stmt.order_by(Release.created_at.desc()).limit(min(max(1, limit), 300)))).all()
        return [
            {"id": str(r.id), "title_id": str(r.title_id) if r.title_id else None,
             "episode_id": str(r.episode_id) if r.episode_id else None,
             "season_number": season_no, "episode_number": episode_no,
             "quality": r.quality, "language": r.language, "subtitle_type": r.subtitle_type,
             "status": r.status, "label": r.label, "size_bytes": r.size_bytes}
            for r, episode_no, season_no in rows
        ]


@router.get("/payments", dependencies=[Depends(admin_gate)])
async def payments_list(provider: str | None = None, status: str | None = None, limit: int = 100):
    async with session_scope() as session:
        stmt = (select(Order, Plan.name_fa, PaymentAttempt, Payment)
                .outerjoin(Plan, Plan.id == Order.plan_id)
                .outerjoin(PaymentAttempt, PaymentAttempt.order_id == Order.id)
                .outerjoin(Payment, Payment.payment_attempt_id == PaymentAttempt.id)
                .order_by(Order.created_at.desc())
                .limit(min(max(1, limit), 200)))
        if provider:
            stmt = stmt.where(PaymentAttempt.provider == provider.upper())
        if status:
            stmt = stmt.where(Order.status == status.upper())
        rows = (await session.execute(stmt)).all()
        return [
            {"order_number": order.order_number, "plan": plan_name,
             "provider": attempt.provider if attempt else None,
             "attempt_status": attempt.status if attempt else None,
             "payment_status": payment.status if payment else None,
             "amount_toman": str(order.amount_toman or 0),
             "paid_at": payment.paid_at.isoformat() if payment and payment.paid_at else None,
             "provider_reference": payment.provider_reference if payment else None}
            for order, plan_name, attempt, payment in rows
        ]


@router.get("/payment-gateway", dependencies=[Depends(admin_gate)])
async def payment_gateway():
    return {
        "winapay": {
            "merchant_configured": bool(settings.winapay_merchant_id),
            "sandbox": bool(settings.winapay_sandbox),
            "base_url": settings.winapay_base_url,
            "public_base_url_configured": bool(settings.public_base_url),
            "callback_url": f"{settings.public_base_url}/payments/winapay/callback" if settings.public_base_url else None,
            "note": "Secretها و Merchant ID واقعی از پنل برگردانده نمی‌شوند؛ شرایط پذیرندگی را با ارائه‌دهنده پرداخت تطبیق دهید.",
        }
    }


@router.get("/audit", dependencies=[Depends(admin_gate)])
async def audit(limit: int = 100):
    async with session_scope() as session:
        rows = await session.scalars(
            select(AdminActionLog).order_by(AdminActionLog.created_at.desc()).limit(min(max(1, limit), 300))
        )
        return [
            {"id": str(x.id), "created_at": x.created_at.isoformat(), "action": x.action,
             "entity_type": x.entity_type, "entity_id": str(x.entity_id) if x.entity_id else None,
             "method": x.method, "path": x.path, "success": x.success, "details": x.details,
             "ip_address": x.ip_address, "actor_user_id": str(x.actor_user_id) if x.actor_user_id else None}
            for x in rows.all()
        ]


@router.get("/settings", dependencies=[Depends(admin_gate)])
async def settings_safe():
    return {
        "app_env": settings.app_env, "telegram_mode": settings.telegram_mode,
        "public_base_url": settings.public_base_url, "allowed_hosts": list(settings.allowed_hosts),
        "rate_limit_enabled": settings.rate_limit_enabled,
        "bot_token_configured": bool(settings.bot_token),
        "admin_token_configured": bool(settings.admin_api_token),
        "database_configured": bool(settings.database_url),
        "redis_configured": bool(settings.redis_url),
        "telegram_storage_required": settings.telegram_storage_required,
        "production_storage_chat_configured": bool(settings.production_storage_chat_id),
        "content_rights_required": settings.content_rights_required,
        "startup_validate": settings.startup_validate,
        "maintenance_in_bot": settings.run_maintenance_in_bot,
        "notifications_in_bot": settings.run_notifications_in_bot,
        "media_upstream_allowlist_configured": bool(settings.media_upstream_allowlist),
        "media_allow_any_https": settings.media_allow_any_https,
        "warning": "Secretها از API پنل برگردانده نمی‌شوند؛ تغییرات محیطی را در .env انجام دهید.",
    }


@router.get("/bot-menu", dependencies=[Depends(admin_gate)])
async def bot_menu():
    from app.bot.keyboards.main_menu import MAIN_MENU_ITEMS
    return {"buttons": [{"text": t, "callback_data": c} for t, c in MAIN_MENU_ITEMS],
            "admin_button": {"text": "🛠 مدیریت", "callback_data": "menu:admin"},
            "commands": ["/start"]}


@router.get("/ui", response_class=HTMLResponse)
async def admin_ui():
    html_path = Path(__file__).parent / "templates" / "admin.html"
    return HTMLResponse(html_path.read_text(encoding="utf-8"))


@router.get("/instance", dependencies=[Depends(admin_gate)])
async def instance():
    return {"instance_id": service_instance_id()}


# ---------- Coupons management (commerce discounts) ----------


class CouponCreate(BaseModel):
    code: str | None = None
    name_fa: str = Field(min_length=1, max_length=160)
    description: str | None = None
    discount_type: str = Field(pattern="^(PERCENT|FIXED_TOMAN)$")
    value: Decimal = Field(gt=0)
    max_uses: int | None = Field(default=None, ge=1)
    per_user_limit: int = Field(default=1, ge=1)
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    active: bool = True
    scope_type: str = Field(pattern="^(ALL|PLAN|USERS)$")
    scope_plan_id: uuid.UUID | None = None
    telegram_user_ids: list[int] = Field(default_factory=list)


def _coupon_payload(c: Coupon, usage: dict) -> dict:
    return {
        "id": str(c.id),
        "code": c.code,
        "name_fa": c.name_fa,
        "description": c.description,
        "discount_type": c.discount_type,
        "value": float(c.value),
        "scope_type": c.scope_type,
        "scope_plan_id": str(c.scope_plan_id) if c.scope_plan_id else None,
        "max_uses": c.max_uses,
        "per_user_limit": c.per_user_limit,
        "valid_from": c.valid_from.isoformat() if c.valid_from else None,
        "valid_until": c.valid_until.isoformat() if c.valid_until else None,
        "active": bool(c.active),
        "usage_count": int(usage.get(c.id, 0)),
    }


@router.get("/coupons", dependencies=[Depends(admin_gate)])
async def coupons_list():
    async with session_scope() as session:
        rows = (
            await session.scalars(select(Coupon).order_by(Coupon.created_at.desc()).limit(500))
        ).all()
        usage_rows = await session.execute(
            select(CouponRedemption.coupon_id, func.count())
            .where(CouponRedemption.status == "REDEEMED")
            .group_by(CouponRedemption.coupon_id)
        )
        usage = {cid: n for cid, n in usage_rows.all()}
        return [_coupon_payload(c, usage) for c in rows]


@router.post("/coupons", dependencies=[Depends(admin_gate)])
async def coupons_create(payload: CouponCreate, request: Request):
    value = payload.value
    if payload.discount_type == "PERCENT" and value > Decimal("100"):
        raise HTTPException(status_code=422, detail="درصد تخفیف نمی‌تواند بیشتر از 100 باشد.")
    if payload.valid_from and payload.valid_until and payload.valid_until <= payload.valid_from:
        raise HTTPException(status_code=422, detail="پایان اعتبار باید بعد از شروع اعتبار باشد.")

    code = (payload.code or f"CP-{uuid.uuid4().hex[:8].upper()}").strip().upper()
    async with session_scope() as session:
        exists = await session.scalar(select(func.count()).select_from(Coupon).where(Coupon.code == code))
        if exists:
            raise HTTPException(status_code=409, detail="این کد تخفیف قبلاً ثبت شده است.")

        if payload.scope_type == "PLAN":
            if not payload.scope_plan_id:
                raise HTTPException(status_code=422, detail="برای محدوده پلن، انتخاب پلن الزامی است.")
            plan = await session.get(Plan, payload.scope_plan_id)
            if plan is None:
                raise HTTPException(status_code=404, detail="پلن انتخاب‌شده پیدا نشد.")

        coupon = Coupon(
            code=code,
            name_fa=payload.name_fa.strip(),
            description=(payload.description or "").strip() or None,
            discount_type=payload.discount_type,
            value=value,
            max_uses=payload.max_uses,
            per_user_limit=max(1, int(payload.per_user_limit or 1)),
            valid_from=payload.valid_from,
            valid_until=payload.valid_until,
            active=payload.active,
            scope_type=payload.scope_type,
            scope_plan_id=payload.scope_plan_id if payload.scope_type == "PLAN" else None,
        )
        session.add(coupon)
        await session.flush()

        if payload.scope_type == "USERS":
            telegram_ids = list({int(x) for x in payload.telegram_user_ids if int(x) > 0})
            if not telegram_ids:
                raise HTTPException(status_code=422, detail="برای محدوده کاربران منتخب، حداقل یک شناسه تلگرام لازم است.")
            users = (await session.scalars(select(User).where(User.telegram_user_id.in_(telegram_ids)))).all()
            found = {u.telegram_user_id for u in users}
            missing = [tid for tid in telegram_ids if tid not in found]
            for u in users:
                session.add(CouponTargetUser(coupon_id=coupon.id, user_id=u.id))
            await session.flush()
            if missing:
                coupon.description = (coupon.description or "") + f" | شناسه‌های یافت‌نشده: {missing[:20]}"
                await session.flush()

        await record_admin_action(
            session,
            action="CREATE_COUPON",
            entity_type="coupon",
            entity_id=coupon.id,
            details={"code": code, "discount_type": payload.discount_type, "value": float(value), "scope_type": payload.scope_type},
            **_request_meta(request),
        )
        return _coupon_payload(coupon, {})


class CouponUpdate(BaseModel):
    active: bool


@router.patch("/coupons/{coupon_id}", dependencies=[Depends(admin_gate)])
async def coupons_update(coupon_id: uuid.UUID, payload: CouponUpdate, request: Request):
    async with session_scope() as session:
        coupon = await session.get(Coupon, coupon_id)
        if coupon is None:
            raise HTTPException(status_code=404, detail="کد تخفیف پیدا نشد.")
        old = bool(coupon.active)
        coupon.active = payload.active
        await record_admin_action(
            session,
            action="UPDATE_COUPON",
            entity_type="coupon",
            entity_id=coupon.id,
            details={"old_active": old, "new_active": payload.active},
            **_request_meta(request),
        )
        return {"ok": True, "id": str(coupon.id), "active": bool(coupon.active)}


# ---------- Encoding repair (mojibake cleanup) ----------


@router.post("/repair-encoding", dependencies=[Depends(admin_gate)])
async def repair_encoding(request: Request):
    """اسکن و اصلاح متن‌های فارسی خراب‌شده (mojibake) در جدول‌های محتوایی."""
    scanned = 0
    changed = 0
    samples: list[dict] = []
    targets = [
        (Title, "title_fa", "عنوان"),
        (Title, "synopsis", "خلاصه"),
        (Genre, "name_fa", "ژانر"),
        (Person, "name_fa", "فرد"),
        (Person, "biography", "زندگی‌نامه"),
        (Collection, "name_fa", "مجموعه"),
        (Collection, "description", "توضیحات مجموعه"),
        (Season, "title", "عنوان فصل"),
        (Season, "synopsis", "خلاصه فصل"),
        (Episode, "title", "عنوان قسمت"),
        (Episode, "synopsis", "خلاصه قسمت"),
        (Release, "label", "برچسب نسخه"),
        (Plan, "name_fa", "نام پلن"),
        (Plan, "description", "توضیحات پلن"),
        (Coupon, "name_fa", "نام کد تخفیف"),
        (Coupon, "description", "توضیحات کد تخفیف"),
        (NotificationTemplate, "title", "عنوان اعلان"),
        (NotificationTemplate, "body", "متن اعلان"),
    ]
    async with session_scope() as session:
        for model, field, label in targets:
            rows = (await session.scalars(select(model))).all()
            for row in rows:
                value = getattr(row, field, None)
                scanned += 1
                fixed = repair_mojibake(value)
                if fixed != value:
                    changed += 1
                    if len(samples) < 20:
                        samples.append({"field": label, "id": str(row.id), "before": value, "after": fixed})
                    setattr(row, field, fixed)
        # نظرهای کاربران (جدول raw بدون ORM)
        comment_rows = (
            await session.execute(text("SELECT id, body FROM content_comments"))
        ).mappings().all()
        for row in comment_rows:
            scanned += 1
            fixed = repair_mojibake(row.get("body"))
            if fixed != row.get("body"):
                changed += 1
                if len(samples) < 20:
                    samples.append({"field": "نظر کاربر", "id": str(row["id"]), "before": row.get("body"), "after": fixed})
                await session.execute(
                    text("UPDATE content_comments SET body = :body WHERE id = :id"),
                    {"body": fixed, "id": row["id"]},
                )
        if changed:
            await record_admin_action(
                session,
                action="REPAIR_UTF8_MOJIBAKE",
                entity_type="content_text",
                entity_id=None,
                details={"scanned": scanned, "changed": changed, "samples": samples[:10]},
                **_request_meta(request),
            )
    return {"apply": True, "scanned": scanned, "changed_fields": changed, "changed": changed, "samples": samples}
