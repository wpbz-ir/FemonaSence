from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

from sqlalchemy import text


def _now():
    return datetime.now(timezone.utc)


async def recover_stale_jobs(session, *, lease_seconds: int) -> int:
    result = await session.execute(
        text(
            """
            UPDATE media_jobs
            SET status = 'RETRY',
                locked_by = NULL,
                locked_at = NULL,
                heartbeat_at = NULL,
                available_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP,
                error_code = 'STALE_LEASE',
                error_message = 'Worker lease expired; job returned to queue.'
            WHERE status = 'RUNNING'
              AND COALESCE(heartbeat_at, locked_at) < CURRENT_TIMESTAMP - make_interval(secs => :lease_seconds)
            """
        ),
        {"lease_seconds": int(lease_seconds)},
    )
    return int(result.rowcount or 0)


async def claim_job(session, *, worker_id: str):
    async with session.begin():
        row = (
            await session.execute(
                text(
                    """
                    SELECT id
                    FROM media_jobs
                    WHERE status IN ('QUEUED', 'RETRY')
                      AND cancel_requested = FALSE
                      AND available_at <= CURRENT_TIMESTAMP
                    ORDER BY priority DESC, created_at ASC
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                    """
                )
            )
        ).scalar_one_or_none()
        if row is None:
            return None

        result = await session.execute(
            text(
                """
                UPDATE media_jobs
                SET status = 'RUNNING',
                    attempts = attempts + 1,
                    locked_by = :worker_id,
                    locked_at = CURRENT_TIMESTAMP,
                    heartbeat_at = CURRENT_TIMESTAMP,
                    started_at = COALESCE(started_at, CURRENT_TIMESTAMP),
                    progress_percent = 0,
                    error_code = NULL,
                    error_message = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = :job_id
                RETURNING id, job_type, source_release_id, source_storage_file_id,
                          target_release_id, target_quality, attempts, max_attempts,
                          work_dir, metadata, cancel_requested
                """
            ),
            {"worker_id": worker_id, "job_id": row},
        )
        return dict(result.mappings().one())


async def heartbeat(session, *, job_id, worker_id: str, progress: float | None = None):
    fields = "heartbeat_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP"
    params = {"job_id": job_id, "worker_id": worker_id}
    if progress is not None:
        fields += ", progress_percent = :progress"
        params["progress"] = max(0, min(float(progress), 100))
    await session.execute(
        text(
            f"UPDATE media_jobs SET {fields} WHERE id = :job_id AND locked_by = :worker_id AND status = 'RUNNING'"
        ),
        params,
    )


async def append_event(session, *, job_id, event_type: str, message: str | None = None, data: dict | None = None):
    await session.execute(
        text(
            """
            INSERT INTO media_job_events (id, job_id, event_type, message, data, created_at)
            VALUES (gen_random_uuid(), :job_id, :event_type, :message, CAST(:data AS jsonb), CURRENT_TIMESTAMP)
            """
        ),
        {
            "job_id": job_id,
            "event_type": event_type,
            "message": message,
            "data": json.dumps(data or {}, ensure_ascii=False),
        },
    )


async def mark_succeeded(session, *, job_id, worker_id: str, output_storage_file_id=None, output_path: str | None = None, metadata: dict | None = None):
    await session.execute(
        text(
            """
            UPDATE media_jobs
            SET status = 'SUCCEEDED',
                finished_at = CURRENT_TIMESTAMP,
                heartbeat_at = NULL,
                locked_at = NULL,
                locked_by = NULL,
                progress_percent = 100,
                output_storage_file_id = COALESCE(:output_storage_file_id, output_storage_file_id),
                output_path = COALESCE(:output_path, output_path),
                metadata = COALESCE(metadata, '{}'::jsonb) || CAST(:metadata AS jsonb),
                updated_at = CURRENT_TIMESTAMP
            WHERE id = :job_id AND locked_by = :worker_id AND status = 'RUNNING'
            """
        ),
        {
            "job_id": job_id,
            "worker_id": worker_id,
            "output_storage_file_id": output_storage_file_id,
            "output_path": output_path,
            "metadata": json.dumps(metadata or {}, ensure_ascii=False),
        },
    )


