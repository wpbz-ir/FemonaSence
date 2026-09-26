from __future__ import annotations

from html import escape
from uuid import UUID

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import select

from app.bot.keyboards.main_menu import back_home_keyboard
from app.bot.keyboards.paginated import paginated_grid
from app.core.brand import BRAND_NAME_FA
from app.core.config import settings
from app.db.models import Title
from app.runtime.db import session_scope
from app.services.catalog import (
    clean_caption,
    list_collections,
    list_genres,
    list_people,
    list_titles,
    list_years,
    newest_titles,
    popular_titles,
    search_titles,
    title_text,
    top_imdb_titles,
)
from app.services.favorites import is_favorite, toggle_favorite
from app.services.user_account import ensure_user
from app.utils.telegram_ui import edit_or_send


router = Router(name="catalog")

_edit_or_send = edit_or_send


class SearchState(StatesGroup):
    query = State()


class CommentState(StatesGroup):
    body = State()


def _safe(value, limit: int = 160) -> str:
    return escape(clean_caption(str(value or ""), limit).strip(), quote=False)


def _title_name(title: Title) -> str:
    return _safe(title_text(title), 120) or "عنوان بدون نام"


def _is_public_title(title: Title) -> bool:
    return str(getattr(title, "status", "")).upper() in {"PUBLISHED", "ACTIVE", "PUBLIC"}


def _home_row():
    return [InlineKeyboardButton(text="🏠 منوی اصلی", callback_data="menu:home")]


def _title_rows(items):
    return [
        [InlineKeyboardButton(text=f"🎬 {_title_name(item)[:58]}", callback_data=f"cv:title:{item.id}")]
        for item in items
    ]


def _extra(title: Title) -> dict:
    data = getattr(title, "extra_data", None)
    return data if isinstance(data, dict) else {}


def _trailer_url(title: Title) -> str | None:
    value = getattr(title, "trailer_url", None) or _extra(title).get("trailer_url")
    value = str(value or "").strip()
    return value if value.startswith("https://") else None


