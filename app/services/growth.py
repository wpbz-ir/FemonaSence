"""سرویس‌های رشد و عملیات دوره‌ای: رفرال، پربازدیدها، پیشنهاد مشابه،
امتیازدهی، گزارش هفتگی ادمین، وین‌بک اشتراک و بک‌آپ خودکار دیتابیس."""
from __future__ import annotations

import json
import logging
import os
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from sqlalchemy import func, select, text

from app.core.config import settings
from app.db.models import (
    AnalyticsEvent,
    DownloadHistory,
    NotificationJob,
    NotificationTemplate,
    Referral,
    ReferralCode,
    ReferralReward,
    User,
    Wallet,
    WalletLedgerEntry,
)
from app.runtime.db import session_scope

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
STATE_PATH = Path(os.getenv("CINEMAVAULT_RUNTIME_DIR", str(PROJECT_ROOT / "data" / "runtime"))) / "growth_state.json"

WINBACK_CODE = "SUBSCRIPTION_WINBACK"


# ---------- state file ----------

def _load_state() -> dict:
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------- دانلودها: ثبت، سهمیه، پربازدید ----------

async def record_download(session, *, user_id, release_id, title_id=None) -> None:
    """ثبت تاریخچه دانلود + رخداد تحلیلی (پایه‌ی «پربازدیدها» و سهمیه روزانه)."""
    if title_id is None:
        row = (
            await session.execute(
                text(
                    """
                    SELECT COALESCE(r.title_id, se.title_id) AS title_id
                    FROM releases r
                    LEFT JOIN episodes e ON e.id = r.episode_id
                    LEFT JOIN seasons s ON s.id = e.season_id
                    LEFT JOIN series se ON se.id = s.series_id
                    WHERE r.id = :rid
                    """
                ),
                {"rid": release_id},
            )
        ).first()
        title_id = row.title_id if row and row.title_id else None
    session.add(DownloadHistory(user_id=user_id, release_id=release_id, status="SUCCESS"))
    session.add(
        AnalyticsEvent(
            event_type="DOWNLOAD",
            user_id=user_id,
            title_id=title_id,
            release_id=release_id,
            payload={},
        )
    )
    await session.flush()


async def daily_download_count(session, *, user_id) -> int:
    return int(
        await session.scalar(
            select(func.count(DownloadHistory.id)).where(
                DownloadHistory.user_id == user_id,
                DownloadHistory.status == "SUCCESS",
                DownloadHistory.created_at >= func.date_trunc("day", func.now()),
            )
        )
        or 0
    )


async def top_downloads(session, *, days: int = 7, limit: int = 20) -> list[dict]:
    """پربازدیدترین عنوان‌ها بر اساس دانلود واقعی (پشتیبانی از فیلم و قسمت سریال)."""
    rows = (
        await session.execute(
            text(
                """
                SELECT t.id AS title_id, t.title_fa, t.title_en, t.original_title, t.poster_url,
                       COUNT(d.id) AS downloads
                FROM download_history d
                JOIN releases r ON r.id = d.release_id
                LEFT JOIN episodes e ON e.id = r.episode_id
                LEFT JOIN seasons s ON s.id = e.season_id
                LEFT JOIN series se ON se.id = s.series_id
                JOIN titles t ON t.id = COALESCE(r.title_id, se.title_id)
                WHERE d.status = 'SUCCESS'
                  AND d.created_at > CURRENT_TIMESTAMP - make_interval(days => :days)
                GROUP BY t.id, t.title_fa, t.title_en, t.original_title, t.poster_url
                ORDER BY downloads DESC
                LIMIT :lim
                """
            ),
            {"days": days, "lim": limit},
        )
    ).mappings().all()
    return [dict(row) for row in rows]


# ---------- پیشنهاد مشابه ----------

async def similar_titles(session, *, title_id, limit: int = 10) -> list[dict]:
    """عنوان‌های هم‌ژانر (حداقل یک ژانر مشترک)، مرتب با امتیاز IMDb."""
    rows = (
        await session.execute(
            text(
                """
                SELECT DISTINCT t.id, t.title_fa, t.title_en, t.original_title, t.poster_url, t.imdb_rating
                FROM title_genres tg1
                JOIN title_genres tg2 ON tg2.genre_id = tg1.genre_id AND tg2.title_id <> tg1.title_id
                JOIN titles t ON t.id = tg2.title_id
                WHERE tg1.title_id = :tid
                  AND t.status IN ('PUBLISHED', 'ACTIVE', 'PUBLIC')
                ORDER BY t.imdb_rating DESC NULLS LAST
                LIMIT :lim
                """
            ),
            {"tid": title_id, "lim": limit},
        )
    ).mappings().all()
    return [dict(row) for row in rows]


