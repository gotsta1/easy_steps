"""track the next retention message in each three-step cycle

Revision ID: 0020
Revises: 0019
"""

from alembic import op
import sqlalchemy as sa


revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "entitlements",
        sa.Column(
            "retention_next_message_index",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    # Existing inactive users have already received the first message in this cycle.
    op.execute(
        """
        UPDATE entitlements
        SET retention_next_message_index = 1
        WHERE product_key = 'club'
          AND status <> 'active'
          AND retention_message_sent_at IS NOT NULL
          AND (
              (kicked_at IS NOT NULL AND retention_message_sent_at >= kicked_at)
              OR (kicked_at IS NULL AND active_until IS NOT NULL
                  AND retention_message_sent_at >= active_until + interval '1 day')
          )
        """
    )
    op.create_check_constraint(
        "ck_entitlements_retention_next_message_index",
        "entitlements",
        "retention_next_message_index BETWEEN 0 AND 2",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_entitlements_retention_next_message_index",
        "entitlements",
        type_="check",
    )
    op.drop_column("entitlements", "retention_next_message_index")
