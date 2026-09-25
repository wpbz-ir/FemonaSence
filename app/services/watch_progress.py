from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import text


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(value, high))


async def get_watch_progress(session, *, user_id, title_id) -> dict | None:
    row = (
        await session.execute(
            text(
                """
                SELECT title_id, release_id, position_seconds, duration_seconds, completed, last_watched_at
                FROM watch_progress
                WHERE user_id = :user_id AND title_id = :title_id
                """
            ),
            {"user_id": user_id, "title_id": title_id},
        )
    ).mappings().first()
    return dict(row) if row else None


async def save_watch_progress(
    session,
    *,
    user_id,
    title_id,
    release_id,
    position_seconds: float,
    duration_seconds: float | None,
) -> dict:
    position = _clamp(float(position_seconds or 0), 0, 864000)
    duration = None if duration_seconds in (None, 0) else _clamp(float(duration_seconds), 1, 864000)
    if duration is not None:
        position = min(position, duration)
    completed = bool(duration is not None and duration > 0 and position >= max(0, duration - 30))

    result = await session.execute(
        text(
            """
            INSERT INTO watch_progress
                (id, user_id, title_id, release_id, position_seconds, duration_seconds, completed, last_watched_at)
            VALUES
                (gen_random_uuid(), :user_id, :title_id, :release_id, :position_seconds, :duration_seconds, :completed, CURRENT_TIMESTAMP)
            ON CONFLICT (user_id, title_id)
            DO UPDATE SET
                release_id = EXCLUDED.release_id,
                position_seconds = EXCLUDED.position_seconds,
                duration_seconds = EXCLUDED.duration_seconds,
                completed = EXCLUDED.completed,
                last_watched_at = CURRENT_TIMESTAMP
            RETURNING title_id, release_id, position_seconds, duration_seconds, completed, last_watched_at
            """
        ),
        {
            "user_id": user_id,
            "title_id": title_id,
            "release_id": release_id,
            "position_seconds": Decimal(f"{position:.3f}"),
            "duration_seconds": Decimal(f"{duration:.3f}") if duration is not None else None,
            "completed": completed,
        },
    )
    return dict(result.mappings().one())


def progress_label(progress: dict | None) -> str | None:
    if not progress or progress.get("completed"):
        return None
    try:
        position = float(progress.get("position_seconds") or 0)
    except (TypeError, ValueError):
        return None
    if position < 5:
        return None
    minutes = int(position // 60)
    seconds = int(position % 60)
    return f"ادامه از {minutes:02d}:{seconds:02d}"
