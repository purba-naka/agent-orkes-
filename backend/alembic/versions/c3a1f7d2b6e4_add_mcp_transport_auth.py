"""add mcp transport/auth and stdio fields

Revision ID: c3a1f7d2b6e4
Revises: b8e2f4a1c7d3
Create Date: 2026-09-24
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "c3a1f7d2b6e4"
down_revision: Union[str, None] = "b8e2f4a1c7d3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "mcp_connections",
        sa.Column("transport", sa.String(length=32), nullable=False,
                  server_default="streamable_http"),
    )
    op.add_column(
        "mcp_connections",
        sa.Column("auth", sa.String(length=16), nullable=False, server_default="oauth"),
    )
    op.add_column("mcp_connections", sa.Column("command", sa.Text(), nullable=True))
    op.add_column("mcp_connections", sa.Column("args", JSONB(), nullable=True))
    op.add_column("mcp_connections", sa.Column("env_ciphertext", sa.LargeBinary(), nullable=True))
    op.add_column("mcp_connections", sa.Column("env_nonce", sa.LargeBinary(), nullable=True))
    # OAuth-only fields; null for no-auth / stdio connections.
    op.alter_column("mcp_connections", "server_url", existing_type=sa.Text(), nullable=True)
    op.alter_column("mcp_connections", "authorization_endpoint", existing_type=sa.Text(), nullable=True)
    op.alter_column("mcp_connections", "token_endpoint", existing_type=sa.Text(), nullable=True)
    op.alter_column("mcp_connections", "client_id", existing_type=sa.Text(), nullable=True)
    op.alter_column("mcp_connections", "redirect_uri", existing_type=sa.Text(), nullable=True)
    op.alter_column("mcp_connections", "return_url", existing_type=sa.Text(), nullable=True)
    op.create_check_constraint(
        "ck_mcp_connection_transport", "mcp_connections",
        "transport IN ('streamable_http', 'sse', 'stdio')",
    )
    op.create_check_constraint(
        "ck_mcp_connection_auth", "mcp_connections",
        "auth IN ('oauth', 'none')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_mcp_connection_auth", "mcp_connections", type_="check")
    op.drop_constraint("ck_mcp_connection_transport", "mcp_connections", type_="check")
    # Rows without OAuth fields cannot survive the downgrade; refuse to lose data.
    op.execute(
        "DELETE FROM mcp_connections WHERE transport != 'streamable_http' OR auth != 'oauth'"
    )
    op.execute(
        "UPDATE mcp_connections SET server_url = '' WHERE server_url IS NULL;"
    )
    op.alter_column("mcp_connections", "return_url", existing_type=sa.Text(), nullable=False)
    op.alter_column("mcp_connections", "redirect_uri", existing_type=sa.Text(), nullable=False)
    op.alter_column("mcp_connections", "client_id", existing_type=sa.Text(), nullable=False)
    op.alter_column("mcp_connections", "token_endpoint", existing_type=sa.Text(), nullable=False)
    op.alter_column("mcp_connections", "authorization_endpoint", existing_type=sa.Text(), nullable=False)
    op.alter_column("mcp_connections", "server_url", existing_type=sa.Text(), nullable=False)
    op.drop_column("mcp_connections", "env_nonce")
    op.drop_column("mcp_connections", "env_ciphertext")
    op.drop_column("mcp_connections", "args")
    op.drop_column("mcp_connections", "command")
    op.drop_column("mcp_connections", "auth")
    op.drop_column("mcp_connections", "transport")