def _title_keyboard(
    title_id,
    *,
    fav: bool,
    reactions: dict,
    trailer: str | None,
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(text="⬇️ دانلود نسخه‌ها", callback_data=f"cv:releases:{title_id}"),
            InlineKeyboardButton(text="📤 اشتراک‌گذاری", callback_data=f"cv:share:{title_id}"),
        ],
        [
            InlineKeyboardButton(text=f"❤️ {reactions['likes']}", callback_data=f"cv:like:{title_id}"),
            InlineKeyboardButton(text=f"👎 {reactions['dislikes']}", callback_data=f"cv:dislike:{title_id}"),
        ],
        [
            InlineKeyboardButton(text="💬 نظرها", callback_data=f"cv:comments:{title_id}"),
            InlineKeyboardButton(text="✍️ نظر بده", callback_data=f"cv:comment:{title_id}"),
        ],
        [
            InlineKeyboardButton(
                text="💔 حذف از لیست من" if fav else "❤️ ذخیره در لیست من",
                callback_data=f"cv:fav:{title_id}",
            ),
            InlineKeyboardButton(text="ℹ️ اطلاعات بیشتر", callback_data=f"cv:info:{title_id}"),
        ],
    ]
    rows.append([InlineKeyboardButton(text="🎲 پیشنهاد مشابه", callback_data=f"cv:suggest:{title_id}")])
    if trailer:
        rows.append([InlineKeyboardButton(text="🎞️ تریلر", url=trailer)])
    rows.append(_home_row())
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _show_titles(callback: CallbackQuery, heading: str, items):
    if not callback.message:
        return
    rows = _title_rows(items)
    rows.append(_home_row())
    body = f"<b>{escape(heading, quote=False)}</b>\n\n"
    body += "موردی پیدا نشد." if not items else "عنوان موردنظر را انتخاب کنید:"
    await _edit_or_send(callback, body, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


def _page_from(data: str) -> int:
    try:
        return max(0, int(data.rsplit(":", 1)[1]))
    except (ValueError, IndexError):
        return 0




@router.callback_query(F.data == "noop")
async def noop_callback(callback: CallbackQuery):
    await callback.answer()


@router.callback_query(F.data.in_({"menu:movies", "menu:series", "menu:animation"}))
async def category_callback(callback: CallbackQuery):
    await callback.answer()
    category = callback.data.split(":", 1)[1]
    async with session_scope() as session:
        items = await list_titles(session, category, limit=20)
    await _show_titles(
        callback,
        {"movies": "🎬 فیلم‌ها", "series": "📺 سریال‌ها", "animation": "🧿 انیمیشن"}[category],
        items,
    )


@router.callback_query(F.data == "menu:new")
async def newest_callback(callback: CallbackQuery):
    await callback.answer()
    async with session_scope() as session:
        items = await newest_titles(session, limit=20)
    await _show_titles(callback, "🆕 تازه‌ها", items)


@router.callback_query(F.data == "menu:popular")
async def popular_callback(callback: CallbackQuery):
    await callback.answer()
    async with session_scope() as session:
        items = await popular_titles(session, limit=20)
    await _show_titles(callback, "🔥 محبوب‌ترین‌ها", items)


@router.callback_query(F.data == "menu:imdb")
async def imdb_callback(callback: CallbackQuery):
    await callback.answer()
    async with session_scope() as session:
        items = await top_imdb_titles(session, limit=20)
    await _show_titles(callback, "⭐ برترین‌های IMDb", items)


@router.callback_query(F.data.regexp(r"^menu:years(:p:\d+)?$"))
async def years_callback(callback: CallbackQuery):
    await callback.answer()
    page = _page_from(callback.data) if ":p:" in callback.data else 0
    async with session_scope() as session:
        years = await list_years(session, limit=120)
    kb = paginated_grid(
        years,
        label=str,
        callback=lambda y: f"cv:year:{y}",
        page=page,
        page_callback=lambda n: f"menu:years:p:{n}",
        page_size=15,
        prefix="📅 ",
        footer=[_home_row()],
    )
    await _edit_or_send(callback,
        "<b>📅 سال تولید</b>\n\nسال موردنظر را انتخاب کنید:",
        reply_markup=kb,
    )


@router.callback_query(F.data.regexp(r"^cv:year:\d{4}$"))
async def year_callback(callback: CallbackQuery):
    await callback.answer()
    year = int(callback.data.split(":")[-1])
    async with session_scope() as session:
        items = await list_titles(session, "movies", year=year, limit=20)
        items.extend(await list_titles(session, "series", year=year, limit=20))
        items.extend(await list_titles(session, "animation", year=year, limit=20))
    await _show_titles(callback, f"📅 تولید {year}", items[:50])


@router.callback_query(F.data.regexp(r"^menu:genres(:p:\d+)?$"))
async def genres_callback(callback: CallbackQuery):
    await callback.answer()
    page = _page_from(callback.data) if ":p:" in callback.data else 0
    async with session_scope() as session:
        genres = await list_genres(session, limit=200)
    kb = paginated_grid(
        genres,
        label=lambda g: g.name_fa,
        callback=lambda g: f"cv:genre:{g.id}",
        page=page,
        page_callback=lambda n: f"menu:genres:p:{n}",
        prefix="🎭 ",
        footer=[_home_row()],
    )
    text = (
        "<b>🎭 ژانرها</b>\n\nژانر را انتخاب کنید:"
        if genres else
        "<b>🎭 ژانرها</b>\n\nهنوز ژانری ثبت نشده است."
    )
    await _edit_or_send(callback, text, reply_markup=kb)


async def _association_titles(session, *, kind: str, entity_id: UUID):
    tables = __import__("app.db.models", fromlist=["Base"]).Base.metadata.tables
    column = f"{kind}_id"
    assoc = next((t for t in tables.values() if "title_id" in t.c and column in t.c), None)
    if assoc is None:
        return None
    stmt = (
        select(Title)
        .join(assoc, assoc.c.title_id == Title.id)
        .where(
            assoc.c[column] == entity_id,
            Title.status.in_(("PUBLISHED", "ACTIVE", "PUBLIC")),
        )
        .order_by(Title.created_at.desc())
        .limit(50)
    )
    return list((await session.scalars(stmt)).all())


@router.callback_query(F.data.regexp(r"^cv:genre:.+$"))
async def genre_callback(callback: CallbackQuery):
    try:
        genre_id = UUID(callback.data.split(":", 2)[2])
    except ValueError:
        await callback.answer("شناسه ژانر نامعتبر است.", show_alert=True)
        return
    await callback.answer()
    async with session_scope() as session:
        items = await _association_titles(session, kind="genre", entity_id=genre_id)
    if items is None:
        await _edit_or_send(callback, "ارتباط ژانر هنوز پیکربندی نشده است.", reply_markup=back_home_keyboard())
        return
    await _show_titles(callback, "🎭 آثار این ژانر", items)


@router.callback_query(F.data.regexp(r"^menu:actors(:p:\d+)?$"))
async def actors_callback(callback: CallbackQuery):
    await callback.answer()
    page = _page_from(callback.data) if ":p:" in callback.data else 0
    async with session_scope() as session:
        people = await list_people(session, limit=200)
    kb = paginated_grid(
        people,
        label=lambda p: p.name_fa or p.name_en,
        callback=lambda p: f"cv:person:{p.id}",
        page=page,
        page_callback=lambda n: f"menu:actors:p:{n}",
        prefix="👤 ",
        footer=[_home_row()],
    )
    text = (
        "<b>👤 بازیگران</b>\n\nشخص را انتخاب کنید:"
        if people else
        "<b>👤 بازیگران</b>\n\nهنوز شخصی ثبت نشده است."
    )
    await _edit_or_send(callback, text, reply_markup=kb)


@router.callback_query(F.data.regexp(r"^cv:person:.+$"))
async def person_callback(callback: CallbackQuery):
    try:
        person_id = UUID(callback.data.split(":", 2)[2])
    except ValueError:
        await callback.answer("شناسه شخص نامعتبر است.", show_alert=True)
        return
    await callback.answer()
    async with session_scope() as session:
        items = await _association_titles(session, kind="person", entity_id=person_id)
    if items is None:
        await _edit_or_send(callback, "ارتباط شخص هنوز پیکربندی نشده است.", reply_markup=back_home_keyboard())
        return
    await _show_titles(callback, "🎭 آثار این شخص", items)


async def _collection_rows(collection_id: UUID, page: int):
    async with session_scope() as session:
        children = await list_collections(session, collection_id, limit=200)
        tables = __import__("app.db.models", fromlist=["Base"]).Base.metadata.tables
        assoc = next((t for t in tables.values() if "title_id" in t.c and "collection_id" in t.c), None)
        items = []
        if assoc is not None:
            stmt = (
                select(Title)
                .join(assoc, assoc.c.title_id == Title.id)
                .where(
                    assoc.c.collection_id == collection_id,
                    Title.status.in_(("PUBLISHED", "ACTIVE", "PUBLIC")),
                )
                .order_by(Title.created_at.desc())
                .limit(50)
            )
            items = list((await session.scalars(stmt)).all())
    child_kb = paginated_grid(
        children,
        label=lambda c: c.name_fa,
        callback=lambda c: f"cv:collection:{c.id}",
        page=page,
        page_callback=lambda n: f"cv:collectionpage:{collection_id}:{n}",
        prefix="🎬 ",
    )
    return child_kb.inline_keyboard, items


@router.callback_query(F.data.regexp(r"^menu:collections(:p:\d+)?$"))
async def collections_callback(callback: CallbackQuery):
    await callback.answer()
    page = _page_from(callback.data) if ":p:" in callback.data else 0
    async with session_scope() as session:
        collections = await list_collections(session, limit=200)
    kb = paginated_grid(
        collections,
        label=lambda c: c.name_fa,
        callback=lambda c: f"cv:collection:{c.id}",
        page=page,
        page_callback=lambda n: f"menu:collections:p:{n}",
        prefix="🎬 ",
        footer=[_home_row()],
    )
    text = (
        "<b>🎬 مجموعه‌ها</b>\n\nمجموعه موردنظر را انتخاب کنید:"
        if collections else
        "<b>🎬 مجموعه‌ها</b>\n\nهنوز مجموعه‌ای ثبت نشده است."
    )
    await _edit_or_send(callback, text, reply_markup=kb)


@router.callback_query(F.data.regexp(r"^cv:collection:.+$"))
async def collection_callback(callback: CallbackQuery):
    try:
        collection_id = UUID(callback.data.split(":", 2)[2])
    except ValueError:
        await callback.answer("شناسه مجموعه نامعتبر است.", show_alert=True)
        return
    await callback.answer()
    rows, items = await _collection_rows(collection_id, 0)
    rows.extend(_title_rows(items))
    rows.append(_home_row())
    await _edit_or_send(callback,
        "<b>🎬 مجموعه</b>\n\nعنوان یا زیرمجموعه را انتخاب کنید:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


@router.callback_query(F.data.regexp(r"^cv:collectionpage:[^:]+:\d+$"))
async def collection_page_callback(callback: CallbackQuery):
    try:
        _, _, raw_collection, raw_page = callback.data.split(":", 3)
        collection_id = UUID(raw_collection)
        page = max(0, int(raw_page))
    except (ValueError, IndexError):
        await callback.answer("صفحه مجموعه نامعتبر است.", show_alert=True)
        return
    await callback.answer()
    rows, items = await _collection_rows(collection_id, page)
    rows.extend(_title_rows(items))
    rows.append(_home_row())
    await _edit_or_send(callback,
        "<b>🎬 مجموعه</b>\n\nعنوان یا زیرمجموعه را انتخاب کنید:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


@router.callback_query(F.data == "menu:search")
async def search_start(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.set_state(SearchState.query)
    await _edit_or_send(callback,
        "<b>🔎 جستجوی حرفه‌ای</b>\n\nنام فارسی، انگلیسی یا عنوان اصلی را ارسال کنید.",
        reply_markup=back_home_keyboard(),
    )


@router.message(SearchState.query, F.text)
async def search_message(message: Message, state: FSMContext):
    async with session_scope() as session:
        items = await search_titles(session, message.text, limit=20)
    await state.clear()
    await message.answer(
        "<b>🔎 نتایج جستجو</b>\n\n" +
        ("موردی پیدا نشد." if not items else "عنوان موردنظر را انتخاب کنید:"),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=_title_rows(items) + [_home_row()]),
    )


def _title_caption(title: Title, reactions: dict) -> str:
    description = _safe(getattr(title, "synopsis", ""), 900) or "توضیحات این عنوان هنوز ثبت نشده است."
    meta = []
    if getattr(title, "release_year", None):
        meta.append(f"📅 {_safe(title.release_year, 8)}")
    if getattr(title, "imdb_rating", None) is not None:
        meta.append(f"⭐ {_safe(title.imdb_rating, 8)} IMDb")
    meta_line = " | ".join(meta)
    return (
        f"<b>🎬 {_title_name(title)}</b>\n"
        + (f"{meta_line}\n\n" if meta_line else "\n")
        + f"{description}\n\n"
        + f"❤️ {reactions['likes']}    👎 {reactions['dislikes']}\n"
        + "\nگزینه موردنظر را انتخاب کنید:"
    )


async def _render_title(callback: CallbackQuery, title_id: UUID):
    from app.services.experience import reaction_summary

    async with session_scope() as session:
        user = await ensure_user(session, callback.from_user)
        if getattr(user, "status", "ACTIVE") != "ACTIVE":
            await _edit_or_send(callback, "حساب کاربری شما فعال نیست.", reply_markup=back_home_keyboard())
            return
        title = await session.get(Title, title_id)
        if title is None or not _is_public_title(title):
            await _edit_or_send(callback, "این عنوان در حال حاضر منتشر نشده است.", reply_markup=back_home_keyboard())
            return
        fav = await is_favorite(session, user_id=user.id, title_id=title.id)
        reactions = await reaction_summary(session, user_id=user.id, title_id=title_id)

    trailer = _trailer_url(title)
    caption = _title_caption(title, reactions)
    keyboard = _title_keyboard(
        title.id,
        fav=fav,
        reactions=reactions,
        trailer=trailer,
    )

    if callback.message and getattr(title, "poster_url", None):
        try:
            await callback.message.delete()
            await callback.bot.send_photo(
                chat_id=callback.from_user.id,
                photo=title.poster_url,
                caption=caption,
                reply_markup=keyboard,
            )
            return
        except Exception:
            pass

    await _edit_or_send(callback, caption, reply_markup=keyboard)


@router.callback_query(F.data.regexp(r"^cv:title:.+$"))
async def title_callback(callback: CallbackQuery):
    try:
        title_id = UUID(callback.data.split(":", 2)[2])
    except ValueError:
        await callback.answer("شناسه عنوان نامعتبر است.", show_alert=True)
        return
    await callback.answer()
    await _render_title(callback, title_id)


@router.callback_query(F.data.regexp(r"^cv:(like|dislike):.+$"))
async def reaction_callback(callback: CallbackQuery):
    from app.services.experience import reaction_summary, toggle_reaction

    try:
        title_id = UUID(callback.data.split(":", 2)[2])
    except ValueError:
        await callback.answer("شناسه عنوان نامعتبر است.", show_alert=True)
        return
    await callback.answer()
    value = 1 if callback.data.startswith("cv:like:") else -1
    async with session_scope() as session:
        user = await ensure_user(session, callback.from_user)
        if getattr(user, "status", "ACTIVE") != "ACTIVE":
            await _edit_or_send(callback, "حساب کاربری شما فعال نیست.", reply_markup=back_home_keyboard())
            return
        title = await session.get(Title, title_id)
        if not title or not _is_public_title(title):
            await _edit_or_send(callback, "این عنوان در حال حاضر منتشر نشده است.", reply_markup=back_home_keyboard())
            return
        summary = await toggle_reaction(session, user_id=user.id, title_id=title_id, value=value)
        fav = await is_favorite(session, user_id=user.id, title_id=title_id)
    if not title or not callback.message:
        return
    keyboard = _title_keyboard(
        title.id,
        fav=fav,
        reactions=summary,
        trailer=_trailer_url(title),
    )
    try:
        await callback.message.edit_reply_markup(reply_markup=keyboard)
    except Exception:
        await _edit_or_send(callback, _title_caption(title, summary), reply_markup=keyboard)


@router.callback_query(F.data.regexp(r"^cv:info:.+$"))
async def info_callback(callback: CallbackQuery):
    try:
        title_id = UUID(callback.data.split(":", 2)[2])
    except ValueError:
        await callback.answer("شناسه عنوان نامعتبر است.", show_alert=True)
        return
    await callback.answer()
    async with session_scope() as session:
        title = await session.get(Title, title_id)
    if not title or not _is_public_title(title):
        await _edit_or_send(callback, "این عنوان در حال حاضر منتشر نشده است.", reply_markup=back_home_keyboard())
        return
    year = getattr(title, "release_year", None) or "ثبت نشده"
    rating = getattr(title, "imdb_rating", None)
    runtime = _extra(title).get("duration") or getattr(title, "runtime_minutes", None) or "ثبت نشده"
    age = _extra(title).get("age_rating") or getattr(title, "age_rating", None)
    director = _extra(title).get("director") or "ثبت نشده"
    countries = _extra(title).get("countries") or getattr(title, "country", None) or "ثبت نشده"
    lines = [
        f"<b>ℹ️ اطلاعات «{_title_name(title)}»</b>",
        "",
        f"📅 سال: {_safe(year, 16)}",
        f"⭐ IMDb: {_safe(rating, 16) if rating is not None else 'ثبت نشده'}",
        f"⏱ مدت: {_safe(runtime, 24)}",
        f"🎬 کارگردان: {_safe(director, 80)}",
        f"🌍 کشور: {_safe(countries, 120)}",
    ]
    if age:
        lines.append(f"🔞 رده سنی: {_safe(age, 32)}")
    await _edit_or_send(callback,
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(
                text="🔙 برگشت به فیلم",
                callback_data=f"cv:title:{title_id}",
            )]]
        ),
    )


