import uuid
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from orchestrator.db.session import get_db_session
from orchestrator.domain.catalog import CatalogService
from orchestrator.domain.schemas import (
    CredentialCreate,
    CredentialResponse,
    CredentialUpdate,
)

router = APIRouter(prefix="/api/v1/credentials", tags=["credentials"])


@router.post("", response_model=CredentialResponse, status_code=status.HTTP_201_CREATED)
async def create_credential(
    payload: CredentialCreate,
    session: AsyncSession = Depends(get_db_session),
) -> CredentialResponse:
    cred = await CatalogService.create_credential(session, payload)
    return CredentialResponse.model_validate(cred)


@router.get("", response_model=list[CredentialResponse])
async def list_credentials(
    session: AsyncSession = Depends(get_db_session),
) -> list[CredentialResponse]:
    creds = await CatalogService.list_credentials(session)
    return [CredentialResponse.model_validate(c) for c in creds]


@router.put("/{credential_id}", response_model=CredentialResponse)
async def update_credential(
    credential_id: uuid.UUID,
    payload: CredentialUpdate,
    session: AsyncSession = Depends(get_db_session),
) -> CredentialResponse:
    cred = await CatalogService.update_credential(session, credential_id, payload)
    if not cred:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Credential not found",
        )
    return CredentialResponse.model_validate(cred)


@router.post("/{credential_id}/disable", response_model=CredentialResponse)
async def disable_credential(
    credential_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
) -> CredentialResponse:
    cred = await CatalogService.disable_credential(session, credential_id)
    if not cred:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Credential not found",
        )
    return CredentialResponse.model_validate(cred)


@router.delete("/{credential_id}", response_model=CredentialResponse)
async def delete_credential(
    credential_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
) -> CredentialResponse:
    cred = await CatalogService.disable_credential(session, credential_id)
    if not cred:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Credential not found",
        )
    return CredentialResponse.model_validate(cred)
