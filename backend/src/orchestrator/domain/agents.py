from typing import Any
import uuid
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from orchestrator.db.models import (
    Agent,
    AgentDraft,
    AgentRevision,
    AgentRevisionDependency,
)
from orchestrator.domain.agent_schemas import (
    AgentCreate,
    AgentDraftUpdate,
    Diagnostic,
    make_default_agent_document,
)
from orchestrator.domain.canonical import compute_content_hash
from orchestrator.domain.validation import (
    validate_draft_document,
    validate_publish_document,
)


class DraftConflictError(Exception):
    def __init__(self, current_version: int, current_draft: AgentDraft):
        super().__init__(f"Draft conflict: expected version is stale (current is {current_version})")
        self.current_version = current_version
        self.current_draft = current_draft


class PublishValidationError(Exception):
    def __init__(self, diagnostics: list[Diagnostic]):
        super().__init__("Publish validation failed")
        self.diagnostics = diagnostics


class AgentService:
    @staticmethod
    async def create_agent(session: AsyncSession, data: AgentCreate) -> Agent:
        agent_id = uuid.uuid4()
        agent = Agent(
            id=agent_id,
            name=data.name,
            description=data.description,
            active_revision_id=None,
        )
        session.add(agent)
        await session.flush()

        doc = make_default_agent_document(
            name=data.name,
            system_prompt=data.system_prompt,
            model_revision_id=str(data.model_revision_id) if data.model_revision_id else None,
        )
        diagnostics = validate_draft_document(doc)
        draft = AgentDraft(
            agent_id=agent_id,
            document=doc,
            validation=[d.model_dump() for d in diagnostics],
            version=1,
        )
        session.add(draft)
        await session.commit()
        refreshed = await AgentService.get_agent(session, agent_id)
        assert refreshed is not None
        return refreshed

    @staticmethod
    async def get_agent(session: AsyncSession, agent_id: uuid.UUID) -> Agent | None:
        stmt = (
            select(Agent)
            .where(Agent.id == agent_id)
            .options(
                selectinload(Agent.draft),
                selectinload(Agent.revisions),
            )
        )
        res = await session.execute(stmt)
        return res.scalar_one_or_none()

    @staticmethod
    async def list_agents(session: AsyncSession) -> list[Agent]:
        stmt = (
            select(Agent)
            .options(
                selectinload(Agent.draft),
                selectinload(Agent.revisions),
            )
            .order_by(Agent.created_at.desc())
        )
        res = await session.execute(stmt)
        return list(res.scalars().all())

    @staticmethod
    async def save_draft(
        session: AsyncSession, agent_id: uuid.UUID, data: AgentDraftUpdate
    ) -> tuple[AgentDraft, list[Diagnostic]]:
        draft = await session.get(AgentDraft, agent_id)
        if not draft:
            raise ValueError(f"Draft for agent {agent_id} not found")

        if draft.version != data.version:
            raise DraftConflictError(draft.version, draft)

        diagnostics = validate_draft_document(data.document)
        draft.document = data.document
        draft.validation = [d.model_dump() for d in diagnostics]
        draft.version += 1
        await session.commit()
        await session.refresh(draft)
        return draft, diagnostics

    @staticmethod
    async def publish_agent(
        session: AsyncSession, agent_id: uuid.UUID
    ) -> tuple[AgentRevision, list[Diagnostic]]:
        agent = await session.get(Agent, agent_id, with_for_update=True)
        if not agent:
            raise ValueError(f"Agent {agent_id} not found")

        draft = await session.get(AgentDraft, agent_id)
        if not draft:
            raise ValueError(f"Draft for agent {agent_id} not found")

        is_valid, diagnostics, manifest = await validate_publish_document(
            session, draft.document, owner_agent_id=agent_id
        )
        if not is_valid:
            raise PublishValidationError(diagnostics)

        content_hash = compute_content_hash(draft.document)

        # Idempotency check: if identical content hash exists for this agent, reuse it
        stmt = select(AgentRevision).where(
            AgentRevision.agent_id == agent_id,
            AgentRevision.content_hash == content_hash,
        )
        existing = (await session.execute(stmt)).scalar_one_or_none()
        if existing:
            agent.active_revision_id = existing.id
            await session.commit()
            return existing, diagnostics

        # Compute next revision number
        rev_stmt = select(func.max(AgentRevision.revision_number)).where(
            AgentRevision.agent_id == agent_id
        )
        max_rev = (await session.execute(rev_stmt)).scalar() or 0
        new_rev_number = max_rev + 1

        rev_id = uuid.uuid4()
        revision = AgentRevision(
            id=rev_id,
            agent_id=agent_id,
            revision_number=new_rev_number,
            document=draft.document,
            dependency_manifest=manifest,
            content_hash=content_hash,
        )
        session.add(revision)

        # Persist every pinned revision dependency represented by a UUID.
        for kind in ("agent", "model", "tool"):
            manifest_key = f"{kind}s"
            for dependency_id in manifest.get(manifest_key, []):
                session.add(
                    AgentRevisionDependency(
                        owner_revision_id=rev_id,
                        dependency_kind=kind,
                        dependency_revision_id=uuid.UUID(dependency_id),
                    )
                )

        agent.active_revision_id = rev_id
        await session.commit()
        await session.refresh(revision)
        return revision, diagnostics

    @staticmethod
    async def get_revision(
        session: AsyncSession, revision_id: uuid.UUID
    ) -> AgentRevision | None:
        return await session.get(AgentRevision, revision_id)

    @staticmethod
    async def list_revisions(
        session: AsyncSession, agent_id: uuid.UUID
    ) -> list[AgentRevision]:
        stmt = (
            select(AgentRevision)
            .where(AgentRevision.agent_id == agent_id)
            .order_by(AgentRevision.revision_number.desc())
        )
        res = await session.execute(stmt)
        return list(res.scalars().all())