# ---------- امتیازدهی ----------

async def record_rating(session, *, user_id, title_id, release_id, value: int) -> None:
    """امتیاز کیفیت نسخه (۱=خوب، ۰=بد) در رخدادهای تحلیلی."""
    session.add(
        AnalyticsEvent(
            event_type="RATE_RELEASE",
            user_id=user_id,
            title_id=title_id,
            release_id=release_id,
            payload={"value": value},
        )
    )
    await session.flush()


# ---------- رفرال ----------

async def ensure_referral_code(session, *, user_id) -> ReferralCode:
    import secrets
    import uuid as _uuid

    row = await session.scalar(select(ReferralCode).where(ReferralCode.user_id == user_id))
    if row:
        return row
    for _ in range(10):
        candidate = secrets.token_hex(4).upper()  # ۸ کاراکتری یکتا
        exists = await session.scalar(select(ReferralCode).where(ReferralCode.code == candidate))
        if not exists:
            break
    else:
        candidate = _uuid.uuid4().hex[:8].upper()
    row = ReferralCode(user_id=user_id, code=candidate, active=True)
    session.add(row)
    await session.flush()
    return row


async def referral_stats(session, *, user_id) -> dict:
    invited = int(
        await session.scalar(select(func.count(Referral.id)).where(Referral.referrer_user_id == user_id))
        or 0
    )
    rewarded = int(
        await session.scalar(
            select(func.count(ReferralReward.id))
            .join(Referral, Referral.id == ReferralReward.referral_id)
            .where(Referral.referrer_user_id == user_id, ReferralReward.status == "GRANTED")
        )
        or 0
    )
    return {"invited": invited, "rewarded": rewarded, "reward_irr": settings.referral_reward_irr}


async def link_referral(session, *, referred_user, code: str) -> dict:
    """اتصال کاربر جدید به معرف + اعطای پاداش کیف پولی به معرف.

    خروجی: {"ok": bool, "reason": str|None, "referrer_tg_id": int|None, "amount_irr": Decimal|None}
    """
    code = (code or "").strip().upper()
    if not code:
        return {"ok": False, "reason": None, "referrer_tg_id": None, "amount_irr": None}

    ref_code = await session.scalar(select(ReferralCode).where(ReferralCode.code == code, ReferralCode.active.is_(True)))
    if ref_code is None:
        return {"ok": False, "reason": None, "referrer_tg_id": None, "amount_irr": None}
    if ref_code.user_id == referred_user.id:
        return {"ok": False, "reason": None, "referrer_tg_id": None, "amount_irr": None}
    # فقط کاربر تازه‌وارد (کمتر از ۲۴ ساعت) قابل اتصال است — جلوگیری از سوءاستفاده
    created_at = getattr(referred_user, "created_at", None)
    if created_at and (datetime.now(timezone.utc) - created_at) > timedelta(hours=24):
        return {"ok": False, "reason": None, "referrer_tg_id": None, "amount_irr": None}
    already = await session.scalar(select(Referral).where(Referral.referred_user_id == referred_user.id))
    if already:
        return {"ok": False, "reason": None, "referrer_tg_id": None, "amount_irr": None}

    referral = Referral(
        referrer_user_id=ref_code.user_id,
        referred_user_id=referred_user.id,
        referral_code_id=ref_code.id,
        status="QUALIFIED",
        qualified_at=datetime.now(timezone.utc),
    )
    session.add(referral)
    await session.flush()

    amount = Decimal(str(settings.referral_reward_irr))
    reward = ReferralReward(
        referral_id=referral.id,
        reward_type="WALLET_CREDIT",
        amount_irr=amount,
        status="GRANTED",
        granted_at=datetime.now(timezone.utc),
    )
    session.add(reward)

    # اعتبار کیف پول معرف + ثبت دفترکل
    wallet = await session.scalar(select(Wallet).where(Wallet.user_id == ref_code.user_id))
    if wallet is None:
        wallet = Wallet(user_id=ref_code.user_id, balance_irr=Decimal("0"))
        session.add(wallet)
        await session.flush()
    wallet.balance_irr = Decimal(wallet.balance_irr or 0) + amount
    session.add(
        WalletLedgerEntry(
            wallet_id=wallet.id,
            amount_irr=amount,
            balance_after_irr=Decimal(wallet.balance_irr),
            entry_type="REFERRAL_REWARD",
            external_reference=f"referral:{referral.id}",
            description="پاداش دعوت دوست",
        )
    )
    referrer_tg = await session.scalar(select(User.telegram_user_id).where(User.id == ref_code.user_id))
    return {"ok": True, "reason": None, "referrer_tg_id": referrer_tg, "amount_irr": amount}


