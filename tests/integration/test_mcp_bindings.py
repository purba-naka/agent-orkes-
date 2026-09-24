"""Agent bindings to MCP servers: publish freezing, runtime resolution, HITL."""

from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit
import uuid

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from orchestrator.api import mcp_connections
from orchestrator.db.models import AgentRevision, AgentRevisionDependency, McpConnection
from orchestrator.db.session import async_session_factory
from orchestrator.main import app
from orchestrator.runtime.hitl import InterruptDecisionError, build_resume_value
from orchestrator.runtime.policy import (
    build_tool_approval_interrupts,
    resolve_mcp_bound_tools,
)
from orchestrator.tools.adapters import ToolInvocationError
from orchestrator.tools.mcp_oauth import McpOAuthService
from orchestrator.tools.mcp_snapshots import mcp_bound_tool_name
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


async def _connected_connection(client, fake) -> tuple[str, str]:
    name = f"notion-{uuid.uuid4().hex[:8]}"
    created = await client.post(
        "/api/v1/mcp-connections",
        headers={"origin": FRONTEND},
        json={"name": name, "server_url": MCP_URL},
    )
    assert created.status_code == 201, created.text
    body = created.json()
    query = {k: v[0] for k, v in parse_qs(urlsplit(body["authorization_url"]).query).items()}
    code = f"code-{uuid.uuid4().hex}"
    fake.challenges[code] = query["code_challenge"]
    callback = await client.get(
        "/api/v1/mcp-connections/callback", params={"state": query["state"], "code": code}
    )
    assert callback.status_code == 303
    return body["connection"]["id"], name


async def _draft_with_bindings(client, connection_id: str, tools: list[dict]) -> tuple[str, dict]:
    """Create an agent whose inline node binds `tools` from the connection."""
    model = await client.post(
        "/api/v1/models",
        json={
            "name": f"test-model-{uuid.uuid4().hex[:8]}",
            "revision": {"provider": "fake", "model_name": "scripted-fake", "parameters": {}},
        },
    )
    assert model.status_code == 201
    agent = await client.post(
        "/api/v1/agents",
        json={
            "name": f"binder-{uuid.uuid4().hex[:8]}",
            "description": "binding test",
            "system_prompt": "You search.",
            "model_revision_id": model.json()["active_revision_id"],
        },
    )
    assert agent.status_code == 201, agent.text
    agent_id = agent.json()["id"]
    draft = (await client.get(f"/api/v1/agents/{agent_id}/draft")).json()
    doc = draft["document"]
    doc["nodes"][0]["agent"]["mcp_bindings"] = [
        {"connection_id": connection_id, "tools": tools}
    ]
    save = await client.put(
        f"/api/v1/agents/{agent_id}/draft",
        json={"version": draft["version"], "document": doc},
    )
    assert save.status_code == 200, save.text
    return agent_id, save.json()


def _binding(document: dict) -> dict:
    return document["nodes"][0]["agent"]["mcp_bindings"][0]


@pytest.mark.asyncio
async def test_publish_freezes_snapshot_and_tracks_dependencies(provider) -> None:
    fake, _ = provider
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        connection_id, connection_name = await _connected_connection(client, fake)
        snap = (await client.post(f"/api/v1/mcp-connections/{connection_id}/snapshot")).json()
        assert snap["tool_count"] == 2

        agent_id, saved = await _draft_with_bindings(
            client,
            connection_id,
            [{"name": "search", "approval": "always"}, {"name": "fetch"}],
        )
        assert saved["validation"] == []

        pub = await client.post(f"/api/v1/agents/{agent_id}/publish")
        assert pub.status_code == 200, pub.text
        rev = pub.json()
        assert _binding(rev["document"])["snapshot_id"] == snap["id"]
        assert rev["dependency_manifest"]["mcp_servers"] == [connection_id]
        assert rev["dependency_manifest"]["mcp_snapshots"] == [snap["id"]]

        # Dependency rows persist the pinned server and snapshot.
        async with async_session_factory() as session:
            rows = (await session.execute(
                select(AgentRevisionDependency).where(
                    AgentRevisionDependency.owner_revision_id == uuid.UUID(rev["id"])
                )
            )).scalars().all()
            kinds = {(row.dependency_kind, str(row.dependency_revision_id)) for row in rows}
            assert ("mcp_server", connection_id) in kinds
            assert ("mcp_snapshot", snap["id"]) in kinds

        # Idempotent republish reuses the frozen revision.
        again = await client.post(f"/api/v1/agents/{agent_id}/publish")
        assert again.json()["id"] == rev["id"]

        # A changed listing freezes a new snapshot -> new revision.
        fake.tools_pages = [{"tools": [
            {"name": "search", "description": "Search pages",
             "inputSchema": {"type": "object", "properties": {"q": {"type": "string"}}}},
            {"name": "fetch", "title": "Fetch"},
            {"name": "archive"},
        ]}]
        new_snap = (await client.post(f"/api/v1/mcp-connections/{connection_id}/snapshot")).json()
        republished = await client.post(f"/api/v1/agents/{agent_id}/publish")
        assert republished.json()["id"] != rev["id"]
        assert _binding(republished.json()["document"])["snapshot_id"] == new_snap["id"]

        await client.delete(f"/api/v1/mcp-connections/{connection_id}")


