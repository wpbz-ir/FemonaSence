from __future__ import annotations

import json
import os
from html import escape
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.runtime.db import session_scope
from app.services.access import can_access_release
from app.services.experience import resolve_media_token
from app.services.media_proxy import proxy_local_file, proxy_media
from app.services.telegram_media import TelegramMediaClient, TelegramMediaError
from app.services.rate_limit import RateLimitExceeded, RateLimitUnavailable, enforce
from app.services.release_matrix import get_release_variant, stream_source
from app.services.watch_progress import get_watch_progress, save_watch_progress


router = APIRouter(tags=["media"])


def _client_ip(request: Request) -> str:
    return (request.client.host if request.client else "")[:64]


def _user_agent(request: Request) -> str:
    return request.headers.get("user-agent", "")[:255]


def _premium_watch_party_allowed() -> bool:
    return os.getenv("ALLOW_PREMIUM_WATCH_PARTY", "0").strip().lower() in {"1", "true", "yes", "on"}


async def _resolve_telegram_source(row: dict) -> dict | None:
    file_id = str(row.get("storage_file_id_value") or "").strip()
    if not file_id:
        return None
    client = TelegramMediaClient(
        token=os.getenv("BOT_TOKEN", "").strip(),
        base_url=os.getenv("TELEGRAM_BOT_API_BASE_URL", "https://api.telegram.org").rstrip("/"),
    )
    try:
        return await client.get_stream_source(file_id)
    except TelegramMediaError:
        return None


async def _resolve_access(session, token: str, request: Request):
    try:
        await enforce(f"media:{token[:24]}", limit=180, window_seconds=60)
    except RateLimitExceeded as exc:
        raise HTTPException(status_code=429, detail="تعداد درخواست‌ها بیش از حد مجاز است.") from exc
    except RateLimitUnavailable as exc:
        raise HTTPException(status_code=503, detail="سامانه محدودکننده درخواست در دسترس نیست.") from exc

    access = await resolve_media_token(
        session,
        token,
        client_ip=_client_ip(request),
        user_agent=_user_agent(request),
    )
    if not access:
        raise HTTPException(status_code=410, detail="لینک پخش منقضی یا نامعتبر است.")
    allowed, reason = await can_access_release(session, access["user_id"], access["release_id"])
    if not allowed:
        raise HTTPException(status_code=403, detail=reason)
    return access


class ProgressPayload(BaseModel):
    position_seconds: float = Field(ge=0, le=864000)
    duration_seconds: float | None = Field(default=None, ge=1, le=864000)


class PartyState(BaseModel):
    position_seconds: float = Field(ge=0, le=864000)
    is_playing: bool


async def _party(session, token: str):
    row = (
        await session.execute(
            text(
                """
                SELECT p.id, p.host_user_id, p.title_id, p.release_id, p.current_position_seconds,
                       p.is_playing, p.expires_at,
                       COUNT(m.id) AS member_count
                FROM watch_parties p
                LEFT JOIN watch_party_members m ON m.party_id = p.id
                WHERE p.invite_token = :token AND p.status = 'ACTIVE' AND p.expires_at > CURRENT_TIMESTAMP
                GROUP BY p.id
                """
            ),
            {"token": token},
        )
    ).mappings().first()
    return dict(row) if row else None


def _security_headers(response: Response) -> Response:
    response.headers["Cache-Control"] = "private, no-store, max-age=0"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive"
    return response


