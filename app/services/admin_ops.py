from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.db.models import ContentPipelineRun, MediaJob, MediaJobEvent


async def list_jobs(session, *, status: str | None = None, limit: int = 50, offset: int = 0):
    stmt = select(MediaJob).order_by(MediaJob.created_at.desc()).offset(max(0, offset)).limit(min(max(1, limit), 100))
    if status:
        stmt = stmt.where(MediaJob.status == status.upper())
    return list((await session.scalars(stmt)).all())


async def get_job(session, job_id):
    job = await session.get(MediaJob, job_id)
    if job is None:
        return None
    events = list(
        (
            await session.scalars(
                select(MediaJobEvent)
                .where(MediaJobEvent.job_id == job.id)
                .order_by(MediaJobEvent.created_at.desc())
                .limit(100)
            )
        ).all()
    )
    return job, events


async def retry_job(session, job_id):
    result = await session.execute(
        __import__("sqlalchemy").text(
            """
            UPDATE media_jobs
            SET status = 'RETRY',
                available_at = CURRENT_TIMESTAMP,
                locked_by = NULL,
                locked_at = NULL,
                heartbeat_at = NULL,
                finished_at = NULL,
                error_code = NULL,
                error_message = NULL,
                cancel_requested = FALSE,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = :id
              AND status IN ('FAILED', 'CANCELLED', 'RETRY', 'QUEUED')
            RETURNING id
            """
        ),
        {"id": job_id},
    )
    return result.scalar_one_or_none()


async def cancel_job(session, job_id):
    result = await session.execute(
        __import__("sqlalchemy").text(
            """
            UPDATE media_jobs
            SET cancel_requested = TRUE,
                updated_at = CURRENT_TIMESTAMP,
                status = CASE
                    WHEN status IN ('QUEUED', 'RETRY') THEN 'CANCELLED'
                    ELSE status
                END,
                finished_at = CASE
                    WHEN status IN ('QUEUED', 'RETRY') THEN CURRENT_TIMESTAMP
                    ELSE finished_at
                END
            WHERE id = :id
              AND status NOT IN ('SUCCEEDED', 'FAILED', 'CANCELLED')
            RETURNING id, status
            """
        ),
        {"id": job_id},
    )
    return result.mappings().first()


async def set_job_priority(session, job_id, priority: int):
    result = await session.execute(
        __import__("sqlalchemy").text(
            """
            UPDATE media_jobs
            SET priority = :priority, updated_at = CURRENT_TIMESTAMP
            WHERE id = :id AND status IN ('QUEUED', 'RETRY')
            RETURNING id, priority
            """
        ),
        {"id": job_id, "priority": max(-1000, min(int(priority), 1000))},
    )
    return result.mappings().first()


async def list_pipeline_runs(session, *, status: str | None = None, limit: int = 50, offset: int = 0):
    stmt = (
        select(ContentPipelineRun)
        .options(selectinload(ContentPipelineRun.items))
        .order_by(ContentPipelineRun.created_at.desc())
        .offset(max(0, offset))
        .limit(min(max(1, limit), 100))
    )
    if status:
        stmt = stmt.where(ContentPipelineRun.status == status.upper())
    return list((await session.scalars(stmt)).all())


async def job_stats(session):
    rows = (
        await session.execute(
            __import__("sqlalchemy").text(
                """
                SELECT status, COUNT(*) AS count
                FROM media_jobs
                GROUP BY status
                ORDER BY status
                """
            )
        )
    ).mappings().all()
    return {row["status"]: int(row["count"]) for row in rows}


async def pipeline_stats(session):
    rows = (
        await session.execute(
            __import__("sqlalchemy").text(
                """
                SELECT status, COUNT(*) AS count
                FROM content_pipeline_runs
                GROUP BY status
                ORDER BY status
                """
            )
        )
    ).mappings().all()
    return {row["status"]: int(row["count"]) for row in rows}
