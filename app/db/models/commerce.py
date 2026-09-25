from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Plan(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "plans"
    __table_args__ = (
        UniqueConstraint("code", name="uq_plans_code"),
        Index("ix_plans_active_sort", "active", "sort_order"),
    )

    code: Mapped[str] = mapped_column(String(32), nullable=False)
    name_fa: Mapped[str] = mapped_column(String(120), nullable=False)
    name_en: Mapped[str | None] = mapped_column(String(120))
    description: Mapped[str | None] = mapped_column(Text)
    price_irr: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False, default=0)
    price_toman: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False, default=0)
    duration_days: Mapped[int] = mapped_column(Integer, nullable=False)
    rank: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    features: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)


class Subscription(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "subscriptions"
    __table_args__ = (
        Index("ix_subscriptions_user_status", "user_id", "status"),
        Index("ix_subscriptions_expires_at", "expires_at"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    plan_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("plans.id", ondelete="RESTRICT"), nullable=False)
    starts_at: Mapped[datetime] = mapped_column(nullable=False)
    expires_at: Mapped[datetime] = mapped_column(nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="ACTIVE")
    auto_renew: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    cancelled_at: Mapped[datetime | None] = mapped_column()
    extra_data: Mapped[dict] = mapped_column("metadata", JSONB, nullable=False, default=dict)

    user: Mapped["User"] = relationship("User", back_populates="subscriptions")
    plan: Mapped["Plan"] = relationship("Plan")


class Order(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "orders"
    __table_args__ = (
        UniqueConstraint("order_number", name="uq_orders_order_number"),
        Index("ix_orders_user_status", "user_id", "status"),
    )

    order_number: Mapped[str] = mapped_column(String(40), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    plan_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("plans.id", ondelete="RESTRICT"))
    amount_irr: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    amount_toman: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="IRR")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="CREATED")
    description: Mapped[str | None] = mapped_column(Text)
    extra_data: Mapped[dict] = mapped_column("metadata", JSONB, nullable=False, default=dict)


class PaymentAttempt(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "payment_attempts"
    __table_args__ = (
        UniqueConstraint("provider", "authority", name="uq_payment_attempt_provider_authority"),
        Index("ix_payment_attempt_order_status", "order_id", "status"),
    )

    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    authority: Mapped[str | None] = mapped_column(String(255))
    payment_url: Mapped[str | None] = mapped_column(Text)
    provider_invoice_id: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="CREATED")
    requested_amount_irr: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    requested_amount_toman: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    raw_callback: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    error_message: Mapped[str | None] = mapped_column(Text)


class Payment(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "payments"
    __table_args__ = (
        UniqueConstraint("provider", "provider_reference", name="uq_payments_provider_reference"),
        Index("ix_payments_order_status", "order_id", "status"),
    )

    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("orders.id", ondelete="RESTRICT"), nullable=False)
    payment_attempt_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("payment_attempts.id", ondelete="RESTRICT"), nullable=False
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_reference: Mapped[str] = mapped_column(String(255), nullable=False)
    amount_irr: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    amount_toman: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PAID")
    paid_at: Mapped[datetime | None] = mapped_column()
    extra_data: Mapped[dict] = mapped_column("metadata", JSONB, nullable=False, default=dict)


class Refund(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "refunds"
    __table_args__ = (
        UniqueConstraint("provider", "provider_reference", name="uq_refunds_provider_reference"),
    )

    payment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("payments.id", ondelete="RESTRICT"), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_reference: Mapped[str] = mapped_column(String(255), nullable=False)
    amount_irr: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="REQUESTED")
    reason: Mapped[str | None] = mapped_column(Text)
    extra_data: Mapped[dict] = mapped_column("metadata", JSONB, nullable=False, default=dict)


class Wallet(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "wallets"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    balance_irr: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False, default=0)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    user: Mapped["User"] = relationship("User", back_populates="wallet")
    ledger_entries: Mapped[list["WalletLedgerEntry"]] = relationship(
        "WalletLedgerEntry", back_populates="wallet", cascade="all, delete-orphan"
    )


class WalletLedgerEntry(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "wallet_ledger_entries"
    __table_args__ = (
        Index("ix_wallet_ledger_wallet_created", "wallet_id", "created_at"),
        UniqueConstraint("external_reference", name="uq_wallet_ledger_external_reference"),
    )

    wallet_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("wallets.id", ondelete="CASCADE"), nullable=False)
    amount_irr: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    balance_after_irr: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    entry_type: Mapped[str] = mapped_column(String(32), nullable=False)
    external_reference: Mapped[str | None] = mapped_column(String(255), unique=True)
    description: Mapped[str | None] = mapped_column(Text)
    extra_data: Mapped[dict] = mapped_column("metadata", JSONB, nullable=False, default=dict)

    wallet: Mapped["Wallet"] = relationship("Wallet", back_populates="ledger_entries")
