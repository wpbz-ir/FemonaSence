"""سرویس‌های رشد و عملیات دوره‌ای: رفرال، پربازدیدها، پیشنهاد مشابه،
امتیازدهی، گزارش هفتگی ادمین، وین‌بک اشتراک و بک‌آپ خودکار دیتابیس."""
from __future__ import annotations

import asyncio
import html
import json
import logging
import os
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from urllib.parse import unquote, urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError

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

# [5-INT-b / 5-G14-e] مرزهای تقویمی (روزِ شنبه‌ی گزارش هفتگی، ساعت بک‌آپ، کلید
# dedupe «یک‌بار در روز») باید بر پایه‌ی روز/ساعتِ محلیِ تهران باشند نه UTC —
# ایران DST ندارد → Asia/Tehran همیشه UTC+03:30 ثابت است (همان الگوی
# releases.py::_tehran_day_start_utc). اگر tzdb نبود، آفست ثابت ساخته می‌شود.
try:
    TEHRAN_TZ = ZoneInfo("Asia/Tehran")
except ZoneInfoNotFoundError:  # pragma: no cover — محیط بدون tzdata (مثلاً ویندوز)
    TEHRAN_TZ = timezone(timedelta(hours=3, minutes=30))


def _tehran_now() -> datetime:
    """زمانِ فعلی در منطقه‌ی زمانی تهران (datetime آگاه از آفست)."""
    return datetime.now(TEHRAN_TZ)


# ---------- state file ----------

def _load_state() -> dict:
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}  # اولین اجرا — فایل هنوز ساخته نشده
    except (OSError, ValueError):
        logger.warning(
            "فایل وضعیت growth خوانده نشد (خراب یا ناخوانا)؛ از حالت خالی شروع می‌شود: %s",
            STATE_PATH,
        )
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


