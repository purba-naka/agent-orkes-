"""add mcp tool snapshots

Revision ID: b8e2f4a1c7d3
Revises: a7d3e1f09c42
Create Date: 2026-09-24
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "b8e2f4a1c7d3"
down_revision: Union[str, None] = "a7d3e1f09c42"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "mcp_tool_snapshots",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("connection_id", sa.UUID(), nullable=False),
        sa.Column("tools_hash", sa.String(length=64), nullable=False),
        sa.Column("tools", JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["connection_id"], ["mcp_connections.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "connection_id", "tools_hash", name="uq_mcp_tool_snapshot_content"
        ),
    )
    op.create_index(
        "ix_mcp_tool_snapshots_connection_id",
        "mcp_tool_snapshots",
        ["connection_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_mcp_tool_snapshots_connection_id", table_name="mcp_tool_snapshots"
    )
    op.drop_table("mcp_tool_snapshots")