@pytest.mark.asyncio
async def test_publish_rejects_invalid_bindings(provider) -> None:
    fake, _ = provider
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        connection_id, _ = await _connected_connection(client, fake)
        assert (await client.post(f"/api/v1/mcp-connections/{connection_id}/snapshot")).status_code == 200

        # Unknown tool name in the allowlist.
        agent_id, saved = await _draft_with_bindings(client, connection_id, [{"name": "ghost"}])
        assert saved["validation"] == []
        ghost = await client.post(f"/api/v1/agents/{agent_id}/publish")
        assert ghost.status_code == 422
        codes = [d["code"] for d in ghost.json()["detail"]["diagnostics"]]
        assert "publish.mcp_tool_not_in_snapshot" in codes

        # Connected but never snapshotted.
        fresh_id, _ = await _connected_connection(client, fake)
        agent2, saved2 = await _draft_with_bindings(client, fresh_id, [{"name": "search"}])
        no_snapshot = await client.post(f"/api/v1/agents/{agent2}/publish")
        assert no_snapshot.status_code == 422
        codes = [d["code"] for d in no_snapshot.json()["detail"]["diagnostics"]]
        assert "publish.mcp_snapshot_missing" in codes

        # needs_reauth connection.
        async with async_session_factory() as session:
            stored = await session.get(McpConnection, uuid.UUID(fresh_id))
            stored.status = "needs_reauth"
            await session.commit()
        stale = await client.post(f"/api/v1/agents/{agent2}/publish")
        assert stale.status_code == 422
        codes = [d["code"] for d in stale.json()["detail"]["diagnostics"]]
        assert "publish.mcp_connection_not_connected" in codes

        # Drafts must not carry snapshot_id.
        draft = (await client.get(f"/api/v1/agents/{agent_id}/draft")).json()
        doc = draft["document"]
        doc["nodes"][0]["agent"]["mcp_bindings"][0]["snapshot_id"] = str(uuid.uuid4())
        frozen = await client.put(
            f"/api/v1/agents/{agent_id}/draft",
            json={"version": draft["version"], "document": doc},
        )
        assert frozen.status_code == 200
        codes = [d["code"] for d in frozen.json()["validation"]]
        assert "node.mcp_binding_snapshot_in_draft" in codes

        # Malformed bindings never reach publish.
        draft = (await client.get(f"/api/v1/agents/{agent_id}/draft")).json()
        doc = draft["document"]
        doc["nodes"][0]["agent"]["mcp_bindings"] = [
            {"connection_id": "not-a-uuid", "tools": []},
            {"tools": [{"name": "search", "approval": "sometimes"}]},
        ]
        malformed = await client.put(
            f"/api/v1/agents/{agent_id}/draft",
            json={"version": draft["version"], "document": doc},
        )
        codes = [d["code"] for d in malformed.json()["validation"]]
        assert "node.invalid_mcp_connection_uuid" in codes
        assert "node.invalid_mcp_binding_tools" in codes
        assert "node.invalid_mcp_tool_approval" in codes

        await client.delete(f"/api/v1/mcp-connections/{connection_id}")
        await client.delete(f"/api/v1/mcp-connections/{fresh_id}")


