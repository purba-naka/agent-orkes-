import json
import uuid
import pytest
from httpx import ASGITransport, AsyncClient

from orchestrator.config import settings
from orchestrator.db.session import async_session_factory
from orchestrator.main import app
from orchestrator.runtime.state import merge_node_outputs





@pytest.mark.asyncio
async def test_conflict_safe_output_reducer():
    # 1. Normal parallel writes for different nodes in same superstep
    s1 = merge_node_outputs({}, {"a": 1, "__step__": 1, "__node__": "a"})
    s2 = merge_node_outputs(s1, {"b": 2, "__step__": 1, "__node__": "b"})
    assert s2["a"] == 1
    assert s2["b"] == 2

    # 2. Duplicate write for node 'a' in same superstep must raise ValueError
    with pytest.raises(ValueError, match="Duplicate write for node 'a' in superstep 1"):
        merge_node_outputs(s2, {"a": 99, "__step__": 1, "__node__": "a"})

    # 3. Write for node 'a' in next superstep (loop) is permitted
    s3 = merge_node_outputs(s2, {"a": 100, "__step__": 2, "__node__": "a"})
    assert s3["a"] == 100


@pytest.mark.asyncio
async def test_fan_out_join_all_and_semantic_enum_routing():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Create models
        model_name = f"topo-model-{uuid.uuid4().hex[:8]}"
        model_res = await client.post(
            "/api/v1/models",
            json={
                "name": model_name,
                "description": "General worker test model",
                "revision": {
                    "provider": "fake",
                    "model_name": "fake-orchestrator",
                    "parameters": {
                        "responses": [
                            json.dumps({"topic": "distributed systems", "research": "all clear", "analysis": "sound", "summary": "Approved"}),
                        ]
                    },
                },
            },
        )
        assert model_res.status_code == 201
        model_rev_id = model_res.json()["active_revision_id"]

        eval_model_name = f"eval-model-{uuid.uuid4().hex[:8]}"
        eval_model_res = await client.post(
            "/api/v1/models",
            json={
                "name": eval_model_name,
                "description": "Evaluator test model",
                "revision": {
                    "provider": "fake",
                    "model_name": "fake-evaluator",
                    "parameters": {
                        "responses": [
                            json.dumps({"verdict": "approved"}),
                        ]
                    },
                },
            },
        )
        assert eval_model_res.status_code == 201
        eval_model_rev_id = eval_model_res.json()["active_revision_id"]

        # 2. Create agent
        agent_res = await client.post(
            "/api/v1/agents",
            json={"name": "multi-agent-orchestrator", "description": "Topology flow"},
        )
        assert agent_res.status_code == 201
        agent_id = agent_res.json()["id"]

        # 3. Build multi-node graph topology
        # START -> entry -> (fan-out) -> research & analysis -> (join: all) -> evaluator -> (semantic enum) -> approved_writer -> exit
        doc = {
            "schema_version": 1,
            "entry_node_id": "entry",
            "recursion_limit": 25,
            "named_exits": ["success", "rejected"],
            "nodes": [
                {
                    "id": "entry",
                    "kind": "agent",
                    "agent": {
                        "mode": "inline",
                        "model_revision_id": model_rev_id,
                        "system_prompt": "You are the entry coordinator.",
                    },
                },
                {
                    "id": "research",
                    "kind": "agent",
                    "agent": {
                        "mode": "inline",
                        "model_revision_id": model_rev_id,
                        "system_prompt": "You are the research agent.",
                    },
                },
                {
                    "id": "analysis",
                    "kind": "agent",
                    "agent": {
                        "mode": "inline",
                        "model_revision_id": model_rev_id,
                        "system_prompt": "You are the analysis agent.",
                    },
                },
                {
                    "id": "evaluator",
                    "kind": "agent",
                    "agent": {
                        "mode": "inline",
                        "model_revision_id": eval_model_rev_id,
                        "system_prompt": "You evaluate reports and return verdict: approved or rejected.",
                        "output_schema": {
                            "type": "object",
                            "properties": {
                                "verdict": {
                                    "type": "string",
                                    "enum": ["approved", "rejected"],
                                }
                            },
                            "required": ["verdict"],
                        },
                    },
                },
                {
                    "id": "approved_writer",
                    "kind": "agent",
                    "agent": {
                        "mode": "inline",
                        "model_revision_id": model_rev_id,
                        "system_prompt": "You write the final approved report.",
                    },
                },
                {
                    "id": "rejected_writer",
                    "kind": "agent",
                    "agent": {
                        "mode": "inline",
                        "model_revision_id": model_rev_id,
                        "system_prompt": "You write rejection feedback.",
                    },
                },
            ],
            "edges": [
                {"kind": "direct", "source": "entry", "target": "research"},
                {"kind": "direct", "source": "entry", "target": "analysis"},
                {
                    "kind": "join",
                    "sources": ["research", "analysis"],
                    "target": "evaluator",
                    "join": "all",
                },
                {
                    "kind": "semantic",
                    "source": ["evaluator", "verdict"],
                    "routes": {
                        "approved": "approved_writer",
                        "rejected": "rejected_writer",
                    },
                    "default": "rejected_writer",
                },
                {"kind": "exit", "source": "approved_writer", "result_name": "success"},
                {"kind": "exit", "source": "rejected_writer", "result_name": "rejected"},
            ],
        }

        # Update draft
        draft_update_res = await client.put(
            f"/api/v1/agents/{agent_id}/draft",
            json={"version": 1, "document": doc},
        )
        assert draft_update_res.status_code == 200
        assert draft_update_res.json()["validation"] == []

        # Publish revision
        pub_res = await client.post(f"/api/v1/agents/{agent_id}/publish")
        assert pub_res.status_code in (200, 201)
        rev_id = pub_res.json()["id"]

        # Run stream
        run_res = await client.post(
            f"/api/v1/agents/{agent_id}/runs",
            json={"input": {"prompt": "Start research flow"}},
        )
        assert run_res.status_code == 200

        events: list[tuple[str, dict]] = []
        for line in run_res.text.split("\n\n"):
            if not line.strip():
                continue
            event_type = ""
            event_data = {}
            for sub in line.split("\n"):
                if sub.startswith("event:"):
                    event_type = sub.split(":", 1)[1].strip()
                elif sub.startswith("data:"):
                    event_data = json.loads(sub.split(":", 1)[1].strip())
            if event_type:
                events.append((event_type, event_data))

        close_event = next((d for t, d in events if t == "close"), None)
        assert close_event is not None
        assert close_event["status"] == "completed"
        assert close_event["result_name"] == "success"
        # Check that outputs from nodes exist
        outputs = close_event["output"].get("outputs", {})
        assert "research" in outputs
        assert "analysis" in outputs
        assert "evaluator" in outputs
        assert outputs["evaluator"]["verdict"] == "approved"
        assert "approved_writer" in outputs


