from datetime import datetime
from typing import Any, Literal
import uuid

from pydantic import BaseModel, ConfigDict, Field, model_validator

from orchestrator.config import settings

ToolKind = Literal["http", "mcp", "retrieval", "code"]
RiskLevel = Literal["low", "medium", "high"]


class ToolRevisionCreate(BaseModel):
    kind: ToolKind
    description: str = Field(..., min_length=1)
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    configuration: dict[str, Any]
    risk_level: RiskLevel = "low"
    is_mutating: bool = False
    max_attempts: int = Field(default=3, ge=1, le=10)

    @model_validator(mode="after")
    def validate_kind_configuration(self) -> "ToolRevisionCreate":
        config = self.configuration
        if self.kind == "code":
            if not config.get("implementation_key") or not config.get("implementation_version"):
                raise ValueError("Code tools require implementation_key and implementation_version")
        elif self.kind == "http":
            if not config.get("url"):
                raise ValueError("HTTP tools require a URL")
            method = str(config.get("method", "POST")).upper()
            if method not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
                raise ValueError("HTTP method is not supported")
        elif self.kind == "mcp":
            if not config.get("server_url") or not config.get("remote_tool_name"):
                raise ValueError("MCP tools require server_url and remote_tool_name")
            if config.get("transport", "streamable_http") != "streamable_http":
                raise ValueError("Only Streamable HTTP MCP transport is supported")
        elif self.kind == "retrieval":
            for field in ("knowledge_base_id", "embedding_model_revision_id"):
                try:
                    uuid.UUID(str(config.get(field)))
                except (TypeError, ValueError, AttributeError) as exc:
                    raise ValueError(f"Retrieval tools require a valid {field}") from exc
            top_k = config.get("top_k", 5)
            if isinstance(top_k, bool) or not isinstance(top_k, int):
                raise ValueError("Retrieval top_k must be an integer")
            if not 1 <= top_k <= settings.retrieval_max_top_k:
                raise ValueError(
                    f"Retrieval top_k must be between 1 and {settings.retrieval_max_top_k}"
                )
            properties = self.input_schema.get("properties", {})
            if "query" not in properties or "query" not in self.input_schema.get("required", []):
                raise ValueError("Retrieval tools require a query input")
        return self


class ToolCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    revision: ToolRevisionCreate


class ToolRevisionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tool_id: uuid.UUID
    revision_number: int
    kind: ToolKind
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    configuration: dict[str, Any]
    risk_level: RiskLevel
    is_mutating: bool
    max_attempts: int
    is_enabled: bool
    created_at: datetime


class ToolResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    active_revision_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime
    active_revision: ToolRevisionResponse | None = None
    revisions: list[ToolRevisionResponse] = Field(default_factory=list)