async def mark_failure(session, *, job: dict, worker_id: str, error_code: str, error_message: str, retry_delay: int):
    retryable = int(job["attempts"]) < int(job["max_attempts"])
    status = "RETRY" if retryable else "FAILED"
    await session.execute(
        text(
            """
            UPDATE media_jobs
            SET status = :status,
                available_at = CURRENT_TIMESTAMP + make_interval(secs => :retry_delay),
                locked_by = NULL,
                locked_at = NULL,
                heartbeat_at = NULL,
                finished_at = CASE WHEN :status = 'FAILED' THEN CURRENT_TIMESTAMP ELSE finished_at END,
                error_code = :error_code,
                error_message = :error_message,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = :job_id AND locked_by = :worker_id AND status = 'RUNNING'
            """
        ),
        {
            "status": status,
            "retry_delay": int(retry_delay),
            "error_code": error_code[:64],
            "error_message": error_message[:4000],
            "job_id": job["id"],
            "worker_id": worker_id,
        },
    )


async def enqueue_transcode(
    session,
    *,
    source_release_id,
    source_storage_file_id,
    target_release_id,
    target_quality: str,
    priority: int = 50,
    max_attempts: int = 3,
):
    quality = str(target_quality).lower().replace("p", "")
    key = f"TRANSCODE:{source_release_id}:{source_storage_file_id}:{target_release_id}:{quality}"
    result = await session.execute(
        text(
            """
            INSERT INTO media_jobs
                (id, job_type, status, priority, attempts, max_attempts, available_at,
                 source_release_id, source_storage_file_id, target_release_id, target_quality,
                 idempotency_key, metadata, created_at, updated_at)
            VALUES
                (gen_random_uuid(), 'TRANSCODE', 'QUEUED', :priority, 0, :max_attempts, CURRENT_TIMESTAMP,
                 :source_release_id, :source_storage_file_id, :target_release_id, :target_quality,
                 :idempotency_key, '{}'::jsonb, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            ON CONFLICT (idempotency_key)
            DO UPDATE SET
                status = CASE
                    WHEN media_jobs.status IN ('FAILED', 'CANCELLED') THEN 'QUEUED'
                    ELSE media_jobs.status
                END,
                attempts = CASE
                    WHEN media_jobs.status IN ('FAILED', 'CANCELLED') THEN 0
                    ELSE media_jobs.attempts
                END,
                available_at = CASE
                    WHEN media_jobs.status IN ('FAILED', 'CANCELLED') THEN CURRENT_TIMESTAMP
                    ELSE media_jobs.available_at
                END,
                locked_by = CASE
                    WHEN media_jobs.status IN ('FAILED', 'CANCELLED') THEN NULL
                    ELSE media_jobs.locked_by
                END,
                locked_at = CASE
                    WHEN media_jobs.status IN ('FAILED', 'CANCELLED') THEN NULL
                    ELSE media_jobs.locked_at
                END,
                heartbeat_at = CASE
                    WHEN media_jobs.status IN ('FAILED', 'CANCELLED') THEN NULL
                    ELSE media_jobs.heartbeat_at
                END,
                finished_at = CASE
                    WHEN media_jobs.status IN ('FAILED', 'CANCELLED') THEN NULL
                    ELSE media_jobs.finished_at
                END,
                error_code = CASE
                    WHEN media_jobs.status IN ('FAILED', 'CANCELLED') THEN NULL
                    ELSE media_jobs.error_code
                END,
                error_message = CASE
                    WHEN media_jobs.status IN ('FAILED', 'CANCELLED') THEN NULL
                    ELSE media_jobs.error_message
                END,
                cancel_requested = FALSE,
                updated_at = CURRENT_TIMESTAMP
            RETURNING id, status
            """
        ),
        {
            "priority": int(priority),
            "max_attempts": max(1, min(int(max_attempts), 10)),
            "source_release_id": source_release_id,
            "source_storage_file_id": source_storage_file_id,
            "target_release_id": target_release_id,
            "target_quality": quality,
            "idempotency_key": key,
        },
    )
    return dict(result.mappings().one())
