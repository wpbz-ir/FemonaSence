from __future__ import annotations

import asyncio
import os
import subprocess
import sys

from dotenv import load_dotenv
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import settings, validate_settings
from app.core.database import async_database_url


def db_url() -> str:
    return async_database_url(settings.database_url)



async def main() -> int:
    load_dotenv()
    problems = validate_settings(strict=False)
    print("=== PRODUCTION PREFLIGHT ===")
    print("Environment:", settings.app_env)
    print("Config problems:", len(problems))
    for item in problems:
        print(" -", item)

    result = subprocess.run(
        [sys.executable, "-m", "alembic", "current"],
        capture_output=True,
        text=True,
    )
    print("Alembic:", (result.stdout or result.stderr).strip())

    db_ok = False
    engine = create_async_engine(db_url(), pool_pre_ping=True)
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
            db_ok = True
    finally:
        await engine.dispose()
    print("Database:", "OK" if db_ok else "ERROR")

    ffmpeg = subprocess.run([os.getenv("FFMPEG_BIN", "ffmpeg"), "-version"], capture_output=True, text=True)
    ffprobe = subprocess.run([os.getenv("FFPROBE_BIN", "ffprobe"), "-version"], capture_output=True, text=True)
    print("FFmpeg:", "OK" if ffmpeg.returncode == 0 else "MISSING")
    print("FFprobe:", "OK" if ffprobe.returncode == 0 else "MISSING")

    print("RESULT:", "READY" if not problems and result.returncode == 0 and db_ok and ffmpeg.returncode == 0 and ffprobe.returncode == 0 else "NOT_READY")
    return 0 if not problems and result.returncode == 0 and db_ok and ffmpeg.returncode == 0 and ffprobe.returncode == 0 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
