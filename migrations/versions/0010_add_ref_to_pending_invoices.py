"""add ref column to pending_invoices

Revision ID: 0010
Revises: 0009
Create Date: 2026-03-16
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "pending_invoices",
        sa.Column("ref", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("pending_invoices", "ref")
