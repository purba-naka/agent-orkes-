"""MCP connections beyond OAuth: no-auth streamable HTTP, stdio, and legacy
HTTP+SSE — all registered through the same mcp-connections API."""

import asyncio
import json
import sys
import uuid

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from orchestrator.api import mcp_connections
from orchestrator.config import settings
from orchestrator.db.models import McpConnection
from orchestrator.main import app
from orchestrator.tools.mcp_oauth import McpOAuthService
from orchestrator.tools.network import NetworkPolicy
from orchestrator.tools.mcp_stdio import stdio_manager

from test_mcp_oauth import MCP_URL, FakeProvider, public

FRONTEND = "http://127.0.0.1:5173"

SSE_URL = "https://sse.fake.test/sse"
SSE_MESSAGE_URL = "https://sse.fake.test/sse-message"

# A minimal line-delimited JSON-RPC MCP server, usable as:
#   python -c STDIO_SERVER_SCRIPT
STDIO_SERVER_SCRIPT = """
import json, os, sys
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    msg = json.loads(line)
    if 'id' not in msg:
        continue
    method = msg.get('method')
    if method == 'initialize':
        result = {'protocolVersion': '2025-03-26', 'capabilities': {}}
    elif method == 'tools/list':
        result = {'tools': [
            {'name': 'echo', 'inputSchema': {'type': 'object'}},
            {'name': 'env', 'inputSchema': {'type': 'object'}},
        ]}
    elif method == 'tools/call':
        name = msg['params']['name']
        if name == 'env':
            text = json.dumps({'env': os.environ.get('STDIO_SECRET')})
        else:
            text = json.dumps({'echo': msg['params'].get('arguments', {}).get('value')})
        result = {'content': [{'type': 'text', 'text': text}]}
    else:
        result = None
    if result is None:
        payload = {'jsonrpc': '2.0', 'id': msg['id'], 'error': {'code': -32601, 'message': 'unknown'}}
    else:
        payload = {'jsonrpc': '2.0', 'id': msg['id'], 'result': result}
    sys.stdout.write(json.dumps(payload) + '\\n')
    sys.stdout.flush()
"""


@pytest.fixture
def open_provider():
    fake = FakeProvider()
    fake.require_auth = False
    transport = httpx.MockTransport(fake.handler)
    app.dependency_overrides[mcp_connections._service] = lambda: McpOAuthService(
        NetworkPolicy(resolver=public), transport
    )
    yield fake, transport
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_no_auth_connection_is_connected_immediately(open_provider) -> None:
    fake, _ = open_provider
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post(
            "/api/v1/mcp-connections",
            headers={"origin": FRONTEND},
            json={
                "name": f"open-{uuid.uuid4().hex[:8]}",
                "server_url": MCP_URL,
                "transport": "streamable_http",
                "auth": "none",
            },
        )
        assert created.status_code == 201, created.text
        body = created.json()
        assert body["authorization_url"] is None
        assert body["connection"]["status"] == "connected"
        assert body["connection"]["transport"] == "streamable_http"
        assert body["connection"]["auth"] == "none"

        remote = await client.get(f"/api/v1/mcp-connections/{body['connection']['id']}/tools")
        assert remote.status_code == 200, remote.text
        assert [t["name"] for t in remote.json()] == ["search", "fetch"]
        # No OAuth handshake happened at all.
        assert fake.token_forms == []

        snapshot = await client.post(f"/api/v1/mcp-connections/{body['connection']['id']}/snapshot")
        assert snapshot.status_code == 200, snapshot.text
        assert [t["name"] for t in snapshot.json()["tools"]] == ["search", "fetch"]

        await client.delete(f"/api/v1/mcp-connections/{body['connection']['id']}")


@pytest.mark.asyncio
async def test_no_auth_connection_rejects_oauth_server_401(open_provider) -> None:
    """A protected server paired with auth=none surfaces the auth failure cleanly."""
    fake, _ = open_provider
    fake.require_auth = True
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post(
            "/api/v1/mcp-connections",
            headers={"origin": FRONTEND},
            json={
                "name": f"open-{uuid.uuid4().hex[:8]}",
                "server_url": MCP_URL,
                "transport": "streamable_http",
                "auth": "none",
            },
        )
        assert created.status_code == 201
        connection_id = created.json()["connection"]["id"]
        remote = await client.get(f"/api/v1/mcp-connections/{connection_id}/tools")
        assert remote.status_code == 409
        assert "credentials" in remote.json()["detail"].lower()
        await client.delete(f"/api/v1/mcp-connections/{connection_id}")


