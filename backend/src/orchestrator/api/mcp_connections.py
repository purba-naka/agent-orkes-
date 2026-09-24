from datetime import datetime
from urllib.parse import urlencode
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import PlainTextResponse, RedirectResponse, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from orchestrator.config import settings
from orchestrator.db.models import McpConnection
from orchestrator.db.session import get_db_session
from orchestrator.tools.adapters import ToolInvocationError, ToolInvoker
from orchestrator.tools.mcp_oauth import McpOAuthError, McpOAuthService
from orchestrator.tools.network import NetworkPolicyError

router = APIRouter(prefix="/api/v1/mcp-connections", tags=["mcp-connections"])


class McpConnectionCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    server_url: str = Field(..., min_length=1, max_length=2048)
    scope: str | None = Field(default=None, max_length=1024)


class McpConnectionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    server_url: str
    status: str
    scope: str | None
    expires_at: datetime | None
    created_at: datetime
    updated_at: datetime


class McpRemoteTool(BaseModel):
    name: str
    title: str | None = None
    description: str | None = None
    input_schema: dict = Field(default_factory=lambda: {"type": "object"})
    output_schema: dict | None = None


class McpAuthorization(BaseModel):
    connection: McpConnectionResponse
    authorization_url: str


def _service() -> McpOAuthService:
    return McpOAuthService()


def _browser_origin(request: Request) -> str:
    """Where the user's browser reaches us. Allowlisted: it becomes redirect_uri."""
    origin = request.headers.get("origin")
    if origin is None:
        return str(request.base_url).rstrip("/")
    if origin not in settings.cors_origins:
        raise HTTPException(status_code=403, detail="Origin is not allowed")
    return origin


@router.post("", response_model=McpAuthorization, status_code=status.HTTP_201_CREATED)
async def create_connection(
    payload: McpConnectionCreate,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    service: McpOAuthService = Depends(_service),
) -> McpAuthorization:
    origin = _browser_origin(request)
    try:
        connection, url = await service.create(
            session,
            name=payload.name,
            server_url=payload.server_url,
            origin=origin,
            scope=payload.scope,
        )
    except (McpOAuthError, NetworkPolicyError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=409, detail="Connection name already exists") from exc
    return McpAuthorization(
        connection=McpConnectionResponse.model_validate(connection), authorization_url=url
    )


@router.get("", response_model=list[McpConnectionResponse])
async def list_connections(
    session: AsyncSession = Depends(get_db_session),
) -> list[McpConnectionResponse]:
    rows = await session.scalars(select(McpConnection).order_by(McpConnection.created_at.desc()))
    return [McpConnectionResponse.model_validate(row) for row in rows]


@router.post("/{connection_id}/authorize", response_model=McpAuthorization)
async def reauthorize_connection(
    connection_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
    service: McpOAuthService = Depends(_service),
) -> McpAuthorization:
    connection = await session.get(McpConnection, connection_id)
    if not connection:
        raise HTTPException(status_code=404, detail="Connection not found")
    url = await service.reauthorize(session, connection)
    return McpAuthorization(
        connection=McpConnectionResponse.model_validate(connection), authorization_url=url
    )


@router.get("/{connection_id}/tools", response_model=list[McpRemoteTool])
async def list_remote_tools(
    connection_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
    service: McpOAuthService = Depends(_service),
) -> list[McpRemoteTool]:
    connection = await session.get(McpConnection, connection_id)
    if not connection:
        raise HTTPException(status_code=404, detail="Connection not found")
    invoker = ToolInvoker(
        session, network_policy=service.network_policy, transport=service.transport
    )
    try:
        tools = await invoker.list_mcp_tools(
            {"server_url": connection.server_url, "connection_id": str(connection.id)}
        )
    except (ToolInvocationError, NetworkPolicyError) as exc:
        code = 409 if getattr(exc, "code", "") == "mcp_auth_required" else 502
        raise HTTPException(status_code=code, detail=str(exc)) from exc
    return [
        McpRemoteTool(
            name=str(tool["name"]),
            title=tool.get("title"),
            description=tool.get("description"),
            input_schema=tool.get("inputSchema") or {"type": "object"},
            output_schema=tool.get("outputSchema"),
        )
        for tool in tools
    ]


@router.delete("/{connection_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_connection(
    connection_id: uuid.UUID, session: AsyncSession = Depends(get_db_session)
) -> None:
    connection = await session.get(McpConnection, connection_id)
    if not connection:
        raise HTTPException(status_code=404, detail="Connection not found")
    await session.delete(connection)
    await session.commit()


@router.get("/callback", include_in_schema=False)
async def oauth_callback(
    state: str | None = None,
    code: str | None = None,
    error: str | None = None,
    session: AsyncSession = Depends(get_db_session),
    service: McpOAuthService = Depends(_service),
) -> Response:
    # The pending connection holds the return page; unknown state has nowhere
    # trusted to redirect to, so it gets a plain error instead.
    connection = (
        await session.scalar(select(McpConnection).where(McpConnection.oauth_state == state))
        if state
        else None
    )
    if not connection:
        return PlainTextResponse("Unknown or already used OAuth state.", status_code=400)
    return_url = connection.return_url
    result: dict[str, str]
    if error or not code:
        result = {"mcp_oauth": "error", "reason": error or "missing_code"}
    else:
        try:
            await service.complete(session, state=state, code=code)
            result = {"mcp_oauth": "connected", "connection": str(connection.id)}
        except (McpOAuthError, NetworkPolicyError) as exc:
            result = {"mcp_oauth": "error", "reason": str(exc)}
    return RedirectResponse(f"{return_url}?{urlencode(result)}", status_code=303)
