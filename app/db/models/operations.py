from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class MediaJob(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "media_jobs"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_media_jobs_idempotency_key"),
        Index("ix_media_jobs_claim", "status", "available_at", "priority", "created_at"),
        Index("ix_media_jobs_release", "source_release_id", "target_release_id"),
        Index("ix_media_jobs_worker_lease", "status", "locked_at"),
    )

    job_type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="QUEUED")
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=50)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    available_at: Mapped[datetime] = mapped_column(nullable=False)
    locked_by: Mapped[str | None] = mapped_column(String(128))
    locked_at: Mapped[datetime | None] = mapped_column()
    heartbeat_at: Mapped[datetime | None] = mapped_column()
    started_at: Mapped[datetime | None] = mapped_column()
    finished_at: Mapped[datetime | None] = mapped_column()
    source_release_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("releases.id", ondelete="CASCADE"), nullable=False)
    source_storage_file_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("storage_files.id", ondelete="RESTRICT"), nullable=False)
    target_release_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("releases.id", ondelete="SET NULL"))
    target_quality: Mapped[str] = mapped_column(String(16), nullable=False)
    output_storage_file_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("storage_files.id", ondelete="SET NULL"))
    work_dir: Mapped[str | None] = mapped_column(Text)
    input_path: Mapped[str | None] = mapped_column(Text)
    output_path: Mapped[str | None] = mapped_column(Text)
    progress_percent: Mapped[float] = mapped_column(nullable=False, default=0)
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    idempotency_key: Mapped[str] = mapped_column(String(220), nullable=False)
    extra_data: Mapped[dict] = mapped_column("metadata", JSONB, nullable=False, default=dict)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class MediaJobEvent(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "media_job_events"
    __table_args__ = (
        Index("ix_media_job_events_job_created", "job_id", "created_at"),
    )

    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("media_jobs.id", ondelete="CASCADE"), nullable=False)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    message: Mapped[str | None] = mapped_column(Text)
    data: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(nullable=False)


class ContentPipelineRun(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "content_pipeline_runs"
    __table_args__ = (
        UniqueConstraint("dedupe_key", name="uq_content_pipeline_runs_dedupe"),
        Index("ix_content_pipeline_runs_title_created", "title_id", "created_at"),
        Index("ix_content_pipeline_runs_status", "status", "created_at"),
    )

    title_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("titles.id", ondelete="CASCADE"), nullable=False)
    source_release_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("releases.id", ondelete="RESTRICT"), nullable=False)
    source_storage_file_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("storage_files.id", ondelete="RESTRICT"), nullable=False)
    requested_qualities: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="QUEUED")
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    dedupe_key: Mapped[str] = mapped_column(String(220), nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column()
    finished_at: Mapped[datetime | None] = mapped_column()

    items: Mapped[list["ContentPipelineItem"]] = relationship(
        "ContentPipelineItem",
        back_populates="run",
        cascade="all, delete-orphan",
        order_by="ContentPipelineItem.quality",
    )


class ContentPipelineItem(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "content_pipeline_items"
    __table_args__ = (
        UniqueConstraint("run_id", "quality", name="uq_content_pipeline_items_run_quality"),
        Index("ix_content_pipeline_items_status", "status", "updated_at"),
        Index("ix_content_pipeline_items_media_job", "media_job_id"),
    )

    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("content_pipeline_runs.id", ondelete="CASCADE"), nullable=False)
    quality: Mapped[str] = mapped_column(String(16), nullable=False)
    target_release_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("releases.id", ondelete="SET NULL"))
    media_job_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("media_jobs.id", ondelete="SET NULL"))
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="QUEUED")
    error_message: Mapped[str | None] = mapped_column(Text)
    queued_at: Mapped[datetime | None] = mapped_column()
    finished_at: Mapped[datetime | None] = mapped_column()

    run: Mapped["ContentPipelineRun"] = relationship("ContentPipelineRun", back_populates="items")


class AdminActionLog(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "admin_action_logs"
    __table_args__ = (
        Index("ix_admin_action_logs_created", "created_at"),
        Index("ix_admin_action_logs_actor_created", "actor_user_id", "created_at"),
    )

    created_at: Mapped[datetime] = mapped_column(nullable=False)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    request_id: Mapped[str | None] = mapped_column(String(128))
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_id: Mapped[uuid.UUID | None] = mapped_column()
    method: Mapped[str | None] = mapped_column(String(16))
    path: Mapped[str | None] = mapped_column(Text)
    status_code: Mapped[int | None] = mapped_column(Integer)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    details: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    ip_address: Mapped[str | None] = mapped_column(String(64))


class ServiceHeartbeat(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "service_heartbeats"
    __table_args__ = (
        UniqueConstraint("service_name", "instance_id", name="uq_service_heartbeats_service_instance"),
        Index("ix_service_heartbeats_last_seen", "last_seen_at"),
    )

    service_name: Mapped[str] = mapped_column(String(64), nullable=False)
    instance_id: Mapped[str] = mapped_column(String(128), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="UP")
    extra_data: Mapped[dict] = mapped_column("metadata", JSONB, nullable=False, default=dict)


class PaymentSession(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "payment_sessions"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_payment_sessions_token_hash"),
        Index("ix_payment_sessions_user_created", "user_id", "created_at"),
        Index("ix_payment_sessions_expires_at", "expires_at"),
    )
    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    purpose: Mapped[str] = mapped_column(String(32), nullable=False, default="SUBSCRIPTION")
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(nullable=False)


class PaymentWebhookEvent(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "payment_webhook_events"
    __table_args__ = (
        UniqueConstraint("provider", "event_key", name="uq_payment_webhook_provider_event"),
        Index("ix_payment_webhook_events_order_created", "order_id", "created_at"),
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    event_key: Mapped[str] = mapped_column(String(255), nullable=False)
    order_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("orders.id", ondelete="SET NULL"), nullable=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="RECEIVED")
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(nullable=False)
    processed_at: Mapped[datetime | None] = mapped_column(nullable=True)


class NotificationAttempt(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "notification_attempts"
    __table_args__ = (
        UniqueConstraint("notification_job_id", "attempt_number", name="uq_notification_attempt_job_number"),
        Index("ix_notification_attempts_job_created", "notification_job_id", "created_at"),
    )
    notification_job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("notification_jobs.id", ondelete="CASCADE"), nullable=False)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    telegram_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(nullable=False)


class TelegramUpdate(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "telegram_updates"
    __table_args__ = (
        UniqueConstraint("update_id", "bot_scope", name="uq_telegram_updates_update_scope"),
        Index("ix_telegram_updates_status_received", "status", "received_at"),
    )
    update_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    bot_scope: Mapped[str] = mapped_column(String(64), nullable=False, default="default")
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="RECEIVED")
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    received_at: Mapped[datetime] = mapped_column(nullable=False)
    processed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)


class ServiceIncident(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "service_incidents"
    __table_args__ = (
        Index("ix_service_incidents_opened", "status", "opened_at"),
        Index("ix_service_incidents_service", "service_name", "opened_at"),
    )
    service_name: Mapped[str] = mapped_column(String(64), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False, default="INFO")
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="OPEN")
    summary: Mapped[str] = mapped_column(String(255), nullable=False)
    details: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    opened_at: Mapped[datetime] = mapped_column(nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(nullable=True)
