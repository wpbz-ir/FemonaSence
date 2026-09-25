"""durable notification claim leases and attempts

Revision ID: 0012_notification_runtime
Revises: 0011_payment_sessions
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0012_notification_runtime"
down_revision: Union[str, Sequence[str], None] = "0011_payment_sessions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("notification_jobs", sa.Column("locked_by", sa.String(length=128), nullable=True))
    op.add_column("notification_jobs", sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("notification_jobs", sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_notification_jobs_claim", "notification_jobs", ["status", "run_at", "created_at"])
    op.create_index("ix_notification_jobs_lease", "notification_jobs", ["status", "locked_at"])

    op.create_table(
        "notification_attempts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("notification_job_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("notification_jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("telegram_message_id", sa.BigInteger(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.UniqueConstraint("notification_job_id", "attempt_number", name="uq_notification_attempt_job_number"),
    )
    op.create_index("ix_notification_attempts_job_created", "notification_attempts", ["notification_job_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_notification_attempts_job_created", table_name="notification_attempts")
    op.drop_table("notification_attempts")
    op.drop_index("ix_notification_jobs_lease", table_name="notification_jobs")
    op.drop_index("ix_notification_jobs_claim", table_name="notification_jobs")
    op.drop_column("notification_jobs", "last_attempt_at")
    op.drop_column("notification_jobs", "locked_at")
    op.drop_column("notification_jobs", "locked_by")
