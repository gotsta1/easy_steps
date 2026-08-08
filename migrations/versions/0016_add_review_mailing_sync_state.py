"""add review mailing synchronization state

Revision ID: 0016
Revises: 0015
Create Date: 2026-08-09
"""

from alembic import op
import sqlalchemy as sa


revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "entitlements",
        sa.Column("review_mailing_state", sa.Text(), nullable=True),
    )
    op.add_column(
        "entitlements",
        sa.Column(
            "review_mailing_synced_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.execute(
        "UPDATE entitlements "
        "SET review_mailing_state = 'enrolled', "
        "review_mailing_synced_at = review_mailing_started_at "
        "WHERE review_mailing_started_at IS NOT NULL"
    )
    op.drop_index(
        "ix_entitlements_review_mailing_pending",
        table_name="entitlements",
    )
    op.create_index(
        "ix_entitlements_review_mailing_reconcile",
        "entitlements",
        ["product_key", "active_until", "id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_entitlements_review_mailing_reconcile",
        table_name="entitlements",
    )
    op.create_index(
        "ix_entitlements_review_mailing_pending",
        "entitlements",
        ["active_until", "id"],
        unique=False,
        postgresql_where=sa.text(
            "product_key = 'club' "
            "AND active_until IS NOT NULL "
            "AND review_mailing_started_at IS NULL"
        ),
    )
    op.drop_column("entitlements", "review_mailing_synced_at")
    op.drop_column("entitlements", "review_mailing_state")
