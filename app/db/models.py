from __future__ import annotations

import enum
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class EntitlementStatus(str, enum.Enum):
    active = "active"
    inactive = "inactive"
    past_due = "past_due"
    canceled = "canceled"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_user_id: Mapped[int] = mapped_column(
        BigInteger, unique=True, nullable=False, index=True
    )
    bothelp_subscriber_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True, default=None
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=sa.text("now()"),
        nullable=False,
    )

    entitlements: Mapped[list[Entitlement]] = relationship(
        "Entitlement", back_populates="user", lazy="select"
    )

    def __repr__(self) -> str:
        return f"<User id={self.id} tg={self.telegram_user_id}>"


class Entitlement(Base):
    __tablename__ = "entitlements"
    __table_args__ = (
        Index("ix_entitlements_user_product", "user_id", "product_key"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    product_key: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[EntitlementStatus] = mapped_column(
        sa.Enum(EntitlementStatus, name="entitlement_status"),
        nullable=False,
        default=EntitlementStatus.inactive,
    )
    # Subscription validity end. NULL means never expires.
    active_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Original plan duration in days (7, 30, 90, etc.). NULL = lifetime.
    duration_days: Mapped[int | None] = mapped_column(
        Integer, nullable=True, default=None
    )
    # Tracks the last expiry notification sent (3, 2, or 1 days before).
    # NULL = no notification sent yet. Reset to NULL on subscription renewal.
    expiry_notified_days: Mapped[int | None] = mapped_column(
        Integer, nullable=True, default=None
    )
    # Timestamp of a 3-hour-before-expiry notification (trial week only).
    expiry_notified_3h_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    # Timestamp of a 10-hour-after-expiry notification.
    expiry_notified_10h_after_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    # Timestamp of a 1-week-after-expiry notification.
    expiry_notified_1w_after_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    # Timestamp of a 30-days-after-expiry notification.
    expiry_notified_30d_after_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=sa.text("now()"),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=sa.text("now()"),
        nullable=False,
    )

    user: Mapped[User] = relationship("User", back_populates="entitlements")

    def __repr__(self) -> str:
        return (
            f"<Entitlement id={self.id} user_id={self.user_id}"
            f" product={self.product_key} status={self.status}>"
        )


class PendingInvoice(Base):
    """Maps a Lava invoice ID to the Telegram user who initiated payment."""

    __tablename__ = "pending_invoices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    lava_invoice_id: Mapped[str] = mapped_column(
        Text, unique=True, nullable=False, index=True
    )
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    offer_id: Mapped[str] = mapped_column(Text, nullable=False)
    plan: Mapped[str] = mapped_column(Text, nullable=False)  # "1w", "1m", "3m", "6m", "12m"
    payment_url: Mapped[str] = mapped_column(Text, nullable=False)
    paid: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=False)
    cuid: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    first_name: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    ref: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=sa.text("now()"),
        nullable=False,
    )

    def __repr__(self) -> str:
        return (
            f"<PendingInvoice id={self.id} lava={self.lava_invoice_id}"
            f" tg={self.telegram_user_id} plan={self.plan} paid={self.paid}>"
        )


class LavaEvent(Base):
    """Idempotency log for incoming Lava webhook events."""

    __tablename__ = "lava_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # Stable unique ID derived from the payload (or a SHA-256 hash fallback).
    event_id: Mapped[str] = mapped_column(Text, unique=True, nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=sa.text("now()"),
        nullable=False,
    )
    payload_json: Mapped[dict] = mapped_column(JSONB, nullable=False)

    def __repr__(self) -> str:
        return f"<LavaEvent id={self.id} event_id={self.event_id} type={self.event_type}>"
