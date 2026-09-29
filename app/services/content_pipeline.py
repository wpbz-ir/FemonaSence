from __future__ import annotations

import hashlib
import re
import uuid
from datetime import datetime, timezone
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from app.db.models import (
    AccessPolicy,
    ContentPipelineItem,
    ContentPipelineRun,
    MediaJob,
    Release,
    ReleaseFile,
    Season,
    Series,
    StorageFile,
    Title,
)
from app.db.models.content import Episode
from app.core.config import settings
from app.core.media_config import load_media_settings
from app.services.media_jobs import enqueue_transcode
from app.services.media_profiles import normalize_quality


DEFAULT_QUALITIES = ("480", "720", "1080")

# [PREFLIGHT] سقف دانلود فایلِ منبع بر اساس نوع Bot API:
#   ابری (api.telegram.org): سقف سختِ getFile = ۲۰ مگابایت — هر jobی با منبعِ بزرگ‌تر
#   در مرحله دانلود شکست می‌خورد؛ اینجا زودتر و شفاف رد می‌شود.
#   محلی (telegram-bot-api --local): تا ۲ گیگابایت (سقف ارسال ۲ گیگابایت هم هست).
_CLOUD_BOT_API_HOSTS = {"api.telegram.org", ""}
_CLOUD_DOWNLOAD_CAP = 20_000_000
_LOCAL_DOWNLOAD_CAP = 2_000_000_000


def _bot_api_download_cap() -> int:
    """سقف دانلود منبع (بایت) بر اساس TELEGRAM_BOT_API_BASE_URL فعلی."""
    host = (urlsplit(load_media_settings().bot_api_base_url).hostname or "").lower()
    return _CLOUD_DOWNLOAD_CAP if host in _CLOUD_BOT_API_HOSTS else _LOCAL_DOWNLOAD_CAP


class PipelineError(ValueError):
    pass


# Canonical defaults for the advanced matrix options (mirrors MatrixRequest in
# app/api/admin_extended.py). Values are truncated to the DB column limits.
_ADVANCED_DEFAULTS = {
    "description": None,
    "target_language": "ORIGINAL",
    "target_subtitle_type": "NONE",
    "target_codec_video": "H.264",
    "target_codec_audio": "AAC",
    "target_container": "MP4",
    "auto_publish": False,
}


def _advanced_settings(
    *,
    description: str | None,
    target_language: str,
    target_subtitle_type: str,
    target_codec_video: str,
    target_codec_audio: str,
    target_container: str,
    auto_publish: bool,
) -> dict:
    """Normalize the advanced matrix options into comparable, storable values."""
    return {
        "description": ((description or "").strip()[:2000]) or None,
        "target_language": ((target_language or "").strip()[:32]) or "ORIGINAL",
        "target_subtitle_type": ((target_subtitle_type or "").strip()[:32]) or "NONE",
        "target_codec_video": ((target_codec_video or "").strip()[:64]) or "H.264",
        "target_codec_audio": ((target_codec_audio or "").strip()[:64]) or "AAC",
        "target_container": ((target_container or "").strip()[:16]) or "MP4",
        "auto_publish": bool(auto_publish),
    }


def _advanced_key(settings: dict) -> str:
    """Stable signature of the advanced options that fork new runs/targets."""
    return ":".join(
        (
            settings["target_language"],
            settings["target_subtitle_type"],
            settings["target_codec_video"],
            settings["target_codec_audio"],
            settings["target_container"],
            "1" if settings["auto_publish"] else "0",
        )
    )


async def _resolve_parent(session, source_release: Release) -> tuple[object, object, object]:
    if source_release.title_id is not None:
        return source_release.title_id, source_release.episode_id, "TITLE"

    if source_release.episode_id is None:
        raise PipelineError("والدِ نسخهٔ منبع نامعتبر است.")

    row = (
        await session.execute(
            select(Episode.id, Season.id.label("season_id"), Series.title_id)
            .join(Season, Season.id == Episode.season_id)
            .join(Series, Series.id == Season.series_id)
            .where(Episode.id == source_release.episode_id)
        )
    ).first()
    if row is None:
        raise PipelineError("قسمتِ والد قابل شناسایی نیست.")
    return row.title_id, source_release.episode_id, "EPISODE"


