"""initial schema

Revision ID: 0001
Revises:
Create Date: 2024-01-01 00:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── entitlement_status enum ───────────────────────────────────────────────
    op.execute(
        "CREATE TYPE entitlement_status AS ENUM "
        "('active', 'inactive', 'past_due', 'canceled')"
    )

    # ── users ─────────────────────────────────────────────────────────────────
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_users_telegram_user_id",
        "users",
        ["telegram_user_id"],
        unique=True,
    )

    # ── entitlements ──────────────────────────────────────────────────────────
    op.create_table(
        "entitlements",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("product_key", sa.Text(), nullable=False),
        sa.Column(
            "status",
            postgresql.ENUM(
                "active",
                "inactive",
                "past_due",
                "canceled",
                name="entitlement_status",
                create_type=False,  # already created above
            ),
            nullable=False,
        ),
        sa.Column("active_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("allowed_to_join_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_entitlements_user_product",
        "entitlements",
        ["user_id", "product_key"],
    )

    # ── lava_events ───────────────────────────────────────────────────────────
    op.create_table(
        "lava_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("event_id", sa.Text(), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("payload_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_id", name="uq_lava_events_event_id"),
    )
    op.create_index("ix_lava_events_event_id", "lava_events", ["event_id"], unique=True)


def downgrade() -> None:
    op.drop_table("lava_events")
    op.drop_index("ix_entitlements_user_product", table_name="entitlements")
    op.drop_table("entitlements")
    op.drop_index("ix_users_telegram_user_id", table_name="users")
    op.drop_table("users")
    op.execute("DROP TYPE entitlement_status")
