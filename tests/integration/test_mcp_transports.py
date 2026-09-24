"""MCP connections beyond OAuth: no-auth streamable HTTP (transports sse/stdio
follow in later slices) — all registered through the same mcp-connections API."""

import uuid

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from orchestrator.api import mcp_connections
from orchestrator.db.models import McpConnection
from orchestrator.main import app
from orchestrator.tools.mcp_oauth import McpOAuthService
from orchestrator.tools.network import NetworkPolicy

from test_mcp_oauth import MCP_URL, FakeProvider, public

FRONTEND = "http://127.0.0.1:5173"


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
