from datetime import datetime, timezone
import uuid
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from pgvector.sqlalchemy import VECTOR

from orchestrator.config import settings
from orchestrator.db.base import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Credential(Base):
    __tablename__ = "credentials"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    nonce: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    key_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    last_four: Mapped[str] = mapped_column(String(8), nullable=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class McpConnection(Base):
    """Link to one MCP server (streamable HTTP + OAuth by default, but also
    no-auth HTTP, legacy HTTP+SSE, and local stdio servers). Secrets are
    AES-GCM encrypted."""

    __tablename__ = "mcp_connections"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    # Remote transports (streamable_http, sse) carry a server_url; stdio does not.
    transport: Mapped[str] = mapped_column(
        String(32), nullable=False, default="streamable_http"
    )
    auth: Mapped[str] = mapped_column(String(16), nullable=False, default="oauth")
    server_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    # stdio only: the executable to spawn and its argv (env secrets are encrypted).
    command: Mapped[str | None] = mapped_column(Text, nullable=True)
    args: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    env_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    env_nonce: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    # pending -> connected; any refresh failure -> needs_reauth
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    # OAuth-only fields; null for auth=none and stdio connections.
    authorization_endpoint: Mapped[str | None] = mapped_column(Text, nullable=True)
    token_endpoint: Mapped[str | None] = mapped_column(Text, nullable=True)
    client_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Registered with the client; OAuth requires the exact same value on every hop.
    redirect_uri: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Frontend page the callback sends the browser back to.
    return_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    scope: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Short-lived authorization state; cleared once the code is exchanged.
    oauth_state: Mapped[str | None] = mapped_column(String(128), nullable=True, unique=True)
    code_verifier: Mapped[str | None] = mapped_column(String(128), nullable=True)
    token_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    token_nonce: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'connected', 'needs_reauth')",
            name="ck_mcp_connection_status",
        ),
        CheckConstraint(
            "transport IN ('streamable_http', 'sse', 'stdio')",
            name="ck_mcp_connection_transport",
        ),
        CheckConstraint(
            "auth IN ('oauth', 'none')",
            name="ck_mcp_connection_auth",
        ),
    )


class McpToolSnapshot(Base):
    """Immutable, content-addressed result of one server's tools/list call."""

    __tablename__ = "mcp_tool_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    connection_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("mcp_connections.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    tools_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    tools: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    __table_args__ = (
        UniqueConstraint("connection_id", "tools_hash", name="uq_mcp_tool_snapshot_content"),
    )


class Model(Base):
    __tablename__ = "models"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    active_revision_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    revisions: Mapped[list["ModelRevision"]] = relationship(
        "ModelRevision",
        back_populates="model",
        cascade="all, delete-orphan",
        order_by="ModelRevision.revision_number.desc()",
    )

    @property
    def active_revision(self) -> "ModelRevision | None":
        if not self.active_revision_id:
            return None
        for r in getattr(self, "revisions", []):
            if r.id == self.active_revision_id:
                return r
        return None


class ModelRevision(Base):
    __tablename__ = "model_revisions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    model_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("models.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    model_name: Mapped[str] = mapped_column(String(255), nullable=False)
    base_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    api_key_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("credentials.id", ondelete="RESTRICT"), nullable=True
    )
    parameters: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    routing: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    context_window: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("model_id", "revision_number", name="uq_model_revision_number"),
    )

    model: Mapped["Model"] = relationship("Model", back_populates="revisions")
    credential: Mapped[Credential | None] = relationship("Credential")


class Tool(Base):
    __tablename__ = "tools"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    active_revision_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    revisions: Mapped[list["ToolRevision"]] = relationship(
        "ToolRevision",
        back_populates="tool",
        cascade="all, delete-orphan",
        order_by="ToolRevision.revision_number.desc()",
    )

    @property
    def active_revision(self) -> "ToolRevision | None":
        if not self.active_revision_id:
            return None
        return next(
            (revision for revision in self.revisions if revision.id == self.active_revision_id),
            None,
        )


class ToolRevision(Base):
    __tablename__ = "tool_revisions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tool_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tools.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    input_schema: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    output_schema: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    configuration: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    risk_level: Mapped[str] = mapped_column(String(32), nullable=False)
    is_mutating: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("tool_id", "revision_number", name="uq_tool_revision_number"),
        CheckConstraint("revision_number > 0", name="ck_tool_revision_number_positive"),
        CheckConstraint("max_attempts > 0", name="ck_tool_max_attempts_positive"),
        CheckConstraint(
            "kind IN ('http', 'mcp', 'retrieval', 'code')",
            name="ck_tool_revision_kind",
        ),
        CheckConstraint(
            "risk_level IN ('low', 'medium', 'high')",
            name="ck_tool_revision_risk_level",
        ),
    )

    tool: Mapped["Tool"] = relationship("Tool", back_populates="revisions")


