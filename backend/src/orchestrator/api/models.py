import time
import uuid
from fastapi import APIRouter, Depends, HTTPException, status
from langchain_core.messages import HumanMessage
from sqlalchemy.ext.asyncio import AsyncSession

from orchestrator.db.session import get_db_session
from orchestrator.domain.catalog import CatalogService
from orchestrator.domain.schemas import (
    ModelCreate,
    ModelResponse,
    ModelRevisionCreate,
    ModelRevisionResponse,
    ModelTestResult,
)

router = APIRouter(prefix="/api/v1", tags=["models"])


@router.post("/models", response_model=ModelResponse, status_code=status.HTTP_201_CREATED)
async def create_model(
    payload: ModelCreate,
    session: AsyncSession = Depends(get_db_session),
) -> ModelResponse:
    model, _ = await CatalogService.create_model(session, payload)
    return ModelResponse.model_validate(model)


@router.get("/models", response_model=list[ModelResponse])
async def list_models(
    session: AsyncSession = Depends(get_db_session),
) -> list[ModelResponse]:
    models = await CatalogService.list_models(session)
    return [ModelResponse.model_validate(m) for m in models]


@router.get("/models/{model_id}", response_model=ModelResponse)
async def get_model(
    model_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
) -> ModelResponse:
    model = await CatalogService.get_model(session, model_id)
    if not model:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Model not found",
        )
    return ModelResponse.model_validate(model)


@router.post(
    "/models/{model_id}/revisions",
    response_model=ModelRevisionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_model_revision(
    model_id: uuid.UUID,
    payload: ModelRevisionCreate,
    session: AsyncSession = Depends(get_db_session),
) -> ModelRevisionResponse:
    try:
        rev = await CatalogService.create_model_revision(session, model_id, payload)
        return ModelRevisionResponse.model_validate(rev)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        )


@router.get("/models/{model_id}/revisions/{revision_id}", response_model=ModelRevisionResponse)
async def get_model_revision_by_model(
    model_id: uuid.UUID,
    revision_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
) -> ModelRevisionResponse:
    rev = await CatalogService.get_model_revision(session, revision_id)
    if not rev or rev.model_id != model_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Model revision not found",
        )
    return ModelRevisionResponse.model_validate(rev)


@router.get("/model-revisions/{revision_id}", response_model=ModelRevisionResponse)
async def get_model_revision(
    revision_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
) -> ModelRevisionResponse:
    rev = await CatalogService.get_model_revision(session, revision_id)
    if not rev:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Model revision not found",
        )
    return ModelRevisionResponse.model_validate(rev)


@router.post("/models/{model_id}/test", response_model=ModelTestResult)
async def test_model_connection(
    model_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
) -> ModelTestResult:
    """Send a tiny ping message through the model's active revision to verify
    that the provider configuration (endpoint, API key, model name) works."""
    model = await CatalogService.get_model(session, model_id)
    if not model:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Model not found",
        )
    if not model.active_revision_id:
        return ModelTestResult(
            status="failed",
            model_id=model_id,
            error="Model has no active revision",
        )

    rev = await CatalogService.get_model_revision(session, model.active_revision_id)
    revision_number = rev.revision_number if rev else None

    try:
        chat_model = await CatalogService.resolve_chat_model(
            session, model.active_revision_id
        )
        started = time.perf_counter()
        response = await chat_model.ainvoke(
            [HumanMessage(content="Reply with the single word: pong")],
            config={"max_concurrency": 1},
        )
        latency_ms = int((time.perf_counter() - started) * 1000)
    except Exception as e:  # noqa: BLE001 - report provider errors to the caller
        return ModelTestResult(
            status="failed",
            model_id=model_id,
            revision_id=model.active_revision_id,
            revision_number=revision_number,
            error=str(e)[:500] or e.__class__.__name__,
        )

    preview = str(response.content).strip()[:200]
    return ModelTestResult(
        status="connected",
        model_id=model_id,
        revision_id=model.active_revision_id,
        revision_number=revision_number,
        latency_ms=latency_ms,
        response_preview=preview or None,
    )