@router.get("/media/access/{token}", response_class=HTMLResponse)
async def media_access(token: str, request: Request):
    async with session_scope() as session:
        access = await _resolve_access(session, token, request)
        if not access:
            raise HTTPException(status_code=410, detail="لینک پخش منقضی یا نامعتبر است.")
        release = await get_release_variant(session, release_id=access["release_id"])
        source = stream_source(release or {})
        telegram_source = await _resolve_telegram_source(release or {}) if release and not source else None
        progress = None
        if release:
            progress = await get_watch_progress(
                session,
                user_id=access["user_id"],
                title_id=release.get("content_title_id") or release.get("title_id"),
            )

    if (not source and not telegram_source) or not release:
        return _security_headers(HTMLResponse(
            "<meta charset='utf-8'><body dir='rtl' style='font-family:system-ui;background:#0b1220;color:#e2e8f0;padding:40px'>"
            "<h2>پخش آنلاین هنوز برای این نسخه فعال نشده است.</h2><p>نسخه دانلودی در ربات قابل دریافت است.</p></body>",
            status_code=409,
        ))

    title = escape(str(release.get("title_fa") or release.get("title_en") or release.get("original_title") or "پخش آنلاین"), quote=False)
    poster = escape(str(release.get("title_poster_url") or ""), quote=True)
    initial_progress = float(progress.get("position_seconds") or 0) if progress and not progress.get("completed") else 0.0
    poster_attr = f" poster='{poster}'" if poster.startswith("https://") else ""
    html = f"""
    <!doctype html><html lang='fa' dir='rtl'><head><meta charset='utf-8'>
    <meta name='viewport' content='width=device-width,initial-scale=1,viewport-fit=cover'>
    <meta name='robots' content='noindex,nofollow,noarchive'><title>فمونا سنس | {title}</title>
    <style>
      :root{{color-scheme:dark}} body{{margin:0;background:#07101d;color:#e5edf7;font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif}}
      .wrap{{max-width:1100px;margin:0 auto;padding:16px}} .hero{{display:flex;justify-content:space-between;align-items:center;gap:10px;margin-bottom:12px}}
      .brand{{font-weight:900}} .hint{{font-size:13px;color:#9eb0c4}} .card{{background:#0f1b2d;border:1px solid #24354b;border-radius:20px;padding:14px;box-shadow:0 18px 60px rgba(0,0,0,.28)}}
      video{{display:block;width:100%;max-height:76vh;background:#000;border-radius:16px}} .meta{{display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap;margin-top:12px;color:#a9b8ca;font-size:13px}}
      .notice{{margin-top:10px;padding:10px 12px;background:#102137;border-radius:12px;color:#c5d6e8;font-size:13px}}
    </style></head><body><div class='wrap'><div class='hero'><div class='brand'>🎬 فمونا سنس</div><div class='hint'>لینک دسترسی زمان‌دار است</div></div>
    <div class='card'><h2 style='margin:0 0 12px'>▶️ {title}</h2>
    <video id='player' controls playsinline disablepictureinpicture controlslist='nodownload noplaybackrate noremoteplayback' preload='metadata'{poster_attr} src='/media/source/{escape(token)}'></video>
    <div class='meta'><span id='progress'>در حال آماده‌سازی…</span><span>موقعیت تماشا به‌صورت خودکار ذخیره می‌شود.</span></div>
    <div class='notice'>پخش اختصاصی فمونا سنس — دانلود از طریق این پخش‌کننده غیرفعال است. لینک دسترسی پس از پایان زمان اعتبار دوباره قابل استفاده نیست.</div>
    </div></div>
    <script>
    const token={json.dumps(token)}; const p=document.getElementById('player'); const box=document.getElementById('progress');
    // غیرفعال‌سازی دانلود: منوی راست‌کلیک، کشیدن‌ورهاکردن و کلیدهای ذخیره
    document.addEventListener('contextmenu',e=>e.preventDefault());
    document.addEventListener('dragstart',e=>e.preventDefault());
    document.addEventListener('keydown',e=>{{if((e.ctrlKey||e.metaKey)&&['s','u'].includes((e.key||'').toLowerCase()))e.preventDefault();}});
    let lastSave=0; let restored=false;
    async function loadProgress(){{
      try{{const r=await fetch('/media/progress/'+encodeURIComponent(token),{{cache:'no-store'}}); if(!r.ok)return; const s=await r.json();
      if(!restored && Number.isFinite(s.position_seconds) && s.position_seconds>5){{p.currentTime=s.position_seconds; restored=true; box.textContent='⏯️ ادامه از '+fmt(s.position_seconds);}}
      }}catch(e){{}}
    }}
    function fmt(v){{v=Math.max(0,Math.floor(v||0)); const m=Math.floor(v/60); const s=v%60; return String(m).padStart(2,'0')+':'+String(s).padStart(2,'0')}}
    async function save(force=false){{
      const now=Date.now(); if(!force && now-lastSave<5000)return; lastSave=now;
      try{{await fetch('/media/progress/'+encodeURIComponent(token),{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{position_seconds:p.currentTime,duration_seconds:Number.isFinite(p.duration)&&p.duration>0?p.duration:null}})}}); box.textContent='⏱️ '+fmt(p.currentTime);}}catch(e){{}}
    }}
    ['pause','seeked'].forEach(ev=>p.addEventListener(ev,()=>save(true))); p.addEventListener('timeupdate',()=>save(false)); window.addEventListener('beforeunload',()=>save(true));
    loadProgress();
    </script></body></html>
    """
    return _security_headers(HTMLResponse(html))


