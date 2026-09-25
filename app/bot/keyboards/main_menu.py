from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


MAIN_MENU_ITEMS: tuple[tuple[str, str], ...] = (
    ("🎬 فیلم", "menu:movies"),
    ("📺 سریال", "menu:series"),
    ("🧿 انیمیشن", "menu:animation"),
    ("🔥 محبوب", "menu:popular"),
    ("🆕 تازه‌ها", "menu:new"),
    ("⭐ IMDb", "menu:imdb"),
    ("🎭 ژانرها", "menu:genres"),
    ("📅 سال", "menu:years"),
    ("🎬 مجموعه‌ها", "menu:collections"),
    ("👤 بازیگران", "menu:actors"),
    ("🔎 جستجو", "menu:search"),
    ("▶️ ادامه تماشا", "menu:continue"),
    ("❤️ علاقه‌مندی‌ها", "menu:favorites"),
    ("🕘 تاریخچه", "menu:history"),
    ("👤 حساب من", "menu:account"),
    ("💎 اشتراک", "menu:subscription"),
    ("💰 کیف پول", "menu:wallet"),
    ("📣 تبلیغات", "menu:ads"),
)


def _grid(items: list[tuple[str, str]], columns: int = 2) -> list[list[InlineKeyboardButton]]:
    rows: list[list[InlineKeyboardButton]] = []
    for i in range(0, len(items), columns):
        rows.append([InlineKeyboardButton(text=t, callback_data=c) for t, c in items[i:i+columns]])
    return rows


def main_menu_keyboard(*, is_admin: bool = False) -> InlineKeyboardMarkup:
    items = list(MAIN_MENU_ITEMS)
    rows = _grid(items, columns=2)
    if is_admin:
        rows.append([InlineKeyboardButton(text="🛠 مدیریت", callback_data="menu:admin")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def back_home_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="🏠 منوی اصلی", callback_data="menu:home")]]
    )
