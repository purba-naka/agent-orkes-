"""add retrieval storage

Revision ID: c4b8f91d2e6a
Revises: 70aab64ef349
Create Date: 2026-09-16
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from pgvector.sqlalchemy import VECTOR

revision: str = "c4b8f91d2e6a"
down_revision: Union[str, None] = "70aab64ef349"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

EMBEDDING_DIMENSIONS = 64


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "knowledge_bases",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("embedding_model_revision_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["embedding_model_revision_id"], ["model_revisions.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_knowledge_bases_name"), "knowledge_bases", ["name"], unique=True)
    op.create_table(
        "knowledge_documents",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("knowledge_base_id", sa.UUID(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("source_uri", sa.String(length=2048), nullable=True),
        sa.Column(
            "metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["knowledge_base_id"], ["knowledge_bases.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_knowledge_documents_knowledge_base_id"),
        "knowledge_documents",
        ["knowledge_base_id"],
        unique=False,
    )
    op.create_table(
        "knowledge_chunks",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("document_id", sa.UUID(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column("embedding", VECTOR(EMBEDDING_DIMENSIONS), nullable=False),
        sa.CheckConstraint("ordinal >= 0", name="ck_knowledge_chunk_ordinal_nonnegative"),
        sa.ForeignKeyConstraint(
            ["document_id"], ["knowledge_documents.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "document_id", "ordinal", name="uq_knowledge_chunk_ordinal"
        ),
    )
    op.create_index(
        op.f("ix_knowledge_chunks_document_id"),
        "knowledge_chunks",
        ["document_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_knowledge_chunks_document_id"), table_name="knowledge_chunks")
    op.drop_table("knowledge_chunks")
    op.drop_index(
        op.f("ix_knowledge_documents_knowledge_base_id"),
        table_name="knowledge_documents",
    )
    op.drop_table("knowledge_documents")
    op.drop_index(op.f("ix_knowledge_bases_name"), table_name="knowledge_bases")
    op.drop_table("knowledge_bases")
    op.execute("DROP EXTENSION IF EXISTS vector")
