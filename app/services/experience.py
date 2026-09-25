from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import quote
import os

from sqlalchemy import text

REACTION_LIKE = 1
REACTION_DISLIKE = -1


def _now() -> datetime:
    return datetime.now(timezone.utc)


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


async def create_media_access_token(
    session,
    *,
    user_id,
    release_id,
    action: str = "STREAM",
    ttl_seconds: int = 600,
) -> str:
    if action not in {"STREAM", "PREVIEW"}:
        raise ValueError("invalid media access action")
    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    expires_at = _now() + timedelta(seconds=max(60, min(ttl_seconds, 3600)))
    await session.execute(
        text(
            """
            INSERT INTO media_access_tokens
                (id, token, token_hash, user_id, release_id, action, expires_at,
                 created_at, last_used_at, use_count)
            VALUES
              (gen_random_uuid(), NULL, :token_hash, :user_id, :release_id, :action, :expires_at,
               CURRENT_TIMESTAMP, NULL, 0)
            """
        ),
        {
            "token_hash": token_hash,
            "user_id": user_id,
            "release_id": release_id,
            "action": action,
            "expires_at": expires_at,
        },
    )
    return token


async def resolve_media_token(
    session,
    token: str,
    *,
    client_ip: str | None = None,
    user_agent: str | None = None,
) -> dict | None:
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    row = (
        await session.execute(
            text(
                """
                UPDATE media_access_tokens
                SET last_used_at = CURRENT_TIMESTAMP,
                    use_count = use_count + 1,
                    last_used_ip = COALESCE(:client_ip, last_used_ip),
                    last_used_user_agent = COALESCE(:user_agent, last_used_user_agent)
                WHERE token_hash = :token_hash
                  AND revoked_at IS NULL
                  AND expires_at > CURRENT_TIMESTAMP
                RETURNING user_id, release_id, action, expires_at, use_count
                """
            ),
            {
                "token_hash": token_hash,
                "client_ip": client_ip,
                "user_agent": (user_agent or "")[:255] or None,
            },
        )
    ).mappings().first()
    return dict(row) if row else None


async def revoke_media_token(session, token: str) -> bool:
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    result = await session.execute(
        text(
            """
            UPDATE media_access_tokens
            SET revoked_at = CURRENT_TIMESTAMP
            WHERE token_hash = :token_hash AND revoked_at IS NULL
            """
        ),
        {"token_hash": token_hash},
    )
    return bool(result.rowcount)


async def create_watch_party(session, *, host_user_id, title_id, release_id, ttl_hours: int = 24) -> str:
    token = secrets.token_urlsafe(24)
    expires_at = _now() + timedelta(hours=max(1, min(ttl_hours, 168)))
    await session.execute(
        text(
            """
            INSERT INTO watch_parties
              (id, host_user_id, title_id, release_id, invite_token, status, current_position_seconds, is_playing, created_at, expires_at, updated_at)
            VALUES
              (gen_random_uuid(), :host_user_id, :title_id, :release_id, :token, 'ACTIVE', 0, FALSE, CURRENT_TIMESTAMP, :expires_at, CURRENT_TIMESTAMP)
            """
        ),
        {"host_user_id": host_user_id, "title_id": title_id, "release_id": release_id, "token": token, "expires_at": expires_at},
    )
    await add_watch_party_member(session, invite_token=token, user_id=host_user_id)
    return token


async def join_watch_party(session, *, invite_token: str, user_id) -> dict | None:
    party = (
        await session.execute(
            text(
                """
                SELECT id, host_user_id, title_id, release_id, invite_token, status, current_position_seconds, is_playing, expires_at
                FROM watch_parties
                WHERE invite_token = :invite_token AND status = 'ACTIVE' AND expires_at > CURRENT_TIMESTAMP
                """
            ),
            {"invite_token": invite_token},
        )
    ).mappings().first()
    if not party:
        return None
    await add_watch_party_member(session, invite_token=invite_token, user_id=user_id)
    return dict(party)


async def add_watch_party_member(session, *, invite_token: str, user_id) -> None:
    await session.execute(
        text(
            """
            INSERT INTO watch_party_members (id, party_id, user_id, joined_at, last_seen_at)
            SELECT gen_random_uuid(), id, :user_id, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
            FROM watch_parties WHERE invite_token = :invite_token
            ON CONFLICT (party_id, user_id)
            DO UPDATE SET last_seen_at = CURRENT_TIMESTAMP
            """
        ),
        {"invite_token": invite_token, "user_id": user_id},
    )


def _public_base() -> str | None:
    base = os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/")
    if not base:
        return None
    if os.getenv("APP_ENV", "development").lower() == "production" and not base.startswith("https://"):
        return None
    return base


def public_watch_url(invite_token: str) -> str | None:
    base = _public_base()
    return f"{base}/watch/{quote(invite_token, safe='')}" if base else None


def public_media_url(token: str) -> str | None:
    base = _public_base()
    return f"{base}/media/access/{quote(token, safe='')}" if base else None
