from typing import Any
import uuid

from jsonschema import Draft202012Validator, SchemaError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from orchestrator.db.models import Credential, KnowledgeBase, ModelRevision, Tool, ToolRevision
from orchestrator.domain.tool_schemas import ToolCreate, ToolRevisionCreate
from orchestrator.tools.registry import code_tool_registry


class ToolConfigurationError(ValueError):
    pass


def validate_tool_revision(data: ToolRevisionCreate) -> None:
    for name, schema in (("input_schema", data.input_schema), ("output_schema", data.output_schema)):
        try:
            Draft202012Validator.check_schema(schema)
        except SchemaError as exc:
            raise ToolConfigurationError(f"{name} is not a valid JSON Schema") from exc
    config = data.configuration
    if data.kind == "code" and not code_tool_registry.contains(
        str(config["implementation_key"]), str(config["implementation_version"])
    ):
        raise ToolConfigurationError("The exact code tool implementation is not registered")
    if data.is_mutating and data.max_attempts > 1 and data.kind in {"http", "mcp"}:
        if not config.get("idempotency_header"):
            raise ToolConfigurationError(
                "Mutating tools with retries require an idempotency_header"
            )


class ToolCatalogService:
    @staticmethod
    async def _validate_credentials(session: AsyncSession, data: ToolRevisionCreate) -> None:
        references = data.configuration.get("credential_headers", {})
        if not isinstance(references, dict):
            raise ToolConfigurationError("credential_headers must be an object")
        for credential_id in references.values():
            try:
                parsed = uuid.UUID(str(credential_id))
            except ValueError as exc:
                raise ToolConfigurationError("Credential reference is invalid") from exc
            credential = await session.get(Credential, parsed)
            if not credential or not credential.is_enabled:
                raise ToolConfigurationError("Credential reference is unavailable")

    @staticmethod
    async def _validate_retrieval_configuration(
        session: AsyncSession, data: ToolRevisionCreate
    ) -> None:
        if data.kind != "retrieval":
            return
        config = data.configuration
        try:
            knowledge_base_id = uuid.UUID(str(config["knowledge_base_id"]))
            embedding_revision_id = uuid.UUID(str(config["embedding_model_revision_id"]))
        except (KeyError, ValueError) as exc:
            raise ToolConfigurationError("Retrieval references are invalid") from exc
        knowledge_base = await session.get(KnowledgeBase, knowledge_base_id)
        revision = await session.get(ModelRevision, embedding_revision_id)
        if not knowledge_base:
            raise ToolConfigurationError("Knowledge base is unavailable")
        if not revision or not revision.is_enabled:
            raise ToolConfigurationError("Embedding model revision is unavailable")
        if knowledge_base.embedding_model_revision_id != revision.id:
            raise ToolConfigurationError(
                "Embedding model revision does not match the knowledge base"
            )

    @staticmethod
    def _make_revision(
        tool_id: uuid.UUID,
        revision_number: int,
        data: ToolRevisionCreate,
        *,
        revision_id: uuid.UUID | None = None,
    ) -> ToolRevision:
        return ToolRevision(
            id=revision_id or uuid.uuid4(),
            tool_id=tool_id,
            revision_number=revision_number,
            kind=data.kind,
            description=data.description,
            input_schema=data.input_schema,
            output_schema=data.output_schema,
            configuration=data.configuration,
            risk_level=data.risk_level,
            is_mutating=data.is_mutating,
            max_attempts=data.max_attempts,
            is_enabled=True,
        )

    @classmethod
    async def create_tool(
        cls, session: AsyncSession, data: ToolCreate
    ) -> tuple[Tool, ToolRevision]:
        validate_tool_revision(data.revision)
        await cls._validate_credentials(session, data.revision)
        await cls._validate_retrieval_configuration(session, data.revision)
        tool_id, revision_id = uuid.uuid4(), uuid.uuid4()
        tool = Tool(id=tool_id, name=data.name, active_revision_id=revision_id)
        revision = cls._make_revision(tool_id, 1, data.revision, revision_id=revision_id)
        session.add_all([tool, revision])
        await session.commit()
        loaded = await cls.get_tool(session, tool_id)
        assert loaded is not None
        return loaded, revision

    @classmethod
    async def create_revision(
        cls, session: AsyncSession, tool_id: uuid.UUID, data: ToolRevisionCreate
    ) -> ToolRevision:
        validate_tool_revision(data)
        await cls._validate_credentials(session, data)
        await cls._validate_retrieval_configuration(session, data)
        tool = await session.get(Tool, tool_id, with_for_update=True)
        if not tool:
            raise LookupError("Tool not found")
        maximum = await session.scalar(
            select(func.max(ToolRevision.revision_number)).where(
                ToolRevision.tool_id == tool_id
            )
        )
        revision = cls._make_revision(tool_id, (maximum or 0) + 1, data)
        session.add(revision)
        tool.active_revision_id = revision.id
        await session.commit()
        await session.refresh(revision)
        return revision

    @staticmethod
    async def list_tools(session: AsyncSession) -> list[Tool]:
        result = await session.execute(
            select(Tool).options(selectinload(Tool.revisions)).order_by(Tool.name)
        )
        return list(result.scalars().all())

    @staticmethod
    async def get_tool(session: AsyncSession, tool_id: uuid.UUID) -> Tool | None:
        result = await session.execute(
            select(Tool)
            .where(Tool.id == tool_id)
            .options(selectinload(Tool.revisions))
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def get_revision(
        session: AsyncSession, revision_id: uuid.UUID
    ) -> ToolRevision | None:
        return await session.get(ToolRevision, revision_id)