@router.callback_query(F.data.regexp(r"^cv:share:.+$"))
async def share_callback(callback: CallbackQuery):
    await callback.answer()
    try:
        title_id = UUID(callback.data.split(":", 2)[2])
    except (ValueError, IndexError):
        await callback.message.answer("شناسه عنوان نامعتبر است.")
        return
    if not settings.bot_username:
        await callback.message.answer("BOT_USERNAME در تنظیمات ربات ثبت نشده است؛ اشتراک‌گذاری لینک آماده نیست.")
        return
    url = f"https://t.me/{settings.bot_username}?start=title_{title_id}"
    async with session_scope() as session:
        title = await session.get(Title, title_id)
    if title is None or not _is_public_title(title):
        await callback.message.answer("این عنوان در حال حاضر برای اشتراک‌گذاری عمومی در دسترس نیست.")
        return
    shared_name = _title_name(title)
    await callback.message.answer(
        f"📤 لینک «{shared_name}» برای شما آماده شد.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="📤 ارسال لینک عنوان", url=url)]]
        ),
    )


@router.callback_query(F.data.regexp(r"^cv:comments:.+$"))
async def comments_callback(callback: CallbackQuery):
    await callback.answer()
    from app.services.experience import list_comments
    try:
        title_id = UUID(callback.data.split(":", 2)[2])
    except (ValueError, IndexError):
        await callback.message.answer("شناسه عنوان نامعتبر است.")
        return
    async with session_scope() as session:
        title = await session.get(Title, title_id)
        if not title or not _is_public_title(title):
            await _edit_or_send(callback, "این عنوان در حال حاضر منتشر نشده است.", reply_markup=back_home_keyboard())
            return
        comments = await list_comments(session, title_id=title_id)
    lines = ["<b>💬 نظرهای کاربران</b>", ""]
    if not comments:
        lines.append("هنوز نظری ثبت نشده است.")
    else:
        for item in comments:
            name = (
                " ".join(x for x in (item.get("first_name"), item.get("last_name")) if x).strip()
                or (f"@{item['username']}" if item.get("username") else "کاربر")
            )
            lines.append(f"<b>{_safe(name, 80)}</b>\n{_safe(item.get('body'), 350)}\n")
    await _edit_or_send(callback,
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="✍️ نظر بده", callback_data=f"cv:comment:{title_id}")],
                [InlineKeyboardButton(text="🔙 برگشت", callback_data=f"cv:title:{title_id}")],
            ]
        ),
    )


