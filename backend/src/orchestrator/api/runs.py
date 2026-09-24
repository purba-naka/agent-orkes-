import uuid
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from orchestrator.db.models import Run, RunInterrupt
from orchestrator.db.session import get_db_session
from orchestrator.domain.agent_schemas import (
    InterruptDecision,
    RunInterruptResponse,
    RunResponse,
)
from orchestrator.runtime.hitl import InterruptDecisionError, prepare_resume
from orchestrator.runtime.resume import resume_run_stream

router = APIRouter(prefix="/api/v1/runs", tags=["runs"])


@router.get("/{run_id}", response_model=RunResponse)
async def get_run(
    run_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
) -> RunResponse:
    row = await session.execute(
        select(Run).where(Run.id == run_id).options(selectinload(Run.interrupts))
    )
    run = row.scalar_one_or_none()
    if not run:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Run not found",
        )
    return RunResponse.model_validate(run)


@router.get("/{run_id}/interrupts", response_model=list[RunInterruptResponse])
async def list_pending_interrupts(
    run_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
) -> list[RunInterruptResponse]:
    run = await session.get(Run, run_id)
    if not run:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Run not found",
        )
    rows = await session.execute(
        select(RunInterrupt)
        .where(
            RunInterrupt.run_id == run_id,
            RunInterrupt.status == "pending",
        )
        .order_by(RunInterrupt.created_at)
    )
    return [RunInterruptResponse.model_validate(item) for item in rows.scalars()]


@router.post("/{run_id}/resume")
async def resume_run(
    run_id: uuid.UUID,
    payload: InterruptDecision,
    session: AsyncSession = Depends(get_db_session),
) -> StreamingResponse:
    try:
        # Validate and reserve the interrupt before returning a streaming response.
        run, revision, _, native_value = await prepare_resume(
            session, run_id, payload.model_dump()
        )
    except InterruptDecisionError as exc:
        code = (
            status.HTTP_409_CONFLICT
            if exc.code == "interrupt_already_resolved"
            else status.HTTP_404_NOT_FOUND
            if exc.code == "interrupt_not_found"
            else status.HTTP_422_UNPROCESSABLE_ENTITY
        )
        raise HTTPException(
            status_code=code,
            detail={"error": exc.code, "message": str(exc)},
        ) from exc

    stream = resume_run_stream(
            run_id,
            payload.model_dump(),
            revision=revision,
            thread_id=run.thread_id,
            conversation_id=run.conversation_id,
            native_value=native_value,
        )
    try:
        first_chunk = await anext(stream)
    except StopAsyncIteration as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "resume_failed", "message": "Resume produced no output"},
        ) from exc

    async def response_stream():
        yield first_chunk
        async for chunk in stream:
            yield chunk

    return StreamingResponse(
        response_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
