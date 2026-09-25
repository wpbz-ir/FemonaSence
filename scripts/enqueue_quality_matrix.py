from __future__ import annotations

import argparse
import asyncio
import os
from uuid import UUID

from dotenv import load_dotenv
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.database import async_database_url
from app.services.media_jobs import enqueue_transcode


async def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(
        description="Queue a release quality matrix for Media Worker"
    )
    parser.add_argument("--source-release", required=True)
    parser.add_argument("--qualities", default="480,720,1080")
    parser.add_argument("--priority", type=int, default=50)
    args = parser.parse_args()

    engine = create_async_engine(
        async_database_url(os.getenv("DATABASE_URL", "")),
        pool_pre_ping=True,
    )
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    source_release_id = UUID(args.source_release)
    qualities = [
        quality.strip().lower().removesuffix("p")
        for quality in args.qualities.split(",")
        if quality.strip()
    ]

    try:
        async with sessions() as session:
            source = (
                await session.execute(
                    text(
                        """
                        SELECT r.id, rf.storage_file_id
                        FROM releases r
                        JOIN release_files rf
                          ON rf.release_id = r.id
                         AND rf.active IS TRUE
                         AND rf.is_primary IS TRUE
                        WHERE r.id = :id
                        LIMIT 1
                        """
                    ),
                    {"id": source_release_id},
                )
            ).mappings().first()
            if not source:
                raise SystemExit("Source release or primary StorageFile not found.")

            title_id = await session.scalar(
                text("SELECT title_id FROM releases WHERE id=:id"),
                {"id": source_release_id},
            )

            for quality in qualities:
                target = await session.execute(
                    text(
                        """
                        SELECT id FROM releases
                        WHERE title_id=:title_id
                          AND REPLACE(LOWER(quality), 'p', '')=:quality
                        ORDER BY priority DESC NULLS LAST,
                                 created_at DESC NULLS LAST
                        LIMIT 1
                        """
                    ),
                    {"title_id": title_id, "quality": quality},
                )
                target_release_id = target.scalar_one_or_none()
                if target_release_id is None:
                    print(f"MISSING_TARGET_RELEASE {quality}p")
                    continue

                queued = await enqueue_transcode(
                    session,
                    source_release_id=source_release_id,
                    source_storage_file_id=source["storage_file_id"],
                    target_release_id=target_release_id,
                    target_quality=quality,
                    priority=args.priority,
                )
                print(f"QUEUED {quality}p {queued['id']} {queued['status']}")

            await session.commit()
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
