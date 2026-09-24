"""add run interrupts

Revision ID: f6a2c8d4e9b1
Revises: c4b8f91d2e6a
Create Date: 2026-09-16
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "f6a2c8d4e9b1"
down_revision: Union[str, None] = "c4b8f91d2e6a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "run_interrupts",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("run_id", sa.UUID(), nullable=False),
        sa.Column("interrupt_id", sa.String(length=255), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column(
            "payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column(
            "decision", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "kind IN ('tool_approval', 'node_review')",
            name="ck_run_interrupt_kind",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'resolved')",
            name="ck_run_interrupt_status",
        ),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "interrupt_id", name="uq_run_interrupt_id"),
    )
    op.create_index(
        op.f("ix_run_interrupts_run_id"),
        "run_interrupts",
        ["run_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_run_interrupts_run_id"), table_name="run_interrupts")
    op.drop_table("run_interrupts")
