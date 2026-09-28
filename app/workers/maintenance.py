from __future__ import annotations

import asyncio
import logging
import os

from sqlalchemy import text

from app.runtime.db import session_scope
from app.services.content_pipeline import sync_pipeline_run
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

# [INT-c] Batch size for retention DELETEs: huge single-statement deletes hold
# one transaction/lock set for minutes and bloat the WAL. Each batch deletes at
# most _RETENTION_BATCH rows; the loop repeats until a batch comes back short.
_RETENTION_BATCH = 10_000

# [INT-c] Coupon-reservation TTL sweep — MUST mirror app/services/coupons.py
# RESERVATION_TTL (30 minutes). coupons.py is session-bound and per-coupon
# (its _release_stale_reservations takes a coupon_id), so maintenance owns the
# periodic cross-coupon sweep as a single bounded UPDATE with the same TTL.
_COUPON_RESERVATION_TTL_MINUTES = 30

# [P1-19 / جداول مرده] watch_parties و media_access_tokens (و watch_progress)
# محصولات «وب‌پلک» حذف‌شده‌اند و از ORM هم خارج شده‌اند، اما ممکن است خود جدول‌ها
# در دیتابیس باقی باشند. الزام حفظ داده: هیچ جدولی DROP نمی‌شود؛ فقط دستورهای
# نگهداریِ این جدول‌ها ایزوله اجرا می‌شوند تا غیبت/خطای آن‌ها (UndefinedTable)
# کل سیکل نگهداری را از کار نیندازد و retention جدول‌های زنده همیشه اجرا شود.
DEAD_WEB_TABLES: tuple[str, ...] = (
    "public.watch_parties",
    "public.media_access_tokens",
    "public.watch_progress",
)

_dead_tables_reported = False
_dead_table_warned: set[str] = set()
_env_int_warned: set[str] = set()


def _env_int(name: str, default: int) -> int:
    """خواندن امنِ متغیر محیطی عددی: مقدار غایب/نامعتبر → پیش‌فرض، با هشدارِ فقط-یک‌بار.

    قبلاً تبدیل intِ محافظت‌نشده روی os.getenv در چند نقطه، یک مقدار نامعتبر را به
    Exception در هر سیکل تبدیل می‌کرد (و کل سیکل نگهداری را می‌کشت).
    """
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        if name not in _env_int_warned:
            _env_int_warned.add(name)
            logger.warning(
                "متغیر محیطی %s=%r عدد صحیح نیست؛ از پیش‌فرض %s استفاده می‌شود.",
                name,
                raw,
                default,
            )
        return default


async def _report_dead_tables_once() -> None:
    """یک‌بار در شروع (و تا اولین موفقیت): فهرست جداول مرده با to_regclass.

    جدول‌های غایب با یک INFO «به‌عنوان ردشده» فهرست می‌شوند تا وضعیت در لاگ
    دیده باشد و لازم نباشد هر ۱۵ ثانیه یک traceback کامل تولید شود.
    اگر DB هنوز بالا نیامده، بی‌صدا دفعه‌ی بعد تلاش می‌شود.
    """
    global _dead_tables_reported
    try:
        async with session_scope() as session:
            missing: list[str] = []
            present: list[str] = []
            for table in DEAD_WEB_TABLES:
                exists = await session.scalar(
                    text("SELECT to_regclass(:table)"), {"table": table}
                )
                (present if exists is not None else missing).append(table)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.debug("dead-table probe skipped (db not ready yet); will retry", exc_info=True)
        return
    _dead_tables_reported = True
    if missing:
        logger.info(
            "نگهداری جداول وبِ بازنشسته رد می‌شود (جدول موجود نیست؛ داده‌ها دست‌نخورده): %s",
            ", ".join(missing),
        )
    if present:
        logger.info(
            "جداول وبِ بازنشسته اما موجود (فقط انقضا/پاک‌سازی ردیف‌ها اجرا می‌شود؛ DROP ممنوع): %s",
            ", ".join(present),
        )


