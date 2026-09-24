import json
import uuid
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from orchestrator.db.session import async_session_factory
from orchestrator.domain.agents import AgentService
from orchestrator.main import app


@pytest.mark.asyncio
async def test_single_node_agent_lifecycle_and_sse_run() -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        # 1. Register a test fake model
        fake_model_resp = await client.post(
            "/api/v1/models",
            json={
                "name": f"test-model-{uuid.uuid4().hex[:8]}",
                "revision": {
                    "provider": "fake",
                    "model_name": "scripted-fake",
                    "parameters": {},
                },
            },
        )
        assert fake_model_resp.status_code == 201
        fake_rev_id = fake_model_resp.json()["active_revision_id"]

        # 2. Create Agent
        agent_name = f"research-agent-{uuid.uuid4().hex[:8]}"
        create_agent_resp = await client.post(
            "/api/v1/agents",
            json={
                "name": agent_name,
                "description": "Single-node test research agent",
                "system_prompt": "You are a research bot.",
                "model_revision_id": fake_rev_id,
            },
        )
        assert create_agent_resp.status_code == 201
        agent_data = create_agent_resp.json()
        agent_id = agent_data["id"]
        assert agent_data["name"] == agent_name
        assert agent_data["active_revision_id"] is None
        assert agent_data["draft"] is not None
        assert agent_data["draft"]["version"] == 1

        # 3. Get Draft and verify default document
        draft_resp = await client.get(f"/api/v1/agents/{agent_id}/draft")
        assert draft_resp.status_code == 200
        draft_data = draft_resp.json()
        doc = draft_data["document"]
        assert doc["schema_version"] == 1
        assert doc["entry_node_id"] == "main"
        assert len(doc["nodes"]) == 1
        assert doc["nodes"][0]["agent"]["model_revision_id"] == fake_rev_id

        # 4. Draft save with optimistic concurrency check
        # Attempt to save with wrong version -> 409 Conflict
        bad_version_resp = await client.put(
            f"/api/v1/agents/{agent_id}/draft",
            json={"version": 999, "document": doc},
        )
        assert bad_version_resp.status_code == 409
        err_body = bad_version_resp.json()["detail"]
        assert err_body["error"] == "draft_conflict"
        assert err_body["current_version"] == 1

        # Update draft with correct version
        doc["system_prompt"] = "Updated prompt for research agent."
        doc["nodes"][0]["agent"]["system_prompt"] = "Updated prompt for research agent."
        save_resp = await client.put(
            f"/api/v1/agents/{agent_id}/draft",
            json={"version": 1, "document": doc},
        )
        assert save_resp.status_code == 200
        saved_draft = save_resp.json()
        assert saved_draft["version"] == 2

        # 5. Publish validation failure: set invalid model_revision_id
        invalid_doc = dict(doc)
        invalid_doc["nodes"] = [
            {
                "id": "main",
                "kind": "agent",
                "agent": {
                    "mode": "inline",
                    "model_revision_id": str(uuid.uuid4()),
                    "system_prompt": "Test",
                },
            }
        ]
        await client.put(
            f"/api/v1/agents/{agent_id}/draft",
            json={"version": 2, "document": invalid_doc},
        )
        pub_fail_resp = await client.post(f"/api/v1/agents/{agent_id}/publish")
        assert pub_fail_resp.status_code == 422
        diag = pub_fail_resp.json()["detail"]["diagnostics"]
        assert any("model_revision" in d["code"] for d in diag)

        # Restore valid model revision & save draft version 3
        await client.put(
            f"/api/v1/agents/{agent_id}/draft",
            json={"version": 3, "document": doc},
        )

        # 6. Publish success
        pub_resp = await client.post(f"/api/v1/agents/{agent_id}/publish")
        assert pub_resp.status_code == 200, pub_resp.text
        rev_data = pub_resp.json()
        rev1_id = rev_data["id"]
        assert rev_data["revision_number"] == 1
        assert rev_data["content_hash"] is not None

        # Verify agent's active revision is set
        agent_detail = await client.get(f"/api/v1/agents/{agent_id}")
        assert agent_detail.json()["active_revision_id"] == rev1_id

        # 7. Idempotent publish: publishing unchanged draft returns same revision
        pub_idem_resp = await client.post(f"/api/v1/agents/{agent_id}/publish")
        assert pub_idem_resp.status_code == 200
        assert pub_idem_resp.json()["id"] == rev1_id
        assert pub_idem_resp.json()["revision_number"] == 1

        # 8. Verify database immutability trigger rejects UPDATE and DELETE
        async with async_session_factory() as session:
            with pytest.raises(Exception, match="agent_revisions is immutable"):
                await session.execute(
                    text("UPDATE agent_revisions SET content_hash = 'tampered' WHERE id = :id"),
                    {"id": uuid.UUID(rev1_id)},
                )
            await session.rollback()

        async with async_session_factory() as session:
            with pytest.raises(Exception, match="agent_revisions is immutable"):
                await session.execute(
                    text("DELETE FROM agent_revisions WHERE id = :id"),
                    {"id": uuid.UUID(rev1_id)},
                )
            await session.rollback()

        # 9. Run agent with SSE stream
        run_resp = await client.post(
            f"/api/v1/agents/{agent_id}/runs",
            json={"input": {"prompt": "Analyze quantum computing"}},
        )
        assert run_resp.status_code == 200
        assert "text/event-stream" in run_resp.headers["content-type"]

        # Parse SSE events from response
        raw_text = run_resp.text
        lines = raw_text.splitlines()

        events: list[tuple[str, dict]] = []
        current_event = None
        for line in lines:
            if line.startswith("event: "):
                current_event = line[7:].strip()
            elif line.startswith("data: ") and current_event:
                data = json.loads(line[6:].strip())
                events.append((current_event, data))
                current_event = None

        event_names = [e[0] for e in events]
        assert "open" in event_names
        assert "native" in event_names
        assert "close" in event_names

        open_event = next(e[1] for e in events if e[0] == "open")
        run_id = open_event["run_id"]
        assert open_event["revision_id"] == rev1_id

        close_event = next(e[1] for e in events if e[0] == "close")
        assert close_event["status"] == "completed"
        assert close_event["result_name"] == "success"
        assert "output" in close_event
        assert close_event["output"]["content"] == "Test response from fake model"

        # 10. Check Run endpoint and persisted database status
        run_status_resp = await client.get(f"/api/v1/runs/{run_id}")
        assert run_status_resp.status_code == 200
        persisted_run = run_status_resp.json()
        assert persisted_run["status"] == "completed"
        assert persisted_run["result_name"] == "success"
        assert persisted_run["result"]["content"] == "Test response from fake model"
        assert persisted_run["finished_at"] is not None
