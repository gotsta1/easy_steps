"""add durable BotHelp subscription status sync state

Revision ID: 0014
Revises: 0013
Create Date: 2026-08-08
"""

from alembic import op
import sqlalchemy as sa


revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("bothelp_subscription_status", sa.Text(), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column(
            "bothelp_status_sync_attempts",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "users",
        sa.Column("bothelp_status_sync_last_error", sa.Text(), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column(
            "bothelp_status_synced_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_users_bothelp_status_sync",
        "users",
        ["bothelp_subscription_status"],
        unique=False,
        postgresql_where=sa.text("bothelp_subscriber_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_users_bothelp_status_sync", table_name="users")
    op.drop_column("users", "bothelp_status_synced_at")
    op.drop_column("users", "bothelp_status_sync_last_error")
    op.drop_column("users", "bothelp_status_sync_attempts")
    op.drop_column("users", "bothelp_subscription_status")