async def _run_dead_table_stmt(session, table: str, stmt, params: dict | None = None) -> None:
    """اجرای یک دستور نگهداری روی جدولِ مرده‌ی وب با «log-and-continue».

    خطا (مثلاً UndefinedTable) با SAVEPOINT جمع می‌شود تا تراکنشِ بیرونی سالم
    بماند — بدون savepoint، اولین خطا کل تراکنش PostgreSQL را abort می‌کرد و
    بقیه‌ی کارهای نگهداری (retention جدول‌های زنده) از دست می‌رفت. هشدارِ هر
    جدول فقط یک‌بار داده می‌شود تا لاگِ هر ۱۵ ثانیه پر نشود.
    """
    try:
        async with session.begin_nested():
            await session.execute(stmt, params or {})
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        if table not in _dead_table_warned:
            _dead_table_warned.add(table)
            logger.warning("نگهداری جدول %s رد شد: %s", table, str(exc)[:300])


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
    # جدول‌های مرده‌ی وب — هر کدام ایزوله (savepoint + log-and-continue)
    await _run_dead_table_stmt(
        session,
        "public.watch_parties",
        text(
            """
            UPDATE watch_parties
            SET status = 'EXPIRED', updated_at = CURRENT_TIMESTAMP
            WHERE status = 'ACTIVE'
              AND expires_at < CURRENT_TIMESTAMP
            """
        ),
    )
    await _run_dead_table_stmt(
        session,
        "public.media_access_tokens",
        text(
            """
            DELETE FROM media_access_tokens
            WHERE expires_at < CURRENT_TIMESTAMP - INTERVAL '1 hour'
               OR revoked_at < CURRENT_TIMESTAMP - INTERVAL '1 day'
            """
        ),
    )


async def _batched_stmt(session, sql: str, params: dict | None = None) -> int:
    """[INT-c] اجرای دسته‌ایِ دستورهای DELETE/UPDATE نگهداری (_RETENTION_BATCH در هر دسته).

    sql باید قالب ctid-محور باشد:
        <DELETE FROM t ...> | <UPDATE t SET ...>
        WHERE ctid IN (SELECT ctid FROM t WHERE <شرط نگهداری> LIMIT :batch)
    و تا وقتی هر دسته کامل برگردد تکرار می‌شود. رشته‌ها فقط ثابت‌های داخل همین
    ماژول‌اند (هیچ ورودی بیرونی در SQL نمی‌نشیند). خروجی: مجموع ردیف‌های تحت‌تأثیر.
    """
    total = 0
    while True:
        result = await session.execute(text(sql), {**(params or {}), "batch": _RETENTION_BATCH})
        deleted = int(result.rowcount or 0)
        total += deleted
        if deleted < _RETENTION_BATCH:
            return total


async def _cleanup_history(session):
    days = max(1, _env_int("AUDIT_RETENTION_DAYS", 180))
    event_days = max(1, _env_int("MEDIA_EVENT_RETENTION_DAYS", 90))
    # [INT-c] هر سه DELETE به‌صورت دسته‌ای اجرا می‌شوند تا تراکنشِ نگهداری روی
    # جداول حجیم (رویدادها/لاگ‌ها) قفلِ چنددقیقه‌ای نگیرد.
    await _batched_stmt(
        session,
        """
        DELETE FROM media_job_events
        WHERE ctid IN (
            SELECT ctid FROM media_job_events
            WHERE created_at < CURRENT_TIMESTAMP - make_interval(days => :days)
            LIMIT :batch
        )
        """,
        {"days": event_days},
    )
    await _batched_stmt(
        session,
        """
        DELETE FROM admin_action_logs
        WHERE ctid IN (
            SELECT ctid FROM admin_action_logs
            WHERE created_at < CURRENT_TIMESTAMP - make_interval(days => :days)
            LIMIT :batch
        )
        """,
        {"days": days},
    )
    await _batched_stmt(
        session,
        """
        DELETE FROM service_heartbeats
        WHERE ctid IN (
            SELECT ctid FROM service_heartbeats
            WHERE last_seen_at < CURRENT_TIMESTAMP - INTERVAL '14 days'
            LIMIT :batch
        )
        """,
    )


