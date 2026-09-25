from __future__ import annotations

import asyncio
import os

from dotenv import load_dotenv
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.database import async_database_url
from app.core.media_config import load_media_settings
from app.services.media_jobs import recover_stale_jobs


async def main() -> None:
    load_dotenv()
    settings = load_media_settings()
    engine = create_async_engine(
        async_database_url(os.getenv("DATABASE_URL", "")),
        pool_pre_ping=True,
    )
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as session:
            count = await recover_stale_jobs(
                session,
                lease_seconds=settings.lease_seconds,
            )
            await session.commit()
            print(f"STALE_MEDIA_JOBS_REQUEUED {count}")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
