from datetime import datetime
import logging
from typing import Any
import uuid

from fastapi import HTTPException
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from orchestrator.config import settings
from orchestrator.db.models import (
    Agent,
    AgentRevision,
    Conversation,
    Message,
    Run,
    RunInterrupt,
    utcnow,
)
from orchestrator.domain.agent_schemas import (
    ConversationCreate,
    ConversationDetailResponse,
    ConversationUpgrade,
    MessageResponse,
    RunResponse,
)

logger = logging.getLogger(__name__)


def parse_checkpoint_message(m: BaseMessage) -> tuple[str, str, Any]:
    msg_id = str(m.id or uuid.uuid4())
    if isinstance(m, HumanMessage) or getattr(m, "type", "") in ("human", "user"):
        role = "user"
    elif isinstance(m, AIMessage) or getattr(m, "type", "") in ("ai", "assistant"):
        role = "assistant"
    elif isinstance(m, SystemMessage) or getattr(m, "type", "") == "system":
        role = "system"
    elif isinstance(m, ToolMessage) or getattr(m, "type", "") == "tool":
        role = "tool"
    else:
        role = "user" if getattr(m, "type", "") == "user" else "assistant"

    if isinstance(m.content, str):
        content = [{"type": "text", "text": m.content}] if m.content else []
    elif isinstance(m.content, list):
        content = list(m.content)
    else:
        content = [{"type": "text", "text": str(m.content)}]

    # Keep what the chat UI needs to show the agent's steps: reasoning, the
    # tool calls it made, and which call a tool result answers.
    reasoning = (getattr(m, "additional_kwargs", None) or {}).get("reasoning_content")
    if reasoning:
        content.insert(0, {"type": "reasoning", "reasoning": str(reasoning)})
    for call in getattr(m, "tool_calls", None) or []:
        content.append(
            {"type": "tool_call", "id": call.get("id"), "name": call.get("name"), "args": call.get("args", {})}
        )
    if role == "tool":
        content = [
            {
                "type": "tool_result",
                "tool_call_id": getattr(m, "tool_call_id", None),
                "name": getattr(m, "name", None),
                "status": getattr(m, "status", "success"),
                "content": m.content if isinstance(m.content, str) else str(m.content),
            }
        ]

    return msg_id, role, content


