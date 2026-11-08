"""Verify BotHelp identities and persist bounded status retries.

Revision ID: 0022
Revises: 0021

Reconcile duplicate subscriber IDs against the BotHelp directory before upgrading.
"""

from alembic import op
import sqlalchemy as sa

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("bothelp_mapping_verified_at", sa.DateTime(timezone=True)))
    op.add_column("users", sa.Column("bothelp_status_next_retry_at", sa.DateTime(timezone=True)))
    op.create_unique_constraint("uq_users_bothelp_subscriber_id", "users", ["bothelp_subscriber_id"])
    # Existing timestamps acknowledged a flow launch, not a direct field update.
    op.execute("""
        UPDATE users SET bothelp_status_synced_at = NULL, bothelp_status_sync_attempts = 0
        WHERE EXISTS (SELECT 1 FROM entitlements e
                      WHERE e.user_id = users.id AND e.product_key = 'club')
    """)


def downgrade() -> None:
    op.drop_constraint("uq_users_bothelp_subscriber_id", "users", type_="unique")
    op.drop_column("users", "bothelp_status_next_retry_at")
    op.drop_column("users", "bothelp_mapping_verified_at")
