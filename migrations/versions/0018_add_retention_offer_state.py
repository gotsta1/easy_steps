"""add one-time retention offer state

Revision ID: 0018
Revises: 0017
Create Date: 2026-09-15
"""

from alembic import op
import sqlalchemy as sa


revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "entitlements",
        "last_post_expiry_hours",
        new_column_name="last_post_kick_hours",
    )
    op.drop_column("entitlements", "expiry_notified_3h_at")
    op.add_column(
        "entitlements",
        sa.Column("retention_message_sent_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "entitlements",
        sa.Column(
            "retention_offers",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "ck_entitlements_retention_offers_once",
        "entitlements",
        "retention_offers >= 0 AND retention_offers <= 1",
    )
    op.create_index(
        "ix_entitlements_retention_message_candidates",
        "entitlements",
        ["kicked_at"],
        unique=False,
        postgresql_where=sa.text(
            "product_key = 'club' AND kicked_at IS NOT NULL"
        ),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_entitlements_retention_message_candidates",
        table_name="entitlements",
    )
    op.drop_constraint(
        "ck_entitlements_retention_offers_once",
        "entitlements",
        type_="check",
    )
    op.drop_column("entitlements", "retention_offers")
    op.drop_column("entitlements", "retention_message_sent_at")
    op.add_column(
        "entitlements",
        sa.Column("expiry_notified_3h_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.alter_column(
        "entitlements",
        "last_post_kick_hours",
        new_column_name="last_post_expiry_hours",
    )
