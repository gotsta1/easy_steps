"""add expiry_notified_10h_after_at to entitlements

Revision ID: 0007_add_expiry_notified_10h_after_at
Revises: 0006_add_duration_days_and_3h_notify
Create Date: 2026-03-14
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0007_add_expiry_notified_10h_after_at"
down_revision = "0006_add_duration_days_and_3h_notify"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "entitlements",
        sa.Column("expiry_notified_10h_after_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("entitlements", "expiry_notified_10h_after_at")
