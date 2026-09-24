from typing import Any
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from orchestrator.db.session import get_db_session
from orchestrator.domain.agent_schemas import (
    ConversationCreate,
    ConversationDetailResponse,
    ConversationMessageCreate,
    ConversationResponse,
    ConversationUpgrade,
)
from orchestrator.domain.conversations import ConversationService
from orchestrator.runtime.runner import run_conversation_stream

router = APIRouter(prefix="/api/v1/conversations", tags=["conversations"])


@router.post(
    "",
    response_model=ConversationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_conversation(
    payload: ConversationCreate,
    session: AsyncSession = Depends(get_db_session),
) -> Any:
    conv = await ConversationService.create_conversation(session, payload)
    return conv


@router.get("", response_model=list[ConversationResponse])
async def list_conversations(
    agent_id: uuid.UUID | None = None,
    session: AsyncSession = Depends(get_db_session),
) -> Any:
    return await ConversationService.list_conversations(session, agent_id=agent_id)


@router.get("/{conversation_id}", response_model=ConversationDetailResponse)
async def get_conversation(
    conversation_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
) -> Any:
    detail = await ConversationService.get_conversation_detail(session, conversation_id)
    if not detail:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "conversation_not_found"},
        )
    return detail


@router.post("/{conversation_id}/messages")
async def send_conversation_message(
    conversation_id: uuid.UUID,
    payload: ConversationMessageCreate,
    session: AsyncSession = Depends(get_db_session),
) -> Any:
    detail = await ConversationService.get_conversation_detail(session, conversation_id)
    if not detail:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "conversation_not_found"},
        )

    return StreamingResponse(
        run_conversation_stream(conversation_id, payload.model_dump(exclude_unset=True)),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post(
    "/{conversation_id}/upgrade",
    response_model=ConversationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upgrade_conversation(
    conversation_id: uuid.UUID,
    payload: ConversationUpgrade,
    session: AsyncSession = Depends(get_db_session),
) -> Any:
    return await ConversationService.upgrade_conversation(session, conversation_id, payload)


@router.post("/{conversation_id}/rebuild-projections")
async def rebuild_conversation_projections(
    conversation_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
) -> Any:
    count = await ConversationService.rebuild_projections(session, conversation_id)
    return {"conversation_id": conversation_id, "rebuilt_count": count}
