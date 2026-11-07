"""Index pending Sheets deliveries for the configured referral.

Revision ID: 0021
Revises: 0020
"""

from alembic import op
import sqlalchemy as sa


revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None

INDEX_NAME = "ix_pending_invoices_gsheet_pending"


def upgrade() -> None:
    op.drop_index(INDEX_NAME, table_name="pending_invoices")
    op.create_index(
        INDEX_NAME,
        "pending_invoices",
        ["ref", "gsheet_attempts", "id"],
        unique=False,
        postgresql_where=sa.text("paid IS TRUE AND gsheet_recorded_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index(INDEX_NAME, table_name="pending_invoices")
    op.create_index(
        INDEX_NAME,
        "pending_invoices",
        ["id"],
        unique=False,
        postgresql_where=sa.text(
            "paid IS TRUE AND ref = 'tanya' AND gsheet_recorded_at IS NULL"
        ),
    )
