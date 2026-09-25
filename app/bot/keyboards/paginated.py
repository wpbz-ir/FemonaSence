from __future__ import annotations

from typing import Callable, Iterable, Sequence

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

PAGE_SIZE = 12


def _columns_for(labels: Sequence[str]) -> int:
    """Pick 2 or 3 columns from the longest label so buttons stay readable on mobile."""
    longest = max((len(x) for x in labels), default=0)
    if longest <= 9:
        return 3
    return 2


def _short(text: str, limit: int) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def paginated_grid(
    items: Iterable,
    *,
    label: Callable[[object], str],
    callback: Callable[[object], str],
    page: int,
    page_callback: Callable[[int], str],
    page_size: int = PAGE_SIZE,
    prefix: str = "",
    footer: Sequence[Sequence[InlineKeyboardButton]] = (),
) -> InlineKeyboardMarkup:
    """Compact grid keyboard with prev/next paging; shared by genres, actors, collections and years."""
    items = list(items)
    pages = max(1, (len(items) + page_size - 1) // page_size)
    page = min(max(page, 0), pages - 1)
    chunk = items[page * page_size : (page + 1) * page_size]

    labels = [_short(f"{prefix}{label(x)}", 22) for x in chunk]
    cols = _columns_for(labels)
    buttons = [
        InlineKeyboardButton(text=text, callback_data=callback(item))
        for text, item in zip(labels, chunk)
    ]
    rows = [buttons[i : i + cols] for i in range(0, len(buttons), cols)]

    if pages > 1:
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton(text="◀️ قبلی", callback_data=page_callback(page - 1)))
        nav.append(InlineKeyboardButton(text=f"{page + 1}/{pages}", callback_data="noop"))
        if page < pages - 1:
            nav.append(InlineKeyboardButton(text="بعدی ▶️", callback_data=page_callback(page + 1)))
        rows.append(nav)

    rows.extend([list(r) for r in footer])
    return InlineKeyboardMarkup(inline_keyboard=rows)