@router.get("/media/source/{token}")
async def media_source(token: str, request: Request):
    async with session_scope() as session:
        access = await _resolve_access(session, token, request)
        release = await get_release_variant(session, release_id=access["release_id"])
        source = stream_source(release or {})
    telegram_source = await _resolve_telegram_source(release or {}) if release and not source else None
    if not source and not telegram_source:
        raise HTTPException(status_code=409, detail="منبع پخش آنلاین برای این نسخه ثبت نشده است.")
    try:
        if telegram_source and telegram_source.get("kind") == "local":
            response = await proxy_local_file(telegram_source["path"], request)
        else:
            response = await proxy_media(source or telegram_source["url"], request)
    except ValueError as exc:
        raise HTTPException(status_code=503, detail="منبع پخش برای این سرور مجاز نیست.") from exc
    except PermissionError as exc:
        raise HTTPException(status_code=502, detail="منبع پخش پاسخ معتبر نداد.") from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail="ارتباط با منبع پخش برقرار نشد.") from exc
    return _security_headers(response)


@router.get("/media/progress/{token}")
async def media_progress(token: str, request: Request):
    async with session_scope() as session:
        access = await _resolve_access(session, token, request)
        if not access:
            raise HTTPException(status_code=410, detail="لینک پخش منقضی یا نامعتبر است.")
        release = await get_release_variant(session, release_id=access["release_id"])
        if not release:
            raise HTTPException(status_code=404, detail="نسخه پیدا نشد.")
        progress = await get_watch_progress(
                session,
                user_id=access["user_id"],
                title_id=release.get("content_title_id") or release.get("title_id"),
            )
    result = progress or {"position_seconds": 0, "duration_seconds": None, "completed": False}
    response = JSONResponse({
        "position_seconds": float(result.get("position_seconds") or 0),
        "duration_seconds": float(result["duration_seconds"]) if result.get("duration_seconds") is not None else None,
        "completed": bool(result.get("completed", False)),
    })
    return _security_headers(response)


@router.post("/media/progress/{token}")
async def save_media_progress(token: str, progress: ProgressPayload, request: Request):
    async with session_scope() as session:
        access = await _resolve_access(session, token, request)
        if not access:
            raise HTTPException(status_code=410, detail="لینک پخش منقضی یا نامعتبر است.")
        release = await get_release_variant(session, release_id=access["release_id"])
        if not release:
            raise HTTPException(status_code=404, detail="نسخه پیدا نشد.")
        saved = await save_watch_progress(
            session,
            user_id=access["user_id"],
            title_id=release.get("content_title_id") or release.get("title_id"),
            release_id=access["release_id"],
            position_seconds=progress.position_seconds,
            duration_seconds=progress.duration_seconds,
        )
        # همگام‌سازی تاریخچه کلاسیک (watch_history) تا منوی «تاریخچه» و «ادامه تماشا» ربات هم به‌روز شود
        content_title = release.get("content_title_id") or release.get("title_id")
        if content_title:
            await session.execute(
                text(
                    """
                    INSERT INTO watch_history (id, user_id, title_id, progress_seconds, completed, last_watched_at)
                    VALUES (gen_random_uuid(), :user_id, :title_id, :progress, :completed, CURRENT_TIMESTAMP)
                    ON CONFLICT (user_id, title_id)
                    DO UPDATE SET
                        progress_seconds = EXCLUDED.progress_seconds,
                        completed = EXCLUDED.completed,
                        last_watched_at = CURRENT_TIMESTAMP
                    """
                ),
                {
                    "user_id": access["user_id"],
                    "title_id": content_title,
                    "progress": int(saved["position_seconds"] or 0),
                    "completed": bool(saved["completed"]),
                },
            )
    return {"ok": True, "position_seconds": float(saved["position_seconds"]), "completed": bool(saved["completed"])}


