from datetime import datetime
import uuid
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field


class CredentialCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    kind: str = Field(..., min_length=1, max_length=64)
    secret: str = Field(..., min_length=1)


class CredentialUpdate(BaseModel):
    secret: str = Field(..., min_length=1)


class CredentialResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    kind: str
    last_four: str
    is_enabled: bool
    created_at: datetime
    updated_at: datetime


class ModelRevisionCreate(BaseModel):
    provider: str = Field(..., min_length=1, max_length=64)
    model_name: str = Field(..., min_length=1, max_length=255)
    base_url: str | None = None
    api_key_id: uuid.UUID | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)
    routing: dict[str, Any] | None = None
    context_window: int | None = None


class ModelRevisionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    model_id: uuid.UUID
    revision_number: int
    provider: str
    model_name: str
    base_url: str | None
    api_key_id: uuid.UUID | None
    parameters: dict[str, Any]
    routing: dict[str, Any] | None
    context_window: int | None
    is_enabled: bool
    created_at: datetime


class ModelCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    revision: ModelRevisionCreate


class ModelResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    active_revision_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime
    active_revision: ModelRevisionResponse | None = None
    revisions: list[ModelRevisionResponse] = Field(default_factory=list)


class ModelTestResult(BaseModel):
    status: Literal["connected", "failed"]
    model_id: uuid.UUID
    revision_id: uuid.UUID | None = None
    revision_number: int | None = None
    latency_ms: int | None = None
    response_preview: str | None = None
    error: str | None = None
