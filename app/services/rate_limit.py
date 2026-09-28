from __future__ import annotations

import os
from dataclasses import dataclass

from redis.asyncio import Redis

from app.core.config import settings


class RateLimitExceeded(RuntimeError):
    pass


class RateLimitUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class RateLimitResult:
    allowed: bool
    count: int


def _enabled() -> bool:
    """Precedence [INT-c]: app/core/config.py GOVERNS the effective behavior.

    The authoritative switch is ``settings.rate_limit_enabled`` — that is the
    value the production validation in validate_settings() checks ("RATE_LIMIT_ENABLED
    must be 1 in production"), so what the config gate approves is exactly what
    this limiter does at runtime. An explicit RATE_LIMIT_ENABLED env value is
    still honored first as an override (it wins over the import-time snapshot
    when a process mutates os.environ after import).

    Previously this module re-parsed the env with its OWN DIFFERENT implicit
    default ("1" in production vs config's "0"), so the settings-side validation
    and the limiter could disagree (e.g. limiter active while
    settings.rate_limit_enabled was False). That shadow default is gone.
    """
    raw = os.getenv("RATE_LIMIT_ENABLED")
    if raw is not None and raw.strip() != "":
        return raw.strip().lower() in {"1", "true", "yes", "on"}
    return settings.rate_limit_enabled


def _fail_closed() -> bool:
    raw = os.getenv("RATE_LIMIT_FAIL_CLOSED", "0")
    return raw.strip().lower() in {"1", "true", "yes", "on"} or settings.rate_limit_fail_closed


def _redis_url() -> str:
    """Precedence [INT-c]: explicit REDIS_URL env override first, then the
    validated ``settings.redis_url`` (the same value config.py's REDIS_URL
    scheme/production checks govern). No more "" shadow default on this side:
    previously an unset REDIS_URL made the limiter silently fail-open even when
    the settings-side validation had passed on the default redis:// URL."""
    return (os.getenv("REDIS_URL") or settings.redis_url or "").strip()


async def hit(key: str, *, limit: int, window_seconds: int) -> RateLimitResult:
    if not _enabled():
        return RateLimitResult(True, 0)

    redis_url = _redis_url()
    if not redis_url:
        if _fail_closed():
            raise RateLimitUnavailable("REDIS_URL is required for rate limiting.")
        return RateLimitResult(True, 0)

    redis = Redis.from_url(redis_url, decode_responses=True)
    try:
        redis_key = f"cv:rl:{key}"
        # [FIX-E] اتمیک‌کردن ساخت کلید: ترتیب قبلی INCR سپس EXPIRE بود؛ اگر
        # EXPIRE گم می‌شد (کرش/قطعی بین دو فراخوانی) کلید بدون TTL می‌ماند و
        # throttle ابدی می‌شد. حالا SET NX EX کلید را همیشه با TTL می‌سازد و
        # INCR بعد از آن فقط شمارش را بالا می‌برد (اولین درخواست پنجره: 0+1=1).
        # بقیه‌ی رفتار (fail-open و ...) بدون تغییر.
        await redis.set(redis_key, 0, ex=max(1, int(window_seconds)), nx=True)
        count = int(await redis.incr(redis_key))
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
