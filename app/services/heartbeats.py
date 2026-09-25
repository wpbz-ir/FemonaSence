from __future__ import annotations

import json
import os
import socket
import uuid

from sqlalchemy import text

INSTANCE_ID = os.getenv("SERVICE_INSTANCE_ID", "").strip() or (
    f"{socket.gethostname()}-{uuid.uuid4().hex[:10]}"
)


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
            "instance_id": INSTANCE_ID,
            "status": status,
            "metadata": json.dumps(metadata or {}, ensure_ascii=False),
        },
    )


def service_instance_id() -> str:
    return INSTANCE_ID
