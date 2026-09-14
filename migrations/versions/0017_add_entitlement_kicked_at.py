"""add entitlement kick timestamp

Revision ID: 0017
Revises: 0016
Create Date: 2026-09-14
"""

from alembic import op
import sqlalchemy as sa


revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "entitlements",
        sa.Column("kicked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_entitlements_kicked_at",
        "entitlements",
        ["kicked_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_entitlements_kicked_at", table_name="entitlements")
    op.drop_column("entitlements", "kicked_at")
