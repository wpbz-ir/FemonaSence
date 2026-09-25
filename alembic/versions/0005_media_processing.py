"""durable media processing and transcode jobs

Revision ID: 0005_media_processing
Revises: 0004_premium_ux_watch_progress
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0005_media_processing"
down_revision = "0004_premium_ux_watch_progress"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "media_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("job_type", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="QUEUED"),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="50"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("locked_by", sa.String(128), nullable=True),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_release_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_storage_file_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_release_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("target_quality", sa.String(16), nullable=False),
        sa.Column("output_storage_file_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("work_dir", sa.Text(), nullable=True),
        sa.Column("input_path", sa.Text(), nullable=True),
        sa.Column("output_path", sa.Text(), nullable=True),
        sa.Column("progress_percent", sa.Numeric(5, 2), nullable=False, server_default="0"),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("idempotency_key", sa.String(220), nullable=False),
        sa.Column("metadata", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["source_release_id"], ["releases.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_storage_file_id"], ["storage_files.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["target_release_id"], ["releases.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["output_storage_file_id"], ["storage_files.id"], ondelete="SET NULL"),
        sa.CheckConstraint("job_type IN ('TRANSCODE', 'PROBE', 'THUMBNAIL')", name="ck_media_jobs_job_type"),
        sa.CheckConstraint("status IN ('QUEUED', 'RUNNING', 'RETRY', 'SUCCEEDED', 'FAILED', 'CANCELLED')", name="ck_media_jobs_status"),
        sa.CheckConstraint("attempts >= 0", name="ck_media_jobs_attempts_nonnegative"),
        sa.CheckConstraint("max_attempts BETWEEN 1 AND 10", name="ck_media_jobs_max_attempts"),
        sa.CheckConstraint("progress_percent BETWEEN 0 AND 100", name="ck_media_jobs_progress"),
        sa.UniqueConstraint("idempotency_key", name="uq_media_jobs_idempotency_key"),
    )
    op.create_index(
        "ix_media_jobs_claim",
        "media_jobs",
        ["status", "available_at", "priority", "created_at"],
    )
    op.create_index("ix_media_jobs_release", "media_jobs", ["source_release_id", "target_release_id"])
    op.create_index("ix_media_jobs_worker_lease", "media_jobs", ["status", "locked_at"])

    op.create_table(
        "media_job_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("data", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["job_id"], ["media_jobs.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_media_job_events_job_created", "media_job_events", ["job_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_media_job_events_job_created", table_name="media_job_events")
    op.drop_table("media_job_events")
    op.drop_index("ix_media_jobs_worker_lease", table_name="media_jobs")
    op.drop_index("ix_media_jobs_release", table_name="media_jobs")
    op.drop_index("ix_media_jobs_claim", table_name="media_jobs")
    op.drop_table("media_jobs")