async def _sweep_stale_coupon_reservations(session) -> int:
    """[INT-c] جاروی دوره‌ای رزروهای منقضی کد تخفیف (TTL = 30 دقیقه).

    سمت coupons.py فقط فرصتی/پر-کوپن است ( reserve_coupon سطرهای همان کد را آزاد
    می‌کند)؛ اگر کوپن دیگر رزرو نشود، سطرهای RESERVED برای همیشه سقف max_uses /
    per_user_limit را اشغال می‌کردند. این تابع همان معنای TTL
    (coupons.py RESERVATION_TTL) را برای «همه» کوپن‌ها در سیکل نگهداری اجرا
    می‌کند — دسته‌ای، تا تراکنش بزرگ نشود. مقدار برگشتی: تعداد رزروهای آزادشده.
    """
    return await _batched_stmt(
        session,
        """
        UPDATE coupon_redemptions
        SET status = 'RELEASED'
        WHERE ctid IN (
            SELECT ctid FROM coupon_redemptions
            WHERE status = 'RESERVED'
              AND reserved_at < CURRENT_TIMESTAMP - make_interval(mins => :ttl_minutes)
            LIMIT :batch
        )
        """,
        {"ttl_minutes": _COUPON_RESERVATION_TTL_MINUTES},
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
    """حلقه‌ی نگهداری — دو فاز جداشده ([P1-19/G07-e B-3]):

    فاز ۱ (فقط دیتابیس): همه‌ی کارها داخل یک تراکنشِ کوتاه session_scope؛
    هیچ فراخوانی bot/شبکه/زیرپرونده‌ای داخل تراکنش نیست.
    فاز ۲ (بدون هیچ نشست بازی): گزارش هفتگی و بک‌آپ pg_dump — growth.py برای
    گزارش نشست خودش را می‌سازد/می‌بندد و dump در asyncio.to_thread اجرا می‌شود،
    پس فراخوانیِ آن‌ها بیرون از تراکنشِ فاز ۱ امن است.

    (پاک‌سازی Deliveryها حذف شد: فقط مصرف‌کننده‌ی جریان مرده‌ی وب بود — ردیف
    Delivery هرگز ساخته نمی‌شود و bot.delete_message داخل تراکنش هم بود.)
    """
    cleanup_counter = 0
    growth_counter = 0
    cleanup_every = max(1, int(600 / max(1, interval_seconds)))
    growth_every = max(1, int(900 / max(1, interval_seconds)))  # هر ~۱۵ دقیقه
    while True:
        run_growth_sends = False
        try:
            if not _dead_tables_reported:
                await _report_dead_tables_once()

            # ---------- فاز ۱: فقط دیتابیس (تراکنش کوتاه) ----------
            async with session_scope() as session:
                await backfill_due_reminders(session)
                await _expire_state(session)

                swept_coupons = await _sweep_stale_coupon_reservations(session)
                if swept_coupons:
                    logger.info("کوپن: %d رزرو منقضی (TTL) آزاد شد.", swept_coupons)

                recovered = await recover_stale_jobs(
                    session,
                    lease_seconds=max(60, _env_int("MEDIA_JOB_LEASE_SECONDS", 900)),
                )
                await _sync_active_pipelines(session)
                await heartbeat(
                    session,
                    service_name="bot_maintenance",
                    metadata={"recovered_jobs": recovered},
                )

                cleanup_counter += 1
                if cleanup_counter >= cleanup_every:
                    await _cleanup_history(session)
                    cleanup_counter = 0

                # وظایف رشدِ فقط-دیتابیسی: قالب‌های اعلان + زمان‌بندی وین‌بک
                growth_counter += 1
                if growth_counter >= growth_every:
                    growth_counter = 0
                    await ensure_notification_templates(session)
                    await schedule_winback_jobs(session)
                    run_growth_sends = True

            # تراکنش فاز ۱ اینجا کامیت/بسته شده است — هیچ نشست بازی وجود ندارد.

            # ---------- فاز ۲: تلگرام/شبکه/زیرپرونده (بدون نشست) ----------
            if run_growth_sends:
                try:
                    await send_weekly_admin_report(bot)
                    await auto_backup_if_due(bot)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("maintenance growth/backup send phase failed")

        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("maintenance loop failed")

        await asyncio.sleep(max(5, interval_seconds))
