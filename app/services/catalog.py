from __future__ import annotations

import re
from datetime import datetime
from typing import Iterable

from sqlalchemy import Select, and_, desc, func, inspect as sa_inspect, or_, select

from app.core.text import clean_text
from app.db.models import (
    Collection,
    DownloadHistory,
    Genre,
    Person,
    Release,
    Title,
    User,
)


TYPE_VALUES = {
    "movies": {"MOVIE", "FILM", "MOVIES", "MOVIE_TITLE"},
    "series": {"SERIES", "TV_SERIES", "SERIAL"},
    "animation": {"ANIMATION", "ANIME", "CARTOON"},
}

TYPE_LABELS = {
    "movies": "🎬 فیلم‌ها",
    "series": "📺 سریال‌ها",
    "animation": "🧿 انیمیشن",
}


def _norm(value) -> str:
    return str(getattr(value, "value", value)).strip().upper() if value is not None else ""


def _mapped(model) -> set[str]:
    return {c.key for c in sa_inspect(model).columns}


def _title_type_col():
    cols = _mapped(Title)
    for name in ("kind", "type", "content_type", "media_type"):
        if name in cols:
            return getattr(Title, name)
    return None


def _title_status_col():
    cols = _mapped(Title)
    for name in ("status", "publish_status", "state"):
        if name in cols:
            return getattr(Title, name)
    return None


def _published(stmt: Select) -> Select:
    status_col = _title_status_col()
    if status_col is not None:
        stmt = stmt.where(
            status_col.in_(("PUBLISHED", "ACTIVE", "PUBLIC", "published", "active", "public"))
        )
    return stmt


def _title_order(stmt: Select, *, imdb: bool = False) -> Select:
    cols = _mapped(Title)
    if imdb and "imdb_rating" in cols:
        return stmt.order_by(desc(Title.imdb_rating), desc(Title.created_at))
    if "created_at" in cols:
        return stmt.order_by(desc(Title.created_at))
    return stmt


async def list_titles(session, category: str, *, year: int | None = None, limit: int = 20):
    stmt = _published(select(Title))
    type_col = _title_type_col()

    if type_col is not None and category in TYPE_VALUES:
        stmt = stmt.where(type_col.in_(TYPE_VALUES[category]))

    if year is not None and "release_year" in _mapped(Title):
        stmt = stmt.where(Title.release_year == year)

    stmt = _title_order(stmt).limit(limit)
    return list((await session.scalars(stmt)).all())


async def newest_titles(session, *, limit: int = 20):
    return list(
        (
            await session.scalars(
                _title_order(_published(select(Title))).limit(limit)
            )
        ).all()
    )


async def top_imdb_titles(session, *, limit: int = 20):
    return list(
        (
            await session.scalars(
                _title_order(_published(select(Title)), imdb=True).limit(limit)
            )
        ).all()
    )


async def search_titles(session, query: str, *, limit: int = 20):
    q = query.strip()
    if not q:
        return []

    like = f"%{q}%"
    cols = _mapped(Title)
    matches = []
    for name in ("title_fa", "title_en", "original_title", "slug"):
        if name in cols:
            matches.append(getattr(Title, name).ilike(like))

    if not matches:
        return []

    stmt = _published(select(Title).where(or_(*matches)))
    return list((await session.scalars(_title_order(stmt).limit(limit))).all())


async def list_genres(session, *, limit: int = 200):
    return list((await session.scalars(select(Genre).where(Genre.active.is_(True)).order_by(Genre.name_fa.asc()).limit(limit))).all())

async def list_years(session, *, limit: int = 120):
    if "release_year" not in _mapped(Title):
        return []
    stmt = (_published(select(Title.release_year))
            .where(Title.release_year.is_not(None))
            .distinct()
            .order_by(desc(Title.release_year))
            .limit(limit))
    return [int(v) for v in (await session.scalars(stmt)).all() if v is not None]


async def list_people(session, *, limit: int = 200):
    return list((await session.scalars(select(Person).order_by(Person.name_en.asc()).limit(limit))).all())


async def list_collections(session, parent_id=None, *, limit: int = 200):
    stmt = select(Collection).where(Collection.active.is_(True))
    if parent_id is None:
        stmt = stmt.where(Collection.parent_id.is_(None))
    else:
        stmt = stmt.where(Collection.parent_id == parent_id)
    return list((await session.scalars(stmt.order_by(Collection.name_fa.asc()).limit(limit))).all())


def title_text(title) -> str:
    primary = title.title_fa or title.title_en or title.original_title or "بدون عنوان"
    pieces = [primary]

    if title.title_en and title.title_en != primary:
        pieces.append(f"({title.title_en})")

    meta = []
    if title.release_year:
        meta.append(str(title.release_year))
    if title.imdb_rating is not None:
        meta.append(f"IMDb {title.imdb_rating:.1f}")

    if meta:
        pieces.append(" • ".join(meta))

    return " ".join(pieces)


def clean_caption(text: str, limit: int = 900) -> str:
    text = re.sub(r"<[^>]+>", "", clean_text(text)).strip()
    return text[:limit]


async def popular_titles(session, *, limit: int = 20):
    stmt = (
        select(Title, func.count(DownloadHistory.id).label("downloads"))
        .join(Release, Release.title_id == Title.id, isouter=True)
        .join(DownloadHistory, DownloadHistory.release_id == Release.id, isouter=True)
    )
    stmt = _published(stmt)
    stmt = stmt.group_by(Title.id).order_by(desc("downloads"), desc(Title.created_at)).limit(limit)
    return [row[0] for row in (await session.execute(stmt)).all()]


async def get_title(session, title_id):
    return await session.get(Title, title_id)
