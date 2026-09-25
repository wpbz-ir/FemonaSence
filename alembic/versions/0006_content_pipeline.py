"""content pipeline runs, quality matrix identity and pipeline tracking

Revision ID: 0006_content_pipeline
Revises: 0005_media_processing
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0006_content_pipeline"
down_revision: Union[str, Sequence[str], None] = "0005_media_processing"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("releases", sa.Column("pipeline_key", sa.String(length=220), nullable=True))
    op.create_index(
        "uq_releases_pipeline_key",
        "releases",
        ["pipeline_key"],
        unique=True,
        postgresql_where=sa.text("pipeline_key IS NOT NULL"),
    )

    op.create_table(
        "content_pipeline_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("title_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("titles.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_release_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("releases.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("source_storage_file_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("storage_files.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("requested_qualities", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="QUEUED"),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("dedupe_key", sa.String(length=220), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("dedupe_key", name="uq_content_pipeline_runs_dedupe"),
    )
    op.create_index("ix_content_pipeline_runs_title_created", "content_pipeline_runs", ["title_id", "created_at"])
    op.create_index("ix_content_pipeline_runs_status", "content_pipeline_runs", ["status", "created_at"])

    op.create_table(
        "content_pipeline_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("content_pipeline_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("quality", sa.String(length=16), nullable=False),
        sa.Column("target_release_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("releases.id", ondelete="SET NULL"), nullable=True),
        sa.Column("media_job_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("media_jobs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="QUEUED"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("queued_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("run_id", "quality", name="uq_content_pipeline_items_run_quality"),
    )
    op.create_index("ix_content_pipeline_items_status", "content_pipeline_items", ["status", "updated_at"])
    op.create_index("ix_content_pipeline_items_media_job", "content_pipeline_items", ["media_job_id"])


def downgrade() -> None:
    op.drop_index("ix_content_pipeline_items_media_job", table_name="content_pipeline_items")
    op.drop_index("ix_content_pipeline_items_status", table_name="content_pipeline_items")
    op.drop_table("content_pipeline_items")
    op.drop_index("ix_content_pipeline_runs_status", table_name="content_pipeline_runs")
    op.drop_index("ix_content_pipeline_runs_title_created", table_name="content_pipeline_runs")
    op.drop_table("content_pipeline_runs")
    op.drop_index("uq_releases_pipeline_key", table_name="releases")
    op.drop_column("releases", "pipeline_key")
