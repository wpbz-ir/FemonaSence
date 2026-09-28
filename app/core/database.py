from __future__ import annotations

from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import SQLAlchemyError


def _sanitized_url_error(reason: str) -> RuntimeError:
    """[INT-c/M2] Build a DATABASE_URL error that NEVER embeds the raw URL.

    SQLAlchemy's make_url()/ArgumentError messages include the full connection
    string verbatim (user AND password included). Every error leaving this
    module is therefore built from static text only; the offending value is
    never interpolated, and exception chaining is suppressed (``raise ... from
    None``) so the original message cannot leak through the traceback either.
    """
    return RuntimeError(f"DATABASE_URL is malformed or unsupported ({reason}).")


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

    # [INT-c/M2] make_url() raises SQLAlchemyError (ArgumentError) whose message
    # embeds the FULL raw URL incl. the password — never let it escape. The
    # sanitized error is raised OUTSIDE the except handler so the suppressed
    # SQLAlchemyError is not attached at all (``from None`` only hides the
    # traceback display; __context__ would still hold the leaky message for
    # programmatic introspection).
    parsed_url: URL | None = None
    try:
        parsed_url = make_url(raw)
    except SQLAlchemyError:
        parsed_url = None
    if parsed_url is None:
        raise _sanitized_url_error("could not be parsed")

    url: URL = parsed_url

    if url.drivername not in {
        "postgresql",
        "postgresql+psycopg",
        "postgresql+psycopg_async",
        "postgresql+asyncpg",
    }:
        # Static text only: the drivername itself is safe to name, the URL is not.
        raise RuntimeError(
            "DATABASE_URL must use postgresql://, postgresql+psycopg://, "
            "postgresql+psycopg_async://, or postgresql+asyncpg://."
        )

    query = dict(url.query)
    sslmode = query.pop("sslmode", None)
    query.pop("channel_binding", None)
    if sslmode is not None and "ssl" not in query:
        query["ssl"] = sslmode

    # NOTE [INT-c/M2 audit of render_as_string(hide_password=False)]: this is the
    # SINGLE credential-bearing render in the module and it is the FUNCTION'S
    # CONTRACT — the returned string is fed straight into create_async_engine()
    # (app/db/session.py, media worker, scripts), and asyncpg has no out-of-band
    # password channel, so hiding the password here would silently break every
    # connection (asyncpg does not read PGPASSWORD). It must therefore never be
    # LOGGED or PRINTED: callers treat it as a secret (verified: none of the
    # call sites log it; error paths above are static-text-only).
    return url.set(
        drivername="postgresql+asyncpg",
        query=query,
    ).render_as_string(hide_password=False)


def async_engine_kwargs_from_url(raw_url: str) -> dict:
    """[INT-c/M3] Shared create_async_engine kwargs for ad-hoc async engines.

    app/db/session.py keeps its own engine (its own pool sizing + echo) and is
    intentionally untouched; this helper centralizes ONLY the pieces every
    secondary engine (media worker + scripts/requeue_stale_media_jobs.py,
    scripts/enqueue_quality_matrix.py, scripts/check_production.py) previously
    got wrong:

    - Neon's "-pooler" host = transaction-mode PgBouncer which cannot serve
      asyncpg prepared statements -> statement_cache_size=0 (the same guard
      app/db/session.py applies);
    - pool_pre_ping + pool_recycle so dropped Neon/Windows connections are
      recycled instead of surfacing as one-shot job failures.

    Returns a dict consumed as ``create_async_engine(**async_engine_kwargs_from_url(raw), ...)``.
    Callers add their own pool_size/max_overflow as needed.
    """
    url = async_database_url(raw_url)
    connect_args: dict = {"timeout": 20}
    if "-pooler" in url:
        # Neon's PgBouncer (transaction mode) cannot serve asyncpg prepared statements.
        connect_args["statement_cache_size"] = 0
    return {
        "url": url,
        "connect_args": connect_args,
        "pool_pre_ping": True,
        "pool_recycle": 900,
    }
