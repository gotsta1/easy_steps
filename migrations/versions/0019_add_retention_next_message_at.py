"""add randomized retention delivery schedule

Revision ID: 0019
Revises: 0018
Create Date: 2026-09-15
"""

from alembic import op
import sqlalchemy as sa


revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "entitlements",
        sa.Column(
            "retention_next_message_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_entitlements_retention_next_message_at",
        "entitlements",
        ["retention_next_message_at"],
        unique=False,
        postgresql_where=sa.text(
            "product_key = 'club' AND retention_next_message_at IS NOT NULL"
        ),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_entitlements_retention_next_message_at",
        table_name="entitlements",
    )
    op.drop_column("entitlements", "retention_next_message_at")
