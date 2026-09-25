from __future__ import annotations

from collections import defaultdict

from sqlalchemy import select

from app.db.models import Release, ReleaseFile, StorageFile


LANGUAGE_LABELS = {
    "FA_DUBBED": "🎙 دوبله فارسی",
    "ORIGINAL": "🌐 زبان اصلی",
    "ENGLISH": "🇬🇧 انگلیسی",
}

SUBTITLE_LABELS = {
    "FA_PERSIAN": "💬 زیرنویس فارسی",
    "ENGLISH": "💬 زیرنویس انگلیسی",
    "NONE": "",
}


def release_label(release) -> str:
    language = LANGUAGE_LABELS.get(release.language, release.language)
    subtitle = SUBTITLE_LABELS.get(release.subtitle_type, release.subtitle_type)
    extra = [
        value
        for value in (
            release.codec_video,
            release.container.upper() if release.container else None,
        )
        if value
    ]
    parts = [release.quality, language]
    if subtitle:
        parts.append(subtitle)
    if extra:
        parts.append(" • ".join(extra))
    return " | ".join(parts)


async def list_published_releases(session, title_id):
    return list(
        (
            await session.scalars(
                select(Release)
                .where(
                    Release.title_id == title_id,
                    Release.status == "PUBLISHED",
                )
                .order_by(Release.priority.desc(), Release.created_at.desc())
            )
        ).all()
    )


async def get_release_with_storage(session, release_id):
    release = await session.get(Release, release_id)
    if release is None:
        return None, None

    storage = await session.scalar(
        select(StorageFile)
        .join(
            ReleaseFile,
            ReleaseFile.storage_file_id == StorageFile.id,
        )
        .where(
            ReleaseFile.release_id == release_id,
            ReleaseFile.active.is_(True),
            ReleaseFile.is_primary.is_(True),
            StorageFile.status == "READY",
        )
        .order_by(StorageFile.created_at.desc())
    )
    return release, storage


def group_releases(releases):
    groups = defaultdict(list)
    for release in releases:
        group = (
            "🎙 دوبله فارسی"
            if release.language == "FA_DUBBED"
            else (
                "💬 زیرنویس فارسی"
                if release.subtitle_type == "FA_PERSIAN"
                else "🌐 زبان اصلی"
            )
        )
        groups[group].append(release)
    return dict(groups)
