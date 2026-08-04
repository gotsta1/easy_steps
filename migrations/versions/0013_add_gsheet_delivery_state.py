"""add durable Google Sheets delivery state

Revision ID: 0013
Revises: 0012
Create Date: 2026-08-04
"""

from alembic import op
import sqlalchemy as sa


revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "pending_invoices",
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "pending_invoices",
        sa.Column("gsheet_recorded_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "pending_invoices",
        sa.Column(
            "gsheet_attempts",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "pending_invoices",
        sa.Column("gsheet_last_error", sa.Text(), nullable=True),
    )
    op.create_index(
        "ix_pending_invoices_gsheet_pending",
        "pending_invoices",
        ["id"],
        unique=False,
        postgresql_where=sa.text(
            "paid IS TRUE AND ref = 'tanya' AND gsheet_recorded_at IS NULL"
        ),
    )

    # Older invoices did not store the payment timestamp. Webhook receipt time
    # is the closest durable value and differs from Lava's timestamp by seconds.
    op.execute(
        """
        UPDATE pending_invoices AS pi
        SET paid_at = COALESCE(
            (
                SELECT le.received_at
                FROM lava_events AS le
                WHERE le.event_type = 'payment.success'
                  AND le.payload_json->>'contractId' = pi.lava_invoice_id
                ORDER BY le.received_at DESC
                LIMIT 1
            ),
            pi.created_at
        )
        WHERE pi.paid IS TRUE AND pi.paid_at IS NULL
        """
    )


def downgrade() -> None:
    op.drop_index(
        "ix_pending_invoices_gsheet_pending",
        table_name="pending_invoices",
    )
    op.drop_column("pending_invoices", "gsheet_last_error")
    op.drop_column("pending_invoices", "gsheet_attempts")
    op.drop_column("pending_invoices", "gsheet_recorded_at")
    op.drop_column("pending_invoices", "paid_at")
