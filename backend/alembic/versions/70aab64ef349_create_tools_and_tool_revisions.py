"""create tools and tool revisions

Revision ID: 70aab64ef349
Revises: b07c5cac0468
Create Date: 2026-09-16
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "70aab64ef349"
down_revision: Union[str, None] = "b07c5cac0468"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "tools",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("active_revision_id", sa.UUID(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_tools_name"), "tools", ["name"], unique=True)
    op.create_table(
        "tool_revisions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tool_id", sa.UUID(), nullable=False),
        sa.Column("revision_number", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("input_schema", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("output_schema", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("configuration", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("risk_level", sa.String(length=32), nullable=False),
        sa.Column("is_mutating", sa.Boolean(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("kind IN ('http', 'mcp', 'retrieval', 'code')", name="ck_tool_revision_kind"),
        sa.CheckConstraint("max_attempts > 0", name="ck_tool_max_attempts_positive"),
        sa.CheckConstraint("revision_number > 0", name="ck_tool_revision_number_positive"),
        sa.CheckConstraint("risk_level IN ('low', 'medium', 'high')", name="ck_tool_revision_risk_level"),
        sa.ForeignKeyConstraint(["tool_id"], ["tools.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tool_id", "revision_number", name="uq_tool_revision_number"),
    )
    op.create_index(op.f("ix_tool_revisions_tool_id"), "tool_revisions", ["tool_id"], unique=False)
    op.execute("""
    CREATE TRIGGER trg_tool_revisions_immutable
    BEFORE UPDATE OR DELETE ON tool_revisions
    FOR EACH ROW EXECUTE FUNCTION prevent_revision_mutation();
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_tool_revisions_immutable ON tool_revisions")
    op.drop_index(op.f("ix_tool_revisions_tool_id"), table_name="tool_revisions")
    op.drop_table("tool_revisions")
    op.drop_index(op.f("ix_tools_name"), table_name="tools")
    op.drop_table("tools")
