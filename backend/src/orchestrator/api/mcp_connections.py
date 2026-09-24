from datetime import datetime
from typing import Literal
from urllib.parse import urlencode
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import PlainTextResponse, RedirectResponse, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from orchestrator.config import settings
from orchestrator.db.models import McpConnection, McpToolSnapshot
from orchestrator.db.session import get_db_session
from orchestrator.tools.adapters import ToolInvocationError, ToolInvoker
from orchestrator.tools.mcp_config import connection_rpc_config, store_connection_env
from orchestrator.tools.mcp_oauth import McpOAuthError, McpOAuthService
from orchestrator.tools.mcp_snapshots import McpSnapshotService
from orchestrator.tools.mcp_stdio import stdio_manager
from orchestrator.tools.network import NetworkPolicyError

router = APIRouter(prefix="/api/v1/mcp-connections", tags=["mcp-connections"])


class McpConnectionCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    transport: Literal["streamable_http", "sse", "stdio"] = "streamable_http"
    auth: Literal["oauth", "none"] = "oauth"
    server_url: str | None = Field(default=None, min_length=1, max_length=2048)
    scope: str | None = Field(default=None, max_length=1024)
    # stdio only: the executable to spawn, its argv, and its env (encrypted at rest).
    command: str | None = Field(default=None, min_length=1, max_length=1024)
    args: list[str] = Field(default_factory=list, max_length=64)
    env: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check_shape(self) -> "McpConnectionCreate":
        if self.transport == "stdio":
            if not self.command:
                raise ValueError("stdio transport requires a command")
            if self.auth != "none":
                raise ValueError("stdio transport does not use OAuth")
        elif not self.server_url:
            raise ValueError("remote transports require a server_url")
        return self


class McpRemoteTool(BaseModel):
    name: str
    title: str | None = None
    description: str | None = None
    input_schema: dict = Field(default_factory=lambda: {"type": "object"})
    output_schema: dict | None = None


class McpSnapshotTool(BaseModel):
    name: str
    title: str | None = None
    description: str | None = None
    input_schema: dict = Field(default_factory=lambda: {"type": "object"})
    output_schema: dict | None = None
    annotations: dict | None = None


class McpSnapshotSummary(BaseModel):
    id: uuid.UUID
    tools_hash: str
    tool_count: int
    created_at: datetime


class McpToolSnapshotResponse(BaseModel):
    id: uuid.UUID
    connection_id: uuid.UUID
    tools_hash: str
    tool_count: int
    tools: list[McpSnapshotTool]
    created_at: datetime


class McpConnectionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    transport: str
    auth: str
    server_url: str | None
    command: str | None
    args: list[str] | None
    status: str
    scope: str | None
    expires_at: datetime | None
    created_at: datetime
    updated_at: datetime
    latest_snapshot: McpSnapshotSummary | None = None


class McpAuthorization(BaseModel):
    connection: McpConnectionResponse
    # Null for connections that skip the OAuth dance (no-auth, stdio).
    authorization_url: str | None = None


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
    if payload.transport == "sse":
        raise HTTPException(
            status_code=422, detail="Transport 'sse' is not supported yet"
        )
    if payload.transport == "stdio" and payload.command not in settings.mcp_stdio_command_allowlist:
        raise HTTPException(
            status_code=403, detail="stdio command is not allowlisted"
        )
    try:
        if payload.auth == "none":
            connection = await _create_direct(session, payload)
            return McpAuthorization(
                connection=McpConnectionResponse.model_validate(connection),
                authorization_url=None,
            )
        connection, url = await service.create(
            session,
            name=payload.name,
            server_url=payload.server_url or "",
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


async def _create_direct(
    session: AsyncSession, payload: McpConnectionCreate
) -> McpConnection:
    """No-OAuth connection (no-auth HTTP or stdio): connected immediately."""
    connection = McpConnection(
        id=uuid.uuid4(),
        name=payload.name,
        transport=payload.transport,
        auth=payload.auth,
        server_url=payload.server_url,
        command=payload.command,
        args=list(payload.args) or None,
        status="connected",
    )
    store_connection_env(connection, payload.env)
    session.add(connection)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=409, detail="Connection name already exists") from exc
    await session.refresh(connection)
    return connection