async def _source_storage(session, release_id):
    row = (
        await session.execute(
            select(ReleaseFile, StorageFile)
            .join(StorageFile, StorageFile.id == ReleaseFile.storage_file_id)
            .where(
                ReleaseFile.release_id == release_id,
                ReleaseFile.active.is_(True),
                StorageFile.status == "READY",
            )
            .order_by(ReleaseFile.is_primary.desc(), StorageFile.created_at.desc())
            .limit(1)
        )
    ).first()
    return row[1] if row else None


def _source_height(release: Release, storage: StorageFile, override: int | None = None) -> int:
    """ارتفاع مؤثرِ منبع — نزدیک‌ترین داده‌ی در دسترس، در ترتیب اتکا:

    ۱) height ثبت‌شده روی خود نسخه (از متادیتای پست ویدیویی تلگرام)
    ۲) متادیتای فایل ذخیره‌سازی (height / video_height)
    ۳) مقدار دستیِ ادمین (فیلد «ارتفاع منبع» در فرم پردازش کیفیت)
    ۴) استخراج از رشته‌ی کیفیتِ ثبت‌شده روی نسخه («1080p» → 1080، «4K» → 2160)

    فایل‌هایی که به‌صورت «فایل/دوکیومنت» در کانال ارسال می‌شوند از تلگرام ابعاد
    نمی‌گیرند؛ برای همین مسیرهای ۳ و ۴ وجود دارند تا ثبتِ ماتریس کیفیت هرگز به
    دلیل نبودِ ابعاد متوقف نشود.
    """
    candidates: list[int] = []
    if release.height:
        candidates.append(int(release.height))
    metadata = storage.extra_data or {}
    value = metadata.get("height") or metadata.get("video_height")
    try:
        candidates.append(int(value))
    except (TypeError, ValueError):
        pass
    if override:
        try:
            candidates.append(int(override))
        except (TypeError, ValueError):
            pass
    for candidate in candidates:
        if candidate > 0:
            return candidate
    # آخرین پشتوانه: کیفیتِ ثبت‌شده‌ی خود نسخه (ارقام فارسی هم نرمال می‌شود)
    quality = str(
        release.quality or ""
    ).translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")).strip().lower()
    mapped = {"4k": 2160, "2k": 1440, "8k": 4320}.get(quality)
    if mapped:
        return mapped
    match = re.search(r"(\d{3,4})\s*p?", quality)
    if match:
        height = int(match.group(1))
        if 100 < height <= 8640:
            return height
    return 0


