from __future__ import annotations

import os
from dataclasses import dataclass

from redis.asyncio import Redis


class RateLimitExceeded(RuntimeError):
    pass


class RateLimitUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class RateLimitResult:
    allowed: bool
    count: int


def _enabled() -> bool:
    raw = os.getenv(
        "RATE_LIMIT_ENABLED",
        "1" if os.getenv("APP_ENV", "development").lower() == "production" else "0",
    )
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _fail_closed() -> bool:
    raw = os.getenv("RATE_LIMIT_FAIL_CLOSED", "0")
    return raw.strip().lower() in {"1", "true", "yes", "on"}


async def hit(key: str, *, limit: int, window_seconds: int) -> RateLimitResult:
    if not _enabled():
        return RateLimitResult(True, 0)

    redis_url = os.getenv("REDIS_URL", "").strip()
    if not redis_url:
        if _fail_closed():
            raise RateLimitUnavailable("REDIS_URL is required for rate limiting.")
        return RateLimitResult(True, 0)

    redis = Redis.from_url(redis_url, decode_responses=True)
    try:
        redis_key = f"cv:rl:{key}"
        count = int(await redis.incr(redis_key))
        if count == 1:
            await redis.expire(redis_key, max(1, int(window_seconds)))
        allowed = count <= max(1, int(limit))
        if not allowed:
            raise RateLimitExceeded("Too many requests.")
        return RateLimitResult(allowed, count)
    except RateLimitExceeded:
        raise
    except Exception as exc:
        if _fail_closed():
            raise RateLimitUnavailable("Rate limiter backend is unavailable.") from exc
        return RateLimitResult(True, 0)
    finally:
        await redis.aclose()


async def enforce(key: str, *, limit: int, window_seconds: int) -> None:
    await hit(key, limit=limit, window_seconds=window_seconds)
