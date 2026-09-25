from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import UUID
from app.db.models import AdminActionLog, AuditLog

def _json_safe(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v) for v in value]
    return str(value)

async def record_admin_action(session, *, actor_user_id=None, request_id: str | None = None,
                              action: str, entity_type: str, entity_id=None,
                              method: str | None = None, path: str | None = None,
                              status_code: int | None = None, success: bool = True,
                              details: dict | None = None, ip_address: str | None = None):
    row = AdminActionLog(created_at=datetime.now(timezone.utc), actor_user_id=actor_user_id,
                         request_id=request_id, action=action, entity_type=entity_type,
                         entity_id=entity_id, method=method, path=path, status_code=status_code,
                         success=success, details=_json_safe(details or {}), ip_address=ip_address)
    session.add(row)
    return row

async def record_audit_log(session, *, actor_user_id=None, action: str, entity_type: str,
                           entity_id=None, old_value: dict | None = None,
                           new_value: dict | None = None, ip_address: str | None = None):
    row = AuditLog(created_at=datetime.now(timezone.utc), actor_user_id=actor_user_id,
                   action=action, entity_type=entity_type, entity_id=entity_id,
                   old_value=_json_safe(old_value or {}), new_value=_json_safe(new_value or {}),
                   ip_address=ip_address)
    session.add(row)
    return row
