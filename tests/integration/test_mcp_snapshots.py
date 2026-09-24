"""Snapshot endpoints: content-addressed freezing of tools/list listings."""

import json
import uuid

import pytest
import httpx
from httpx import ASGITransport, AsyncClient

from orchestrator.api import mcp_connections
from orchestrator.db.models import McpConnection
from orchestrator.main import app
from orchestrator.tools.mcp_oauth import McpOAuthService
from orchestrator.tools.mcp_snapshots import McpSnapshotService
from orchestrator.tools.network import NetworkPolicy
from test_mcp_oauth import FRONTEND, MCP_URL, FakeProvider, public


@pytest.fixture
def provider():
    fake = FakeProvider()
    transport = httpx.MockTransport(fake.handler)
    app.dependency_overrides[mcp_connections._service] = lambda: McpOAuthService(
        NetworkPolicy(resolver=public), transport
    )
    yield fake, transport
    app.dependency_overrides.clear()


async def _connected_connection(client, fake) -> str:
    created = await client.post(
        "/api/v1/mcp-connections",
        headers={"origin": FRONTEND},
        json={"name": f"notion-{uuid.uuid4().hex[:8]}", "server_url": MCP_URL},
    )
    assert created.status_code == 201, created.text
    body = created.json()
    from urllib.parse import parse_qs, urlsplit

    query = {k: v[0] for k, v in parse_qs(urlsplit(body["authorization_url"]).query).items()}
    code = f"code-{uuid.uuid4().hex}"
    fake.challenges[code] = query["code_challenge"]
    callback = await client.get(
        "/api/v1/mcp-connections/callback", params={"state": query["state"], "code": code}
    )
    assert callback.status_code == 303
    return body["connection"]["id"]


@pytest.mark.asyncio
async def test_snapshot_refresh_dedupes_and_versions(provider) -> None:
    fake, _ = provider
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        connection_id = await _connected_connection(client, fake)

        # No snapshot yet.
        missing = await client.get(f"/api/v1/mcp-connections/{connection_id}/snapshot")
        assert missing.status_code == 404

        first = await client.post(f"/api/v1/mcp-connections/{connection_id}/snapshot")
        assert first.status_code == 200, first.text
        snap = first.json()
        assert snap["tool_count"] == 2
        assert [t["name"] for t in snap["tools"]] == ["search", "fetch"]
        assert snap["tools"][0]["input_schema"]["properties"]["q"] == {"type": "string"}
        assert snap["tools"][1]["input_schema"] == {"type": "object"}

        # Identical listing -> same snapshot row (content-addressed).
        again = await client.post(f"/api/v1/mcp-connections/{connection_id}/snapshot")
        assert again.json()["id"] == snap["id"]

        # Listing changed -> new snapshot, old one retained.
        fake.tools_pages = [
            {
                "tools": [
                    {
                        "name": "search",
                        "description": "Search pages",
                        "inputSchema": {"type": "object", "properties": {"q": {"type": "string"}}},
                        "annotations": {"readOnlyHint": True},
                    },
                    {"name": "fetch", "title": "Fetch"},
                    {"name": "archive"},
                ]
            }
        ]
        changed = await client.post(f"/api/v1/mcp-connections/{connection_id}/snapshot")
        assert changed.status_code == 200
        new_snap = changed.json()
        assert new_snap["id"] != snap["id"]
        assert new_snap["tool_count"] == 3
        assert new_snap["tools"][0]["annotations"] == {"readOnlyHint": True}

        latest = await client.get(f"/api/v1/mcp-connections/{connection_id}/snapshot")
        assert latest.json()["id"] == new_snap["id"]

        listed = (await client.get("/api/v1/mcp-connections")).json()
        row = next(c for c in listed if c["id"] == connection_id)
        assert row["latest_snapshot"]["id"] == new_snap["id"]
        assert row["latest_snapshot"]["tool_count"] == 3
        assert "token" not in json.dumps(row)

        await client.delete(f"/api/v1/mcp-connections/{connection_id}")


@pytest.mark.asyncio
async def test_snapshot_refresh_reauth_keeps_old_snapshot(provider) -> None:
    fake, _ = provider
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        connection_id = await _connected_connection(client, fake)
        first = await client.post(f"/api/v1/mcp-connections/{connection_id}/snapshot")
        assert first.status_code == 200
        snap_id = first.json()["id"]

        # Force reauth: rejected refresh flips the connection to needs_reauth.
        fake.refresh_ok = False
        from datetime import datetime, timedelta, timezone

        from orchestrator.db.session import async_session_factory

        async with async_session_factory() as session:
            stored = await session.get(McpConnection, uuid.UUID(connection_id))
            stored.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            await session.commit()

        stale = await client.post(f"/api/v1/mcp-connections/{connection_id}/snapshot")
        assert stale.status_code == 409

        # The old snapshot is untouched and still served.
        latest = await client.get(f"/api/v1/mcp-connections/{connection_id}/snapshot")
        assert latest.status_code == 200
        assert latest.json()["id"] == snap_id

        await client.delete(f"/api/v1/mcp-connections/{connection_id}")


@pytest.mark.asyncio
async def test_snapshot_hash_is_order_independent(provider) -> None:
    fake, _ = provider
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        connection_id = await _connected_connection(client, fake)
        first = (await client.post(f"/api/v1/mcp-connections/{connection_id}/snapshot")).json()

        # Same tools in a different page order -> same content hash, same row.
        fake.tools_pages = [
            {"tools": [{"name": "fetch", "title": "Fetch"}], "nextCursor": "p2"},
            {
                "tools": [
                    {
                        "name": "search",
                        "description": "Search pages",
                        "inputSchema": {"type": "object", "properties": {"q": {"type": "string"}}},
                    }
                ]
            },
        ]
        reordered = await client.post(f"/api/v1/mcp-connections/{connection_id}/snapshot")
        assert reordered.json()["id"] == first["id"]

        await client.delete(f"/api/v1/mcp-connections/{connection_id}")


def test_normalize_and_hash_reject_unnamed_tools() -> None:
    from orchestrator.tools.adapters import ToolInvocationError
    from orchestrator.tools.mcp_snapshots import normalize_tool_entry, snapshot_tools_hash

    entry = normalize_tool_entry(
        {"name": "x", "inputSchema": "junk", "annotations": "junk", "title": "  "}
    )
    assert entry == {
        "name": "x",
        "title": None,
        "description": None,
        "input_schema": {"type": "object"},
        "output_schema": None,
        "annotations": None,
    }
    import pytest as _pytest

    with _pytest.raises(ToolInvocationError):
        normalize_tool_entry({"description": "no name"})
    a = [{"name": "a"}, {"name": "b"}]
    b = [{"name": "b"}, {"name": "a"}]
    assert snapshot_tools_hash(a) == snapshot_tools_hash(b)
    assert snapshot_tools_hash(a) != snapshot_tools_hash([{"name": "a"}])
