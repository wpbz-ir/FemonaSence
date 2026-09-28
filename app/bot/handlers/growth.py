from __future__ import annotations

import logging
from html import escape
from urllib.parse import quote
from uuid import UUID

from aiogram import F, Router
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQuery,
    InlineQueryResultArticle,
    InputTextMessageContent,
)
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.core.config import settings
from app.runtime.db import session_scope
from app.services.catalog import clean_caption, search_titles, title_text
from app.services.growth import (
    ensure_referral_code,
    record_rating,
    referral_stats,
    similar_titles,
    top_downloads,
)
from app.services.user_account import ensure_user
from app.utils.telegram_ui import edit_or_send as _edit_or_send

router = Router(name="growth")

logger = logging.getLogger(__name__)


# ---------- 🎁 دعوت دوستان (رفرال) ----------

@router.callback_query(F.data == "menu:referral")
async def referral_menu(callback: CallbackQuery):
    await callback.answer()
    async with session_scope() as session:
        user = await ensure_user(session, callback.from_user)
        code_row = await ensure_referral_code(session, user_id=user.id)
        stats = await referral_stats(session, user_id=user.id)

    bot_username = settings.bot_username
    if bot_username:
        link = f"https://t.me/{bot_username}?start=ref_{code_row.code}"
        link_line = f"<code>{link}</code>"
        share_url = "https://t.me/share/url?url=" + quote(link, safe="") + "&text=" + quote("فیلم و سریال را از فمونا سنس دانلود کن! 🎬")
        share_row = [[InlineKeyboardButton(text="📤 اشتراک‌گذاری لینک دعوت", url=share_url)]]
    else:
        link_line = f"کد دعوت: <code>{code_row.code}</code>\n<i>(BOT_USERNAME در .env تنظیم نشده — لینک پس از تنظیم ساخته می‌شود)</i>"
        share_row = []

    await _edit_or_send(
        callback,
        "<b>🎁 دعوت دوستان</b>\n\n"
        "لینک اختصاصی شما را برای دوستانتان بفرستید:\n"
        f"{link_line}\n\n"
        "💎 به‌ازای هر دوستی که با لینک شما وارد ربات شود، "
        f"<b>{int(stats['reward_irr'] / 1000):,}</b> هزار ریال اعتبار کیف پول هدیه می‌گیرید.\n\n"
        f"👥 دعوت‌شده‌ها: <b>{stats['invited']}</b>\n"
        f"✅ پاداش‌های دریافتی: <b>{stats['rewarded']}</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            *share_row,
            [InlineKeyboardButton(text="🏠 منوی اصلی", callback_data="menu:home")],
        ]),
    )


# ---------- 🔥 پربازدیدها ----------

