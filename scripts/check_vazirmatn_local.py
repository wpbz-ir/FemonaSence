from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FONT_DIR = ROOT / "app" / "api" / "static" / "fonts"
REQUIRED = ("Vazirmatn-Regular.woff2", "Vazirmatn-Bold.woff2")

for name in REQUIRED:
    path = FONT_DIR / name
    if not path.is_file():
        raise SystemExit(f"FONT_MISSING:{name}")
    data = path.read_bytes()
    if len(data) < 4 or data[:4] != b"wOF2":
        raise SystemExit(f"FONT_INVALID_WOFF2:{name}")

print("VAZIRMATN_LOCAL_OK")