@router.get("", response_model=list[McpConnectionResponse])
async def list_connections(
    session: AsyncSession = Depends(get_db_session),
) -> list[McpConnectionResponse]:
    rows = await session.scalars(select(McpConnection).order_by(McpConnection.created_at.desc()))
    connections = list(rows)
    # Latest snapshot per connection (few connections; single pass in Python).
    snapshots = await session.scalars(
        select(McpToolSnapshot).order_by(McpToolSnapshot.created_at.desc())
    )
    latest_by_connection: dict[uuid.UUID, McpToolSnapshot] = {}
    for snapshot in snapshots:
        latest_by_connection.setdefault(snapshot.connection_id, snapshot)
    responses = []
    for row in connections:
        response = McpConnectionResponse.model_validate(row)
        snapshot = latest_by_connection.get(row.id)
        if snapshot:
            response.latest_snapshot = McpSnapshotSummary(
                id=snapshot.id,
                tools_hash=snapshot.tools_hash,
                tool_count=len(snapshot.tools),
                created_at=snapshot.created_at,
            )
        responses.append(response)
    return responses


@router.post("/{connection_id}/authorize", response_model=McpAuthorization)
async def reauthorize_connection(
    connection_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
    service: McpOAuthService = Depends(_service),
) -> McpAuthorization:
    connection = await session.get(McpConnection, connection_id)
    if not connection:
        raise HTTPException(status_code=404, detail="Connection not found")
    if connection.auth != "oauth":
        raise HTTPException(status_code=422, detail="Connection does not use OAuth")
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
        tools = await invoker.list_mcp_tools(connection_rpc_config(connection))
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


@router.get("/{connection_id}/snapshot", response_model=McpToolSnapshotResponse)
async def get_latest_snapshot(
    connection_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
) -> McpToolSnapshotResponse:
    connection = await session.get(McpConnection, connection_id)
    if not connection:
        raise HTTPException(status_code=404, detail="Connection not found")
    snapshot = await McpSnapshotService.latest(session, connection_id)
    if not snapshot:
        raise HTTPException(status_code=404, detail="No snapshot recorded for this connection")
    return _snapshot_response(snapshot)


@router.post("/{connection_id}/snapshot", response_model=McpToolSnapshotResponse)
async def refresh_snapshot(
    connection_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
    service: McpOAuthService = Depends(_service),
) -> McpToolSnapshotResponse:
    """Fetch the live tools/list and freeze it. Failures keep the old snapshot."""
    connection = await session.get(McpConnection, connection_id)
    if not connection:
        raise HTTPException(status_code=404, detail="Connection not found")
    snapshot_service = McpSnapshotService(service.network_policy, service.transport)
    try:
        snapshot = await snapshot_service.refresh(session, connection)
    except (ToolInvocationError, NetworkPolicyError) as exc:
        code = 409 if getattr(exc, "code", "") == "mcp_auth_required" else 502
        raise HTTPException(status_code=code, detail=str(exc)) from exc
    return _snapshot_response(snapshot)


def _snapshot_response(snapshot: McpToolSnapshot) -> McpToolSnapshotResponse:
    return McpToolSnapshotResponse(
        id=snapshot.id,
        connection_id=snapshot.connection_id,
        tools_hash=snapshot.tools_hash,
        tool_count=len(snapshot.tools),
        tools=[McpSnapshotTool.model_validate(tool) for tool in snapshot.tools],
        created_at=snapshot.created_at,
    )


@router.delete("/{connection_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_connection(
    connection_id: uuid.UUID, session: AsyncSession = Depends(get_db_session)
) -> None:
    connection = await session.get(McpConnection, connection_id)
    if not connection:
        raise HTTPException(status_code=404, detail="Connection not found")
    if connection.transport == "stdio":
        stdio_manager.kill(str(connection.id))
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
