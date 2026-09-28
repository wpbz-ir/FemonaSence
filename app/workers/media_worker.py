from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import random
import shutil
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.database import async_engine_kwargs_from_url
from app.core.media_config import load_media_settings
from app.services.content_pipeline import sync_pipeline_run
from app.services.ffmpeg_engine import FFmpegError, probe, transcode
from app.services.heartbeats import heartbeat
from app.services.media_jobs import (
    append_event,
    claim_job,
    heartbeat as job_heartbeat,
    mark_failure,
    mark_succeeded,
    recover_stale_jobs,
)
from app.services.media_profiles import get_profile
from app.services.telegram_media import (
    TelegramMediaClient,
    TelegramMediaError,
    attach_storage_to_release,
    find_storage_by_sha256,
    register_storage_file,
)


logger = logging.getLogger("femona.media_worker")

# Retry backoff bounds for the per-job lease heartbeat loop (see
# _lease_heartbeat): retries stay fast enough to keep the lease alive while
# still avoiding a hot retry storm against a struggling database.
LEASE_HEARTBEAT_BACKOFF_BASE = 2.0
LEASE_HEARTBEAT_BACKOFF_MAX = 30.0

# Poll-loop crash guard bounds: when a poll iteration fails (DB blip,
# transient network error), the worker logs the failure and retries after a
# small jittered delay instead of letting the exception kill the process.
POLL_ERROR_BACKOFF_MIN = 2.0
POLL_ERROR_BACKOFF_MAX = 5.0

# [INT-c] Stale-lease recovery cadence: recover_stale_jobs() runs every N poll
# iterations (and once before the first claim) instead of ONCE per process
# lifetime. Previously the recovery block ran a single time and the loop body
# ended in an unconditional ``break``; if the RUNTIME maintenance loop was
# down/failing, lease-expired RUNNING jobs stayed RUNNING forever. A short
# session per recovery keeps the extra DB work negligible.
RECOVER_EVERY_N_POLLS = 20


