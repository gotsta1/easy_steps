"""replace 3 post-expiry timestamp columns with single last_post_expiry_hours

Revision ID: 0011
Revises: 0010
Create Date: 2026-03-17
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "entitlements",
        sa.Column("last_post_expiry_hours", sa.Integer(), nullable=True),
    )
    # Backfill: preserve highest threshold already sent
    op.execute(
        "UPDATE entitlements SET last_post_expiry_hours = 720 "
        "WHERE expiry_notified_30d_after_at IS NOT NULL"
    )
    op.execute(
        "UPDATE entitlements SET last_post_expiry_hours = 168 "
        "WHERE expiry_notified_1w_after_at IS NOT NULL "
        "AND last_post_expiry_hours IS NULL"
    )
    op.execute(
        "UPDATE entitlements SET last_post_expiry_hours = 10 "
        "WHERE expiry_notified_10h_after_at IS NOT NULL "
        "AND last_post_expiry_hours IS NULL"
    )
    op.drop_column("entitlements", "expiry_notified_10h_after_at")
    op.drop_column("entitlements", "expiry_notified_1w_after_at")
    op.drop_column("entitlements", "expiry_notified_30d_after_at")


def downgrade() -> None:
    op.add_column(
        "entitlements",
        sa.Column("expiry_notified_10h_after_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "entitlements",
        sa.Column("expiry_notified_1w_after_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "entitlements",
        sa.Column("expiry_notified_30d_after_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.drop_column("entitlements", "last_post_expiry_hours")
