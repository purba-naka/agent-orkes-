import json
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from orchestrator.db.models import AgentRevision, AgentRevisionDependency
from orchestrator.db.session import async_session_factory
from orchestrator.main import app
from orchestrator.runtime.compiler import GraphCompiler
from orchestrator.runtime.runner import normalize_input


async def create_model(client: AsyncClient, response: dict[str, str]) -> str:
    result = await client.post(
        "/api/v1/models",
        json={
            "name": f"ref-model-{uuid.uuid4().hex[:8]}",
            "revision": {
                "provider": "fake",
                "model_name": "referenced-agent-test",
                "parameters": {"responses": [json.dumps(response)]},
            },
        },
    )
    assert result.status_code == 201, result.text
    return result.json()["active_revision_id"]


async def create_and_publish_agent(
    client: AsyncClient, name: str, document: dict
) -> dict:
    created = await client.post(
        "/api/v1/agents", json={"name": name, "description": "Referenced agent test"}
    )
    assert created.status_code == 201, created.text
    agent_id = created.json()["id"]
    saved = await client.put(
        f"/api/v1/agents/{agent_id}/draft",
        json={"version": 1, "document": document},
    )
    assert saved.status_code == 200, saved.text
    published = await client.post(f"/api/v1/agents/{agent_id}/publish")
    assert published.status_code == 200, published.text
    return {"agent_id": agent_id, "revision": published.json()}


def inline_document(model_revision_id: str, response_field: str) -> dict:
    return {
        "schema_version": 1,
        "input_schema": {
            "type": "object",
            "properties": {"prompt": {"type": "string"}},
            "required": ["prompt"],
        },
        "entry_node_id": "main",
        "named_exits": ["success"],
        "nodes": [
            {
                "id": "main",
                "kind": "agent",
                "agent": {
                    "mode": "inline",
                    "model_revision_id": model_revision_id,
                    "system_prompt": f"Return {response_field}.",
                    "output_schema": {
                        "type": "object",
                        "properties": {response_field: {"type": "string"}},
                        "required": [response_field],
                    },
                },
            }
        ],
        "edges": [],
    }


@pytest.mark.asyncio
async def test_parallel_referenced_agents_are_private_and_pinned_transitively() -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        left_model = await create_model(client, {"value": "left"})
        right_model = await create_model(client, {"value": "right"})
        join_model = await create_model(client, {"joined": "complete"})

        leaf = await create_and_publish_agent(
            client,
            f"leaf-{uuid.uuid4().hex[:8]}",
            inline_document(left_model, "value"),
        )
        nested_document = {
            "schema_version": 1,
            "input_schema": {
                "type": "object",
                "properties": {"prompt": {"type": "string"}},
                "required": ["prompt"],
            },
            "entry_node_id": "nested",
            "named_exits": ["success"],
            "nodes": [
                {
                    "id": "nested",
                    "kind": "agent",
                    "agent": {
                        "mode": "ref",
                        "agent_revision_id": leaf["revision"]["id"],
                        "input_mapping": {"prompt": ["input", "prompt"]},
                        "output_mapping": {"value": ["outputs", "main", "value"]},
                        "result_name": "success",
                    },
                }
            ],
            "edges": [],
        }
        nested = await create_and_publish_agent(
            client, f"nested-{uuid.uuid4().hex[:8]}", nested_document
        )
        right = await create_and_publish_agent(
            client,
            f"right-{uuid.uuid4().hex[:8]}",
            inline_document(right_model, "value"),
        )

        parent_document = {
            "schema_version": 1,
            "entry_node_id": "start",
            "named_exits": ["success"],
            "nodes": [
                {
                    "id": "start",
                    "kind": "agent",
                    "agent": {
                        "mode": "inline",
                        "model_revision_id": join_model,
                        "system_prompt": "Start parallel work.",
                    },
                },
                {
                    "id": "left",
                    "kind": "agent",
                    "agent": {
                        "mode": "ref",
                        "agent_revision_id": nested["revision"]["id"],
                        "input_mapping": {"prompt": ["input", "prompt"]},
                        "output_mapping": {"value": ["outputs", "nested", "value"]},
                    },
                },
                {
                    "id": "right",
                    "kind": "agent",
                    "agent": {
                        "mode": "ref",
                        "agent_revision_id": right["revision"]["id"],
                        "input_mapping": {"prompt": ["input", "prompt"]},
                        "output_mapping": {"value": ["outputs", "main", "value"]},
                    },
                },
                {
                    "id": "joiner",
                    "kind": "agent",
                    "agent": {
                        "mode": "inline",
                        "model_revision_id": join_model,
                        "system_prompt": "Join the private agent results.",
                    },
                },
            ],
            "edges": [
                {"kind": "direct", "source": "start", "target": "left"},
                {"kind": "direct", "source": "start", "target": "right"},
                {
                    "kind": "join",
                    "sources": ["left", "right"],
                    "target": "joiner",
                    "join": "all",
                },
                {"kind": "exit", "source": "joiner", "result_name": "success"},
            ],
        }
        parent = await create_and_publish_agent(
            client, f"parent-{uuid.uuid4().hex[:8]}", parent_document
        )

    manifest = parent["revision"]["dependency_manifest"]
    assert set(manifest["agents"]) == {
        leaf["revision"]["id"],
        nested["revision"]["id"],
        right["revision"]["id"],
    }
    assert set(manifest["models"]) == {left_model, right_model, join_model}

    async with async_session_factory() as session:
        revision = await session.get(
            AgentRevision, uuid.UUID(parent["revision"]["id"])
        )
        assert revision is not None
        dependencies = (
            await session.execute(
                select(AgentRevisionDependency).where(
                    AgentRevisionDependency.owner_revision_id == revision.id,
                    AgentRevisionDependency.dependency_kind == "agent",
                )
            )
        ).scalars().all()
        assert {str(dependency.dependency_revision_id) for dependency in dependencies} == set(
            manifest["agents"]
        )

        graph = await GraphCompiler.compile(session, revision)
        state = normalize_input({"prompt": "work"}, uuid.uuid4())
        namespaces: set[tuple[str, ...]] = set()
        result = state
        async for namespace, mode, payload in graph.astream(
            state, stream_mode=["updates", "values"], subgraphs=True
        ):
            if namespace:
                namespaces.add(tuple(namespace))
            if not namespace and mode == "values":
                result = payload

    assert any(namespace[0].startswith("left:") for namespace in namespaces)
    assert any(namespace[0].startswith("right:") for namespace in namespaces)
    assert result["outputs"]["left"] == {"value": "left"}
    assert result["outputs"]["right"] == {"value": "right"}
    assert result["result_name"] == "success"
    assert len(result["messages"]) == 3