@router.get("/media/party/{token}")
async def party_media(token: str, request: Request):
    try:
        await enforce(f"party:{token[:24]}", limit=240, window_seconds=60)
    except RateLimitExceeded as exc:
        raise HTTPException(status_code=429, detail="تعداد درخواست‌ها بیش از حد مجاز است.") from exc
    except RateLimitUnavailable as exc:
        raise HTTPException(status_code=503, detail="سامانه محدودکننده درخواست در دسترس نیست.") from exc

    async with session_scope() as session:
        party = await _party(session, token)
        if not party:
            raise HTTPException(status_code=410, detail="اتاق تماشای گروهی منقضی شده است.")
        release = await get_release_variant(session, release_id=party["release_id"])
        allowed, _reason = await can_access_release(session, party["host_user_id"], party["release_id"])
        if not allowed:
            raise HTTPException(status_code=403, detail="دسترسی میزبان به این نسخه معتبر نیست.")
        if not _premium_watch_party_allowed():
            policy = await session.scalar(
                text("SELECT requires_subscription FROM access_policies WHERE release_id=:release_id"),
                {"release_id": party["release_id"]},
            )
            if policy:
                raise HTTPException(status_code=403, detail="تماشای گروهی برای محتوای اشتراکی غیرفعال است.")
        source = stream_source(release or {})
    telegram_source = await _resolve_telegram_source(release or {}) if release and not source else None
    if not source and not telegram_source:
        raise HTTPException(status_code=409, detail="منبع پخش آنلاین برای این نسخه ثبت نشده است.")
    try:
        if telegram_source and telegram_source.get("kind") == "local":
            response = await proxy_local_file(telegram_source["path"], request)
        else:
            response = await proxy_media(source or telegram_source["url"], request)
    except ValueError as exc:
        raise HTTPException(status_code=503, detail="منبع پخش برای این سرور مجاز نیست.") from exc
    except PermissionError as exc:
        raise HTTPException(status_code=502, detail="منبع پخش پاسخ معتبر نداد.") from exc
    return _security_headers(response)


