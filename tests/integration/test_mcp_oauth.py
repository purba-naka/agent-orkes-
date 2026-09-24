"""End-to-end MCP OAuth against a fake authorization server + MCP server."""

import base64
from datetime import datetime, timedelta, timezone
import hashlib
import json
from urllib.parse import parse_qs, urlsplit
import uuid

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from orchestrator.api import mcp_connections
from orchestrator.db.models import McpConnection
from orchestrator.db.session import async_session_factory
from orchestrator.main import app
from orchestrator.tools.adapters import ToolInvocationError, ToolInvoker
from orchestrator.tools.mcp_oauth import McpOAuthService
from orchestrator.tools.network import NetworkPolicy

MCP_URL = "https://mcp.fake.test/mcp"


class FakeProvider:
    def __init__(self) -> None:
        self.challenges: dict[str, str] = {}
        self.issued = 0
        self.valid: set[str] = set()
        self.refresh_ok = True
        self.token_forms: list[dict[str, str]] = []
        self.mcp_methods: list[str] = []

    def _tokens(self) -> dict:
        self.issued += 1
        access = f"access-{self.issued}"
        self.valid = {access}
        return {"access_token": access, "refresh_token": f"refresh-{self.issued}", "expires_in": 3600}

    async def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url).split("?")[0]
        if url == MCP_URL:
            auth = request.headers.get("authorization", "")
            if auth.removeprefix("Bearer ") not in self.valid:
                return httpx.Response(
                    401,
                    headers={
                        "www-authenticate": 'Bearer resource_metadata="https://mcp.fake.test/.well-known/oauth-protected-resource/mcp"'
                    },
                )
            body = json.loads(request.read())
            self.mcp_methods.append(body["method"])
            if body["method"] == "initialize":
                result = {"protocolVersion": "2025-06-18", "capabilities": {}}
                return httpx.Response(
                    200,
                    headers={"mcp-session-id": "s1", "content-type": "text/event-stream"},
                    text=f"event: message\ndata: {json.dumps({'jsonrpc': '2.0', 'id': 1, 'result': result})}\n\n",
                )
            if body["method"] == "notifications/initialized":
                return httpx.Response(202)
            if body["method"] == "tools/list":
                page = [
                    {"tools": [{"name": "search", "description": "Search pages", "inputSchema": {"type": "object", "properties": {"q": {"type": "string"}}}}], "nextCursor": "p2"},
                    {"tools": [{"name": "fetch", "title": "Fetch"}]},
                ][1 if body["params"].get("cursor") == "p2" else 0]
                return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": page})
            assert request.headers["mcp-protocol-version"] == "2025-06-18"
            text = json.dumps({"jsonrpc": "2.0", "id": 2, "result": {"structuredContent": {"pages": 3}}})
            return httpx.Response(
                200, headers={"content-type": "text/event-stream"}, text=f"data: {text}\n\n"
            )
        if url.endswith("/.well-known/oauth-protected-resource/mcp"):
            return httpx.Response(200, json={"resource": MCP_URL, "authorization_servers": ["https://auth.fake.test"], "scopes_supported": ["default"]})
        if url == "https://auth.fake.test/.well-known/oauth-authorization-server":
            return httpx.Response(200, json={
                "authorization_endpoint": "https://auth.fake.test/authorize",
                "token_endpoint": "https://auth.fake.test/token",
                "registration_endpoint": "https://auth.fake.test/register",
                "code_challenge_methods_supported": ["S256"],
            })
        if url == "https://auth.fake.test/register":
            assert json.loads(request.read())["token_endpoint_auth_method"] == "none"
            return httpx.Response(201, json={"client_id": "client-123"})
        if url == "https://auth.fake.test/token":
            form = {k: v[0] for k, v in parse_qs(request.read().decode()).items()}
            self.token_forms.append(form)
            assert form["client_id"] == "client-123" and form["resource"] == MCP_URL
            if form["grant_type"] == "authorization_code":
                digest = hashlib.sha256(form["code_verifier"].encode()).digest()
                challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
                if self.challenges.get(form["code"]) != challenge:
                    return httpx.Response(400, json={"error": "invalid_grant"})
                return httpx.Response(200, json=self._tokens())
            if form["grant_type"] == "refresh_token" and self.refresh_ok:
                return httpx.Response(200, json=self._tokens())
            return httpx.Response(400, json={"error": "invalid_grant"})
        return httpx.Response(404)

    def authorize(self, authorization_url: str) -> tuple[str, str]:
        """Simulate the user approving in the browser."""
        query = {k: v[0] for k, v in parse_qs(urlsplit(authorization_url).query).items()}
        assert query["code_challenge_method"] == "S256" and query["resource"] == MCP_URL
        code = f"code-{uuid.uuid4().hex}"
        self.challenges[code] = query["code_challenge"]
        return query["state"], code


async def public(host: str, port: int) -> list[str]:
    return ["8.8.8.8"]


@pytest.fixture
def provider():
    fake = FakeProvider()
    transport = httpx.MockTransport(fake.handler)
    app.dependency_overrides[mcp_connections._service] = lambda: McpOAuthService(
        NetworkPolicy(resolver=public), transport
    )
    yield fake, transport
    app.dependency_overrides.clear()


def mcp_revision(connection_id: str):
    from types import SimpleNamespace

    return SimpleNamespace(
        is_enabled=True,
        kind="mcp",
        input_schema={"type": "object"},
        output_schema={"type": "object", "required": ["pages"]},
        configuration={"server_url": MCP_URL, "remote_tool_name": "search", "connection_id": connection_id},
        is_mutating=False,
        max_attempts=1,
    )