@pytest.mark.asyncio
async def test_stdio_connection_end_to_end(monkeypatch) -> None:
    monkeypatch.setattr(settings, "mcp_stdio_command_allowlist", [sys.executable])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post(
            "/api/v1/mcp-connections",
            headers={"origin": FRONTEND},
            json={
                "name": f"local-{uuid.uuid4().hex[:8]}",
                "transport": "stdio",
                "auth": "none",
                "command": sys.executable,
                "args": ["-c", STDIO_SERVER_SCRIPT],
                "env": {"STDIO_SECRET": "s3cret"},
            },
        )
        assert created.status_code == 201, created.text
        body = created.json()
        assert body["authorization_url"] is None
        assert body["connection"]["status"] == "connected"
        assert body["connection"]["transport"] == "stdio"
        assert body["connection"]["server_url"] is None
        # env values never echo back through the API
        assert "s3cret" not in created.text and "s3cret" not in str(body)
        connection_id = body["connection"]["id"]

        remote = await client.get(f"/api/v1/mcp-connections/{connection_id}/tools")
        assert remote.status_code == 200, remote.text
        assert [t["name"] for t in remote.json()] == ["echo", "env"]

        snapshot = await client.post(f"/api/v1/mcp-connections/{connection_id}/snapshot")
        assert snapshot.status_code == 200, snapshot.text
        assert [t["name"] for t in snapshot.json()["tools"]] == ["echo", "env"]

        deleted = await client.delete(f"/api/v1/mcp-connections/{connection_id}")
        assert deleted.status_code == 204
    stdio_manager.shutdown()


@pytest.mark.asyncio
async def test_stdio_connection_invocation_passes_env(monkeypatch) -> None:
    from orchestrator.db.session import async_session_factory
    from orchestrator.tools.adapters import ToolInvoker
    from orchestrator.tools.mcp_config import connection_rpc_config

    monkeypatch.setattr(settings, "mcp_stdio_command_allowlist", [sys.executable])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post(
            "/api/v1/mcp-connections",
            headers={"origin": FRONTEND},
            json={
                "name": f"local-{uuid.uuid4().hex[:8]}",
                "transport": "stdio",
                "auth": "none",
                "command": sys.executable,
                "args": ["-c", STDIO_SERVER_SCRIPT],
                "env": {"STDIO_SECRET": "s3cret"},
            },
        )
        assert created.status_code == 201, created.text
        connection_id = created.json()["connection"]["id"]

    try:
        async with async_session_factory() as session:
            connection = await session.get(McpConnection, uuid.UUID(connection_id))
            invoker = ToolInvoker(session)
            config = connection_rpc_config(connection)
            assert await invoker.invoke_mcp_tool(config, "echo", {"value": "hi"}) == {
                "echo": "hi"
            }
            assert await invoker.invoke_mcp_tool(config, "env", {}) == {"env": "s3cret"}
    finally:
        stdio_manager.shutdown()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.delete(f"/api/v1/mcp-connections/{connection_id}")


@pytest.mark.asyncio
async def test_stdio_command_allowlist_gates_creation() -> None:
    # Default allowlist is empty: the transport is disabled.
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        denied = await client.post(
            "/api/v1/mcp-connections",
            headers={"origin": FRONTEND},
            json={
                "name": f"local-{uuid.uuid4().hex[:8]}",
                "transport": "stdio",
                "auth": "none",
                "command": "definitely-not-allowlisted",
            },
        )
        assert denied.status_code == 403
        assert "allowlist" in denied.json()["detail"]


class FakeSseServer:
    """Legacy SSE MCP server: GET stream + 202-only POST message endpoint,
    with responses pushed onto the stream as `message` events."""

    def __init__(self) -> None:
        self.queue: asyncio.Queue = asyncio.Queue()
        self.requests: list[dict] = []

    async def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url).split("?")[0]
        if request.method == "GET" and url == SSE_URL:
            async def stream():
                yield b"event: endpoint\ndata: /sse-message\n\n"
                while True:
                    chunk = await self.queue.get()
                    if chunk is None:
                        return
                    yield chunk

            return httpx.Response(
                200, headers={"content-type": "text/event-stream"}, content=stream()
            )
        if request.method == "POST" and url == SSE_MESSAGE_URL:
            message = json.loads(request.read())
            self.requests.append(message)
            if "id" not in message:
                return httpx.Response(202)
            method = message.get("method")
            if method == "initialize":
                result = {"protocolVersion": "2025-03-26", "capabilities": {}}
            elif method == "tools/list":
                result = {"tools": [{"name": "search", "inputSchema": {"type": "object"}}]}
            elif method == "tools/call":
                result = {"structuredContent": {"pages": 3}}
            else:
                result = {}
            event = json.dumps(
                {"jsonrpc": "2.0", "id": message["id"], "result": result}
            )
            self.queue.put_nowait(f"event: message\ndata: {event}\n\n".encode())
            return httpx.Response(202)
        return httpx.Response(404)