async def build_quality_matrix(
    session,
    *,
    source_release_id,
    requested_qualities: tuple[str, ...] | list[str] | None = None,
    created_by_user_id=None,
    requires_subscription: bool = False,
    minimum_plan_rank: int = 0,
    priority: int = 50,
    source_height: int | None = None,
    description: str | None = None,
    target_language: str = "ORIGINAL",
    target_subtitle_type: str = "NONE",
    target_codec_video: str = "H.264",
    target_codec_audio: str = "AAC",
    target_container: str = "MP4",
    auto_publish: bool = False,
):
    source = await session.get(Release, source_release_id)
    if source is None:
        raise PipelineError("نسخهٔ منبع پیدا نشد.")
    if source.status in {"ARCHIVED", "DISABLED"}:
        raise PipelineError("نسخهٔ منبع غیرفعال یا بایگانی شده است.")

    title_id, episode_id, parent_type = await _resolve_parent(session, source)
    storage = await _source_storage(session, source.id)
    if storage is None:
        raise PipelineError("فایل ذخیره‌سازی آماده‌ای به این نسخه متصل نیست.")

    # [PREFLIGHT] حجم فایل منبع را پیش از ساخت هر jobای بسنجیم تا به‌جای شکستِ
    # مبهمِ کارگر رسانه در مرحله دانلود، همین‌جا پیام روشن فارسی به ادمین برسد.
    try:
        source_size = int(getattr(storage, "size_bytes", None) or 0)
    except (TypeError, ValueError):
        source_size = 0
    cap = _bot_api_download_cap()
    if source_size > cap:
        if cap == _CLOUD_DOWNLOAD_CAP:
            raise PipelineError(
                f"حجم فایل منبع ({source_size // 1_000_000} مگابایت) بیشتر از سقف دانلود Bot API ابری تلگرام (۲۰ مگابایت) است و قابل پردازش نیست. "
                "برای پردازش فایل‌های حجیم باید سرور Bot API محلی را فعال کنید «TELEGRAM_BOT_API_BASE_URL=http://127.0.0.1:8081»؛ راهنمای کامل: docs/LOCAL_BOT_API_SETUP_FA.md"
            )
        raise PipelineError(
            f"حجم فایل منبع ({source_size // 1_000_000} مگابایت) از سقف پردازش (۲ گیگابایت) بیشتر است."
        )

    source_height = _source_height(source, storage, override=source_height)
    if source_height <= 0:
        raise PipelineError(
            "ابعاد منبع مشخص نیست؛ در فرم «پردازش کیفیت» فیلد «ارتفاع منبع (p)» را پر کنید. "
            "(فایل‌هایی که به‌صورت «فایل» در کانال ارسال می‌شوند ابعاد ندارند)"
        )
    if settings.content_rights_required:
        title = await session.get(Title, title_id)
        if title is None or not title.rights_verified:
            raise PipelineError("این عنوان تا زمان ثبت و تأیید مجوز پخش قابل انتشار نیست.")

    raw = requested_qualities or DEFAULT_QUALITIES
    qualities = sorted({normalize_quality(value) for value in raw}, key=lambda x: int(x))
    qualities = [q for q in qualities if int(q) <= source_height]
    if not qualities:
        raise PipelineError(f"هیچ‌یک از کیفیت‌های درخواستی برای منبعِ {source_height}p قابل ساخت نیست.")

    advanced = _advanced_settings(
        description=description,
        target_language=target_language,
        target_subtitle_type=target_subtitle_type,
        target_codec_video=target_codec_video,
        target_codec_audio=target_codec_audio,
        target_container=target_container,
        auto_publish=auto_publish,
    )
    advanced_key = _advanced_key(advanced)
    default_advanced_key = _advanced_key(_ADVANCED_DEFAULTS)
    # Only non-default advanced options fork new dedupe/pipeline keys, so legacy
    # callers (and existing DB rows) keep their original stable keys.
    advanced_digest = (
        ""
        if advanced_key == default_advanced_key
        else hashlib.sha256(advanced_key.encode("utf-8")).hexdigest()[:12]
    )
    # Persist the full advanced snapshot (including description) on created
    # targets so nothing requested by the admin is lost.
    advanced_payload = dict(advanced) if (advanced_digest or advanced["description"]) else None

    dedupe_key = f"{source.id}:{storage.id}:{','.join(qualities)}:{int(bool(requires_subscription))}:{int(minimum_plan_rank)}"
    if advanced_digest:
        dedupe_key = f"{dedupe_key}:{advanced_digest}"
    run = await session.scalar(
        select(ContentPipelineRun)
        .where(ContentPipelineRun.dedupe_key == dedupe_key)
        .options(selectinload(ContentPipelineRun.items))
    )

    inserted_here = False
    if run is None:
        # SELECT-then-INSERT race on uq_content_pipeline_runs_dedupe: two
        # concurrent matrix builds can both observe "no run" above. Make the
        # insert idempotent (ON CONFLICT DO NOTHING) and re-SELECT the
        # surviving row instead of crashing with an uncaught IntegrityError.
        insert_result = await session.execute(
            pg_insert(ContentPipelineRun)
            .values(
                id=uuid.uuid4(),
                title_id=title_id,
                source_release_id=source.id,
                source_storage_file_id=storage.id,
                requested_qualities=qualities,
                status="QUEUED",
                created_by_user_id=created_by_user_id,
                dedupe_key=dedupe_key,
            )
            .on_conflict_do_nothing(constraint="uq_content_pipeline_runs_dedupe")
        )
        # rowcount == 1 iff THIS txn won the insert race.
        inserted_here = (insert_result.rowcount or 0) > 0
        run = await session.scalar(
            select(ContentPipelineRun)
            .where(ContentPipelineRun.dedupe_key == dedupe_key)
            .options(selectinload(ContentPipelineRun.items))
        )
        if run is None:  # defensive: our own committed-in-txn write must exist
            raise PipelineError("اجرای خط تولید ساخته یا خوانده نشد.")

    if not inserted_here and run.status in {"QUEUED", "RUNNING", "SUCCEEDED"}:
        return run

    if run.status == "FAILED":
        run.status = "QUEUED"
        run.error_message = None
        run.finished_at = None

    run.status = "RUNNING"
    run.started_at = run.started_at or datetime.now(timezone.utc)

    for quality in qualities:
        existing_item = next((item for item in run.items if item.quality == quality), None)
        if existing_item is None:
            existing_item = ContentPipelineItem(run_id=run.id, quality=quality, status="QUEUED")
            session.add(existing_item)
            await session.flush()

        # Do not duplicate successful outputs.
        if existing_item.status == "SUCCEEDED":
            continue

        pipeline_key = f"matrix:{source.id}:{storage.id}:{quality}"
        if advanced_digest:
            pipeline_key = f"{pipeline_key}:{advanced_digest}"
        target = await session.scalar(
            select(Release).where(Release.pipeline_key == pipeline_key)
        )

        if target is None:
            common = dict(
                label=f"{quality}p • فمونا",
                quality=f"{quality}p",
                width=None,
                height=int(quality),
                codec_video=advanced["target_codec_video"],
                codec_audio=advanced["target_codec_audio"],
                container=advanced["target_container"],
                language=advanced["target_language"],
                subtitle_type=advanced["target_subtitle_type"],
                status="DRAFT",
                source="PIPELINE",
                priority=int(priority),
                pipeline_key=pipeline_key,
                technical_metadata={
                    "pipeline": "quality_matrix",
                    "source_release_id": str(source.id),
                    "source_storage_file_id": str(storage.id),
                    "target_quality": f"{quality}p",
                    **(
                        {"advanced_settings": dict(advanced_payload)}
                        if advanced_payload
                        else {}
                    ),
                },
            )
            candidate = Release(
                title_id=title_id if parent_type == "TITLE" else None,
                episode_id=episode_id if parent_type == "EPISODE" else None,
                **common,
            )
            # [FIX/RACE] مهاجرت 0006 روی releases.pipeline_key ایندکس یکتای جزئی
            # (uq_releases_pipeline_key) ساخته است؛ رقیبی که بین SELECT بالا و flush
            # ما کامیت شود، flush را با IntegrityError می‌کُشد (500 در پنل). داخل
            # SAVEPOINT ثبت می‌کنیم؛ در تعارض، نقطه ذخیره برمی‌گردد و ردیفِ برنده
            # رقیب دوباره خوانده می‌شود — دیگر crash و نسخهٔ تکراری وجود ندارد.
            try:
                async with session.begin_nested():
                    session.add(candidate)
                    await session.flush()
            except IntegrityError:
                rival = await session.scalar(
                    select(Release).where(Release.pipeline_key == pipeline_key)
                )
                if rival is None:
                    raise
                target = rival
            else:
                target = candidate

        policy = await session.scalar(
            select(AccessPolicy).where(AccessPolicy.release_id == target.id)
        )
        if policy is None:
            # RACE (uq_access_policies_release): یک سازندهٔ هم‌زمان می‌تواند پالیسیِ
            # همان target را بین SELECT بالا و flush ما ثبت کند — ON CONFLICT DO NOTHING
            # + re-select تضمین می‌کند دقیقاً یک ردیف بماند و IntegrityError بالا نزند.
            await session.execute(
                pg_insert(AccessPolicy)
                .values(
                    release_id=target.id,
                    access_type="PREMIUM" if requires_subscription else "PUBLIC",
                    requires_subscription=bool(requires_subscription),
                    minimum_plan_rank=max(0, int(minimum_plan_rank)),
                    active=True,
                    extra_data={"source": "content_pipeline"},
                )
                .on_conflict_do_nothing(constraint="uq_access_policies_release")
            )
            policy = await session.scalar(
                select(AccessPolicy).where(AccessPolicy.release_id == target.id)
            )
            if policy is None:
                # عملاً دست‌نیافتنی: DO NOTHING یعنی برندهٔ هم‌زمان کامیت شده و دیده می‌شود.
                raise RuntimeError(f"ردیف سیاست دسترسی پس از ثبت برای نسخه {target.id} پیدا نشد.")
        else:
            policy.requires_subscription = bool(requires_subscription)
            policy.minimum_plan_rank = max(0, int(minimum_plan_rank))
            policy.access_type = "PREMIUM" if requires_subscription else "PUBLIC"
            policy.active = True

        result = await enqueue_transcode(
            session,
            source_release_id=source.id,
            source_storage_file_id=storage.id,
            target_release_id=target.id,
            target_quality=quality,
            priority=priority,
        )

        existing_item.target_release_id = target.id
        existing_item.media_job_id = result["id"]
        existing_item.status = "QUEUED"
        existing_item.queued_at = datetime.now(timezone.utc)
        existing_item.error_message = None

    await session.flush()
    return run


