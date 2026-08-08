"""add durable review mailing delivery state

Revision ID: 0015
Revises: 0014
Create Date: 2026-08-09
"""

from alembic import op
import sqlalchemy as sa


revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "entitlements",
        sa.Column(
            "review_mailing_started_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "entitlements",
        sa.Column(
            "review_mailing_attempts",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "entitlements",
        sa.Column("review_mailing_last_error", sa.Text(), nullable=True),
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


def downgrade() -> None:
    op.drop_index(
        "ix_entitlements_review_mailing_pending",
        table_name="entitlements",
    )
    op.drop_column("entitlements", "review_mailing_last_error")
    op.drop_column("entitlements", "review_mailing_attempts")
    op.drop_column("entitlements", "review_mailing_started_at")