@router.callback_query(F.data == "menu:top")
async def top_menu(callback: CallbackQuery):
    await callback.answer()
    async with session_scope() as session:
        rows = await top_downloads(session, days=7, limit=20)
    if rows:
        text = "<b>🔥 پربازدیدترین این هفته</b>\n\nبر اساس دانلود واقعی کاربران:"
        buttons = []
        for row in rows:
            name = row.get("title_fa") or row.get("title_en") or row.get("original_title") or "عنوان"
            buttons.append([InlineKeyboardButton(
                text=f"🎬 {name[:40]} ({int(row['downloads'])} دانلود)"[:64],
                callback_data=f"cv:title:{row['title_id']}",
            )])
    else:
        text = "<b>🔥 پربازدیدترین</b>\n\nهنوز آمار دانلود این هفته ثبت نشده است."
        buttons = []
    buttons.append([InlineKeyboardButton(text="🏠 منوی اصلی", callback_data="menu:home")])
    await _edit_or_send(callback, text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


# ---------- 🎲 پیشنهاد مشابه ----------

@router.callback_query(F.data.regexp(r"^cv:suggest:.+$"))
async def suggest_callback(callback: CallbackQuery):
    try:
        title_id = UUID(callback.data.split(":", 2)[2])
    except (ValueError, IndexError):
        await callback.answer("شناسه عنوان نامعتبر است.", show_alert=True)
        return
    await callback.answer()
    async with session_scope() as session:
        items = await similar_titles(session, title_id=title_id, limit=10)
    if items:
        text = "<b>🎲 پیشنهاد مشابه</b>\n\nبر اساس ژانرهای مشترک با این عنوان:"
        buttons = []
        for item in items:
            name = item.get("title_fa") or item.get("title_en") or item.get("original_title") or "عنوان"
            rating = f" ⭐{item['imdb_rating']}" if item.get("imdb_rating") else ""
            buttons.append([InlineKeyboardButton(
                text=f"🎬 {name[:40]}{rating}"[:64],
                callback_data=f"cv:title:{item['id']}",
            )])
    else:
        text = "<b>🎲 پیشنهاد مشابه</b>\n\nفعلاً عنوان هم‌ژانر مشابهی ثبت نشده است."
        buttons = []
    buttons.append([InlineKeyboardButton(text="🔙 برگشت", callback_data=f"cv:title:{title_id}")])
    await _edit_or_send(callback, text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


# ---------- ⭐ امتیازدهی بعد از دانلود ----------

_RATE_RELEASE_SQL = """
    SELECT 1
    FROM releases r
    LEFT JOIN episodes e ON e.id = r.episode_id
    LEFT JOIN seasons s ON s.id = e.season_id
    LEFT JOIN series se ON se.id = s.series_id
    JOIN titles t ON t.id = COALESCE(r.title_id, se.title_id)
    WHERE r.id = :rid
      AND COALESCE(UPPER(t.status), '') IN ('PUBLISHED', 'ACTIVE', 'PUBLIC')
"""


@router.callback_query(F.data.regexp(r"^cv:rate:.+$"))
async def rate_callback(callback: CallbackQuery):
    parts = callback.data.split(":")
    if len(parts) != 4 or parts[1] != "rate":
        await callback.answer()
        return
    try:
        release_id = UUID(parts[2])
        value = max(0, min(1, int(parts[3])))
    except ValueError:
        await callback.answer()
        return

    # [5-INT-b / 5-G15-c F4] اولین callback.answer() بلافاصله و قبل از هر کار DB —
    # اسپینرِ کلاینت بدون توجه به نتیجه آزاد می‌شود (dead-air ممنوع).
    await callback.answer()

    async def _notify_failure(text_: str) -> None:
        """هشدار خطا: پاسِ دوم ممکن است تلگرام نپذیرد؛ پیامِ جایگزین می‌فرستیم."""
        try:
            await callback.answer(text_, show_alert=True)
        except Exception:
            if callback.message is not None:
                try:
                    await callback.message.answer(text_)
                except Exception:
                    pass

    try:
        async with session_scope() as session:
            # [5-INT-b / 5-G15-c F4] اعتبارسنجی وجود نسخه + عمومی‌بودن عنوان قبل از
            # record_rating — هر UUID معتبر دیگر بدون بررسی رکورد نمی‌سازد
            # (IntegrityError FK از قبل مهار شده و رأیِ محتوای نامرئی ثبت نمی‌شود).
            exists = (
                await session.execute(text(_RATE_RELEASE_SQL), {"rid": release_id})
            ).first()
            if exists is None:
                await _notify_failure("این نسخه برای امتیازدهی در دسترس نیست.")
                return
            user = await ensure_user(session, callback.from_user)
            await record_rating(session, user_id=user.id, title_id=None, release_id=release_id, value=value)
    except IntegrityError:
        # مسابقه/کلید خارجی — ثبت نشد؛ لاگ + پاس فارسی.
        logger.warning("record_rating failed (release_id=%s)", release_id, exc_info=True)
        await _notify_failure("ثبت نشد. لطفاً دوباره تلاش کنید.")
        return
    if callback.message:
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
    # پیام تشکر به‌صورت بهترین‌تلاش (پاس اول مصرف شده؛ در صورت رد شدن تلگرام نادیده گرفته می‌شود).
    try:
        await callback.answer(
            "✅ ممنون از نظر شما!" if value else "🙏 ثبت شد. کیفیت را بررسی می‌کنیم.",
            show_alert=True,
        )
    except Exception:
        pass


# ---------- 🔎 جستجوی سریع (Inline Mode) ----------

@router.inline_query()
async def inline_search(query: InlineQuery):
    term = (query.query or "").strip()
    if len(term) < 2:
        await query.answer([], cache_time=5, is_personal=True)
        return
    async with session_scope() as session:
        titles = await search_titles(session, term, limit=12)

    results = []
    for t in titles:
        # [P1-15] قرارداد مشترک با handlers/catalog.py::_safe: اول حذف تگ‌ها، بعد escape.
        # نسخه‌ی بدون escape فقط برای فیلدهای متنی سادهٔ نتیجهٔ اینلاین (title/description)
        # استفاده می‌شود؛ هر echo در متن پیام با parse_mode=HTML باید escape شود وگرنه
        # کاراکترهای < > & پارس mode را می‌شکنند (TelegramBadRequest) یا تزریق HTML می‌شوند.
        plain_name = clean_caption(title_text(t), 64).strip() or "عنوان"
        name = escape(plain_name, quote=False)
        meta = []
        if getattr(t, "release_year", None):
            meta.append(str(t.release_year))
        if getattr(t, "imdb_rating", None) is not None:
            meta.append(f"IMDb {t.imdb_rating}")
        description = " • ".join(meta) or "مشاهده در فمونا سنس"
        content = InputTextMessageContent(
            message_text=(
                f"<b>🎬 {name}</b>\n"
                "از ربات فمونا سنس — دانلود نسخه‌های مختلف فیلم و سریال"
            ),
            parse_mode="HTML",
        )
        thumbnail = getattr(t, "poster_url", None)
        thumbnail = thumbnail if isinstance(thumbnail, str) and thumbnail.startswith("https://") else None
        article = InlineQueryResultArticle(
            id=str(t.id),
            title=plain_name,
            description=description,
            thumbnail_url=thumbnail,
            input_message_content=content,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="🎬 صفحه عنوان و دانلود", callback_data=f"cv:title:{t.id}"),
            ]]),
        )
        results.append(article)
    try:
        await query.answer(results, cache_time=30, is_personal=True)
    except Exception:
        # [P1-15] بلع بی‌صدای خطای answer باعث dead-air می‌شود؛ قابل‌تشخیص لاگ می‌شود.
        logger.warning(
            "inline search answer failed (term=%r results=%d)",
            term,
            len(results),
            exc_info=True,
        )
