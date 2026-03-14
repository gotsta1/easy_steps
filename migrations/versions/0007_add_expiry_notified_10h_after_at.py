"""add expiry_notified_10h_after_at to entitlements

Revision ID: 0007
Revises: 0006
Create Date: 2026-03-14
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "entitlements",
        sa.Column("expiry_notified_10h_after_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("entitlements", "expiry_notified_10h_after_at")