@pytest.mark.asyncio
async def test_loop_without_conditional_exit_rejected_at_publish():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        model_name = f"loop-model-{uuid.uuid4().hex[:8]}"
        model_res = await client.post(
            "/api/v1/models",
            json={
                "name": model_name,
                "revision": {
                    "provider": "fake",
                    "model_name": "fake",
                    "parameters": {"responses": ["hello"]},
                },
            },
        )
        assert model_res.status_code == 201
        model_rev_id = model_res.json()["active_revision_id"]

        agent_res = await client.post(
            "/api/v1/agents",
            json={"name": "infinite-loop-agent"},
        )
        agent_id = agent_res.json()["id"]

        # Loop: A -> B -> A (both direct, no conditional exit)
        doc = {
            "schema_version": 1,
            "entry_node_id": "node_a",
            "recursion_limit": 25,
            "named_exits": ["success"],
            "nodes": [
                {
                    "id": "node_a",
                    "kind": "agent",
                    "agent": {
                        "mode": "inline",
                        "model_revision_id": model_rev_id,
                        "system_prompt": "Node A",
                    },
                },
                {
                    "id": "node_b",
                    "kind": "agent",
                    "agent": {
                        "mode": "inline",
                        "model_revision_id": model_rev_id,
                        "system_prompt": "Node B",
                    },
                },
            ],
            "edges": [
                {"kind": "direct", "source": "node_a", "target": "node_b"},
                {"kind": "direct", "source": "node_b", "target": "node_a"},
            ],
        }

        await client.put(
            f"/api/v1/agents/{agent_id}/draft",
            json={"version": 1, "document": doc},
        )

        pub_res = await client.post(f"/api/v1/agents/{agent_id}/publish")
        assert pub_res.status_code == 422
        diags = pub_res.json()["detail"]["diagnostics"]
        diag_codes = [d["code"] for d in diags]
        assert "publish.loop_without_conditional_exit" in diag_codes


