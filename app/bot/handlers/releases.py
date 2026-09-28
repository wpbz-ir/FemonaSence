from __future__ import annotations

import html
from datetime import datetime, timedelta, timezone
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import func, select

from app.core.config import settings
from app.runtime.db import session_scope
from app.services.access import can_access_release
from app.services.growth import record_download
from app.services.release_matrix import delivery_target, get_release_variant, list_release_variants, release_badges, release_label
from app.services.rbac import is_super_admin
from app.services.user_account import ensure_user
from app.utils.telegram_ui import edit_or_send as _edit_or_send
from app.db.models import DownloadHistory, Subscription, Title


router = Router(name="releases")


# [P1-14] سهمیه‌ی روزانه باید بر پایه‌ی «روز تقویمی ایران» شمرده شود، نه نیمه‌شبِ
# منطقه‌ی زمانی سرور/DB. ایران DST ندارد → Asia/Tehran همیشه UTC+03:30 ثابت است.
# اگر tzdb در دسترس نباشد (مثلاً ویندوز بدون بسته‌ی tzdata)، همان آفست ثابت را
# مستقیم می‌سازیم تا شمارش سهمیه به‌جای خطا، درست ادامه پیدا کند.
try:
    TEHRAN_TZ = ZoneInfo("Asia/Tehran")
except ZoneInfoNotFoundError:  # pragma: no cover - وابسته به محیط استقرار
    TEHRAN_TZ = timezone(timedelta(hours=3, minutes=30))


def _tehran_day_start_utc() -> datetime:
    """شروع روزِ امروز در تقویم تهران، به‌صورت datetime آگاه از UTC.

    خروجی برای مقایسه با download_history.created_at (timestamptz) استفاده می‌شود؛
    چون مرزِ روز به UTC تبدیل شده، مقایسه یک بازه‌ی ساده روی created_at می‌ماند و
    ایندکس ix_download_history_user_created (user_id, created_at) به‌کار می‌افتد.
    """
    now_tehran = datetime.now(TEHRAN_TZ)
    day_start_tehran = now_tehran.replace(hour=0, minute=0, second=0, microsecond=0)
    return day_start_tehran.astimezone(timezone.utc)


async def _tehran_daily_download_count(session, *, user_id) -> int:
    """تعداد دانلود موفق «امروز» از نگاه کاربر (روز تقویمی تهران).

    همان پرس‌وجوی growth.daily_download_count است؛ فقط مرزِ «امروز» به‌جای
    date_trunc روی منطقه‌ی زمانی DB با _tehran_day_start_utc() حساب می‌شود
    (یعنی دانلود ساعت ۰۰:۳۰ تهران = ۲۱:۰۰ UTCِ روزِ قبل، به روزِ تازه می‌رود).
    """
    return int(
        await session.scalar(
            select(func.count(DownloadHistory.id)).where(
                DownloadHistory.user_id == user_id,
                DownloadHistory.status == "SUCCESS",
                DownloadHistory.created_at >= _tehran_day_start_utc(),
            )
        )
        or 0
    )


def _back_title(title_id) -> list[InlineKeyboardButton]:
    return [InlineKeyboardButton(text="🔙 برگشت", callback_data=f"cv:title:{title_id}")]


def _honest_badges(row: dict) -> str:
    """نشان‌های نسخه با معنای درست، پس از حذف پخش وب.

    release_badges() نشان «پخش» را از stream_source می‌سازد (stream_url/preview_url/
    media_url در extra_data) — بازمانده‌ی پخش وبِ حذف‌شده؛ دیگر مسیر پخش جداگانه‌ای
    وجود ندارد و تنها راه دریافت، ارسال مستقیم فایل در تلگرام است. منطق دست‌نخورده
    می‌ماند اما برچسب صادقانه می‌شود: «آماده پخش در تلگرام».
    """
    badges = release_badges(row)
    return " · ".join("آماده پخش در تلگرام" if badge == "پخش" else badge for badge in badges)


def _release_button(row: dict) -> InlineKeyboardButton:
    badges = _honest_badges(row)
    suffix = f" · {badges}" if badges else ""
    return InlineKeyboardButton(text=f"⬇️ {release_label(row)}{suffix}"[:64], callback_data=f"cv:download:{row['id']}")