async def _auto_publish_ready_targets(session, run: ContentPipelineRun) -> int:
    """Publish READY matrix targets whose run requested auto_publish=True.

    The auto_publish intent is recorded on each created target release under
    technical_metadata["advanced_settings"] when the run is built; this publish
    step consumes it once every quality job of the run has succeeded. Safe to
    re-run: only READY targets are flipped, so repeated syncs are no-ops.
    """
    if not run.items:
        return 0

    flagged = []
    for item in run.items:
        if item.target_release_id is None:
            continue
        target = await session.get(Release, item.target_release_id)
        if target is None or target.status != "READY":
            continue
        advanced = (target.technical_metadata or {}).get("advanced_settings") or {}
        if advanced.get("auto_publish"):
            flagged.append(target)
    if not flagged:
        return 0

    if settings.content_rights_required:
        try:
            source_release = await session.get(Release, run.source_release_id)
            if source_release is None:
                return 0
            title_id, _, _ = await _resolve_parent(session, source_release)
            title = await session.get(Title, title_id)
        except PipelineError:
            return 0
        if title is None or not title.rights_verified:
            return 0

    for target in flagged:
        target.status = "PUBLISHED"
    await session.flush()
    return len(flagged)


async def sync_pipeline_run(session, run_id):
    run = await session.get(ContentPipelineRun, run_id, options=[selectinload(ContentPipelineRun.items)])
    if run is None:
        return None

    if not run.items:
        run.status = "FAILED"
        run.error_message = "این اجرا هیچ آیتمی ندارد."
        return run

    statuses = []
    for item in run.items:
        if item.media_job_id is None:
            statuses.append(item.status)
            continue
        job = await session.scalar(
            select(MediaJob).where(MediaJob.id == item.media_job_id)
        )
        if job is None:
            statuses.append(item.status)
            continue
        if job.status == "SUCCEEDED":
            item.status = "SUCCEEDED"
            item.finished_at = item.finished_at or datetime.now(timezone.utc)
        elif job.status in {"FAILED", "CANCELLED"}:
            item.status = job.status
            item.error_message = job.error_message
        elif job.status == "RUNNING":
            item.status = "RUNNING"
        else:
            item.status = "QUEUED"
        statuses.append(item.status)

    if all(status == "SUCCEEDED" for status in statuses):
        run.status = "SUCCEEDED"
        run.finished_at = run.finished_at or datetime.now(timezone.utc)
        await _auto_publish_ready_targets(session, run)
    elif any(status in {"FAILED", "CANCELLED"} for status in statuses):
        run.status = "FAILED"
        run.error_message = "یک یا چند کارِ کیفیت شکست خورده است."
    else:
        run.status = "RUNNING"

    await session.flush()
    return run
