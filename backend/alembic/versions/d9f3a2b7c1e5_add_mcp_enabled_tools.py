"""add mcp connection enabled_tools

Revision ID: d9f3a2b7c1e5
Revises: c3a1f7d2b6e4
Create Date: 2026-09-24
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "d9f3a2b7c1e5"
down_revision: Union[str, None] = "c3a1f7d2b6e4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "mcp_connections",
        sa.Column("enabled_tools", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
    )


def downgrade() -> None:
    op.drop_column("mcp_connections", "enabled_tools")