@router.get("/watch/{token}", response_class=HTMLResponse)
async def watch_party_page(token: str):
    async with session_scope() as session:
        party = await _party(session, token)
        if not party:
            raise HTTPException(status_code=410, detail="اتاق تماشای گروهی منقضی شده است.")
        release = await get_release_variant(session, release_id=party["release_id"])
        allowed, _reason = await can_access_release(session, party["host_user_id"], party["release_id"])
        if not allowed:
            raise HTTPException(status_code=403, detail="دسترسی میزبان به این نسخه معتبر نیست.")
        if not _premium_watch_party_allowed():
            policy = await session.scalar(
                text("SELECT requires_subscription FROM access_policies WHERE release_id=:release_id"),
                {"release_id": party["release_id"]},
            )
            if policy:
                raise HTTPException(status_code=403, detail="تماشای گروهی برای محتوای اشتراکی غیرفعال است.")
        source = stream_source(release or {})
    telegram_source = await _resolve_telegram_source(release or {}) if release and not source else None
    title = escape(str((release or {}).get("title_fa") or (release or {}).get("title_en") or "تماشای گروهی"), quote=False)
    if not source and not telegram_source:
        return _security_headers(HTMLResponse(f"<meta charset='utf-8'><body dir='rtl'><h2>{title}</h2><p>منبع ویدئو آماده نیست.</p></body>", status_code=409))
    html = f"""
    <!doctype html><html lang='fa' dir='rtl'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
    <meta name='robots' content='noindex,nofollow,noarchive'><title>فمونا سنس | {title}</title>
    <style>body{{margin:0;background:#07101d;color:#e5edf7;font-family:system-ui,sans-serif}}.wrap{{max-width:1100px;margin:auto;padding:16px}}.card{{background:#0f1b2d;border:1px solid #24354b;border-radius:20px;padding:14px}}video{{width:100%;max-height:75vh;background:#000;border-radius:16px}}.meta{{display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap;margin-top:12px;color:#a9b8ca;font-size:13px}}</style>
    </head><body><div class='wrap'><div class='card'><h2>👥 {title}</h2><video id='player' controls playsinline disablepictureinpicture controlslist='nodownload noplaybackrate noremoteplayback' src='/media/party/{escape(token)}'></video><div class='meta'><span id='status'>در حال اتصال…</span><span>اعضای اتاق: {int(party.get('member_count') or 1)}</span></div></div></div>
    <script>
    document.addEventListener('contextmenu',e=>e.preventDefault());
    document.addEventListener('dragstart',e=>e.preventDefault());
    const token={json.dumps(token)}; const p=document.getElementById('player'); const status=document.getElementById('status'); let applying=false; let last=0;
    async function getState(){{try{{const r=await fetch('/watch/'+encodeURIComponent(token)+'/state',{{cache:'no-store'}});if(!r.ok)return;const s=await r.json();if(!applying&&Number.isFinite(s.position_seconds)&&Math.abs(p.currentTime-s.position_seconds)>2)p.currentTime=s.position_seconds;if(s.is_playing&&p.paused){{applying=true;try{{await p.play()}}catch(e){{}}finally{{applying=false}}}}if(!s.is_playing&&!p.paused)p.pause();status.textContent=s.is_playing?'▶️ در حال پخش':'⏸️ متوقف'}}catch(e){{}}}}
    async function pushState(){{const now=Date.now();if(now-last<900)return;last=now;try{{await fetch('/watch/'+encodeURIComponent(token)+'/state',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{position_seconds:p.currentTime,is_playing:!p.paused}})}})}}catch(e){{}}}}
    ['play','pause','seeked'].forEach(ev=>p.addEventListener(ev,pushState));setInterval(getState,1500);getState();
    </script></body></html>
    """
    return _security_headers(HTMLResponse(html))


@router.get("/watch/{token}/state")
async def party_state(token: str):
    try:
        await enforce(f"party:{token[:24]}", limit=240, window_seconds=60)
    except RateLimitExceeded as exc:
        raise HTTPException(status_code=429, detail="تعداد درخواست‌ها بیش از حد مجاز است.") from exc
    except RateLimitUnavailable as exc:
        raise HTTPException(status_code=503, detail="سامانه محدودکننده درخواست در دسترس نیست.") from exc
    async with session_scope() as session:
        party = await _party(session, token)
    if not party:
        raise HTTPException(status_code=410, detail="اتاق تماشای گروهی منقضی شده است.")
    response = JSONResponse({"position_seconds": float(party["current_position_seconds"] or 0), "is_playing": bool(party["is_playing"])})
    return _security_headers(response)


@router.post("/watch/{token}/state")
async def set_party_state(token: str, state: PartyState):
    try:
        await enforce(f"party:{token[:24]}", limit=240, window_seconds=60)
    except RateLimitExceeded as exc:
        raise HTTPException(status_code=429, detail="تعداد درخواست‌ها بیش از حد مجاز است.") from exc
    except RateLimitUnavailable as exc:
        raise HTTPException(status_code=503, detail="سامانه محدودکننده درخواست در دسترس نیست.") from exc
    async with session_scope() as session:
        party = await _party(session, token)
        if not party:
            raise HTTPException(status_code=410, detail="اتاق تماشای گروهی منقضی شده است.")
        result = await session.execute(
            text(
                """
                UPDATE watch_parties
                SET current_position_seconds = :position_seconds, is_playing = :is_playing, updated_at = CURRENT_TIMESTAMP
                WHERE invite_token = :token AND status = 'ACTIVE' AND expires_at > CURRENT_TIMESTAMP
                """
            ),
            {"token": token, "position_seconds": state.position_seconds, "is_playing": state.is_playing},
        )
        if result.rowcount != 1:
            raise HTTPException(status_code=409, detail="وضعیت اتاق تغییر کرده یا منقضی شده است.")
    return {"ok": True}
