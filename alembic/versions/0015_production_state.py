"""production deployment state and incident records

Revision ID: 0015_production_state
Revises: 0014_storage_controls
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0015_production_state"
down_revision: Union[str, Sequence[str], None] = "0014_storage_controls"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "service_incidents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("service_name", sa.String(length=64), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False, server_default="INFO"),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="OPEN"),
        sa.Column("summary", sa.String(length=255), nullable=False),
        sa.Column("details", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("opened_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_service_incidents_opened", "service_incidents", ["status", "opened_at"])
    op.create_index("ix_service_incidents_service", "service_incidents", ["service_name", "opened_at"])


def downgrade() -> None:
    op.drop_index("ix_service_incidents_service", table_name="service_incidents")
    op.drop_index("ix_service_incidents_opened", table_name="service_incidents")
    op.drop_table("service_incidents")