@router.callback_query(F.data.regexp(r"^cv:comment:.+$"))
async def comment_start(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    title_id = callback.data.split(":", 2)[2]
    try:
        parsed_title_id = UUID(title_id)
    except ValueError:
        await _edit_or_send(callback, "شناسه عنوان نامعتبر است.", reply_markup=back_home_keyboard())
        return
    async with session_scope() as session:
        title = await session.get(Title, parsed_title_id)
    if not title or not _is_public_title(title):
        await _edit_or_send(callback, "این عنوان در حال حاضر منتشر نشده است.", reply_markup=back_home_keyboard())
        return
    await state.set_state(CommentState.body)
    await state.update_data(title_id=title_id)
    await _edit_or_send(callback,
        "<b>✍️ ثبت نظر</b>\n\nنظر خود را در یک پیام ارسال کنید (۲ تا ۱۰۰۰ نویسه).",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(
                text="🔙 لغو",
                callback_data=f"cv:commentcancel:{title_id}",
            )]]
        ),
    )


@router.callback_query(F.data.regexp(r"^cv:commentcancel:.+$"))
async def comment_cancel(callback: CallbackQuery, state: FSMContext):
    try:
        title_id = UUID(callback.data.split(":", 2)[2])
    except (ValueError, IndexError):
        await callback.answer("شناسه عنوان نامعتبر است.", show_alert=True)
        return
    await callback.answer()
    await state.clear()
    await _render_title(callback, title_id)


