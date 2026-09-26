from __future__ import annotations

import asyncio
import logging
import os

from sqlalchemy import text

from app.runtime.db import session_scope
from app.services.content_pipeline import sync_pipeline_run
from app.services.delivery import expire_deliveries
from app.services.growth import (
    auto_backup_if_due,
    ensure_notification_templates,
    schedule_winback_jobs,
    send_weekly_admin_report,
)
from app.services.heartbeats import heartbeat
from app.services.media_jobs import recover_stale_jobs
from app.services.subscription_reminders import backfill_due_reminders

logger = logging.getLogger(__name__)


async def _expire_state(session):
    await session.execute(
        text(
            """
            UPDATE subscriptions
            SET status = 'EXPIRED'
            WHERE status = 'ACTIVE'
              AND expires_at < CURRENT_TIMESTAMP
            """
        )
    )
    await session.execute(
        text(
            """
            UPDATE watch_parties
            SET status = 'EXPIRED', updated_at = CURRENT_TIMESTAMP
            WHERE status = 'ACTIVE'
              AND expires_at < CURRENT_TIMESTAMP
            """
        )
    )
    await session.execute(
        text(
            """
            DELETE FROM media_access_tokens
            WHERE expires_at < CURRENT_TIMESTAMP - INTERVAL '1 hour'
               OR revoked_at < CURRENT_TIMESTAMP - INTERVAL '1 day'
            """
        )
    )


async def _cleanup_history(session):
    days = max(1, int(os.getenv("AUDIT_RETENTION_DAYS", "180")))
    event_days = max(1, int(os.getenv("MEDIA_EVENT_RETENTION_DAYS", "90")))
    await session.execute(
        text(
            """
            DELETE FROM media_job_events
            WHERE created_at < CURRENT_TIMESTAMP - make_interval(days => :days)
            """
        ),
        {"days": event_days},
    )
    await session.execute(
        text(
            """
            DELETE FROM admin_action_logs
            WHERE created_at < CURRENT_TIMESTAMP - make_interval(days => :days)
            """
        ),
        {"days": days},
    )
    await session.execute(
        text(
            """
            DELETE FROM service_heartbeats
            WHERE last_seen_at < CURRENT_TIMESTAMP - INTERVAL '14 days'
            """
        )
    )


async def _sync_active_pipelines(session):
    rows = (
        await session.execute(
            text(
                """
                SELECT id
                FROM content_pipeline_runs
                WHERE status IN ('QUEUED', 'RUNNING')
                ORDER BY updated_at
                LIMIT 50
                """
            )
        )
    ).scalars().all()
    for run_id in rows:
        await sync_pipeline_run(session, run_id)


async def maintenance_loop(bot, *, interval_seconds: int = 15):
    cleanup_counter = 0
    growth_counter = 0
    growth_every = max(1, int(900 / max(1, interval_seconds)))  # هر ~۱۵ دقیقه
    while True:
        try:
            async with session_scope() as session:
                await expire_deliveries(session, bot)
                await backfill_due_reminders(session)
                await _expire_state(session)

                recovered = await recover_stale_jobs(
                    session,
                    lease_seconds=max(60, int(os.getenv("MEDIA_JOB_LEASE_SECONDS", "900"))),
                )
                await _sync_active_pipelines(session)
                await heartbeat(
                    session,
                    service_name="bot_maintenance",
                    metadata={"recovered_jobs": recovered},
                )

                cleanup_counter += 1
                if cleanup_counter >= max(1, int(600 / max(1, interval_seconds))):
                    await _cleanup_history(session)
                    cleanup_counter = 0

                # وظایف رشد: قالب‌های اعلان، وین‌بک، گزارش هفتگی، بک‌آپ خودکار
                growth_counter += 1
                if growth_counter >= growth_every:
                    growth_counter = 0
                    await ensure_notification_templates(session)
                    await schedule_winback_jobs(session)
                    await send_weekly_admin_report(bot)
                    await auto_backup_if_due(bot)

        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("maintenance loop failed")

        await asyncio.sleep(max(5, interval_seconds))