FRONTEND = "http://127.0.0.1:5173"


@pytest.mark.asyncio
async def test_oauth_redirects_follow_browser_origin(provider) -> None:
    fake, _ = provider
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        rejected = await client.post(
            "/api/v1/mcp-connections",
            headers={"origin": "https://evil.test"},
            json={"name": f"notion-{uuid.uuid4().hex[:8]}", "server_url": MCP_URL},
        )
        assert rejected.status_code == 403

        created = (await client.post(
            "/api/v1/mcp-connections",
            headers={"origin": FRONTEND},
            json={"name": f"notion-{uuid.uuid4().hex[:8]}", "server_url": MCP_URL},
        )).json()
        query = parse_qs(urlsplit(created["authorization_url"]).query)
        assert query["redirect_uri"] == [f"{FRONTEND}/api/v1/mcp-connections/callback"]

        state, code = fake.authorize(created["authorization_url"])
        callback = await client.get("/api/v1/mcp-connections/callback", params={"state": state, "code": code})
        assert callback.headers["location"].startswith(f"{FRONTEND}/?mcp_oauth=connected")
        assert fake.token_forms[-1]["redirect_uri"] == query["redirect_uri"][0]
        await client.delete(f"/api/v1/mcp-connections/{created['connection']['id']}")


@pytest.mark.asyncio
async def test_oauth_connect_invoke_refresh_and_reauth(provider) -> None:
    fake, transport = provider
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post(
            "/api/v1/mcp-connections",
            headers={"origin": FRONTEND},
            json={"name": f"notion-{uuid.uuid4().hex[:8]}", "server_url": MCP_URL},
        )
        assert created.status_code == 201, created.text
        body = created.json()
        connection_id = body["connection"]["id"]
        assert body["connection"]["status"] == "pending"

        state, code = fake.authorize(body["authorization_url"])
        bad = await client.get("/api/v1/mcp-connections/callback", params={"state": "forged", "code": code})
        assert bad.status_code == 400

        callback = await client.get("/api/v1/mcp-connections/callback", params={"state": state, "code": code})
        assert callback.status_code == 303
        assert "mcp_oauth=connected" in callback.headers["location"]

        replay = await client.get("/api/v1/mcp-connections/callback", params={"state": state, "code": code})
        assert replay.status_code == 400

        remote = await client.get(f"/api/v1/mcp-connections/{connection_id}/tools")
        assert remote.status_code == 200, remote.text
        assert [t["name"] for t in remote.json()] == ["search", "fetch"]
        assert remote.json()[0]["input_schema"]["properties"]["q"] == {"type": "string"}
        assert remote.json()[1]["input_schema"] == {"type": "object"}
        fake.mcp_methods.clear()

        listed = (await client.get("/api/v1/mcp-connections")).json()
        row = next(c for c in listed if c["id"] == connection_id)
        assert row["status"] == "connected"
        assert "token" not in json.dumps(row)

    async with async_session_factory() as session:
        stored = await session.get(McpConnection, uuid.UUID(connection_id))
        assert b"access-1" not in stored.token_ciphertext

        invoker = ToolInvoker(session, network_policy=NetworkPolicy(resolver=public), transport=transport)
        assert await invoker.invoke(mcp_revision(connection_id), {"q": "x"}) == {"pages": 3}
        assert fake.mcp_methods == ["initialize", "notifications/initialized", "tools/call"]

        # Expired token: refreshed transparently.
        stored.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        await session.commit()
        assert await invoker.invoke(mcp_revision(connection_id), {"q": "x"}) == {"pages": 3}
        assert fake.token_forms[-1] == {
            "grant_type": "refresh_token", "refresh_token": "refresh-1",
            "client_id": "client-123", "resource": MCP_URL,
        }

        # Refresh rejected: connection flips to needs_reauth, tool fails cleanly.
        fake.refresh_ok = False
        stored = await session.get(McpConnection, uuid.UUID(connection_id))
        stored.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        await session.commit()
        with pytest.raises(ToolInvocationError) as err:
            await invoker.invoke(mcp_revision(connection_id), {"q": "x"})
        assert err.value.code == "mcp_auth_required"
        await session.refresh(stored)
        assert stored.status == "needs_reauth"

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        stale = await client.get(f"/api/v1/mcp-connections/{connection_id}/tools")
        assert stale.status_code == 409

        again = await client.post(f"/api/v1/mcp-connections/{connection_id}/authorize")
        state, code = fake.authorize(again.json()["authorization_url"])
        callback = await client.get("/api/v1/mcp-connections/callback", params={"state": state, "code": code})
        assert "mcp_oauth=connected" in callback.headers["location"]
        assert (await client.delete(f"/api/v1/mcp-connections/{connection_id}")).status_code == 204


@pytest.mark.asyncio
async def test_tool_rejects_connection_with_mismatched_server(provider) -> None:
    fake, _ = provider
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = (await client.post(
            "/api/v1/mcp-connections",
            headers={"origin": FRONTEND},
            json={"name": f"notion-{uuid.uuid4().hex[:8]}", "server_url": MCP_URL},
        )).json()
        response = await client.post("/api/v1/tools", json={
            "name": f"mcp-{uuid.uuid4().hex[:8]}",
            "revision": {
                "kind": "mcp",
                "description": "search",
                "input_schema": {"type": "object"},
                "output_schema": {"type": "object"},
                "configuration": {
                    "server_url": "https://other.test/mcp",
                    "remote_tool_name": "search",
                    "connection_id": created["connection"]["id"],
                },
            },
        })
        assert response.status_code == 422
        assert "server_url" in response.text
        await client.delete(f"/api/v1/mcp-connections/{created['connection']['id']}")
