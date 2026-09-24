import uuid
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from orchestrator.db.models import AgentRevision
from orchestrator.db.session import get_db_session
from orchestrator.domain.agent_schemas import (
    AgentCreate,
    AgentDraftResponse,
    AgentDraftUpdate,
    AgentResponse,
    AgentRevisionResponse,
    RunCreate,
)
from orchestrator.domain.agents import (
    AgentService,
    DraftConflictError,
    PublishValidationError,
)
from orchestrator.runtime.runner import run_agent_stream

router = APIRouter(prefix="/api/v1", tags=["agents"])


@router.post("/agents", response_model=AgentResponse, status_code=status.HTTP_201_CREATED)
async def create_agent(
    payload: AgentCreate,
    session: AsyncSession = Depends(get_db_session),
) -> AgentResponse:
    agent = await AgentService.create_agent(session, payload)
    return AgentResponse.model_validate(agent)


@router.get("/agents", response_model=list[AgentResponse])
async def list_agents(
    session: AsyncSession = Depends(get_db_session),
) -> list[AgentResponse]:
    agents = await AgentService.list_agents(session)
    return [AgentResponse.model_validate(a) for a in agents]


@router.get("/agents/{agent_id}", response_model=AgentResponse)
async def get_agent(
    agent_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
) -> AgentResponse:
    agent = await AgentService.get_agent(session, agent_id)
    if not agent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Agent not found",
        )
    return AgentResponse.model_validate(agent)


@router.get("/agents/{agent_id}/draft", response_model=AgentDraftResponse)
async def get_agent_draft(
    agent_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
) -> AgentDraftResponse:
    agent = await AgentService.get_agent(session, agent_id)
    if not agent or not agent.draft:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Agent draft not found",
        )
    return AgentDraftResponse.model_validate(agent.draft)


@router.put("/agents/{agent_id}/draft", response_model=AgentDraftResponse)
async def update_agent_draft(
    agent_id: uuid.UUID,
    payload: AgentDraftUpdate,
    session: AsyncSession = Depends(get_db_session),
) -> AgentDraftResponse:
    try:
        draft, _ = await AgentService.save_draft(session, agent_id, payload)
        return AgentDraftResponse.model_validate(draft)
    except DraftConflictError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "draft_conflict",
                "message": str(e),
                "current_version": e.current_version,
                "current_draft": AgentDraftResponse.model_validate(e.current_draft).model_dump(mode="json"),
            },
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        )


@router.post("/agents/{agent_id}/publish", response_model=AgentRevisionResponse)
async def publish_agent(
    agent_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
) -> AgentRevisionResponse:
    try:
        revision, _ = await AgentService.publish_agent(session, agent_id)
        return AgentRevisionResponse.model_validate(revision)
    except PublishValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error": "publish_validation_failed",
                "diagnostics": [d.model_dump() for d in e.diagnostics],
            },
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        )


@router.get("/agents/{agent_id}/revisions", response_model=list[AgentRevisionResponse])
async def list_agent_revisions(
    agent_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
) -> list[AgentRevisionResponse]:
    revs = await AgentService.list_revisions(session, agent_id)
    return [AgentRevisionResponse.model_validate(r) for r in revs]


@router.get("/agent-revisions/{revision_id}", response_model=AgentRevisionResponse)
async def get_agent_revision(
    revision_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
) -> AgentRevisionResponse:
    rev = await AgentService.get_revision(session, revision_id)
    if not rev:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Agent revision not found",
        )
    return AgentRevisionResponse.model_validate(rev)


@router.post("/agents/{agent_id}/runs")
async def create_agent_run(
    agent_id: uuid.UUID,
    payload: RunCreate,
    session: AsyncSession = Depends(get_db_session),
) -> StreamingResponse:
    agent = await AgentService.get_agent(session, agent_id)
    if not agent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Agent not found",
        )
    if not agent.active_revision_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Agent has no active published revision. Publish the agent first.",
        )

    revision = await session.get(AgentRevision, agent.active_revision_id)
    if not revision:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Active revision record missing",
        )

    return StreamingResponse(
        run_agent_stream(agent, revision, payload.input),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
