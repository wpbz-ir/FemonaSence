from __future__ import annotations

import uuid

from aiogram import Bot
from fastapi import Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import delete, select

from app.api.admin import _request_meta, admin_gate, router
from app.core.config import settings
from app.db.models import MembershipChannel
from app.runtime.db import session_scope
from app.services.ads import (
    get_ad_settings,
    get_or_create_ad_settings,
    list_ad_requests,
    serialize_ad_settings,
    set_ad_request_status,
    ad_request_counts,
)
from app.services.audit import record_admin_action
from app.services.membership import (
    list_channels,
    load_gate_config,
    resolve_channel_target,
    save_gate_config,
)
from app.services.text_normalization import clean_text


# ================= ADS =================

class AdSettingsPayload(BaseModel):
    channel_chat_id: int | None = Field(default=None, ge=-9_999_999_999_999, le=9_999_999_999_999)
    channel_username: str | None = Field(default=None, max_length=255)
    rates_text: str | None = Field(default=None, max_length=4000)
    instructions_text: str | None = Field(default=None, max_length=4000)
    contact_text: str | None = Field(default=None, max_length=2000)
    auto_channel_publish: bool = False
    active: bool = True


@router.get("/ads/settings", dependencies=[Depends(admin_gate)])
async def ads_settings_get():
    async with session_scope() as session:
        row = await get_ad_settings(session)
        return serialize_ad_settings(row)


@router.put("/ads/settings", dependencies=[Depends(admin_gate)])
async def ads_settings_put(payload: AdSettingsPayload, request: Request):
    async with session_scope() as session:
        row = await get_or_create_ad_settings(session)
        row.channel_chat_id = payload.channel_chat_id
        username = (payload.channel_username or "").strip().lstrip("@") or None
        row.channel_username = username
        row.rates_text = clean_text(payload.rates_text, 4000) or None
        row.instructions_text = clean_text(payload.instructions_text, 4000) or None
        row.contact_text = clean_text(payload.contact_text, 2000) or None
        row.auto_channel_publish = payload.auto_channel_publish
        row.active = payload.active
        await record_admin_action(
            session,
            action="ADS_SETTINGS_UPDATE",
            entity_type="ad_settings",
            entity_id=row.id,
            details={"active": row.active, "auto_publish": row.auto_channel_publish},
            **_request_meta(request),
        )
        return serialize_ad_settings(row)


@router.get("/ads/requests", dependencies=[Depends(admin_gate)])
async def ads_requests_list(status: str | None = None, limit: int = 50):
    async with session_scope() as session:
        rows = await list_ad_requests(session, status=status, limit=limit)
        counts = await ad_request_counts(session)
        return {"items": rows, "counts": counts}


class AdStatusPayload(BaseModel):
    status: str = Field(pattern="^(PENDING|APPROVED|REJECTED|PUBLISHED)$")
    admin_note: str | None = Field(default=None, max_length=2000)


@router.post("/ads/requests/{request_id}/status", dependencies=[Depends(admin_gate)])
async def ads_request_status(request_id: uuid.UUID, payload: AdStatusPayload, request: Request):
    async with session_scope() as session:
        row = await set_ad_request_status(
            session,
            request_id,
            status=payload.status,
            admin_note=payload.admin_note,
        )
        if row is None:
            raise HTTPException(status_code=404, detail="درخواست پیدا نشد.")
        await record_admin_action(
            session,
            action="ADS_REQUEST_STATUS",
            entity_type="ad_request",
            entity_id=row.id,
            details={"status": row.status},
            **_request_meta(request),
        )
        return {"ok": True, "id": str(row.id), "status": row.status}


# ================= MEMBERSHIP (عضویت اجباری) =================

class MembershipChannelPayload(BaseModel):
    target: str = Field(max_length=255, description="شناسه عددی کانال یا @username")
    title: str | None = Field(default=None, max_length=255)
    invite_url: str | None = Field(default=None, max_length=1000)
    required: bool = True
    active: bool = True


@router.get("/membership/config", dependencies=[Depends(admin_gate)])
async def membership_config_get():
    return load_gate_config()