class ConversationService:
    @staticmethod
    async def create_conversation(
        session: AsyncSession, data: ConversationCreate
    ) -> Conversation:
        agent = await session.get(Agent, data.agent_id)
        if not agent:
            raise HTTPException(status_code=404, detail={"error": "agent_not_found"})

        if not agent.active_revision_id:
            raise HTTPException(
                status_code=400,
                detail={"error": "agent_has_no_published_revision"},
            )

        title = data.title.strip()
        if not title:
            title = f"Conversation with {agent.name}"

        conv_id = uuid.uuid4()
        conv = Conversation(
            id=conv_id,
            agent_revision_id=agent.active_revision_id,
            title=title,
            parent_conversation_id=None,
            upgrade_summary=None,
            created_at=utcnow(),
            updated_at=utcnow(),
        )
        session.add(conv)
        await session.commit()
        await session.refresh(conv)
        return conv

    @staticmethod
    async def list_conversations(
        session: AsyncSession, agent_id: uuid.UUID | None = None
    ) -> list[Conversation]:
        stmt = (
            select(Conversation)
            .options(
                selectinload(Conversation.agent_revision).selectinload(AgentRevision.agent)
            )
            .order_by(Conversation.updated_at.desc())
        )
        if agent_id:
            stmt = stmt.join(AgentRevision, Conversation.agent_revision_id == AgentRevision.id).where(
                AgentRevision.agent_id == agent_id
            )

        res = await session.execute(stmt)
        return list(res.scalars().all())

    @staticmethod
    async def get_conversation_detail(
        session: AsyncSession, conversation_id: uuid.UUID
    ) -> ConversationDetailResponse | None:
        stmt = (
            select(Conversation)
            .where(Conversation.id == conversation_id)
            .options(
                selectinload(Conversation.agent_revision).selectinload(AgentRevision.agent),
                selectinload(Conversation.messages),
                selectinload(Conversation.runs).selectinload(Run.interrupts),
            )
        )
        res = await session.execute(stmt)
        conv = res.scalar_one_or_none()
        if not conv:
            return None

        agent_rev = conv.agent_revision
        agent = agent_rev.agent if agent_rev else None

        messages_sorted = sorted(conv.messages, key=lambda m: m.sequence)
        runs_sorted = sorted(conv.runs, key=lambda r: r.started_at, reverse=True)

        return ConversationDetailResponse(
            id=conv.id,
            agent_revision_id=conv.agent_revision_id,
            title=conv.title,
            parent_conversation_id=conv.parent_conversation_id,
            upgrade_summary=conv.upgrade_summary,
            created_at=conv.created_at,
            updated_at=conv.updated_at,
            agent_id=agent.id if agent else None,
            agent_name=agent.name if agent else None,
            revision_number=agent_rev.revision_number if agent_rev else None,
            messages=[MessageResponse.model_validate(m) for m in messages_sorted],
            runs=[RunResponse.model_validate(r) for r in runs_sorted],
        )

    @staticmethod
    async def project_messages_from_checkpoint(
        session: AsyncSession,
        conversation_id: uuid.UUID,
        run_id: uuid.UUID,
    ) -> int:
        async with AsyncPostgresSaver.from_conn_string(settings.checkpointer_url) as checkpointer:
            checkpoint_tuple = await checkpointer.aget_tuple(
                {"configurable": {"thread_id": str(conversation_id)}}
            )

        if not checkpoint_tuple or "channel_values" not in checkpoint_tuple.checkpoint:
            return 0

        checkpoint_messages = checkpoint_tuple.checkpoint["channel_values"].get("messages", [])
        if not checkpoint_messages:
            return 0

        existing_stmt = select(Message).where(Message.conversation_id == conversation_id)
        existing_res = await session.execute(existing_stmt)
        existing_map = {m.langgraph_message_id: m for m in existing_res.scalars().all()}

        count = 0
        for idx, m in enumerate(checkpoint_messages):
            msg_id, role, content = parse_checkpoint_message(m)
            sequence = idx + 1
            existing = existing_map.get(msg_id)

            target_run_id = existing.run_id if existing else run_id

            stmt = insert(Message).values(
                id=existing.id if existing else uuid.uuid4(),
                conversation_id=conversation_id,
                run_id=target_run_id,
                langgraph_message_id=msg_id,
                role=role,
                content=content,
                sequence=sequence,
                created_at=existing.created_at if existing else utcnow(),
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=["conversation_id", "langgraph_message_id"],
                set_={
                    "role": stmt.excluded.role,
                    "content": stmt.excluded.content,
                    "sequence": stmt.excluded.sequence,
                },
            )
            await session.execute(stmt)
            count += 1

        await session.commit()
        return count

    @staticmethod
    async def rebuild_projections(
        session: AsyncSession,
        conversation_id: uuid.UUID,
    ) -> int:
        conv = await session.get(Conversation, conversation_id)
        if not conv:
            raise HTTPException(status_code=404, detail={"error": "conversation_not_found"})

        async with AsyncPostgresSaver.from_conn_string(settings.checkpointer_url) as checkpointer:
            checkpoint_tuple = await checkpointer.aget_tuple(
                {"configurable": {"thread_id": str(conversation_id)}}
            )

        if not checkpoint_tuple or "channel_values" not in checkpoint_tuple.checkpoint:
            return 0

        checkpoint_messages = checkpoint_tuple.checkpoint["channel_values"].get("messages", [])
        if not checkpoint_messages:
            return 0

        latest_run_stmt = (
            select(Run.id)
            .where(Run.conversation_id == conversation_id)
            .order_by(Run.started_at.desc())
            .limit(1)
        )
        fallback_run_id = (await session.execute(latest_run_stmt)).scalar()
        if not fallback_run_id:
            fallback_run_id = uuid.uuid4()
            run = Run(
                id=fallback_run_id,
                agent_revision_id=conv.agent_revision_id,
                conversation_id=conversation_id,
                thread_id=conversation_id,
                mode="conversation",
                status="completed",
                started_at=utcnow(),
                finished_at=utcnow(),
            )
            session.add(run)
            await session.flush()

        existing_stmt = select(Message).where(Message.conversation_id == conversation_id)
        existing_res = await session.execute(existing_stmt)
        existing_map = {m.langgraph_message_id: m for m in existing_res.scalars().all()}

        active_msg_ids = set()
        count = 0
        for idx, m in enumerate(checkpoint_messages):
            msg_id, role, content = parse_checkpoint_message(m)
            active_msg_ids.add(msg_id)
            sequence = idx + 1
            existing = existing_map.get(msg_id)

            target_run_id = existing.run_id if existing else fallback_run_id

            stmt = insert(Message).values(
                id=existing.id if existing else uuid.uuid4(),
                conversation_id=conversation_id,
                run_id=target_run_id,
                langgraph_message_id=msg_id,
                role=role,
                content=content,
                sequence=sequence,
                created_at=existing.created_at if existing else utcnow(),
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=["conversation_id", "langgraph_message_id"],
                set_={
                    "role": stmt.excluded.role,
                    "content": stmt.excluded.content,
                    "sequence": stmt.excluded.sequence,
                },
            )
            await session.execute(stmt)
            count += 1

        if active_msg_ids:
            delete_stmt = delete(Message).where(
                Message.conversation_id == conversation_id,
                Message.langgraph_message_id.not_in(active_msg_ids),
            )
            await session.execute(delete_stmt)

        await session.commit()
        return count

    @staticmethod
    async def upgrade_conversation(
        session: AsyncSession,
        conversation_id: uuid.UUID,
        data: ConversationUpgrade,
    ) -> Conversation:
        stmt = (
            select(Conversation)
            .where(Conversation.id == conversation_id)
            .options(
                selectinload(Conversation.agent_revision).selectinload(AgentRevision.agent)
            )
        )
        res = await session.execute(stmt)
        old_conv = res.scalar_one_or_none()
        if not old_conv:
            raise HTTPException(status_code=404, detail={"error": "conversation_not_found"})

        agent = old_conv.agent_revision.agent if old_conv.agent_revision else None
        if not agent:
            raise HTTPException(status_code=404, detail={"error": "agent_not_found"})

        if not agent.active_revision_id:
            raise HTTPException(
                status_code=400,
                detail={"error": "agent_has_no_active_revision"},
            )

        new_conv = Conversation(
            id=uuid.uuid4(),
            agent_revision_id=agent.active_revision_id,
            title=f"{old_conv.title} (Upgraded)",
            parent_conversation_id=old_conv.id,
            upgrade_summary=data.summary,
            created_at=utcnow(),
            updated_at=utcnow(),
        )
        session.add(new_conv)
        await session.commit()
        await session.refresh(new_conv)
        return new_conv
