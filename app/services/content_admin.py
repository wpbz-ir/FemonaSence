from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select

from app.core.config import settings
from app.db.models import (
    Collection,
    Genre,
    Episode,
    Person,
    Release,
    ReleaseFile,
    Season,
    Series,
    StorageFile,
    StorageProvider,
    Title,
)


def _slug(text: str) -> str:
    value = re.sub(r"[^\w\u0600-\u06FF]+", "-", text.strip().lower())
    return value.strip("-") or str(uuid.uuid4())


async def _unique_slug(session, model, base: str) -> str:
    """Return a slug that does not collide with an existing row of `model`."""
    base = _slug(base)[:150]
    candidate = base
    for _ in range(50):
        exists = await session.scalar(select(model.id).where(model.slug == candidate))
        if exists is None:
            return candidate
        candidate = f"{base}-{uuid.uuid4().hex[:6]}"
    return f"{base}-{uuid.uuid4().hex}"


async def list_titles(session, limit: int = 50, offset: int = 0):
    limit = max(1, min(int(limit), 200))
    offset = max(0, int(offset))
    stmt = (
        select(Title)
        .order_by(Title.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    return list((await session.scalars(stmt)).all())


async def get_title(session, title_id):
    return await session.get(Title, title_id)


async def create_title(
    session,
    *,
    kind: str,
    title_fa: str,
    title_en: str | None = None,
    original_title: str | None = None,
    synopsis: str | None = None,
    release_year: int | None = None,
    imdb_id: str | None = None,
    imdb_rating: float | None = None,
    imdb_votes: int | None = None,
    poster_url: str | None = None,
    backdrop_url: str | None = None,
    status: str = "DRAFT",
    rights_verified: bool = False,
    rights_reference: str | None = None,
):
    normalized_kind = kind.strip().upper()
    if normalized_kind not in {"MOVIE", "SERIES", "ANIMATION"}:
        raise ValueError("kind باید یکی از MOVIE، SERIES یا ANIMATION باشد.")
    if settings.content_rights_required and status.upper() == "PUBLISHED" and not rights_verified:
        raise ValueError("برای انتشار Title باید rights_verified=True باشد.")
    title = Title(
        kind=normalized_kind,
        title_fa=title_fa,
        title_en=title_en,
        original_title=original_title,
        slug=await _unique_slug(session, Title, title_en or original_title or title_fa),
        synopsis=synopsis,
        release_year=release_year,
        imdb_id=imdb_id,
        imdb_rating=imdb_rating,
        imdb_votes=imdb_votes,
        poster_url=poster_url,
        backdrop_url=backdrop_url,
        status=status,
        rights_verified=rights_verified,
        rights_reference=rights_reference,
    )
    if title.status == "PUBLISHED":
        title.published_at = datetime.now(timezone.utc)
    session.add(title)
    await session.flush()
    if normalized_kind == "SERIES":
        session.add(Series(title_id=title.id, total_seasons=0, ongoing=False))
        await session.flush()
    return title


async def create_genre(session, *, name_fa: str, name_en: str, slug: str | None = None):
    row = Genre(
        name_fa=name_fa,
        name_en=name_en,
        slug=slug or await _unique_slug(session, Genre, name_en),
        active=True,
    )
    session.add(row)
    await session.flush()
    return row


async def create_person(session, *, name_en: str, name_fa: str | None = None):
    row = Person(
        name_en=name_en,
        name_fa=name_fa,
        slug=await _unique_slug(session, Person, name_en),
    )
    session.add(row)
    await session.flush()
    return row


async def create_collection(
    session,
    *,
    name_fa: str,
    name_en: str | None = None,
    parent_id=None,
    description: str | None = None,
):
    row = Collection(
        name_fa=name_fa,
        name_en=name_en,
        slug=await _unique_slug(session, Collection, name_en or name_fa),
        parent_id=parent_id,
        description=description,
        active=True,
    )
    session.add(row)
    await session.flush()
    return row


async def create_release(
    session,
    *,
    title_id,
    quality: str,
    language: str = "ORIGINAL",
    subtitle_type: str = "NONE",
    label: str | None = None,
    width: int | None = None,
    height: int | None = None,
    codec_video: str | None = None,
    codec_audio: str | None = None,
    container: str | None = None,
    size_bytes: int | None = None,
    status: str = "DRAFT",
):
    if str(status).upper() == "PUBLISHED":
        raise ValueError("Release را ابتدا READY ایجاد کنید؛ پس از اتصال فایل آن را منتشر کنید.")
    title = await session.get(Title, title_id)
    if title is None:
        raise ValueError("عنوان پیدا نشد.")
    if title.kind == "SERIES":
        raise ValueError("برای سریال باید Release را روی قسمت ثبت کنید.")
    if settings.content_rights_required and title.status == "PUBLISHED" and not title.rights_verified:
        raise ValueError("Title منتشرشده بدون تأیید حقوق قابل استفاده نیست.")
    release = Release(
        title_id=title_id,
        quality=quality,
        language=language,
        subtitle_type=subtitle_type,
        label=label,
        width=width,
        height=height,
        codec_video=codec_video,
        codec_audio=codec_audio,
        container=container,
        size_bytes=size_bytes,
        status=status,
    )
    session.add(release)
    await session.flush()
    return release


async def register_telegram_storage_file(
    session,
    *,
    chat_id: int,
    message_id: int,
    file_unique_key: str,
    filename: str | None = None,
    file_id: str | None = None,
    size_bytes: int | None = None,
):
    provider = await session.scalar(
        select(StorageProvider).where(StorageProvider.code == "TELEGRAM")
    )
    if provider is None:
        raise ValueError("Telegram Storage Provider وجود ندارد. Seed را اجرا کنید.")

    existing = await session.scalar(
        select(StorageFile).where(
            StorageFile.provider_id == provider.id,
            StorageFile.file_unique_key == file_unique_key,
        )
    )
    if existing:
        return existing

    row = StorageFile(
        provider_id=provider.id,
        chat_id=chat_id,
        message_id=message_id,
        file_id=file_id,
        file_unique_key=file_unique_key,
        filename=filename,
        size_bytes=size_bytes,
        status="READY",
    )
    session.add(row)
    await session.flush()
    return row


async def attach_storage_file(session, *, release_id, storage_file_id, primary=True):
    release = await session.get(Release, release_id)
    if release is None:
        raise ValueError("Release پیدا نشد.")
    storage = await session.get(StorageFile, storage_file_id)
    if storage is None:
        raise ValueError("فایل ذخیره‌سازی پیدا نشد.")
    if storage.status != "READY" or storage.storage_scope != "PRODUCTION" or not storage.file_id:
        raise ValueError("فایل Storage برای اتصال آماده نیست.")
    existing = await session.scalar(
        select(ReleaseFile).where(
            ReleaseFile.release_id == release_id, ReleaseFile.storage_file_id == storage_file_id
        )
    )
    if primary:
        siblings = await session.scalars(select(ReleaseFile).where(ReleaseFile.release_id == release_id))
        for sibling in siblings.all():
            sibling.is_primary = False
    if existing is not None:
        existing.is_primary = primary
        existing.active = True
        await session.flush()
        return existing
    row = ReleaseFile(
        release_id=release_id,
        storage_file_id=storage_file_id,
        is_primary=primary,
        active=True,
    )
    session.add(row)
    await session.flush()
    return row


async def _series_for_title(session, title_id) -> Series:
    title = await session.get(Title, title_id)
    if title is None:
        raise ValueError("عنوان پیدا نشد.")
    if title.kind != "SERIES":
        raise ValueError("فصل و قسمت فقط برای عنوان از نوع سریال قابل ثبت است.")
    series = await session.scalar(select(Series).where(Series.title_id == title_id))
    if series is None:
        series = Series(title_id=title_id, total_seasons=0, ongoing=False)
        session.add(series)
        await session.flush()
    return series


async def create_season(session, *, title_id, season_number: int, title: str | None = None, synopsis: str | None = None):
    series = await _series_for_title(session, title_id)
    exists = await session.scalar(
        select(Season.id).where(Season.series_id == series.id, Season.season_number == season_number)
    )
    if exists is not None:
        raise ValueError("این فصل قبلاً ثبت شده است.")
    row = Season(series_id=series.id, season_number=season_number, title=title, synopsis=synopsis)
    session.add(row)
    await session.flush()
    count = await session.scalar(select(func.count()).select_from(Season).where(Season.series_id == series.id))
    series.total_seasons = int(count or 0)
    return row


async def create_episode(
    session,
    *,
    season_id,
    episode_number: int,
    title: str | None = None,
    synopsis: str | None = None,
    runtime_minutes: int | None = None,
):
    season = await session.get(Season, season_id)
    if season is None:
        raise ValueError("فصل پیدا نشد.")
    exists = await session.scalar(
        select(Episode.id).where(Episode.season_id == season_id, Episode.episode_number == episode_number)
    )
    if exists is not None:
        raise ValueError("این قسمت قبلاً ثبت شده است.")
    row = Episode(
        season_id=season_id,
        episode_number=episode_number,
        title=title,
        synopsis=synopsis,
        runtime_minutes=runtime_minutes,
    )
    session.add(row)
    await session.flush()
    return row


async def list_series_tree(session, title_id):
    series = await session.scalar(select(Series).where(Series.title_id == title_id))
    if series is None:
        return []
    seasons = (
        await session.scalars(
            select(Season).where(Season.series_id == series.id).order_by(Season.season_number.asc())
        )
    ).all()
    out = []
    for season in seasons:
        episodes = (
            await session.scalars(
                select(Episode).where(Episode.season_id == season.id).order_by(Episode.episode_number.asc())
            )
        ).all()
        out.append(
            {
                "id": str(season.id),
                "season_number": season.season_number,
                "title": season.title,
                "episodes": [
                    {"id": str(e.id), "episode_number": e.episode_number, "title": e.title} for e in episodes
                ],
            }
        )
    return out


async def create_episode_release(session, *, episode_id, **fields):
    episode = await session.get(Episode, episode_id)
    if episode is None:
        raise ValueError("قسمت پیدا نشد.")
    if str(fields.get("status", "DRAFT")).upper() == "PUBLISHED":
        raise ValueError("Release را ابتدا READY ایجاد کنید؛ پس از اتصال فایل آن را منتشر کنید.")
    release = Release(episode_id=episode_id, title_id=None, **fields)
    session.add(release)
    await session.flush()
    return release


async def list_storage_files(session, *, unattached_only: bool = True, limit: int = 50, offset: int = 0):
    stmt = select(StorageFile).order_by(StorageFile.created_at.desc())
    if unattached_only:
        attached = select(ReleaseFile.storage_file_id)
        stmt = stmt.where(StorageFile.id.not_in(attached))
    return list((await session.scalars(stmt.offset(offset).limit(limit))).all())