@pytest.mark.asyncio
async def test_runtime_resolves_and_invokes_bound_tools(provider) -> None:
    fake, transport = provider
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        connection_id, connection_name = await _connected_connection(client, fake)
        assert (await client.post(f"/api/v1/mcp-connections/{connection_id}/snapshot")).status_code == 200
        agent_id, _ = await _draft_with_bindings(
            client, connection_id, [{"name": "search", "approval": "always"}, {"name": "fetch"}]
        )
        pub = await client.post(f"/api/v1/agents/{agent_id}/publish")
        assert pub.status_code == 200, pub.text
        rev_id = pub.json()["id"]

    async with async_session_factory() as session:
        revision = await session.get(AgentRevision, uuid.UUID(rev_id))
        bindings = revision.document["nodes"][0]["agent"]["mcp_bindings"]
        tools, info = await resolve_mcp_bound_tools(
            session,
            bindings,
            network_policy=NetworkPolicy(resolver=public),
            transport=transport,
        )
        search = mcp_bound_tool_name(connection_name, "search")
        fetch = mcp_bound_tool_name(connection_name, "fetch")
        assert [t.name for t in tools] == [search, fetch]
        assert info[search]["approval"] == "always"
        assert info[fetch]["approval"] == "never"

        fake.mcp_methods.clear()
        assert await tools[0].ainvoke({"q": "x"}) == {"pages": 3}
        assert fake.mcp_methods == ["initialize", "notifications/initialized", "tools/call"]

        # Connection flipped to needs_reauth after resolve: invoke fails cleanly.
        stored = await session.get(McpConnection, uuid.UUID(connection_id))
        stored.status = "needs_reauth"
        await session.commit()
        with pytest.raises(ToolInvocationError) as err:
            await tools[0].ainvoke({"q": "x"})
        assert err.value.code == "mcp_auth_required"

    # Resolve-time failures: disconnected connection and name collisions.
    async with async_session_factory() as session:
        revision = await session.get(AgentRevision, uuid.UUID(rev_id))
        bindings = revision.document["nodes"][0]["agent"]["mcp_bindings"]
        with pytest.raises(ValueError, match="not connected"):
            await resolve_mcp_bound_tools(session, bindings)
        stored = await session.get(McpConnection, uuid.UUID(connection_id))
        stored.status = "connected"
        await session.commit()
        with pytest.raises(ValueError, match="collision"):
            await resolve_mcp_bound_tools(
                session, bindings, reserved_names={mcp_bound_tool_name(connection_name, "search")}
            )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.delete(f"/api/v1/mcp-connections/{connection_id}")


@pytest.mark.asyncio
async def test_hitl_edit_validates_bound_tool_against_pinned_snapshot(provider) -> None:
    fake, _ = provider
    fake.tools_pages = [{
        "tools": [{
            "name": "search",
            "description": "Search pages",
            "inputSchema": {
                "type": "object",
                "properties": {"q": {"type": "string"}},
                "required": ["q"],
            },
        }]
    }]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        connection_id, connection_name = await _connected_connection(client, fake)
        assert (await client.post(f"/api/v1/mcp-connections/{connection_id}/snapshot")).status_code == 200
        agent_id, _ = await _draft_with_bindings(
            client, connection_id, [{"name": "search", "approval": "always"}]
        )
        pub = await client.post(f"/api/v1/agents/{agent_id}/publish")
        assert pub.status_code == 200, pub.text

    tool_name = mcp_bound_tool_name(connection_name, "search")
    row = SimpleNamespace(
        kind="tool_approval",
        payload={"namespace": [], "value": {"action_requests": [{"name": tool_name, "args": {}}]}},
    )
    async with async_session_factory() as session:
        revision = await session.get(AgentRevision, uuid.UUID(pub.json()["id"]))
        result = await build_resume_value(
            session, revision, row, {"action": "edit", "input": {"q": "hello"}}
        )
        assert result == {
            "decisions": [
                {"type": "edit", "edited_action": {"name": tool_name, "args": {"q": "hello"}}}
            ]
        }
        with pytest.raises(InterruptDecisionError, match="pinned schema"):
            await build_resume_value(
                session, revision, row, {"action": "edit", "input": {}}
            )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.delete(f"/api/v1/mcp-connections/{connection_id}")


def test_bound_tools_with_always_approval_interrupt() -> None:
    native = SimpleNamespace(risk_level="low")
    interrupts = build_tool_approval_interrupts(
        {"native_tool": native},
        {"server_search": {"approval": "always"}, "server_fetch": {"approval": "never"}},
        {"tool_approval": {"risk_levels": ["high"]}},
    )
    assert interrupts == {
        "server_search": {"allowed_decisions": ["approve", "edit", "reject"]}
    }
