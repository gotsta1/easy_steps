"""add cuid and first_name to pending_invoices

Revision ID: 0008
Revises: 0007
Create Date: 2026-03-15
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "pending_invoices",
        sa.Column("cuid", sa.Text(), nullable=True),
    )
    op.add_column(
        "pending_invoices",
        sa.Column("first_name", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("pending_invoices", "first_name")
    op.drop_column("pending_invoices", "cuid")
