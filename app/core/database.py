from __future__ import annotations

from sqlalchemy.engine import URL, make_url


def async_database_url(raw_url: str) -> str:
    """Normalize supported PostgreSQL URLs for SQLAlchemy's asyncpg dialect.

    The deployment keeps the original DATABASE_URL untouched so Alembic may
    continue to use its synchronous psycopg connection. Async application
    engines call this function and receive a postgresql+asyncpg URL.
    """
    raw = (raw_url or "").strip()
    if not raw:
        raise RuntimeError("DATABASE_URL is not configured.")

    if raw.startswith("postgres://"):
        raw = "postgresql://" + raw[len("postgres://"):]

    url: URL = make_url(raw)
    if url.drivername not in {
        "postgresql",
        "postgresql+psycopg",
        "postgresql+psycopg_async",
        "postgresql+asyncpg",
    }:
        raise RuntimeError(
            "DATABASE_URL must use postgresql://, postgresql+psycopg://, "
            "postgresql+psycopg_async://, or postgresql+asyncpg://."
        )

    query = dict(url.query)
    sslmode = query.pop("sslmode", None)
    query.pop("channel_binding", None)
    if sslmode is not None and "ssl" not in query:
        query["ssl"] = sslmode

    return url.set(
        drivername="postgresql+asyncpg",
        query=query,
    ).render_as_string(hide_password=False)