@router.callback_query(F.data.regexp(r"^cv:releases:.+$"))
async def release_list(callback: CallbackQuery):
    try:
        title_id = UUID(callback.data.split(":", 2)[2])
    except (ValueError, IndexError):
        await callback.answer("شناسه عنوان نامعتبر است.", show_alert=True)
        return
    await callback.answer()
    async with session_scope() as session:
        title = await session.get(Title, title_id)
        if title and str(getattr(title, "status", "")).upper() in {"PUBLISHED", "ACTIVE", "PUBLIC"}:
            rows = await list_release_variants(session, title_id=title_id)
        else:
            rows = []
    if not title:
        await _edit_or_send(callback, "عنوان پیدا نشد.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[_back_title(title_id)]))
        return
    if str(getattr(title, "status", "")).upper() not in {"PUBLISHED", "ACTIVE", "PUBLIC"}:
        await _edit_or_send(callback, "این عنوان در حال حاضر منتشر نشده است.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[_back_title(title_id)]))
        return
    buttons = [[_release_button(row)] for row in rows if delivery_target(row)]
    if not buttons:
        buttons = [[InlineKeyboardButton(text="⏳ هنوز نسخه قابل دانلود ثبت نشده است.", callback_data="cv:no-op")]]
    buttons.append(_back_title(title_id))
    # [P1-14] نام عنوان ادمین‌ساخت است و parse_mode پیش‌فرض bot برابر HTML است
    # (session.py)؛ نامی با <>& پیام را می‌شکند یا تزریق می‌شود — همیشه escape شود.
    title_name = html.escape(str(title.title_fa or title.title_en or title.original_title), quote=False)
    await _edit_or_send(
        callback,
        f"<b>⬇️ نسخه‌های «{title_name}»</b>\n\n"
        "نسخه موردنظر را انتخاب کنید؛ فایل مستقیماً از تلگرام برای شما ارسال می‌شود:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )


@router.callback_query(F.data == "cv:no-op")
async def no_op(callback: CallbackQuery):
    await callback.answer("هنوز نسخه آماده‌ای ثبت نشده است.")


@router.callback_query(F.data.regexp(r"^cv:download:.+$"))
async def download_release(callback: CallbackQuery):
    try:
        release_id = UUID(callback.data.split(":", 2)[2])
    except (ValueError, IndexError):
        await callback.answer("شناسه نسخه نامعتبر است.", show_alert=True)
        return
    await callback.answer()
    async with session_scope() as session:
        row = await get_release_variant(session, release_id=release_id)
        if not row:
            await callback.message.answer("نسخه پیدا نشد.")
            return
        user = await ensure_user(session, callback.from_user)
        allowed, reason = await can_access_release(session, user.id, release_id)
        if not allowed:
            await callback.message.answer(reason)
            return
        # 🛡 سهمیه دانلود روزانه برای کاربران بدون اشتراک معتبر (ادمین و اشتراکِ فعال = نامحدود)
        # [INT-c/M4] FREE_DAILY_DOWNLOAD_LIMIT=0 یعنی «صفر»، نه «نامحدود»:
        # شرط قبلی `if limit and limit > 0` با 0 کل بلوک سهمیه را رد می‌کرد و
        # کاربرِ بدون اشتراک عملاً دانلود رایگانِ بی‌نهایت داشت. حالا صادقانه
        # وارونه شده: ادمین ارشد و مشترکِ فعال از این گذر می‌کنند؛ غیرمشترک با
        # limit=0 همیشه رد می‌شود (used >= 0 همیشه برقرار است).
        limit = settings.free_daily_download_limit
        if not await is_super_admin(session, user.id):
            # [P1-14] status='ACTIVE' به‌تنهایی کافی نیست: رکورد اشتراکِ منقضی ممکن است
            # همچنان ACTIVE بماند (job انقضا با تأخیر/خطا). مثل access.py و account.py،
            # اشتراک فقط وقتی نامحدود می‌بخشد که expires_at هم نگذشته باشد.
            sub = await session.scalar(
                select(Subscription).where(
                    Subscription.user_id == user.id,
                    Subscription.status == "ACTIVE",
                    Subscription.expires_at >= datetime.now(timezone.utc),
                )
            )
            if not sub:
                # [P1-14] مرزِ «امروز» = روز تقویمی تهران (+03:30 ثابت)، نه نیمه‌شب DB.
                used = await _tehran_daily_download_count(session, user_id=user.id)
                if used >= limit:
                    await callback.message.answer(
                        f"⛔ سهمیه دانلود رایگان امروز شما ({limit} مورد) به پایان رسیده است.\n"
                        "💎 با تهیه اشتراک، دانلود نامحدود خواهید داشت.",
                        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                            [InlineKeyboardButton(text="💎 خرید اشتراک", callback_data="menu:subscription")],
                            [InlineKeyboardButton(text="🏠 منوی اصلی", callback_data="menu:home")],
                        ]),
                    )
                    return
        target = delivery_target(row)
    if not target:
        await callback.message.answer("فایل این نسخه هنوز به Telegram Storage متصل نیست.")
        return
    try:
        await callback.bot.copy_message(chat_id=callback.from_user.id, from_chat_id=target[0], message_id=target[1])
    except Exception:
        await callback.message.answer("ارسال فایل انجام نشد. وضعیت Storage را بررسی کنید.")
        return
    # ثبت آمار دانلود (پایه «پربازدیدها» و گزارش‌ها)
    async with session_scope() as session:
        await record_download(session, user_id=user.id, release_id=release_id)
    await callback.message.answer("نسخه انتخابی برای شما ارسال شد. ✅")
    # ⭐ امتیازدهی کیفیت
    await callback.message.answer(
        "نظرتان درباره کیفیت این نسخه چیست؟",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="👍 خوب بود", callback_data=f"cv:rate:{release_id}:1"),
            InlineKeyboardButton(text="👎 مشکل داشت", callback_data=f"cv:rate:{release_id}:0"),
        ]]),
    )