def _db_url() -> str:
    # Kept as a named seam for tests; the Neon "-pooler" statement-cache guard
    # and pool_pre_ping/recycle live in async_engine_kwargs_from_url().
    return async_engine_kwargs_from_url(os.getenv("DATABASE_URL", ""))["url"]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(4 * 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


async def _cancel_if_requested(session, job_id) -> bool:
    row = (
        await session.execute(
            text("SELECT cancel_requested, status FROM media_jobs WHERE id = :id"),
            {"id": job_id},
        )
    ).mappings().first()
    return bool(row and row["cancel_requested"] and row["status"] == "RUNNING")


async def _finish_cancelled(session, job_id, worker_id: str):
    await session.execute(
        text(
            """
            UPDATE media_jobs
            SET status = 'CANCELLED',
                finished_at = CURRENT_TIMESTAMP,
                locked_by = NULL,
                locked_at = NULL,
                heartbeat_at = NULL,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = :id AND locked_by = :worker_id AND status = 'RUNNING'
            """
        ),
        {"id": job_id, "worker_id": worker_id},
    )
    await append_event(session, job_id=job_id, event_type="CANCELLED", message="Cancelled by admin.")


class MediaWorker:
    def __init__(self):
        load_dotenv()
        self.settings = load_media_settings()
        # [INT-c] Fail fast and loud on a missing bot token — mirrors the
        # notification worker (app/workers/notification_worker.py:
        # ``RuntimeError("BOT_TOKEN is not configured.")``). Previously the
        # worker started token-less and only died in a 401 storm at the first
        # Telegram call.
        token = os.getenv("BOT_TOKEN", "").strip()
        if not token:
            raise RuntimeError("BOT_TOKEN is not configured.")
        # [INT-c/M3] Engine built with the SAME Neon pooler guard as
        # app/db/session.py (statement_cache_size=0 on "-pooler" hosts) via the
        # shared helper in app/core/database.py.
        self.engine = create_async_engine(
            **async_engine_kwargs_from_url(os.getenv("DATABASE_URL", "")),
            pool_size=5,
            max_overflow=5,
        )
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        self.telegram = TelegramMediaClient(
            token=token,
            base_url=self.settings.bot_api_base_url,
        )
        self.semaphore = asyncio.Semaphore(self.settings.worker_concurrency)
        self.stop_event = asyncio.Event()

    async def run(self):
        self.settings.work_root.mkdir(parents=True, exist_ok=True)
        tasks: set[asyncio.Task] = set()
        poll_count = 0
        while not self.stop_event.is_set():
            try:
                # [INT-c] Periodic stale-lease recovery: once before the first
                # claim and then every RECOVER_EVERY_N_POLLS successful polls
                # (a failing iteration retries recovery on the next pass).
                # The heartbeat metadata keeps reporting the recovered count so
                # /instance + dashboard visibility is unchanged.
                if poll_count % RECOVER_EVERY_N_POLLS == 0:
                    async with self.sessions() as session:
                        recovered = await recover_stale_jobs(
                            session,
                            lease_seconds=self.settings.lease_seconds,
                        )
                        await heartbeat(
                            session,
                            service_name="media_worker",
                            metadata={
                                "worker_id": self.settings.worker_id,
                                "concurrency": self.settings.worker_concurrency,
                                "recovered_jobs": recovered,
                            },
                        )
                        await session.commit()
                    if recovered:
                        logger.info("media worker requeued %d stale job(s)", recovered)

                while len(tasks) < self.settings.worker_concurrency and not self.stop_event.is_set():
                    async with self.sessions() as session:
                        job = await claim_job(session, worker_id=self.settings.worker_id)
                        await session.commit()
                    if job is None:
                        break
                    task = asyncio.create_task(self._run_claimed(job))
                    tasks.add(task)
                    task.add_done_callback(tasks.discard)

                async with self.sessions() as session:
                    await heartbeat(
                        session,
                        service_name="media_worker",
                        metadata={
                            "worker_id": self.settings.worker_id,
                            "active_tasks": len(tasks),
                        },
                    )
                    await session.commit()
            except asyncio.CancelledError:
                raise
            except Exception:
                # Per-iteration crash guard: one DB blip / transient error in
                # claim or heartbeat must NOT kill the whole MEDIA process.
                # Already-claimed jobs keep running as tasks (each guarded in
                # _run_claimed); we just log, back off briefly and re-poll.
                logger.exception("media worker poll iteration failed; retrying")
                await asyncio.sleep(
                    random.uniform(POLL_ERROR_BACKOFF_MIN, POLL_ERROR_BACKOFF_MAX)
                )
                continue

            poll_count += 1
            await asyncio.sleep(self.settings.poll_seconds)

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await self.engine.dispose()

    async def _run_claimed(self, job: dict):
        async with self.semaphore:
            try:
                await self.process_job(job)
            except Exception as exc:
                logger.exception("job failed: %s", job["id"])
                async with self.sessions() as session:
                    await mark_failure(
                        session,
                        job=job,
                        worker_id=self.settings.worker_id,
                        error_code=getattr(exc, "code", "WORKER_ERROR"),
                        error_message=str(exc),
                        retry_delay=min(
                            3600,
                            30 * (2 ** max(0, int(job["attempts"]) - 1)),
                        ),
                    )
                    await append_event(
                        session,
                        job_id=job["id"],
                        event_type="FAILED",
                        message=str(exc),
                        data={"error_code": getattr(exc, "code", "WORKER_ERROR")},
                    )
                    await session.commit()
            finally:
                # پاک‌سازی دایرکتوری کار در هر مسیر (موفق یا ناموفق) تا فایل‌های
                # چندگیگابایتی روی دیسک باقی نمانند.
                if self.settings.cleanup_temp:
                    shutil.rmtree(self.settings.work_root / str(job["id"]), ignore_errors=True)

    async def _lease_heartbeat(self, job_id, stop: asyncio.Event):
        """Keep the RUNNING job's lease alive while the transcode runs.

        Self-healing: this loop must NEVER exit on a transient DB blip. If it
        died silently, the lease would expire while the transcode keeps going,
        recover_stale_jobs() would hand the job to another worker, and the
        same source would be transcoded twice. So every iteration catches
        Exception, logs, backs off briefly and CONTINUES; the loop only exits
        when the stop event is set or the task is cancelled (CancelledError
        is re-raised so shutdown/cleanup still works).
        """
        backoff = LEASE_HEARTBEAT_BACKOFF_BASE
        while not stop.is_set():
            try:
                await asyncio.sleep(self.settings.heartbeat_seconds)
                if stop.is_set():
                    break
                async with self.sessions() as session:
                    await job_heartbeat(
                        session,
                        job_id=job_id,
                        worker_id=self.settings.worker_id,
                    )
                    await session.commit()
                backoff = LEASE_HEARTBEAT_BACKOFF_BASE  # healthy beat: reset
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "lease heartbeat failed for job %s; retrying in %.1fs",
                    job_id,
                    backoff,
                )
                await asyncio.sleep(backoff)
                backoff = min(LEASE_HEARTBEAT_BACKOFF_MAX, backoff * 2)

    async def process_job(self, job: dict):
        job_id = job["id"]
        if job["job_type"] != "TRANSCODE":
            raise FFmpegError("UNSUPPORTED_JOB_TYPE", f"Unsupported media job type: {job['job_type']}")

        if job.get("cancel_requested"):
            async with self.sessions() as session:
                await _finish_cancelled(session, job_id, self.settings.worker_id)
                await session.commit()
            return

        work = self.settings.work_root / str(job_id)
        work.mkdir(parents=True, exist_ok=True)
        input_path = work / "source.bin"
        output_path = work / f"release-{job['target_quality']}p.mp4"

        async with self.sessions() as session:
            await session.execute(
                text(
                    """
                    UPDATE media_jobs
                    SET work_dir=:work_dir, input_path=:input_path, updated_at=CURRENT_TIMESTAMP
                    WHERE id=:id AND locked_by=:worker
                    """
                ),
                {
                    "work_dir": str(work),
                    "input_path": str(input_path),
                    "id": job_id,
                    "worker": self.settings.worker_id,
                },
            )
            await append_event(
                session,
                job_id=job_id,
                event_type="STARTED",
                message="Media worker started processing.",
            )
            await session.commit()

        async with self.sessions() as session:
            file_id = await session.scalar(
                text(
                    "SELECT file_id FROM storage_files WHERE id=:id AND status='READY'"
                ),
                {"id": job["source_storage_file_id"]},
            )
            if not file_id:
                raise TelegramMediaError(
                    "SOURCE_STORAGE_UNAVAILABLE",
                    "Source StorageFile is not ready.",
                )

        await self.telegram.download_file(
            file_id=file_id,
            destination=input_path,
        )
        if input_path.stat().st_size < 1024:
            raise TelegramMediaError(
                "SOURCE_FILE_TOO_SMALL",
                "Source file is unexpectedly small.",
            )

        async with self.sessions() as session:
            if await _cancel_if_requested(session, job_id):
                await _finish_cancelled(session, job_id, self.settings.worker_id)
                await session.commit()
                return
            await job_heartbeat(
                session,
                job_id=job_id,
                worker_id=self.settings.worker_id,
                progress=5,
            )
            await append_event(
                session,
                job_id=job_id,
                event_type="DOWNLOADED",
                message="Source downloaded.",
            )
            await session.commit()

        media = await probe(self.settings, input_path)
        source_height = int(media["video"]["height"])
        profile = get_profile(str(job["target_quality"]))

        async with self.sessions() as session:
            if await _cancel_if_requested(session, job_id):
                await _finish_cancelled(session, job_id, self.settings.worker_id)
                await session.commit()
                return
            await job_heartbeat(
                session,
                job_id=job_id,
                worker_id=self.settings.worker_id,
                progress=15,
            )
            await session.commit()

        lease_stop = asyncio.Event()
        lease_task = asyncio.create_task(self._lease_heartbeat(job_id, lease_stop))
        try:
            await transcode(
                self.settings,
                input_path,
                output_path,
                profile,
                source_height,
            )
        finally:
            lease_stop.set()
            lease_task.cancel()
            await asyncio.gather(lease_task, return_exceptions=True)

        output_probe = await probe(self.settings, output_path)
        output_hash = _sha256(output_path)

        async with self.sessions() as session:
            if await _cancel_if_requested(session, job_id):
                await _finish_cancelled(session, job_id, self.settings.worker_id)
                await session.commit()
                return
            await job_heartbeat(
                session,
                job_id=job_id,
                worker_id=self.settings.worker_id,
                progress=75,
            )
            await append_event(
                session,
                job_id=job_id,
                event_type="TRANSCODED",
                message="FFmpeg transcode complete.",
                data={
                    "source": media,
                    "output": output_probe,
                    "sha256": output_hash,
                },
            )
            await session.commit()

        async with self.sessions() as session:
            storage_id = await find_storage_by_sha256(
                session,
                sha256=output_hash,
            )

        if storage_id is None:
            if self.settings.storage_chat_id is None:
                raise TelegramMediaError(
                    "STORAGE_CHAT_MISSING",
                    "TELEGRAM_STORAGE_CHAT_ID is not configured.",
                )

            caption = f"🎬 فمونا سنس | {profile.code}p\\nJob: {job_id}"
            sent = await self.telegram.send_video(
                chat_id=self.settings.storage_chat_id,
                path=output_path,
                caption=caption,
                width=output_probe["video"]["width"],
                height=output_probe["video"]["height"],
                duration=int(output_probe["duration"]),
            )
            async with self.sessions() as session:
                storage_id = await register_storage_file(
                    session,
                    provider_code="TELEGRAM",
                    message=sent,
                    source_job_id=job_id,
                    sha256=output_hash,
                )
                await session.commit()

        async with self.sessions() as session:
            if job.get("target_release_id"):
                # Honor the advanced quality-matrix request: when the target
                # Release carries technical_metadata.advanced_settings (set by
                # build_quality_matrix's advanced flow), its target_container /
                # target_codec_video / target_codec_audio describe the intended
                # OUTPUT container/codec and must not be clobbered by the
                # hardcoded 'MP4' / output-probe values below. Fall back to the
                # probe-derived values (legacy behavior) when advanced_settings
                # is absent. NOTE: the ffmpeg mux itself stays profile-driven
                # (MP4/x264/AAC); a non-default target_container/codec is
                # recorded here as the requested output metadata.
                advanced_row = (
                    await session.execute(
                        text(
                            "SELECT technical_metadata -> 'advanced_settings' AS advanced_settings "
                            "FROM releases WHERE id = :release_id"
                        ),
                        {"release_id": job["target_release_id"]},
                    )
                ).mappings().first()
                raw_advanced = advanced_row["advanced_settings"] if advanced_row else None
                advanced = dict(raw_advanced) if isinstance(raw_advanced, dict) else {}
                out_container = str(advanced.get("target_container") or "").strip() or "MP4"
                out_codec_video = (
                    str(advanced.get("target_codec_video") or "").strip()
                    or (output_probe["video"].get("codec") or "h264")
                )
                out_codec_audio = str(advanced.get("target_codec_audio") or "").strip() or "aac"

                await attach_storage_to_release(
                    session,
                    release_id=job["target_release_id"],
                    storage_file_id=storage_id,
                )

                await session.execute(
                    text(
                        """
                        UPDATE releases
                        SET status='READY',
                            width=:width,
                            height=:height,
                            size_bytes=:size_bytes,
                            codec_video=:codec_video,
                            codec_audio=:codec_audio,
                            container=:container,
                            technical_metadata = COALESCE(technical_metadata, '{}'::jsonb) ||
                                CAST(:metadata AS jsonb)
                        WHERE id=:release_id
                        """
                    ),
                    {
                        "release_id": job["target_release_id"],
                        "width": output_probe["video"]["width"],
                        "height": output_probe["video"]["height"],
                        "size_bytes": output_path.stat().st_size,
                        "codec_video": out_codec_video,
                        "codec_audio": out_codec_audio,
                        "container": out_container,
                        "metadata": json.dumps(
                            {
                                "media_hash_sha256": output_hash,
                                "source_probe": media,
                                "output_probe": output_probe,
                                "storage_file_id": str(storage_id),
                            },
                            ensure_ascii=False,
                        ),
                    },
                )

            await mark_succeeded(
                session,
                job_id=job_id,
                worker_id=self.settings.worker_id,
                output_storage_file_id=storage_id,
                output_path=str(output_path),
                metadata={
                    "sha256": output_hash,
                    "source_probe": media,
                    "output_probe": output_probe,
                    "profile": profile.code,
                },
            )
            await append_event(
                session,
                job_id=job_id,
                event_type="PUBLISHED",
                message="Output stored and attached to target Release.",
                data={"storage_file_id": str(storage_id)},
            )

            item_id = await session.scalar(
                text(
                    "SELECT id FROM content_pipeline_items WHERE media_job_id=:job_id LIMIT 1"
                ),
                {"job_id": job_id},
            )
            if item_id:
                await session.execute(
                    text(
                        """
                        UPDATE content_pipeline_items
                        SET status='SUCCEEDED', finished_at=CURRENT_TIMESTAMP,
                            error_message=NULL, updated_at=CURRENT_TIMESTAMP
                        WHERE id=:id
                        """
                    ),
                    {"id": item_id},
                )
                run_id = await session.scalar(
                    text("SELECT run_id FROM content_pipeline_items WHERE id=:id"),
                    {"id": item_id},
                )
                if run_id:
                    await sync_pipeline_run(session, run_id)

            await session.commit()


async def main():
    worker = MediaWorker()
    try:
        await worker.run()
    finally:
        await worker.engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