@router.put("/membership/config", dependencies=[Depends(admin_gate)])
async def membership_config_put(payload: dict, request: Request):
    enabled = bool(payload.get("enabled", False))
    value = save_gate_config(enabled=enabled)
    async with session_scope() as session:
        await record_admin_action(
            session,
            action="MEMBERSHIP_GATE_TOGGLE",
            entity_type="membership_config",
            entity_id=None,
            details={"enabled": enabled},
            **_request_meta(request),
        )
    return value


@router.get("/membership/channels", dependencies=[Depends(admin_gate)])
async def membership_channels_list():
    async with session_scope() as session:
        return await list_channels(session)


@router.post("/membership/channels", dependencies=[Depends(admin_gate)])
async def membership_channel_create(payload: MembershipChannelPayload, request: Request):
    bot = None
    if settings.bot_token:
        try:
            bot = Bot(token=settings.bot_token)
        except Exception:
            bot = None
    try:
        resolved = await resolve_channel_target(bot, payload.target)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        if bot is not None:
            try:
                await bot.session.close()
            except Exception:
                pass

    async with session_scope() as session:
        exists = await session.scalar(
            select(MembershipChannel).where(MembershipChannel.telegram_chat_id == resolved["telegram_chat_id"])
        )
        if exists:
            raise HTTPException(status_code=409, detail="این کانال قبلاً ثبت شده است.")
        row = MembershipChannel(
            telegram_chat_id=resolved["telegram_chat_id"],
            username=resolved["username"],
            title=(payload.title or "").strip() or clean_text(resolved["title"], 255) or resolved["username"] or str(resolved["telegram_chat_id"]),
            invite_url=(payload.invite_url or "").strip() or None,
            required=payload.required,
            active=payload.active,
        )
        session.add(row)
        await session.flush()
        await record_admin_action(
            session,
            action="MEMBERSHIP_CHANNEL_CREATE",
            entity_type="membership_channel",
            entity_id=row.id,
            details={"chat_id": row.telegram_chat_id, "username": row.username},
            **_request_meta(request),
        )
        return {
            "id": str(row.id),
            "telegram_chat_id": row.telegram_chat_id,
            "username": row.username,
            "title": row.title,
            "invite_url": row.invite_url,
            "required": row.required,
            "active": row.active,
        }


class MembershipChannelUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=255)
    invite_url: str | None = Field(default=None, max_length=1000)
    required: bool | None = None
    active: bool | None = None


@router.patch("/membership/channels/{channel_id}", dependencies=[Depends(admin_gate)])
async def membership_channel_update(channel_id: uuid.UUID, payload: MembershipChannelUpdate, request: Request):
    async with session_scope() as session:
        row = await session.get(MembershipChannel, channel_id)
        if row is None:
            raise HTTPException(status_code=404, detail="کانال پیدا نشد.")
        if payload.title is not None:
            row.title = clean_text(payload.title, 255) or row.title
        if payload.invite_url is not None:
            row.invite_url = payload.invite_url.strip() or None
        if payload.required is not None:
            row.required = payload.required
        if payload.active is not None:
            row.active = payload.active
        await record_admin_action(
            session,
            action="MEMBERSHIP_CHANNEL_UPDATE",
            entity_type="membership_channel",
            entity_id=row.id,
            details={"active": row.active, "required": row.required},
            **_request_meta(request),
        )
        return {"ok": True, "id": str(row.id)}


@router.delete("/membership/channels/{channel_id}", dependencies=[Depends(admin_gate)])
async def membership_channel_delete(channel_id: uuid.UUID, request: Request):
    async with session_scope() as session:
        row = await session.get(MembershipChannel, channel_id)
        if row is None:
            raise HTTPException(status_code=404, detail="کانال پیدا نشد.")
        await session.execute(delete(MembershipChannel).where(MembershipChannel.id == channel_id))
        await record_admin_action(
            session,
            action="MEMBERSHIP_CHANNEL_DELETE",
            entity_type="membership_channel",
            entity_id=None,
            details={"chat_id": row.telegram_chat_id},
            **_request_meta(request),
        )
    return {"ok": True}

