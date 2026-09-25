from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class AdSetting(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """تنظیمات سراسری تبلیغات — فقط یک ردیف (singleton=1) وجود دارد."""

    __tablename__ = "ad_settings"

    singleton: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, unique=True)
    channel_chat_id: Mapped[int | None] = mapped_column(BigInteger)
    channel_username: Mapped[str | None] = mapped_column(String(255))
    rates_text: Mapped[str | None] = mapped_column(Text)
    instructions_text: Mapped[str | None] = mapped_column(Text)
    contact_text: Mapped[str | None] = mapped_column(Text)
    auto_channel_publish: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    extra_data: Mapped[dict] = mapped_column("metadata", JSONB, nullable=False, default=dict)


class AdRequest(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """درخواست تبلیغ کاربر — متن یا فایل ارسالی کاربر برای انتشار در کانال تبلیغات."""

    __tablename__ = "ad_requests"
    __table_args__ = (
        Index("ix_ad_requests_status_created", "status", "created_at"),
        Index("ix_ad_requests_user", "user_id"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    content_type: Mapped[str] = mapped_column(String(32), nullable=False, default="TEXT")
    content_text: Mapped[str | None] = mapped_column(Text)
    telegram_file_id: Mapped[str | None] = mapped_column(String(255))
    source_chat_id: Mapped[int | None] = mapped_column(BigInteger)
    source_message_id: Mapped[int | None] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING")
    admin_note: Mapped[str | None] = mapped_column(Text)
    published_chat_id: Mapped[int | None] = mapped_column(BigInteger)
    published_message_id: Mapped[int | None] = mapped_column(BigInteger)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