class KnowledgeBase(Base):
    __tablename__ = "knowledge_bases"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    embedding_model_revision_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("model_revisions.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    embedding_model_revision: Mapped["ModelRevision"] = relationship("ModelRevision")
    documents: Mapped[list["KnowledgeDocument"]] = relationship(
        "KnowledgeDocument", back_populates="knowledge_base", cascade="all, delete-orphan"
    )


class KnowledgeDocument(Base):
    __tablename__ = "knowledge_documents"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    knowledge_base_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    source_uri: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    knowledge_base: Mapped["KnowledgeBase"] = relationship(
        "KnowledgeBase", back_populates="documents"
    )
    chunks: Mapped[list["KnowledgeChunk"]] = relationship(
        "KnowledgeChunk",
        back_populates="document",
        cascade="all, delete-orphan",
        order_by="KnowledgeChunk.ordinal.asc()",
    )


class KnowledgeChunk(Base):
    __tablename__ = "knowledge_chunks"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("knowledge_documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict
    )
    embedding: Mapped[list[float]] = mapped_column(
        VECTOR(settings.retrieval_embedding_dimensions), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("document_id", "ordinal", name="uq_knowledge_chunk_ordinal"),
        CheckConstraint("ordinal >= 0", name="ck_knowledge_chunk_ordinal_nonnegative"),
    )

    document: Mapped["KnowledgeDocument"] = relationship(
        "KnowledgeDocument", back_populates="chunks"
    )


class Agent(Base):
    __tablename__ = "agents"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    active_revision_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    draft: Mapped["AgentDraft | None"] = relationship(
        "AgentDraft", back_populates="agent", uselist=False, cascade="all, delete-orphan"
    )
    revisions: Mapped[list["AgentRevision"]] = relationship(
        "AgentRevision",
        back_populates="agent",
        cascade="all, delete-orphan",
        order_by="AgentRevision.revision_number.desc()",
    )

    @property
    def active_revision(self) -> "AgentRevision | None":
        if not self.active_revision_id:
            return None
        for r in getattr(self, "revisions", []):
            if r.id == self.active_revision_id:
                return r
        return None


class AgentDraft(Base):
    __tablename__ = "agent_drafts"

    agent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agents.id", ondelete="CASCADE"),
        primary_key=True,
    )
    document: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    validation: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    agent: Mapped["Agent"] = relationship("Agent", back_populates="draft")


class AgentRevision(Base):
    __tablename__ = "agent_revisions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    agent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agents.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    document: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    dependency_manifest: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    __table_args__ = (
        UniqueConstraint("agent_id", "revision_number", name="uq_agent_revision_number"),
        UniqueConstraint("agent_id", "content_hash", name="uq_agent_revision_content_hash"),
    )

    agent: Mapped["Agent"] = relationship("Agent", back_populates="revisions")
    dependencies: Mapped[list["AgentRevisionDependency"]] = relationship(
        "AgentRevisionDependency",
        back_populates="owner_revision",
        cascade="all, delete-orphan",
    )


class AgentRevisionDependency(Base):
    __tablename__ = "agent_revision_dependencies"

    owner_revision_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agent_revisions.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    dependency_kind: Mapped[str] = mapped_column(String(64), primary_key=True)
    dependency_revision_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True
    )

    owner_revision: Mapped["AgentRevision"] = relationship(
        "AgentRevision", back_populates="dependencies"
    )


class Run(Base):
    __tablename__ = "runs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    agent_revision_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agent_revisions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    thread_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    mode: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    result_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    usage: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    sanitized_error: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    agent_revision: Mapped["AgentRevision"] = relationship("AgentRevision")
    conversation: Mapped["Conversation | None"] = relationship(
        "Conversation", back_populates="runs"
    )
    interrupts: Mapped[list["RunInterrupt"]] = relationship(
        "RunInterrupt", back_populates="run", cascade="all, delete-orphan"
    )


class RunInterrupt(Base):
    __tablename__ = "run_interrupts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    interrupt_id: Mapped[str] = mapped_column(String(255), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    decision: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        UniqueConstraint("run_id", "interrupt_id", name="uq_run_interrupt_id"),
        CheckConstraint(
            "kind IN ('tool_approval', 'node_review')",
            name="ck_run_interrupt_kind",
        ),
        CheckConstraint(
            "status IN ('pending', 'resolved')",
            name="ck_run_interrupt_status",
        ),
    )

    run: Mapped["Run"] = relationship("Run", back_populates="interrupts")


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    agent_revision_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agent_revisions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    parent_conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    upgrade_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    agent_revision: Mapped["AgentRevision"] = relationship("AgentRevision")
    parent_conversation: Mapped["Conversation | None"] = relationship(
        "Conversation", remote_side="Conversation.id"
    )
    messages: Mapped[list["Message"]] = relationship(
        "Message",
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="Message.sequence.asc()",
    )
    runs: Mapped[list["Run"]] = relationship("Run", back_populates="conversation")


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    langgraph_message_id: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    content: Mapped[Any] = mapped_column(JSONB, nullable=False)
    sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    __table_args__ = (
        UniqueConstraint(
            "conversation_id",
            "langgraph_message_id",
            name="uq_conversation_langgraph_message_id",
        ),
    )

    conversation: Mapped["Conversation"] = relationship(
        "Conversation", back_populates="messages"
    )
    run: Mapped["Run"] = relationship("Run")
