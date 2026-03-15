"""add post-expiry notification tracking for 1 week and 30 days

Revision ID: 0009
Revises: 0008
Create Date: 2026-03-15
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "entitlements",
        sa.Column("expiry_notified_1w_after_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "entitlements",
        sa.Column("expiry_notified_30d_after_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("entitlements", "expiry_notified_30d_after_at")
    op.drop_column("entitlements", "expiry_notified_1w_after_at")
