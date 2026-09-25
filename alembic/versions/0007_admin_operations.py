"""durable admin operation audit trail

Revision ID: 0007_admin_operations
Revises: 0006_content_pipeline
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0007_admin_operations"
down_revision: Union[str, Sequence[str], None] = "0006_content_pipeline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "admin_action_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("request_id", sa.String(length=128), nullable=True),
        sa.Column("action", sa.String(length=100), nullable=False),
        sa.Column("entity_type", sa.String(length=64), nullable=False),
        sa.Column("entity_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("method", sa.String(length=16), nullable=True),
        sa.Column("path", sa.Text(), nullable=True),
        sa.Column("status_code", sa.Integer(), nullable=True),
        sa.Column("success", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("details", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("ip_address", sa.String(length=64), nullable=True),
    )
    op.create_index("ix_admin_action_logs_created", "admin_action_logs", ["created_at"])
    op.create_index("ix_admin_action_logs_actor_created", "admin_action_logs", ["actor_user_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_admin_action_logs_actor_created", table_name="admin_action_logs")
    op.drop_index("ix_admin_action_logs_created", table_name="admin_action_logs")
    op.drop_table("admin_action_logs")
