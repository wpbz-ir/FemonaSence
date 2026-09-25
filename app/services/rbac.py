from __future__ import annotations

from sqlalchemy import select

from app.db.models import Permission, Role, role_permissions, user_roles


async def has_role(session, user_id, role_name: str) -> bool:
    stmt = (
        select(user_roles.c.user_id)
        .join(Role, Role.id == user_roles.c.role_id)
        .where(
            user_roles.c.user_id == user_id,
            Role.name == role_name,
        )
        .limit(1)
    )
    return (await session.scalar(stmt)) is not None


async def is_super_admin(session, user_id) -> bool:
    return await has_role(session, user_id, "SUPER_ADMIN")


async def has_permission(session, user_id, permission_code: str) -> bool:
    # Permission uses `code`, not `name`.
    stmt = (
        select(Permission.id)
        .join(role_permissions, role_permissions.c.permission_id == Permission.id)
        .join(user_roles, user_roles.c.role_id == role_permissions.c.role_id)
        .where(
            user_roles.c.user_id == user_id,
            Permission.code == permission_code,
        )
        .limit(1)
    )
    return (await session.scalar(stmt)) is not None
