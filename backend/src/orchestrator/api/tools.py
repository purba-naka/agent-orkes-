import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from orchestrator.db.session import get_db_session
from orchestrator.domain.tool_schemas import (
    ToolCreate,
    ToolResponse,
    ToolRevisionCreate,
    ToolRevisionResponse,
)
from orchestrator.domain.tools import ToolCatalogService, ToolConfigurationError

router = APIRouter(prefix="/api/v1", tags=["tools"])


@router.post("/tools", response_model=ToolResponse, status_code=status.HTTP_201_CREATED)
async def create_tool(
    payload: ToolCreate, session: AsyncSession = Depends(get_db_session)
) -> ToolResponse:
    try:
        tool, _ = await ToolCatalogService.create_tool(session, payload)
        return ToolResponse.model_validate(tool)
    except ToolConfigurationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=409, detail="Tool name already exists") from exc


@router.get("/tools", response_model=list[ToolResponse])
async def list_tools(
    session: AsyncSession = Depends(get_db_session),
) -> list[ToolResponse]:
    return [
        ToolResponse.model_validate(tool)
        for tool in await ToolCatalogService.list_tools(session)
    ]


@router.get("/tools/{tool_id}", response_model=ToolResponse)
async def get_tool(
    tool_id: uuid.UUID, session: AsyncSession = Depends(get_db_session)
) -> ToolResponse:
    tool = await ToolCatalogService.get_tool(session, tool_id)
    if not tool:
        raise HTTPException(status_code=404, detail="Tool not found")
    return ToolResponse.model_validate(tool)


@router.post(
    "/tools/{tool_id}/revisions",
    response_model=ToolRevisionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_tool_revision(
    tool_id: uuid.UUID,
    payload: ToolRevisionCreate,
    session: AsyncSession = Depends(get_db_session),
) -> ToolRevisionResponse:
    try:
        revision = await ToolCatalogService.create_revision(session, tool_id, payload)
        return ToolRevisionResponse.model_validate(revision)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ToolConfigurationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get(
    "/tools/{tool_id}/revisions/{revision_id}", response_model=ToolRevisionResponse
)
async def get_tool_revision_by_tool(
    tool_id: uuid.UUID,
    revision_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
) -> ToolRevisionResponse:
    revision = await ToolCatalogService.get_revision(session, revision_id)
    if not revision or revision.tool_id != tool_id:
        raise HTTPException(status_code=404, detail="Tool revision not found")
    return ToolRevisionResponse.model_validate(revision)


@router.get("/tool-revisions/{revision_id}", response_model=ToolRevisionResponse)
async def get_tool_revision(
    revision_id: uuid.UUID, session: AsyncSession = Depends(get_db_session)
) -> ToolRevisionResponse:
    revision = await ToolCatalogService.get_revision(session, revision_id)
    if not revision:
        raise HTTPException(status_code=404, detail="Tool revision not found")
    return ToolRevisionResponse.model_validate(revision)