async def top_downloads(session, *, days: int = 7, limit: int = 20) -> list[dict]:
    """پربازدیدترین عنوان‌ها بر اساس دانلود واقعی (پشتیبانی از فیلم و قسمت سریال).

    [5-INT-b / 5-G15-c F1] فقط عنوان‌های عمومی — بدون فیلترِ status، عناوینِ
    پیش‌نویس از این لیست به همه‌ی کاربران نشت می‌کردند (مقادیر status دقیقاً مثل
    release_matrix.py / catalog.py).
    """
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
                  AND COALESCE(UPPER(t.status), '') IN ('PUBLISHED', 'ACTIVE', 'PUBLIC')
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
                  AND COALESCE(UPPER(t.status), '') IN ('PUBLISHED', 'ACTIVE', 'PUBLIC')
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
    """امتیاز کیفیت نسخه (۱=خوب، ۰=بد) در رخدادهای تحلیلی.

    [FIX-E] دفاع لایه‌دوم در سرویس: مقدار قبل از ثبت کلمپ می‌شود تا payload
    تحلیلی همیشه نرمال باشد — حتی اگر مسیرِ تازه‌ای بدون clamp صدا بزند.
    """
    value = max(0, min(1, int(value)))
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
    last_error: IntegrityError | None = None
    for attempt in range(11):
        # ۱۰ نامزد تصادفی ۸-کاراکتری + نامزد نهایی uuid — هر کدام در صورت برخورد
        # با savepoint بازگردانی و با نامزد بعدی ادامه می‌یابد.
        candidate = secrets.token_hex(4).upper() if attempt < 10 else _uuid.uuid4().hex[:8].upper()
        row = ReferralCode(user_id=user_id, code=candidate, active=True)
        session.add(row)
        try:
            # savepoint: رقابت هم‌زمان روی referral_codes.user_id (یکتا برای هر کاربر)
            # یا uq_referral_codes_code (کد هم‌زمان برداشته‌شده) — در حالت اول ردیف
            # برنده دوباره خوانده می‌شود، در حالت دوم نامزد بعدی امتحان می‌شود.
            async with session.begin_nested():
                await session.flush()
        except IntegrityError as exc:
            last_error = exc
            existing = await session.scalar(
                select(ReferralCode).where(ReferralCode.user_id == user_id)
            )
            if existing is not None:
                return existing
            continue
        return row
    if last_error is not None:
        raise last_error
    raise RuntimeError("referral code generation exhausted without an IntegrityError")


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
    # [FIX-E] قرارداد «کمتر از ۲۴ ساعت» یعنی مرزِ ۲۴:۰۰:۰۰ هم رد می‌شود (>=، نه >)
    created_at = getattr(referred_user, "created_at", None)
    if created_at and (datetime.now(timezone.utc) - created_at) >= timedelta(hours=24):
        return {"ok": False, "reason": None, "referrer_tg_id": None, "amount_irr": None}
    # [P1-11] قفل ردیف کاربرِ معرفی‌شده قبل از بررسی «قبلاً معرفی شده» — دو کلیک/
    # درخواست همزمان روی یک کاربر سری می‌شوند؛ تراکنش دوم پس از کامیت اولی، ردیف
    # Referral را در بررسی زیر می‌بیند و بدون اعتبارِ پاداشِ دوم خارج می‌شود (idempotent).
    locked_referred_id = await session.scalar(
        select(User.id).where(User.id == referred_user.id).with_for_update()
    )
    if locked_referred_id is None:
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
    # [P1-11] SELECT .. FOR UPDATE روی ردیف کیف پول قبل از اعتبار — دو پاداش همزمان
    # با معرفِ یکسان (کاربران متفاوت) دیگر lost-update نمی‌خورند؛ موجودی پس از قفل خوانده می‌شود.
    wallet = await session.scalar(
        select(Wallet).where(Wallet.user_id == ref_code.user_id).with_for_update()
    )
    if wallet is None:
        wallet = Wallet(user_id=ref_code.user_id, balance_irr=Decimal("0"), version=1)
        session.add(wallet)
        try:
            # savepoint: رقابت get-or-create روی wallets.user_id UNIQUE —
            # کیف‌پولِ ساخته‌شده در تراکنش هم‌زمان دوباره خوانده می‌شود.
            async with session.begin_nested():
                await session.flush()
        except IntegrityError:
            wallet = await session.scalar(
                select(Wallet).where(Wallet.user_id == ref_code.user_id).with_for_update()
            )
            if wallet is None:
                raise
    wallet.balance_irr = Decimal(wallet.balance_irr or 0) + amount
    wallet.version = int(wallet.version or 0) + 1
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
    """قالب‌های اعلان را اگر نبودند می‌سازد (بذر خودکار).

    [FIX-E] check-then-insert بین دو نمونه‌ی هم‌زمان (ربات/ورکر نگهداری) روی
    uq_notification_templates_code می‌ترکید و کل تراکنش نگهداری را abort می‌کرد؛
    INSERT ... ON CONFLICT DO NOTHING اتمی است و rowcount=1 فقط برای برنده.
    """
    created = 0
    for code, body in _TEMPLATES.items():
        result = await session.execute(
            pg_insert(NotificationTemplate)
            .values(
                code=code,
                title=code,
                body=body,
                active=True,
            )
            .on_conflict_do_nothing(constraint="uq_notification_templates_code")
        )
        created += int(result.rowcount or 0)
    return created


async def schedule_winback_jobs(session) -> int:
    """برای اشتراک‌هایی که در ۴۸ ساعت گذشته منقضی شده‌اند، پیام بازگشت (وین‌بک) زمان‌بندی می‌کند.

    [FIX-E] مثل ensure_notification_templates: check-then-insert روی
    uq_notification_jobs_dedupe → INSERT ... ON CONFLICT DO NOTHING (اتمیک).
    """
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
        result = await session.execute(
            pg_insert(NotificationJob)
            .values(
                user_id=user_id,
                subscription_id=sub_id,
                notification_type=WINBACK_CODE,
                dedupe_key=dedupe_key,
                run_at=datetime.now(timezone.utc),
                status="PENDING",
            )
            .on_conflict_do_nothing(constraint="uq_notification_jobs_dedupe")
        )
        created += int(result.rowcount or 0)
    return created


