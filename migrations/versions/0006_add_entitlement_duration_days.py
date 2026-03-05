"""add duration_days to entitlements

Revision ID: 0006
Revises: 0005
Create Date: 2026-03-05 00:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "entitlements",
        sa.Column("duration_days", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("entitlements", "duration_days")

