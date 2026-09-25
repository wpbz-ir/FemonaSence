from __future__ import annotations

import asyncio

from sqlalchemy import text

from app.bot.app import create_bot
from app.db.models import Base
from app.db.session import engine


async def main() -> None:
    print(f"[1/3] SQLAlchemy metadata tables: {len(Base.metadata.tables)}")
    print("[2/3] Bot factory: OK" if create_bot else "[2/3] Bot factory: FAILED")

    print("[3/3] Database connection:", end=" ")

    async with engine.connect() as conn:
        value = await conn.scalar(text("SELECT 1"))
        print(f"OK (SELECT {value})")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