@pytest.mark.asyncio
async def test_transitive_referenced_agent_cycle_is_path_addressed() -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        model_id = await create_model(client, {"value": "seed"})
        agent_a = await create_and_publish_agent(
            client,
            f"cycle-a-{uuid.uuid4().hex[:8]}",
            inline_document(model_id, "value"),
        )
        agent_b = await create_and_publish_agent(
            client,
            f"cycle-b-{uuid.uuid4().hex[:8]}",
            {
                "schema_version": 1,
                "input_schema": {"type": "object"},
                "entry_node_id": "call_a",
                "named_exits": ["success"],
                "nodes": [
                    {
                        "id": "call_a",
                        "kind": "agent",
                        "agent": {
                            "mode": "ref",
                            "agent_revision_id": agent_a["revision"]["id"],
                            "input_mapping": {"prompt": ["input", "prompt"]},
                        },
                    }
                ],
                "edges": [],
            },
        )

        draft = await client.get(f"/api/v1/agents/{agent_a['agent_id']}/draft")
        cycle_document = {
            "schema_version": 1,
            "input_schema": {"type": "object"},
            "entry_node_id": "call_b",
            "named_exits": ["success"],
            "nodes": [
                {
                    "id": "call_b",
                    "kind": "agent",
                    "agent": {
                        "mode": "ref",
                        "agent_revision_id": agent_b["revision"]["id"],
                        "input_mapping": {"prompt": ["input", "prompt"]},
                    },
                }
            ],
            "edges": [],
        }
        saved = await client.put(
            f"/api/v1/agents/{agent_a['agent_id']}/draft",
            json={"version": draft.json()["version"], "document": cycle_document},
        )
        assert saved.status_code == 200
        published = await client.post(f"/api/v1/agents/{agent_a['agent_id']}/publish")

    assert published.status_code == 422
    cycle_diagnostic = next(
        diagnostic
        for diagnostic in published.json()["detail"]["diagnostics"]
        if diagnostic["code"] == "publish.agent_ref_cycle"
    )
    assert cycle_diagnostic["path"].startswith("/nodes/0/agent/agent_revision_id")
    assert "/document/nodes/0/agent/agent_revision_id" in cycle_diagnostic["path"]