# ---------- گزارش هفتگی ادمین ----------

async def weekly_report_data(session) -> dict:
    async def scalar(q, params=None):
        return (await session.execute(text(q), params or {})).scalar()

    return {
        "users_total": await scalar("SELECT COUNT(*) FROM users"),
        "users_new": await scalar("SELECT COUNT(*) FROM users WHERE created_at > CURRENT_TIMESTAMP - INTERVAL '7 days'"),
        "downloads_week": await scalar("SELECT COUNT(*) FROM download_history WHERE created_at > CURRENT_TIMESTAMP - INTERVAL '7 days' AND status='SUCCESS'"),
        # [P1-19/درآمد] محاسبه به تفکیک واحد پول — بدون هیچ تقسیم بین-واحدی:
        # وین‌پی: amount_irr همیشه 0 ذخیره می‌شود و مبلغ واقعی تومانی در amount_toman است
        # (winapay_billing.py:236-238) → جمعِ amount_toman.
        "revenue_week_winapay_toman": await scalar(
            """
            SELECT COALESCE(SUM(p.amount_toman), 0)
            FROM payments p
            WHERE p.provider = 'WINAPAY' AND p.status = 'PAID'
              AND p.created_at > CURRENT_TIMESTAMP - INTERVAL '7 days'
            """
        ),
        # ستاره تلگرام: تعداد ستاره‌ها در amount_irr با currency='XTR' ذخیره می‌شود
        # (billing.py:132) → این عدد «تعداد ستاره» است، نه ریال؛ جداگانه گزارش می‌شود.
        "revenue_week_xtr_stars": await scalar(
            """
            SELECT COALESCE(SUM(p.amount_irr), 0)
            FROM payments p
            WHERE p.currency = 'XTR' AND p.status = 'PAID'
              AND p.created_at > CURRENT_TIMESTAMP - INTERVAL '7 days'
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
                      AND COALESCE(UPPER(t.status), '') IN ('PUBLISHED', 'ACTIVE', 'PUBLIC')
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
    """گزارش هفتگی — فقط روزهای شنبه، یک‌بار در روز (قفل با فایل وضعیت).

    [5-INT-b / 5-G14-e] «شنبه» و کلیدِ dedupe بر پایه‌ی تاریخِ محلیِ تهران‌اند؛
    نسخه‌ی UTC قبلی گزارش را ۳/۵ ساعت دیر می‌فرستاد (شنبه ۰۰:۰۰–۰۳:۲۹ تهران
    هنوز «جمعه»ی UTC بود) و کلید dedupe روزِ UTC بود.
    """
    today = _tehran_now().date()
    if today.weekday() != 5:  # شنبه (به تقویم تهران)
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
    # [P1-19/گزارش] html.escape(quote=False): نام عنوان‌ها ساختار HTML گزارش را خراب نکنند
    # (parse_mode=HTML پیش‌فرض make_bot است). بدون تزریق/شکستن parse mode.
    top_lines = (
        "\n".join(
            f"  {i}️⃣ {html.escape(str(name), quote=False)} — {_fa(dl)} دانلود"
            for i, (name, dl) in enumerate(data["top"], start=1)
        )
        or "  —"
    )
    report = (
        "📊 <b>گزارش هفتگی فمونا سنس</b>\n\n"
        f"👥 کاربران: {_fa(data['users_total'])} (+{_fa(data['users_new'])} این هفته)\n"
        f"⬇️ دانلودهای هفته: {_fa(data['downloads_week'])}\n"
        f"💰 درآمد هفته (ریالی/تومانی): {_fa(data['revenue_week_winapay_toman'])} تومان\n"
        f"⭐️ درآمد هفته (ستاره‌ای): {_fa(data['revenue_week_xtr_stars'])} ستاره\n"
        f"⏳ اشتراک‌های رو به انقضا (۷ روز): {_fa(data['subs_expiring'])}\n"
        f"📣 درخواست‌های تبلیغ در انتظار: {_fa(data['ads_pending'])}\n\n"
        f"🔥 پربازدیدهای هفته:\n{top_lines}"
    )
    sent = False
    for admin_id in admin_ids:
        try:
            await bot.send_message(int(admin_id), report)
            sent = True
        except Exception as exc:  # خطای ارسال به یک ادمین، ارسال به بقیه را نبند
            logger.warning("ارسال گزارش هفتگی به ادمین %s ناموفق بود: %s", admin_id, exc)
            continue
    if sent:
        state["last_report"] = today.isoformat()
        _save_state(state)
    return sent


# ---------- بک‌آپ خودکار دیتابیس ----------

def _parse_db_url(url: str) -> tuple[str, str, str, str, str]:
    """postgresql+psycopg://user:pass@host:port/db?sslmode=require → (user, password, host, port, db)

    با urllib.parse تجزیه می‌شود تا پسوردهای با نویسه‌های خاص (percent-encoded،
    شامل @ یا :) و پارامترهای اتصال مثل sslmode=... درست خوانده شوند؛
    کوئری‌استرینگ عمداً نادیده گرفته می‌شود (بخشی از نام دیتابیس نمی‌شود).
    """
    parts = urlsplit((url or "").strip())
    if not parts.scheme or not parts.hostname:
        raise ValueError("DATABASE_URL نامعتبر است (scheme/host قابل خواندن نیست).")
    db = unquote((parts.path or "").lstrip("/"))
    if not db:
        raise ValueError("DATABASE_URL نامعتبر است (نام دیتابیس خالی است).")
    try:
        port = str(parts.port or 5432)
    except ValueError:
        raise ValueError("DATABASE_URL نامعتبر است (پورت نامعتبر است).") from None
    user = unquote(parts.username or "")
    password = unquote(parts.password or "")
    host = parts.hostname or "localhost"
    return user, password, host, port, db


async def auto_backup_if_due(bot) -> bool:
    """بک‌آپ روزانه pg_dump و ارسال به کانال تلگرامی — نیازمند pg_dump روی سرور.

    [5-INT-b / 5-G14-e] ساعتِ بک‌آپ (AUTO_BACKUP_HOUR منظورِ اپراتور ~۳ بامداد
    محلی است) و کلیدِ dedupe روزانه بر پایه‌ی تهران‌اند، نه UTC.
    """
    if not settings.auto_backup_enabled or not settings.auto_backup_chat_id:
        return False
    now_tehran = _tehran_now()
    state = _load_state()
    if state.get("last_backup") == now_tehran.date().isoformat():
        return False
    if now_tehran.hour < settings.auto_backup_hour:
        return False
    now = datetime.now(timezone.utc)  # فقط برای نام فایل/کپشن (با برچسب UTC)
    try:
        user, password, host, port, db = _parse_db_url(settings.database_url)
        with tempfile.TemporaryDirectory() as tmp:
            out = str(Path(tmp) / f"cinemavault_{now.strftime('%Y%m%d_%H%M')}.dump")
            env = dict(os.environ, PGPASSWORD=password)
            # [P1-19/بک‌آپ] اجرای pg_dump در thread جدا (asyncio.to_thread) تا رویدادلوپ
            # تا ۹۰۰ ثانیه قفل نشود؛ PGPASSWORD در env و timeout=900 دقیقاً مثل قبل حفظ شده‌اند.
            result = await asyncio.to_thread(
                subprocess.run,
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
        state["last_backup"] = now_tehran.date().isoformat()
        _save_state(state)
        return True
    except FileNotFoundError:
        logger.warning("pg_dump not found — auto backup skipped (it must be installed on the server)")
        return False
    except Exception:
        logger.exception("auto backup failed")
        return False
