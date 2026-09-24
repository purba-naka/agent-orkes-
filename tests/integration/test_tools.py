import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from orchestrator.db.models import AgentRevision, AgentRevisionDependency
from orchestrator.db.session import async_session_factory
from orchestrator.main import app
from orchestrator.runtime.compiler import GraphCompiler
from orchestrator.runtime.runner import normalize_input
from orchestrator.tools.registry import code_tool_registry


@pytest.mark.asyncio
async def test_tool_catalog_revisions_and_agent_dependency_rows() -> None:
    key = f"test.echo.{uuid.uuid4().hex}"
    code_tool_registry.register(key, "1", lambda payload: payload)
    revision_payload = {
        "kind": "code",
        "description": "Echo deterministic JSON",
        "input_schema": {
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
        },
        "output_schema": {
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
        },
        "configuration": {
            "implementation_key": key,
            "implementation_version": "1",
        },
        "risk_level": "low",
        "is_mutating": False,
        "max_attempts": 2,
    }

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        created = await client.post(
            "/api/v1/tools",
            json={"name": f"echo-{uuid.uuid4().hex[:8]}", "revision": revision_payload},
        )
        assert created.status_code == 201, created.text
        tool = created.json()
        tool_id = tool["id"]
        revision_id = tool["active_revision_id"]
        assert tool["active_revision"]["revision_number"] == 1

        revised_payload = dict(revision_payload)
        revised_payload["description"] = "Echo deterministic JSON exactly"
        revised = await client.post(
            f"/api/v1/tools/{tool_id}/revisions", json=revised_payload
        )
        assert revised.status_code == 201, revised.text
        revision_2 = revised.json()
        assert revision_2["revision_number"] == 2

        detail = await client.get(f"/api/v1/tools/{tool_id}")
        assert detail.status_code == 200
        assert detail.json()["active_revision_id"] == revision_2["id"]
        assert len(detail.json()["revisions"]) == 2

        revision_detail = await client.get(
            f"/api/v1/tool-revisions/{revision_2['id']}"
        )
        assert revision_detail.status_code == 200

        agent = await client.post(
            "/api/v1/agents", json={"name": f"tool-agent-{uuid.uuid4().hex[:8]}"}
        )
        assert agent.status_code == 201
        agent_id = agent.json()["id"]
        document = {
            "schema_version": 1,
            "input_schema": {
                "type": "object",
                "properties": {"value": {"type": "string"}},
                "required": ["value"],
            },
            "entry_node_id": "echo",
            "named_exits": ["success"],
            "nodes": [
                {
                    "id": "echo",
                    "kind": "tool",
                    "tool_revision_id": revision_id,
                    "input_mapping": {"value": ["input", "value"]},
                    "retry": {"max_attempts": 2},
                }
            ],
            "edges": [],
        }
        saved = await client.put(
            f"/api/v1/agents/{agent_id}/draft",
            json={"version": 1, "document": document},
        )
        assert saved.status_code == 200, saved.text
        published = await client.post(f"/api/v1/agents/{agent_id}/publish")
        assert published.status_code == 200, published.text
        published_data = published.json()
        assert published_data["dependency_manifest"]["tools"] == [revision_id]
        assert published_data["dependency_manifest"]["code_tools"] == [
            {"implementation_key": key, "implementation_version": "1"}
        ]

    async with async_session_factory() as session:
        dependency = await session.scalar(
            select(AgentRevisionDependency).where(
                AgentRevisionDependency.owner_revision_id
                == uuid.UUID(published_data["id"]),
                AgentRevisionDependency.dependency_kind == "tool",
            )
        )
        assert dependency is not None
        assert dependency.dependency_revision_id == uuid.UUID(revision_id)

        published_revision = await session.get(
            AgentRevision, uuid.UUID(published_data["id"])
        )
        assert published_revision is not None
        graph = await GraphCompiler.compile(session, published_revision)
        result = await graph.ainvoke(
            normalize_input({"value": "mapped"}, uuid.uuid4())
        )
        assert result["outputs"]["echo"] == {"value": "mapped"}


@pytest.mark.asyncio
async def test_tool_publish_rejects_missing_required_mapping() -> None:
    key = f"test.required.{uuid.uuid4().hex}"
    code_tool_registry.register(key, "1", lambda payload: payload)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        tool = await client.post(
            "/api/v1/tools",
            json={
                "name": f"required-{uuid.uuid4().hex[:8]}",
                "revision": {
                    "kind": "code",
                    "description": "Requires a value",
                    "input_schema": {
                        "type": "object",
                        "properties": {"value": {"type": "string"}},
                        "required": ["value"],
                    },
                    "output_schema": {"type": "object"},
                    "configuration": {
                        "implementation_key": key,
                        "implementation_version": "1",
                    },
                },
            },
        )
        revision_id = tool.json()["active_revision_id"]
        agent = await client.post(
            "/api/v1/agents", json={"name": f"invalid-tool-agent-{uuid.uuid4().hex[:8]}"}
        )
        agent_id = agent.json()["id"]
        document = {
            "schema_version": 1,
            "entry_node_id": "call",
            "nodes": [
                {
                    "id": "call",
                    "kind": "tool",
                    "tool_revision_id": revision_id,
                    "input_mapping": {},
                }
            ],
            "edges": [],
        }
        await client.put(
            f"/api/v1/agents/{agent_id}/draft",
            json={"version": 1, "document": document},
        )
        published = await client.post(f"/api/v1/agents/{agent_id}/publish")

    assert published.status_code == 422
    diagnostics = published.json()["detail"]["diagnostics"]
    assert any(
        diagnostic["code"] == "publish.mapping_required_target_missing"
        and diagnostic["path"] == "/nodes/0/input_mapping"
        for diagnostic in diagnostics
    )
