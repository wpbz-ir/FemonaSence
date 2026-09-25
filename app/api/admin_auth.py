from __future__ import annotations

import hmac

from fastapi import HTTPException, Request


def require_admin_token(request: Request) -> None:
    expected = request.app.state.settings.admin_api_token
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="ADMIN_API_TOKEN تنظیم نشده است.",
        )

    header = request.headers.get("Authorization", "")
    if not header.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Authorization لازم است.")

    provided = header[7:].strip()
    if not provided or not hmac.compare_digest(provided, expected):
        raise HTTPException(status_code=403, detail="دسترسی غیرمجاز.")