@pytest.mark.asyncio
async def test_reachability_and_exit_path_validation():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        model_name = f"reach-model-{uuid.uuid4().hex[:8]}"
        model_res = await client.post(
            "/api/v1/models",
            json={
                "name": model_name,
                "revision": {
                    "provider": "fake",
                    "model_name": "fake",
                    "parameters": {"responses": ["hello"]},
                },
            },
        )
        assert model_res.status_code == 201
        model_rev_id = model_res.json()["active_revision_id"]

        agent_res = await client.post(
            "/api/v1/agents",
            json={"name": "unreachable-agent"},
        )
        agent_id = agent_res.json()["id"]

        # Node B is unreachable, Node A has no path to end
        doc = {
            "schema_version": 1,
            "entry_node_id": "node_a",
            "recursion_limit": 25,
            "named_exits": ["success"],
            "nodes": [
                {
                    "id": "node_a",
                    "kind": "agent",
                    "agent": {
                        "mode": "inline",
                        "model_revision_id": model_rev_id,
                        "system_prompt": "Node A",
                    },
                },
                {
                    "id": "node_b",
                    "kind": "agent",
                    "agent": {
                        "mode": "inline",
                        "model_revision_id": model_rev_id,
                        "system_prompt": "Node B",
                    },
                },
            ],
            "edges": [],
        }

        await client.put(
            f"/api/v1/agents/{agent_id}/draft",
            json={"version": 1, "document": doc},
        )

        pub_res = await client.post(f"/api/v1/agents/{agent_id}/publish")
        assert pub_res.status_code == 422
        diags = pub_res.json()["detail"]["diagnostics"]
        codes = [d["code"] for d in diags]
        assert "publish.unreachable_node" in codes
        assert "publish.no_path_to_end" in codes


@pytest.mark.asyncio
async def test_loop_with_mechanical_conditional_exit_executes_and_terminates():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        model_name = f"loop-cond-model-{uuid.uuid4().hex[:8]}"
        model_res = await client.post(
            "/api/v1/models",
            json={
                "name": model_name,
                "revision": {
                    "provider": "fake",
                    "model_name": "fake-loop",
                    "parameters": {
                        "responses": [
                            json.dumps({"status": "completed", "loops": 1}),
                            json.dumps({"summary": "Loop completed"}),
                        ]
                    },
                },
            },
        )
        assert model_res.status_code == 201
        model_rev_id = model_res.json()["active_revision_id"]

        agent_res = await client.post(
            "/api/v1/agents",
            json={"name": "loop-agent-with-exit"},
        )
        agent_id = agent_res.json()["id"]

        # Loop with mechanical conditional exit:
        # entry -> worker -> (mechanical: if status == "completed" then finisher else worker)
        # finisher -> exit: success
        doc = {
            "schema_version": 1,
            "entry_node_id": "entry",
            "recursion_limit": 25,
            "named_exits": ["success"],
            "nodes": [
                {
                    "id": "entry",
                    "kind": "agent",
                    "agent": {
                        "mode": "inline",
                        "model_revision_id": model_rev_id,
                        "system_prompt": "Entry",
                    },
                },
                {
                    "id": "worker",
                    "kind": "agent",
                    "agent": {
                        "mode": "inline",
                        "model_revision_id": model_rev_id,
                        "system_prompt": "Worker",
                    },
                },
                {
                    "id": "finisher",
                    "kind": "agent",
                    "agent": {
                        "mode": "inline",
                        "model_revision_id": model_rev_id,
                        "system_prompt": "Finisher",
                    },
                },
            ],
            "edges": [
                {"kind": "direct", "source": "entry", "target": "worker"},
                {
                    "kind": "mechanical",
                    "source": ["worker", "status"],
                    "operator": "eq",
                    "value": "completed",
                    "then": "finisher",
                    "else": "worker",
                },
                {"kind": "exit", "source": "finisher", "result_name": "success"},
            ],
        }

        update_res = await client.put(
            f"/api/v1/agents/{agent_id}/draft",
            json={"version": 1, "document": doc},
        )
        assert update_res.status_code == 200
        assert update_res.json()["validation"] == []

        pub_res = await client.post(f"/api/v1/agents/{agent_id}/publish")
        assert pub_res.status_code in (200, 201)

        run_res = await client.post(
            f"/api/v1/agents/{agent_id}/runs",
            json={"input": {"prompt": "Run loop"}},
        )
        assert run_res.status_code == 200

        events = []
        for line in run_res.text.split("\n\n"):
            if not line.strip():
                continue
            event_type = ""
            event_data = {}
            for sub in line.split("\n"):
                if sub.startswith("event:"):
                    event_type = sub.split(":", 1)[1].strip()
                elif sub.startswith("data:"):
                    event_data = json.loads(sub.split(":", 1)[1].strip())
            if event_type:
                events.append((event_type, event_data))

        close_event = next((d for t, d in events if t == "close"), None)
        assert close_event is not None
        assert close_event["status"] == "completed"
        assert close_event["result_name"] == "success"
        outputs = close_event["output"].get("outputs", {})
        assert "worker" in outputs
        assert outputs["worker"]["status"] == "completed"
        assert "finisher" in outputs
