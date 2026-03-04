"""add bothelp_subscriber_id to users

Revision ID: 0004
Revises: 0003
Create Date: 2026-03-04 00:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("bothelp_subscriber_id", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("users", "bothelp_subscriber_id")
