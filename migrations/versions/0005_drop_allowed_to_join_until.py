"""drop allowed_to_join_until from entitlements

Revision ID: 0005
Revises: 0004
Create Date: 2026-03-05 00:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column("entitlements", "allowed_to_join_until")


def downgrade() -> None:
    op.add_column(
        "entitlements",
        sa.Column("allowed_to_join_until", sa.DateTime(timezone=True), nullable=True),
    )
