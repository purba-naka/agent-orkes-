import uuid

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from orchestrator.db.session import get_db_session
from orchestrator.domain.knowledge_schemas import (
    KnowledgeBaseCreate,
    KnowledgeBaseResponse,
    KnowledgeDocumentCreate,
    KnowledgeDocumentResponse,
)
from orchestrator.retrieval.service import KnowledgeError, KnowledgeService

router = APIRouter(prefix="/api/v1", tags=["knowledge"])


@router.post(
    "/knowledge-bases",
    response_model=KnowledgeBaseResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_knowledge_base(
    payload: KnowledgeBaseCreate,
    session: AsyncSession = Depends(get_db_session),
) -> KnowledgeBaseResponse:
    try:
        knowledge_base = await KnowledgeService.create_knowledge_base(session, payload)
    except KnowledgeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=409, detail="Knowledge base name already exists") from exc
    return KnowledgeBaseResponse.model_validate(knowledge_base)


@router.get("/knowledge-bases", response_model=list[KnowledgeBaseResponse])
async def list_knowledge_bases(
    session: AsyncSession = Depends(get_db_session),
) -> list[KnowledgeBaseResponse]:
    return [
        KnowledgeBaseResponse.model_validate(item)
        for item in await KnowledgeService.list_knowledge_bases(session)
    ]


@router.post(
    "/knowledge-bases/{knowledge_base_id}/documents",
    response_model=KnowledgeDocumentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_document(
    knowledge_base_id: uuid.UUID,
    payload: KnowledgeDocumentCreate,
    session: AsyncSession = Depends(get_db_session),
) -> KnowledgeDocumentResponse:
    try:
        document = await KnowledgeService.create_document(
            session, knowledge_base_id, payload
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except KnowledgeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    documents = await KnowledgeService.list_documents(session, knowledge_base_id)
    created = next(item for item in documents if item["id"] == document.id)
    return KnowledgeDocumentResponse.model_validate(created)


@router.get(
    "/knowledge-bases/{knowledge_base_id}/documents",
    response_model=list[KnowledgeDocumentResponse],
)
async def list_documents(
    knowledge_base_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
) -> list[KnowledgeDocumentResponse]:
    try:
        documents = await KnowledgeService.list_documents(session, knowledge_base_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return [KnowledgeDocumentResponse.model_validate(item) for item in documents]


@router.delete(
    "/knowledge-bases/{knowledge_base_id}/documents/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_document(
    knowledge_base_id: uuid.UUID,
    document_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
) -> Response:
    try:
        deleted = await KnowledgeService.delete_document(
            session, knowledge_base_id, document_id
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="Document not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
