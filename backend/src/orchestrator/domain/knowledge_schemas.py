from datetime import datetime
from typing import Any
import uuid

from pydantic import BaseModel, ConfigDict, Field


class KnowledgeBaseCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    embedding_model_revision_id: uuid.UUID


class KnowledgeBaseResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    embedding_model_revision_id: uuid.UUID
    created_at: datetime


class KnowledgeDocumentCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    content: str = Field(..., min_length=1)
    source_uri: str | None = Field(default=None, max_length=2048)
    metadata: dict[str, Any] = Field(default_factory=dict)


class KnowledgeDocumentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: uuid.UUID
    knowledge_base_id: uuid.UUID
    title: str
    source_uri: str | None
    metadata: dict[str, Any]
    chunk_count: int
    created_at: datetime
