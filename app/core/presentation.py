from __future__ import annotations

from html import escape

from app.services.catalog import clean_caption, title_text


def safe_user_text(value, limit: int = 200) -> str:
    return escape(clean_caption(str(value or ""), limit).strip(), quote=False)


def title_label(title) -> str:
    return safe_user_text(title_text(title), 140) or "عنوان بدون نام"


def bool_badge(value: bool, on: str, off: str) -> str:
    return on if value else off
