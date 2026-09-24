from datetime import datetime
from typing import Any, Literal
import uuid
from pydantic import BaseModel, ConfigDict, Field


class Diagnostic(BaseModel):
    code: str
    path: str
    message: str
    severity: Literal["error", "warning"] = "error"


class AgentCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: str = Field(default="", max_length=2000)
    system_prompt: str = Field(default="You are a helpful assistant.")
    model_revision_id: uuid.UUID | None = None


class AgentDraftUpdate(BaseModel):
    version: int = Field(..., description="Expected current draft version for optimistic locking")
    document: dict[str, Any]


class AgentDraftResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    agent_id: uuid.UUID
    document: dict[str, Any]
    validation: list[Diagnostic] = Field(default_factory=list)
    version: int
    updated_at: datetime


class AgentRevisionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    agent_id: uuid.UUID
    revision_number: int
    document: dict[str, Any]
    dependency_manifest: dict[str, Any]
    content_hash: str
    created_at: datetime


class AgentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    description: str
    active_revision_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime
    active_revision: AgentRevisionResponse | None = None
    draft: AgentDraftResponse | None = None


class RunCreate(BaseModel):
    input: dict[str, Any] = Field(default_factory=dict)


class RunInterruptResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    run_id: uuid.UUID
    interrupt_id: str
    kind: str
    payload: dict[str, Any]
    status: str
    decision: dict[str, Any] | None
    created_at: datetime
    resolved_at: datetime | None


class InterruptDecision(BaseModel):
    interrupt_id: str = Field(..., min_length=1)
    action: Literal["approve", "edit", "reject", "accept", "revise", "abort"]
    input: dict[str, Any] | None = None
    output: dict[str, Any] | None = None
    reason: str | None = Field(default=None, max_length=2000)


class RunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    agent_revision_id: uuid.UUID
    conversation_id: uuid.UUID | None
    thread_id: uuid.UUID
    mode: str
    status: str
    result_name: str | None
    result: dict[str, Any] | None
    usage: dict[str, Any]
    sanitized_error: dict[str, Any] | None
    started_at: datetime
    finished_at: datetime | None
    interrupts: list[RunInterruptResponse] = Field(default_factory=list)


class MessageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    conversation_id: uuid.UUID
    run_id: uuid.UUID
    langgraph_message_id: str
    role: str
    content: Any
    sequence: int
    created_at: datetime


class ConversationCreate(BaseModel):
    agent_id: uuid.UUID
    title: str = Field(default="", max_length=255)


class ConversationUpgrade(BaseModel):
    summary: str | None = None


class ConversationMessageCreate(BaseModel):
    content: Any | None = None
    text: str | None = None


class ConversationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    agent_revision_id: uuid.UUID
    title: str
    parent_conversation_id: uuid.UUID | None
    upgrade_summary: str | None
    created_at: datetime
    updated_at: datetime


class ConversationDetailResponse(ConversationResponse):
    agent_id: uuid.UUID | None = None
    agent_name: str | None = None
    revision_number: int | None = None
    messages: list[MessageResponse] = Field(default_factory=list)
    runs: list[RunResponse] = Field(default_factory=list)


def make_default_agent_document(
    name: str,
    system_prompt: str = "You are a helpful assistant.",
    model_revision_id: str | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "system_prompt": system_prompt,
        "input_schema": {
            "type": "object",
            "properties": {"prompt": {"type": "string"}},
            "required": ["prompt"],
        },
        "output_schema": {
            "type": "object",
            "properties": {"output": {"type": "string"}},
        },
        "context_policy": {
            "memory": False,
            "knowledge_top_k": 0,
            "upstream": [],
        },
        "middleware_policy": {
            "summarization": {"enabled": False},
            "context_editing": {"enabled": False},
            "pii": {"enabled": False},
            "tool_approval": {"risky_only": False},
        },
        "recursion_limit": 25,
        "entry_node_id": "main",
        "nodes": [
            {
                "id": "main",
                "kind": "agent",
                "agent": {
                    "mode": "inline",
                    "model_revision_id": model_revision_id,
                    "system_prompt": system_prompt,
                    "input_schema": {"type": "object"},
                    "output_schema": {"type": "object"},
                    "tool_revision_ids": [],
                    "agent_tool_revision_ids": [],
                    "context_policy": {"upstream": []},
                    "middleware_policy": {},
                    "review_output": False,
                },
                "retry": {"max_attempts": 3},
                "timeout": {"run_seconds": 120, "idle_seconds": 30},
            }
        ],
        "edges": [],
        "named_exits": ["success"],
    }
