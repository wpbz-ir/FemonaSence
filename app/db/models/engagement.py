from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import BigInteger, Boolean, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Favorite(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "favorites"
    __table_args__ = (UniqueConstraint("user_id", "title_id", name="uq_favorites_user_title"),)

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    title_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("titles.id", ondelete="CASCADE"), nullable=False)


class WatchHistory(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "watch_history"
    __table_args__ = (
        UniqueConstraint("user_id", "title_id", name="uq_watch_history_user_title"),
        Index("ix_watch_history_user_updated", "user_id", "updated_at"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    title_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("titles.id", ondelete="CASCADE"), nullable=False)
    episode_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("episodes.id", ondelete="SET NULL"))
    progress_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_watched_at: Mapped[datetime] = mapped_column(nullable=False)


class DownloadHistory(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "download_history"
    __table_args__ = (
        Index("ix_download_history_user_created", "user_id", "created_at"),
        Index("ix_download_history_release_created", "release_id", "created_at"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    release_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("releases.id", ondelete="RESTRICT"), nullable=False)
    telegram_message_id: Mapped[int | None] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64))


class NotificationTemplate(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "notification_templates"
    __table_args__ = (UniqueConstraint("code", name="uq_notification_templates_code"),)

    code: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class NotificationJob(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "notification_jobs"
    __table_args__ = (
        UniqueConstraint("dedupe_key", name="uq_notification_jobs_dedupe"),
        Index("ix_notification_jobs_due", "status", "run_at"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    template_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("notification_templates.id", ondelete="SET NULL"))
    subscription_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("subscriptions.id", ondelete="CASCADE"))
    notification_type: Mapped[str] = mapped_column(String(64), nullable=False)
    dedupe_key: Mapped[str] = mapped_column(String(180), nullable=False)
    run_at: Mapped[datetime] = mapped_column(nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
    sent_at: Mapped[datetime | None] = mapped_column()
    locked_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    locked_at: Mapped[datetime | None] = mapped_column(nullable=True)
    last_attempt_at: Mapped[datetime | None] = mapped_column(nullable=True)


class ReferralCode(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "referral_codes"
    __table_args__ = (UniqueConstraint("code", name="uq_referral_codes_code"),)

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class Referral(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "referrals"
    __table_args__ = (UniqueConstraint("referred_user_id", name="uq_referrals_referred_user"),)

    referrer_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    referred_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    referral_code_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("referral_codes.id", ondelete="RESTRICT"), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING")
    qualified_at: Mapped[datetime | None] = mapped_column()


class ReferralReward(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "referral_rewards"
    __table_args__ = (
        UniqueConstraint("referral_id", "reward_type", name="uq_referral_reward_type"),
    )

    referral_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("referrals.id", ondelete="CASCADE"), nullable=False)
    reward_type: Mapped[str] = mapped_column(String(64), nullable=False)
    amount_irr: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING")
    granted_at: Mapped[datetime | None] = mapped_column()


class AnalyticsEvent(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "analytics_events"
    __table_args__ = (
        Index("ix_analytics_events_type_created", "event_type", "created_at"),
        Index("ix_analytics_events_user_created", "user_id", "created_at"),
    )

    created_at: Mapped[datetime] = mapped_column(nullable=False, default=lambda: datetime.now(timezone.utc))
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    title_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("titles.id", ondelete="SET NULL"))
    release_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("releases.id", ondelete="SET NULL"))
    session_id: Mapped[str | None] = mapped_column(String(128))
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)


class AuditLog(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "audit_logs"
    __table_args__ = (Index("ix_audit_logs_created", "created_at"),)

    created_at: Mapped[datetime] = mapped_column(nullable=False, default=lambda: datetime.now(timezone.utc))
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_id: Mapped[uuid.UUID | None] = mapped_column()
    old_value: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    new_value: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    ip_address: Mapped[str | None] = mapped_column(String(64))