# ---------- اعلان‌ها: قالب خودکار + وین‌بک ----------

_TEMPLATES = {
    "SUBSCRIPTION_EXPIRY_7D": "⏳ <b>{name}</b> گرامی،\n\nاشتراک شما <b>۷ روز دیگر</b> به پایان می‌رسد. برای قطع نشدن دسترسی، از بخش «💎 اشتراک» تمدید کنید.",
    "SUBSCRIPTION_EXPIRY_1D": "⚠️ <b>{name}</b> گرامی،\n\nاشتراک شما <b>فردا</b> به پایان می‌رسد! برای ادامه استفاده، همین حالا تمدید کنید.",
    WINBACK_CODE: "💌 <b>{name}</b> گرامی،\n\nدلتان برای فیلم‌ها تنگ شده؟ اشتراک شما به پایان رسیده و برای شما یک <b>پیشنهاد ویژه بازگشت</b> در نظر گرفته‌ایم.{coupon}\n\nبرای تمدید: «💎 اشتراک» در منوی اصلی.",
}


async def ensure_notification_templates(session) -> int:
    """قالب‌های اعلان را اگر نبودند می‌سازد (بذر خودکار)."""
    created = 0
    for code, body in _TEMPLATES.items():
        exists = await session.scalar(select(NotificationTemplate.id).where(NotificationTemplate.code == code))
        if exists:
            continue
        session.add(
            NotificationTemplate(
                code=code,
                title=code,
                body=body,
                active=True,
            )
        )
        created += 1
    if created:
        await session.flush()
    return created


async def schedule_winback_jobs(session) -> int:
    """برای اشتراک‌هایی که در ۴۸ ساعت گذشته منقضی شده‌اند، پیام بازگشت (وین‌بک) زمان‌بندی می‌کند."""
    rows = (
        await session.execute(
            text(
                """
                SELECT s.id, s.user_id
                FROM subscriptions s
                WHERE s.status = 'EXPIRED'
                  AND s.expires_at > CURRENT_TIMESTAMP - INTERVAL '48 hours'
                """
            )
        )
    ).all()
    created = 0
    for sub_id, user_id in rows:
        dedupe_key = f"subscription:{sub_id}:{WINBACK_CODE}"
        exists = await session.scalar(select(NotificationJob.id).where(NotificationJob.dedupe_key == dedupe_key))
        if exists:
            continue
        session.add(
            NotificationJob(
                user_id=user_id,
                subscription_id=sub_id,
                notification_type=WINBACK_CODE,
                dedupe_key=dedupe_key,
                run_at=datetime.now(timezone.utc),
                status="PENDING",
            )
        )
        created += 1
    return created


# ---------- گزارش هفتگی ادمین ----------

async def weekly_report_data(session) -> dict:
    async def scalar(q, params=None):
        return (await session.execute(text(q), params or {})).scalar()

    return {
        "users_total": await scalar("SELECT COUNT(*) FROM users"),
        "users_new": await scalar("SELECT COUNT(*) FROM users WHERE created_at > CURRENT_TIMESTAMP - INTERVAL '7 days'"),
        "downloads_week": await scalar("SELECT COUNT(*) FROM download_history WHERE created_at > CURRENT_TIMESTAMP - INTERVAL '7 days' AND status='SUCCESS'"),
        "revenue_week_toman": await scalar(
            """
            SELECT COALESCE(SUM(p.amount_irr),0)/10.0
            FROM payments p
            WHERE p.status = 'PAID' AND p.created_at > CURRENT_TIMESTAMP - INTERVAL '7 days'
            """
        ),
        "subs_expiring": await scalar("SELECT COUNT(*) FROM subscriptions WHERE status='ACTIVE' AND expires_at > CURRENT_TIMESTAMP AND expires_at < CURRENT_TIMESTAMP + INTERVAL '7 days'"),
        "ads_pending": await scalar("SELECT COUNT(*) FROM ad_requests WHERE status='PENDING'"),
        "top": (
            await session.execute(
                text(
                    """
                    SELECT COALESCE(t.title_fa, t.title_en, t.original_title, '—') AS name, COUNT(d.id) AS downloads
                    FROM download_history d
                    JOIN releases r ON r.id = d.release_id
                    LEFT JOIN episodes e ON e.id = r.episode_id
                    LEFT JOIN seasons s ON s.id = e.season_id
                    LEFT JOIN series se ON se.id = s.series_id
                    JOIN titles t ON t.id = COALESCE(r.title_id, se.title_id)
                    WHERE d.created_at > CURRENT_TIMESTAMP - INTERVAL '7 days' AND d.status='SUCCESS'
                    GROUP BY t.id, name ORDER BY downloads DESC LIMIT 5
                    """
                )
            )
        ).all(),
    }


