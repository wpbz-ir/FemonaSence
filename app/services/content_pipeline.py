from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
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
from app.services.media_jobs import enqueue_transcode
from app.services.media_profiles import normalize_quality


DEFAULT_QUALITIES = ("480", "720", "1080")


class PipelineError(ValueError):
    pass


async def _resolve_parent(session, source_release: Release) -> tuple[object, object, object]:
    if source_release.title_id is not None:
        return source_release.title_id, source_release.episode_id, "TITLE"

    if source_release.episode_id is None:
        raise PipelineError("Source release parent is invalid.")

    row = (
        await session.execute(
            select(Episode.id, Season.id.label("season_id"), Series.title_id)
            .join(Season, Season.id == Episode.season_id)
            .join(Series, Series.id == Season.series_id)
            .where(Episode.id == source_release.episode_id)
        )
    ).first()
    if row is None:
        raise PipelineError("Episode parent could not be resolved.")
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


def _source_height(release: Release, storage: StorageFile) -> int:
    if release.height:
        return int(release.height)
    metadata = storage.extra_data or {}
    value = metadata.get("height") or metadata.get("video_height")
    try:
        return int(value)
    except (TypeError, ValueError):
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
):
    source = await session.get(Release, source_release_id)
    if source is None:
        raise PipelineError("Source release not found.")
    if source.status in {"ARCHIVED", "DISABLED"}:
        raise PipelineError("Source release is disabled or archived.")

    title_id, episode_id, parent_type = await _resolve_parent(session, source)
    storage = await _source_storage(session, source.id)
    if storage is None:
        raise PipelineError("Source release has no READY StorageFile.")

    source_height = _source_height(source, storage)
    if source_height <= 0:
        raise PipelineError("Source dimensions are required before building the quality matrix.")
    if settings.content_rights_required:
        title = await session.get(Title, title_id)
        if title is None or not title.rights_verified:
            raise PipelineError("این عنوان تا زمان ثبت و تأیید مجوز پخش قابل انتشار نیست.")

    raw = requested_qualities or DEFAULT_QUALITIES
    qualities = sorted({normalize_quality(value) for value in raw}, key=lambda x: int(x))
    qualities = [q for q in qualities if int(q) <= source_height]
    if not qualities:
        raise PipelineError(f"No requested quality is supported by the {source_height}p source.")

    dedupe_key = f"{source.id}:{storage.id}:{','.join(qualities)}:{int(bool(requires_subscription))}:{int(minimum_plan_rank)}"
    run = await session.scalar(
        select(ContentPipelineRun)
        .where(ContentPipelineRun.dedupe_key == dedupe_key)
        .options(selectinload(ContentPipelineRun.items))
    )

    if run is None:
        run = ContentPipelineRun(
            title_id=title_id,
            source_release_id=source.id,
            source_storage_file_id=storage.id,
            requested_qualities=qualities,
            status="QUEUED",
            created_by_user_id=created_by_user_id,
            dedupe_key=dedupe_key,
        )
        session.add(run)
        await session.flush()
    elif run.status in {"QUEUED", "RUNNING", "SUCCEEDED"}:
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
        target = await session.scalar(
            select(Release).where(Release.pipeline_key == pipeline_key)
        )

        if target is None:
            common = dict(
                label=f"{quality}p • فمونا",
                quality=f"{quality}p",
                width=None,
                height=int(quality),
                codec_video="H.264",
                codec_audio="AAC",
                container="MP4",
                status="DRAFT",
                source="PIPELINE",
                priority=int(priority),
                pipeline_key=pipeline_key,
                technical_metadata={
                    "pipeline": "quality_matrix",
                    "source_release_id": str(source.id),
                    "source_storage_file_id": str(storage.id),
                    "target_quality": f"{quality}p",
                },
            )
            target = Release(
                title_id=title_id if parent_type == "TITLE" else None,
                episode_id=episode_id if parent_type == "EPISODE" else None,
                **common,
            )
            session.add(target)
            await session.flush()

        policy = await session.scalar(
            select(AccessPolicy).where(AccessPolicy.release_id == target.id)
        )
        if policy is None:
            policy = AccessPolicy(
                release_id=target.id,
                access_type="PREMIUM" if requires_subscription else "PUBLIC",
                requires_subscription=bool(requires_subscription),
                minimum_plan_rank=max(0, int(minimum_plan_rank)),
                active=True,
                extra_data={"source": "content_pipeline"},
            )
            session.add(policy)
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


async def sync_pipeline_run(session, run_id):
    run = await session.get(ContentPipelineRun, run_id, options=[selectinload(ContentPipelineRun.items)])
    if run is None:
        return None

    if not run.items:
        run.status = "FAILED"
        run.error_message = "Pipeline has no items."
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
    elif any(status in {"FAILED", "CANCELLED"} for status in statuses):
        run.status = "FAILED"
        run.error_message = "One or more quality jobs failed."
    else:
        run.status = "RUNNING"

    await session.flush()
    return run
