"""add pending_invoices table

Revision ID: 0002
Revises: 0001
Create Date: 2026-03-04 00:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "pending_invoices",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("lava_invoice_id", sa.Text(), nullable=False),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column("offer_id", sa.Text(), nullable=False),
        sa.Column("plan", sa.Text(), nullable=False),
        sa.Column("payment_url", sa.Text(), nullable=False),
        sa.Column("paid", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("lava_invoice_id", name="uq_pending_invoices_lava_id"),
    )
    op.create_index(
        "ix_pending_invoices_lava_invoice_id",
        "pending_invoices",
        ["lava_invoice_id"],
        unique=True,
    )
    op.create_index(
        "ix_pending_invoices_telegram_user_id",
        "pending_invoices",
        ["telegram_user_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_pending_invoices_telegram_user_id", table_name="pending_invoices")
    op.drop_index("ix_pending_invoices_lava_invoice_id", table_name="pending_invoices")
    op.drop_table("pending_invoices")
