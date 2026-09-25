from __future__ import annotations

import re
from typing import Any

from sqlalchemy import text


def _num(value: Any) -> float:
    try:
        if hasattr(value, "timestamp"):
            return float(value.timestamp())
        match = re.search(r"\d+(?:\.\d+)?", str(value))
        return float(match.group(0)) if match else 0.0
    except Exception:
        return 0.0


def _quality(row: dict) -> str:
    extra = row.get("extra_data") or {}
    for key in ("quality", "resolution", "video_quality", "height"):
        value = row.get(key)
        if value in (None, "") and isinstance(extra, dict):
            value = extra.get(key)
        if value not in (None, ""):
            text_value = str(value).strip()
            if text_value.isdigit():
                return f"{text_value}p"
            if re.fullmatch(r"\d{3,4}p", text_value, re.I):
                return text_value.lower()
            return text_value
    return "کیفیت نامشخص"


def _bag_value(row: dict, *keys: str) -> str | None:
    extra = row.get("extra_data") or {}
    for key in keys:
        value = row.get(key)
        if value in (None, "") and isinstance(extra, dict):
            value = extra.get(key)
        if value not in (None, ""):
            return str(value).strip()
    return None


def release_label(row: dict) -> str:
    quality = _quality(row)
    source = _bag_value(row, "source", "release_source")
    codec = _bag_value(row, "codec")
    audio = _bag_value(row, "audio", "audio_language", "language")
    subtitle = _bag_value(row, "subtitle", "subtitle_language")
    parts = [quality]
    for value in (source, codec, audio, subtitle):
        if value:
            parts.append(value)
    return " | ".join(parts)[:64]


def release_badges(row: dict) -> list[str]:
    badges: list[str] = []
    if delivery_target(row):
        badges.append("دانلود")
    if stream_source(row):
        badges.append("پخش")
    if not badges:
        badges.append("آماده‌سازی")
    return badges


def _published_clause() -> str:
    return "COALESCE(UPPER(r.status), '') IN ('PUBLISHED','ACTIVE','PUBLIC')"


async def list_release_variants(session, *, title_id) -> list[dict]:
    result = await session.execute(
        text(
            f"""
            SELECT r.*,
                   COALESCE(r.title_id, series.title_id) AS content_title_id,
                   sf.id AS storage_file_id,
                   sf.chat_id AS storage_chat_id,
                   sf.message_id AS storage_message_id,
                   sf.file_id AS storage_file_id_value,
                   sf.status AS storage_status,
                   sf.extra_data AS storage_extra_data
            FROM releases r
            LEFT JOIN episodes ep ON ep.id = r.episode_id
            LEFT JOIN seasons se ON se.id = ep.season_id
            LEFT JOIN series ON series.id = se.series_id
            JOIN titles t ON t.id = COALESCE(r.title_id, series.title_id)
            LEFT JOIN release_files rf ON rf.release_id = r.id
            LEFT JOIN storage_files sf
              ON sf.id = rf.storage_file_id
             AND sf.status = 'READY'
            WHERE (r.title_id = :title_id OR series.title_id = :title_id)
              AND {_published_clause()}
              AND COALESCE(UPPER(t.status), '') IN ('PUBLISHED','ACTIVE','PUBLIC')
            ORDER BY r.created_at DESC NULLS LAST
            """
        ),
        {"title_id": title_id},
    )
    unique: dict[str, dict] = {}
    for raw in result.mappings().all():
        row = dict(raw)
        key = str(row["id"])
        if key not in unique or (not unique[key].get("storage_file_id") and row.get("storage_file_id")):
            unique[key] = row
    rows = list(unique.values())
    rows.sort(key=lambda x: (_num(_quality(x)), _num(x.get("created_at") or 0)), reverse=True)
    return rows


async def get_release_variant(session, *, release_id) -> dict | None:
    result = await session.execute(
        text(
            f"""
            SELECT r.*,
                   COALESCE(r.title_id, series.title_id) AS content_title_id,
                   t.poster_url AS title_poster_url,
                   t.title_fa AS title_fa,
                   t.title_en AS title_en,
                   t.original_title AS original_title,
                   sf.id AS storage_file_id,
                   sf.chat_id AS storage_chat_id,
                   sf.message_id AS storage_message_id,
                   sf.file_id AS storage_file_id_value,
                   sf.status AS storage_status,
                   sf.extra_data AS storage_extra_data
            FROM releases r
            LEFT JOIN episodes ep ON ep.id = r.episode_id
            LEFT JOIN seasons se ON se.id = ep.season_id
            LEFT JOIN series ON series.id = se.series_id
            JOIN titles t ON t.id = COALESCE(r.title_id, series.title_id)
            LEFT JOIN release_files rf ON rf.release_id = r.id
            LEFT JOIN storage_files sf
              ON sf.id = rf.storage_file_id
             AND sf.status = 'READY'
            WHERE r.id = :release_id
              AND {_published_clause()}
              AND COALESCE(UPPER(t.status), '') IN ('PUBLISHED','ACTIVE','PUBLIC')
            ORDER BY sf.created_at DESC NULLS LAST
            LIMIT 1
            """
        ),
        {"release_id": release_id},
    )
    row = result.mappings().first()
    return dict(row) if row else None


def stream_source(row: dict) -> str | None:
    for bag_key in ("storage_extra_data", "extra_data"):
        bag = row.get(bag_key) or {}
        if isinstance(bag, dict):
            for key in ("stream_url", "preview_url", "media_url"):
                value = str(bag.get(key) or "").strip()
                if value.startswith("https://"):
                    return value
    return None



def delivery_target(row: dict) -> tuple[int, int] | None:
    if row.get("storage_chat_id") is None or row.get("storage_message_id") is None:
        return None
    return int(row["storage_chat_id"]), int(row["storage_message_id"])