def _fa(n) -> str:
    try:
        return f"{int(n):,}".replace(",", "،")
    except (TypeError, ValueError):
        return str(n)


async def send_weekly_admin_report(bot) -> bool:
    """گزارش هفتگی — فقط روزهای شنبه، یک‌بار در روز (قفل با فایل وضعیت)."""
    today = datetime.now(timezone.utc).date()
    if today.weekday() != 5:  # شنبه
        return False
    state = _load_state()
    if state.get("last_report") == today.isoformat():
        return False
    async with session_scope() as session:
        data = await weekly_report_data(session)
        admin_ids = set(
            (await session.execute(text("SELECT DISTINCT u.telegram_user_id FROM users u JOIN user_roles ur ON ur.user_id=u.id JOIN roles r ON r.id=ur.role_id WHERE r.name='SUPER_ADMIN'"))).scalars().all()
        )
        if settings.admin_user_id:
            admin_ids.add(settings.admin_user_id)
    top_lines = "\n".join(f"  {i}️⃣ {name} — {_fa(dl)} دانلود" for i, (name, dl) in enumerate(data["top"], start=1)) or "  —"
    report = (
        "📊 <b>گزارش هفتگی فمونا سنس</b>\n\n"
        f"👥 کاربران: {_fa(data['users_total'])} (+{_fa(data['users_new'])} این هفته)\n"
        f"⬇️ دانلودهای هفته: {_fa(data['downloads_week'])}\n"
        f"💰 درآمد هفته: {_fa(data['revenue_week_toman'])} تومان\n"
        f"⏳ اشتراک‌های رو به انقضا (۷ روز): {_fa(data['subs_expiring'])}\n"
        f"📣 درخواست‌های تبلیغ در انتظار: {_fa(data['ads_pending'])}\n\n"
        f"🔥 پربازدیدهای هفته:\n{top_lines}"
    )
    sent = False
    for admin_id in admin_ids:
        try:
            await bot.send_message(int(admin_id), report)
            sent = True
        except Exception:
            continue
    if sent:
        state["last_report"] = today.isoformat()
        _save_state(state)
    return sent


# ---------- بک‌آپ خودکار دیتابیس ----------

def _parse_db_url(url: str) -> tuple[str, str, str, str, str]:
    """postgresql+psycopg://user:pass@host:port/db → (user, password, host, port, db)"""
    rest = url.split("://", 1)[1]
    userpass, hostpart = rest.rsplit("@", 1)
    user, password = userpass.split(":", 1)
    hostport, db = hostpart.split("/", 1)
    host, _, port = hostport.partition(":")
    return user, password, host or "localhost", port or "5432", db


async def auto_backup_if_due(bot) -> bool:
    """بک‌آپ روزانه pg_dump و ارسال به کانال تلگرامی — نیازمند pg_dump روی سرور."""
    if not settings.auto_backup_enabled or not settings.auto_backup_chat_id:
        return False
    now = datetime.now(timezone.utc)
    state = _load_state()
    if state.get("last_backup") == now.date().isoformat():
        return False
    if now.hour < settings.auto_backup_hour:
        return False
    try:
        user, password, host, port, db = _parse_db_url(settings.database_url)
        with tempfile.TemporaryDirectory() as tmp:
            out = str(Path(tmp) / f"cinemavault_{now.strftime('%Y%m%d_%H%M')}.dump")
            env = dict(os.environ, PGPASSWORD=password)
            result = subprocess.run(
                ["pg_dump", "-h", host, "-p", port, "-U", user, "-Fc", "-f", out, db],
                env=env,
                capture_output=True,
                timeout=900,
            )
            if result.returncode != 0 or not Path(out).exists():
                logger.error("pg_dump failed: %s", result.stderr.decode(errors="replace")[-500:])
                return False
            from aiogram.types import FSInputFile

            await bot.send_document(
                int(settings.auto_backup_chat_id),
                FSInputFile(out),
                caption=f"💾 بک‌آپ خودکار دیتابیس — {now.strftime('%Y-%m-%d %H:%M')} UTC",
            )
        state["last_backup"] = now.date().isoformat()
        _save_state(state)
        return True
    except FileNotFoundError:
        logger.warning("pg_dump not found — auto backup skipped (it must be installed on the server)")
        return False
    except Exception:
        logger.exception("auto backup failed")
        return False