@pytest.fixture
def sse_server():
    fake = FakeSseServer()
    transport = httpx.MockTransport(fake.handler)
    app.dependency_overrides[mcp_connections._service] = lambda: McpOAuthService(
        NetworkPolicy(resolver=public), transport
    )
    yield fake, transport
    fake.queue.put_nowait(None)  # release the suspended GET stream
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_sse_connection_end_to_end(sse_server) -> None:
    fake, transport = sse_server
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post(
            "/api/v1/mcp-connections",
            headers={"origin": FRONTEND},
            json={
                "name": f"legacy-{uuid.uuid4().hex[:8]}",
                "transport": "sse",
                "auth": "none",
                "server_url": SSE_URL,
            },
        )
        assert created.status_code == 201, created.text
        body = created.json()
        assert body["connection"]["status"] == "connected"
        assert body["connection"]["transport"] == "sse"
        connection_id = body["connection"]["id"]

        remote = await client.get(f"/api/v1/mcp-connections/{connection_id}/tools")
        assert remote.status_code == 200, remote.text
        assert [t["name"] for t in remote.json()] == ["search"]

        snapshot = await client.post(f"/api/v1/mcp-connections/{connection_id}/snapshot")
        assert snapshot.status_code == 200, snapshot.text
        assert [t["name"] for t in snapshot.json()["tools"]] == ["search"]

        await client.delete(f"/api/v1/mcp-connections/{connection_id}")

    methods = [message.get("method") for message in fake.requests]
    assert methods == ["initialize", "notifications/initialized", "tools/list"] * 2


@pytest.mark.asyncio
async def test_sse_tool_invocation(sse_server) -> None:
    from orchestrator.db.session import async_session_factory
    from orchestrator.tools.adapters import ToolInvoker
    from orchestrator.tools.mcp_config import connection_rpc_config

    fake, transport = sse_server
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post(
            "/api/v1/mcp-connections",
            headers={"origin": FRONTEND},
            json={
                "name": f"legacy-{uuid.uuid4().hex[:8]}",
                "transport": "sse",
                "auth": "none",
                "server_url": SSE_URL,
            },
        )
        assert created.status_code == 201, created.text
        connection_id = created.json()["connection"]["id"]

    try:
        async with async_session_factory() as session:
            connection = await session.get(McpConnection, uuid.UUID(connection_id))
            invoker = ToolInvoker(
                session, network_policy=NetworkPolicy(resolver=public), transport=transport
            )
            result = await invoker.invoke_mcp_tool(
                connection_rpc_config(connection), "search", {}
            )
            assert result == {"pages": 3}
    finally:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.delete(f"/api/v1/mcp-connections/{connection_id}")


@pytest.mark.asyncio
async def test_unsupported_transport_and_shape_validation(open_provider) -> None:
    _, _ = open_provider
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        gated = await client.post(
            "/api/v1/mcp-connections",
            headers={"origin": FRONTEND},
            json={"name": f"sse-{uuid.uuid4().hex[:8]}", "server_url": MCP_URL, "transport": "sse"},
        )
        assert gated.status_code == 422

        stdio_no_command = await client.post(
            "/api/v1/mcp-connections",
            headers={"origin": FRONTEND},
            json={"name": f"stdio-{uuid.uuid4().hex[:8]}", "transport": "stdio", "auth": "none"},
        )
        assert stdio_no_command.status_code == 422
        assert "command" in stdio_no_command.text

        no_url = await client.post(
            "/api/v1/mcp-connections",
            headers={"origin": FRONTEND},
            json={"name": f"x-{uuid.uuid4().hex[:8]}", "auth": "none"},
        )
        assert no_url.status_code == 422
        assert "server_url" in no_url.text


@pytest.mark.asyncio
async def test_no_auth_connection_env_is_encrypted_at_rest(open_provider) -> None:
    """No env on remote transports yet, but the storage path must never leak."""
    _, _ = open_provider
    from orchestrator.db.session import async_session_factory
    from orchestrator.tools.mcp_config import connection_env, store_connection_env

    async with async_session_factory() as session:
        connection = McpConnection(
            id=uuid.uuid4(),
            name=f"envtest-{uuid.uuid4().hex[:8]}",
            transport="stdio",
            auth="none",
            command="python",
            status="connected",
        )
        store_connection_env(connection, {"API_KEY": "super-secret-value"})
        session.add(connection)
        await session.commit()

        stored = await session.get(McpConnection, connection.id)
        assert stored.env_ciphertext is not None
        assert b"super-secret-value" not in stored.env_ciphertext
        assert connection_env(stored) == {"API_KEY": "super-secret-value"}

        await session.delete(stored)
        await session.commit()
