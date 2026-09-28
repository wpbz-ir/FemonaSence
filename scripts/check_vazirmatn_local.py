"""Vazirmatn font availability check — RETIRED for local woff2 files.

[P0-10] The admin panel (app/api/templates/admin.html) now loads Vazirmatn from
the Google Fonts CDN, and app/api/static/fonts no longer exists, so there are
no local woff2 files to verify. The check keeps its protective intent: it fails
when the panel's CDN font wiring is missing (which would break the Persian UI
typeface), otherwise it exits 0.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ADMIN_HTML = ROOT / "app" / "api" / "templates" / "admin.html"

print("VAZIRMATN_LOCAL_RETIRED: local woff2 fonts were replaced by the Google Fonts CDN")

required = (
    "https://fonts.googleapis.com",
    "https://fonts.gstatic.com",
    "family=Vazirmatn",
    "font-family:Vazirmatn",
)
html = ADMIN_HTML.read_text(encoding="utf-8")
missing = [needle for needle in required if needle not in html]
if missing:
    raise SystemExit(f"FONT_CDN_REFERENCE_MISSING:{missing}")

print("VAZIRMATN_CDN_OK")
