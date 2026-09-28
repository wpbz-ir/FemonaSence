from __future__ import annotations

import json
import os
import socket

from sqlalchemy import text

# ---------------------------------------------------------------------------
# [P1-20] Instance identity — computed ONCE at import.
#
# The service_heartbeats table is keyed by (service_name, instance_id) with an
# ON CONFLICT upsert (uq_service_heartbeats_service_instance, migration 0009;
# ORM: app/db/models/operations.py), so instance_id MUST be stable across
# restarts. The previous fallback mixed a random uuid suffix into the id, so
# every restart piled up a fresh "ghost" row per service that only the 14-day
# cleanup (app/workers/maintenance.py) ever removed.
#
# Identity granularity is per-SERVICE (not per-process): every caller passes
# its own service_name — "web" (app/api/main.py startup/shutdown),
# "media_worker" (app/workers/media_worker.py), "bot_maintenance"
# (app/workers/maintenance.py) — and the fallback is a pure function of
# hostname + that service name, so each service keeps exactly ONE upserted
# heartbeat row per host and restarts refresh the same row.
#
# Precedence:
#   1. SERVICE_INSTANCE_ID env var (documented in .env.example) — set it when
#      several instances of the SAME service share one host;
#   2. otherwise "<hostname>:<service_name>".
# ---------------------------------------------------------------------------
_HOSTNAME: str = socket.gethostname()
_ENV_INSTANCE_ID: str = os.getenv("SERVICE_INSTANCE_ID", "").strip()


def service_instance_id(service_name: str = "web") -> str:
    """Stable heartbeat identity for a service; env override wins."""
    if _ENV_INSTANCE_ID:
        return _ENV_INSTANCE_ID
    return f"{_HOSTNAME}:{service_name}"


# Process-level identity, computed once. Kept for callers without a service
# context: app/api/admin.py GET /instance self-reports the "web" identity,
# which matches the row the web process writes via heartbeat().
INSTANCE_ID: str = service_instance_id()


async def heartbeat(
    session,
    *,
    service_name: str,
    status: str = "UP",
    metadata: dict | None = None,
):
    await session.execute(
        text(
            """
            INSERT INTO service_heartbeats
              (id, created_at, updated_at, service_name, instance_id, last_seen_at, status, metadata)
            VALUES
              (gen_random_uuid(), CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, :service_name, :instance_id,
               CURRENT_TIMESTAMP, :status, CAST(:metadata AS jsonb))
            ON CONFLICT (service_name, instance_id)
            DO UPDATE SET
              updated_at = CURRENT_TIMESTAMP,
              last_seen_at = CURRENT_TIMESTAMP,
              status = EXCLUDED.status,
              metadata = EXCLUDED.metadata
            """
        ),
        {
            "service_name": service_name,
            "instance_id": service_instance_id(service_name),
            "status": status,
            "metadata": json.dumps(metadata or {}, ensure_ascii=False),
        },
    )
