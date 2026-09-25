from __future__ import annotations

import json
import os
import time
from pathlib import Path
from tempfile import NamedTemporaryFile

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from sqlalchemy import select

from app.db.models import MembershipChannel, User
from app.runtime.db import session_scope

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = Path(os.getenv("CINEMAVAULT_RUNTIME_DIR", str(PROJECT_ROOT / "data" / "runtime"))) / "membership.json"

# کش درون‌پردازه‌ای نتیجه بررسی عضویت (jitter کوتاه تا فراخوانی getChatMember روی
# هر پیام به Telegram API اسپم نزند)
_CACHE: dict[int, tuple[float, bool, tuple]] = {}
_CACHE_TTL_SECONDS = 90

_JOINED_STATUSES = {"creator", "administrator", "member", "restricted"}


# ---------- Runtime toggle (فعال/غیرفعال کردن دروازه از پنل) ----------

def load_gate_config() -> dict:
    if not CONFIG_PATH.exists():
        return {"enabled": False}
    try:
        raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"enabled": False}
    if not isinstance(raw, dict):
        return {"enabled": False}
    return {"enabled": bool(raw.get("enabled", False))}


def save_gate_config(*, enabled: bool) -> dict:
    value = {"enabled": bool(enabled)}
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile("w", encoding="utf-8", dir=CONFIG_PATH.parent, delete=False) as tmp:
        json.dump(value, tmp, ensure_ascii=False, indent=2)
        tmp.write("\n")
        temp_name = tmp.name
    try:
        os.replace(temp_name, CONFIG_PATH)
    finally:
        try:
            Path(temp_name).unlink(missing_ok=True)
        except OSError:
            pass
    return value


# ---------- Channels CRUD (پنل ادمین) ----------

async def list_channels(session) -> list[dict]:
    rows = (await session.scalars(select(MembershipChannel).order_by(MembershipChannel.created_at))).all()
    return [
        {
            "id": str(row.id),
            "telegram_chat_id": row.telegram_chat_id,
            "username": row.username,
            "title": row.title,
            "invite_url": row.invite_url,
            "required": row.required,
            "active": row.active,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }
        for row in rows
    ]


async def resolve_channel_target(bot: Bot | None, target: str) -> dict:
    """ورودی پنل: شناسه عددی (‎-100…‎) یا @username — به شناسه عددی + username حل می‌شود."""
    target = (target or "").strip()
    if not target:
        raise ValueError("شناسه یا نام کاربری کانال را وارد کنید.")
    if target.startswith("@"):
        username = target.lstrip("@")
        chat_id: int | None = None
        title = ""
        if bot is not None:
            try:
                chat = await bot.get_chat(f"@{username}")
                chat_id = int(chat.id)
                title = str(chat.title or "")
            except Exception:
                chat_id = None
        if chat_id is None:
            raise ValueError(
                "حل خودکار شناسه کانال ممکن نشد (ربات باید ادمین کانال باشد یا API در دسترس باشد). "
                "شناسه عددی کانال را دستی وارد کنید."
            )
        return {"telegram_chat_id": chat_id, "username": username, "title": title}
    try:
        chat_id = int(target)
    except ValueError as exc:
        raise ValueError("شناسه کانال باید عددی (مثل ‎-1001234567890‎) یا با @ شروع شود.") from exc
    return {"telegram_chat_id": chat_id, "username": None, "title": ""}


# ---------- Gate check ----------

def invalidate_cache(telegram_user_id: int | None = None) -> None:
    if telegram_user_id is None:
        _CACHE.clear()
    else:
        _CACHE.pop(telegram_user_id, None)


async def active_required_channels(session) -> list[MembershipChannel]:
    return list(
        (
            await session.scalars(
                select(MembershipChannel).where(
                    MembershipChannel.active.is_(True),
                    MembershipChannel.required.is_(True),
                )
            )
        ).all()
    )


async def gate_enabled(session) -> bool:
    if not load_gate_config().get("enabled"):
        return False
    return bool(await active_required_channels(session))


async def is_admin_user(session, telegram_user_id: int) -> bool:
    if settings_admin_id() == telegram_user_id:
        return True
    row = await session.scalar(select(User).where(User.telegram_user_id == telegram_user_id))
    if row is None:
        return False
    from app.services.rbac import is_super_admin

    return await is_super_admin(session, row.id)


def settings_admin_id() -> int | None:
    from app.core.config import settings

    return settings.admin_user_id


async def check_membership(
    bot: Bot,
    session,
    telegram_user_id: int,
    *,
    force_refresh: bool = False,
) -> tuple[bool, list[MembershipChannel]]:
    """خروجی: (عضو همه کانال‌های الزامی است؟، فهرست کانال‌های جاافتاده)."""
    channels = await active_required_channels(session)
    if not channels:
        return True, []

    if not force_refresh:
        cached = _CACHE.get(telegram_user_id)
        if cached and time.monotonic() - cached[0] < _CACHE_TTL_SECONDS:
            return cached[1], list(cached[2])

    missing: list[MembershipChannel] = []
    for channel in channels:
        target = f"@{channel.username}" if channel.username else channel.telegram_chat_id
        try:
            member = await bot.get_chat_member(target, telegram_user_id)
            if str(getattr(member, "status", "left")) not in _JOINED_STATUSES:
                missing.append(channel)
        except TelegramBadRequest as exc:
            message = str(exc).lower()
            if "user not found" in message or "participant" in message:
                missing.append(channel)
            elif "chat not found" in message or "not a member" in message or "member not found" in message:
                # کانال پیکربندی‌نشده/ربات عضو نیست — کاربران را قفل نکن
                continue
            else:
                missing.append(channel)
        except (TelegramForbiddenError, Exception):
            # خطای شبکه/API — fail-open تا دسترسی کاربران سالم قطع نشود
            continue

    joined = len(missing) == 0
    _CACHE[telegram_user_id] = (time.monotonic(), joined, tuple(missing))
    return joined, missing
