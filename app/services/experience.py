from __future__ import annotations

from sqlalchemy import text

REACTION_LIKE = 1
REACTION_DISLIKE = -1


async def toggle_reaction(session, *, user_id, title_id, value: int) -> dict:
    if value not in (REACTION_LIKE, REACTION_DISLIKE):
        raise ValueError("reaction value is invalid")
    selected = await session.scalar(
        text(
            """
            WITH deleted AS (
                DELETE FROM content_reactions
                WHERE user_id = :user_id AND title_id = :title_id AND value = :value
                RETURNING value
            ), upserted AS (
                INSERT INTO content_reactions (id, user_id, title_id, value, created_at, updated_at)
                SELECT gen_random_uuid(), :user_id, :title_id, :value, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                WHERE NOT EXISTS (SELECT 1 FROM deleted)
                ON CONFLICT (user_id, title_id)
                DO UPDATE SET value = EXCLUDED.value, updated_at = CURRENT_TIMESTAMP
                RETURNING value
            )
            SELECT COALESCE((SELECT value FROM deleted LIMIT 1), (SELECT value FROM upserted LIMIT 1), 0)
            """
        ),
        {"user_id": user_id, "title_id": title_id, "value": value},
    )
    counts = await session.execute(
        text(
            """
            SELECT COUNT(*) FILTER (WHERE value = 1) AS likes,
                   COUNT(*) FILTER (WHERE value = -1) AS dislikes
            FROM content_reactions WHERE title_id = :title_id
            """
        ),
        {"title_id": title_id},
    )
    row = counts.mappings().one()
    return {"selected": int(selected or 0), "likes": int(row["likes"] or 0), "dislikes": int(row["dislikes"] or 0)}


async def reaction_summary(session, *, user_id, title_id) -> dict:
    row = (
        await session.execute(
            text(
                """
                SELECT
                  COALESCE((SELECT value FROM content_reactions WHERE user_id = :user_id AND title_id = :title_id), 0) AS selected,
                  COUNT(*) FILTER (WHERE value = 1) AS likes,
                  COUNT(*) FILTER (WHERE value = -1) AS dislikes
                FROM content_reactions WHERE title_id = :title_id
                """
            ),
            {"user_id": user_id, "title_id": title_id},
        )
    ).mappings().one()
    return {"selected": int(row["selected"] or 0), "likes": int(row["likes"] or 0), "dislikes": int(row["dislikes"] or 0)}


async def list_comments(session, *, title_id, limit: int = 30) -> list[dict]:
    rows = (
        await session.execute(
            text(
                """
                SELECT c.id, c.body, c.created_at, u.first_name, u.last_name, u.username
                FROM content_comments c
                JOIN users u ON u.id = c.user_id
                WHERE c.title_id = :title_id AND c.status = 'PUBLISHED'
                ORDER BY c.created_at DESC
                LIMIT :limit
                """
            ),
            {"title_id": title_id, "limit": max(1, min(limit, 50))},
        )
    ).mappings().all()
    return [dict(row) for row in rows]


async def add_comment(session, *, user_id, title_id, body: str) -> dict:
    clean = " ".join((body or "").split()).strip()
    if len(clean) < 2 or len(clean) > 1000:
        raise ValueError("comment length is invalid")
    row = (
        await session.execute(
            text(
                """
                INSERT INTO content_comments (id, user_id, title_id, body, status, created_at, updated_at)
                VALUES (gen_random_uuid(), :user_id, :title_id, :body, 'PUBLISHED', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                RETURNING id, body, created_at
                """
            ),
            {"user_id": user_id, "title_id": title_id, "body": clean},
        )
    ).mappings().one()
    return dict(row)