@router.message(CommentState.body, F.text)
async def comment_message(message: Message, state: FSMContext):
    data = await state.get_data()
    try:
        title_id = UUID(data["title_id"])
    except (KeyError, ValueError):
        await state.clear()
        await message.answer("نشست ثبت نظر منقضی شده است.", reply_markup=back_home_keyboard())
        return
    from app.services.experience import add_comment
    try:
        async with session_scope() as session:
            user = await ensure_user(session, message.from_user)
            if getattr(user, "status", "ACTIVE") != "ACTIVE":
                await message.answer("حساب کاربری شما فعال نیست.", reply_markup=back_home_keyboard())
                return
            title = await session.get(Title, title_id)
            if not title or not _is_public_title(title):
                await message.answer("این عنوان در حال حاضر منتشر نشده است.", reply_markup=back_home_keyboard())
                await state.clear()
                return
            await add_comment(session, user_id=user.id, title_id=title_id, body=message.text)
    except ValueError:
        await message.answer("متن نظر باید بین ۲ تا ۱۰۰۰ نویسه باشد.")
        return
    await state.clear()
    await message.answer("نظر شما ثبت شد. 💬", reply_markup=back_home_keyboard())


@router.callback_query(F.data.regexp(r"^cv:fav:.+$"))
async def favorite_callback(callback: CallbackQuery):
    try:
        title_id = UUID(callback.data.split(":", 2)[2])
    except (ValueError, IndexError):
        await callback.answer("شناسه عنوان نامعتبر است.", show_alert=True)
        return
    await callback.answer()
    async with session_scope() as session:
        user = await ensure_user(session, callback.from_user)
        if getattr(user, "status", "ACTIVE") != "ACTIVE":
            await _edit_or_send(callback, "حساب کاربری شما فعال نیست.", reply_markup=back_home_keyboard())
            return
        title = await session.get(Title, title_id)
        if not title or not _is_public_title(title):
            await _edit_or_send(callback, "این عنوان در حال حاضر منتشر نشده است.", reply_markup=back_home_keyboard())
            return
        await toggle_favorite(session, user_id=user.id, title_id=title_id)
    await _render_title(callback, title_id)
