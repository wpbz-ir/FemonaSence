"""production service heartbeats

Revision ID: 0009_production_hardening
Revises: 0008_secure_playback
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0009_production_hardening"
down_revision: Union[str, Sequence[str], None] = "0008_secure_playback"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "media_jobs",
        sa.Column("cancel_requested", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_index("ix_media_jobs_cancel_requested", "media_jobs", ["cancel_requested", "status"])

    op.create_table(
        "service_heartbeats",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("service_name", sa.String(length=64), nullable=False),
        sa.Column("instance_id", sa.String(length=128), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="UP"),
        sa.Column("metadata", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.UniqueConstraint("service_name", "instance_id", name="uq_service_heartbeats_service_instance"),
    )
    op.create_index("ix_service_heartbeats_last_seen", "service_heartbeats", ["last_seen_at"])


def downgrade() -> None:
    op.drop_index("ix_service_heartbeats_last_seen", table_name="service_heartbeats")
    op.drop_table("service_heartbeats")
    op.drop_index("ix_media_jobs_cancel_requested", table_name="media_jobs")
    op.drop_column("media_jobs", "cancel_requested")
