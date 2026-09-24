import json
import uuid
import pytest
from httpx import ASGITransport, AsyncClient

from orchestrator.main import app


@pytest.mark.asyncio
async def test_multi_turn_conversation_persistence_and_rebuild() -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        # 1. Register a test fake model with memory_echo behavior
        fake_model_resp = await client.post(
            "/api/v1/models",
            json={
                "name": f"test-conv-model-{uuid.uuid4().hex[:8]}",
                "revision": {
                    "provider": "fake",
                    "model_name": "scripted-fake",
                    "parameters": {"behavior": "memory_echo"},
                },
            },
        )
        assert fake_model_resp.status_code == 201
        fake_rev_id = fake_model_resp.json()["active_revision_id"]

        # 2. Create Agent
        agent_resp = await client.post(
            "/api/v1/agents",
            json={
                "name": f"chat-agent-{uuid.uuid4().hex[:8]}",
                "description": "Multi-turn test agent",
                "system_prompt": "You are a friendly assistant with memory.",
                "model_revision_id": fake_rev_id,
            },
        )
        assert agent_resp.status_code == 201
        agent_id = agent_resp.json()["id"]

        # 3. Cannot create conversation before publishing
        conv_fail_resp = await client.post(
            "/api/v1/conversations",
            json={"agent_id": agent_id, "title": "Unpublished Conv"},
        )
        assert conv_fail_resp.status_code == 400

        # 4. Publish agent revision 1
        pub_resp = await client.post(f"/api/v1/agents/{agent_id}/publish")
        assert pub_resp.status_code == 200
        rev1_id = pub_resp.json()["id"]

        # 5. Create conversation pinned to revision 1
        conv_create_resp = await client.post(
            "/api/v1/conversations",
            json={"agent_id": agent_id, "title": "Memory Test Conversation"},
        )
        assert conv_create_resp.status_code == 201
        conv_data = conv_create_resp.json()
        conv_id = conv_data["id"]
        assert conv_data["agent_revision_id"] == rev1_id
        assert conv_data["title"] == "Memory Test Conversation"

        # 6. Turn 1: User introduces their favorite color
        turn1_resp = await client.post(
            f"/api/v1/conversations/{conv_id}/messages",
            json={"content": [{"type": "text", "text": "My favorite color is navy blue"}]},
        )
        assert turn1_resp.status_code == 200
        assert "text/event-stream" in turn1_resp.headers["content-type"]

        # Parse SSE events
        turn1_events = []
        current_event = None
        for line in turn1_resp.text.splitlines():
            if line.startswith("event: "):
                current_event = line[7:].strip()
            elif line.startswith("data: ") and current_event:
                turn1_events.append((current_event, json.loads(line[6:].strip())))
                current_event = None

        turn1_event_names = [e[0] for e in turn1_events]
        assert "open" in turn1_event_names
        assert "native" in turn1_event_names
        assert "close" in turn1_event_names

        # Verify projections after Turn 1
        detail_resp1 = await client.get(f"/api/v1/conversations/{conv_id}")
        assert detail_resp1.status_code == 200
        detail1 = detail_resp1.json()
        assert len(detail1["messages"]) == 2
        assert detail1["messages"][0]["role"] == "user"
        assert detail1["messages"][0]["sequence"] == 1
        assert "navy blue" in str(detail1["messages"][0]["content"])
        assert detail1["messages"][1]["role"] == "assistant"
        assert detail1["messages"][1]["sequence"] == 2
        assert len(detail1["runs"]) == 1
        assert detail1["runs"][0]["mode"] == "conversation"
        assert detail1["runs"][0]["status"] == "completed"

    # 7. Turn 2: Simulate process restart / new HTTP client session
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as fresh_client:
        turn2_resp = await fresh_client.post(
            f"/api/v1/conversations/{conv_id}/messages",
            json={"text": "What is my favorite color?"},
        )
        assert turn2_resp.status_code == 200

        turn2_events = []
        current_event = None
        for line in turn2_resp.text.splitlines():
            if line.startswith("event: "):
                current_event = line[7:].strip()
            elif line.startswith("data: ") and current_event:
                turn2_events.append((current_event, json.loads(line[6:].strip())))
                current_event = None

        close_event = next(e[1] for e in turn2_events if e[0] == "close")
        assert close_event["status"] == "completed"
        # The agent correctly remembers 'navy blue' from checkpoint state!
        assert "navy blue" in close_event["output"]["content"].lower()

        # Verify projections after Turn 2: 4 messages in total
        detail_resp2 = await fresh_client.get(f"/api/v1/conversations/{conv_id}")
        assert detail_resp2.status_code == 200
        detail2 = detail_resp2.json()
        assert len(detail2["messages"]) == 4
        assert [m["sequence"] for m in detail2["messages"]] == [1, 2, 3, 4]
        assert detail2["messages"][2]["role"] == "user"
        assert detail2["messages"][3]["role"] == "assistant"
        assert "navy blue" in str(detail2["messages"][3]["content"]).lower()

        # 8. Rebuild projections directly from checkpoints
        rebuild_resp = await fresh_client.post(
            f"/api/v1/conversations/{conv_id}/rebuild-projections"
        )
        assert rebuild_resp.status_code == 200
        assert rebuild_resp.json()["rebuilt_count"] == 4

        # Verify detail after rebuild is identical
        detail_after_rebuild = (await fresh_client.get(f"/api/v1/conversations/{conv_id}")).json()
        assert len(detail_after_rebuild["messages"]) == 4
        assert [m["sequence"] for m in detail_after_rebuild["messages"]] == [1, 2, 3, 4]

        # 9. Upgrade conversation to new revision
        # Update agent draft and publish revision 2
        draft_resp = await fresh_client.get(f"/api/v1/agents/{agent_id}/draft")
        draft_data = draft_resp.json()
        doc = draft_data["document"]
        doc["system_prompt"] = "You are an upgraded v2 assistant."
        await fresh_client.put(
            f"/api/v1/agents/{agent_id}/draft",
            json={"version": draft_data["version"], "document": doc},
        )
        pub2_resp = await fresh_client.post(f"/api/v1/agents/{agent_id}/publish")
        assert pub2_resp.status_code == 200
        rev2_id = pub2_resp.json()["id"]
        assert rev2_id != rev1_id

        upgrade_resp = await fresh_client.post(
            f"/api/v1/conversations/{conv_id}/upgrade",
            json={"summary": "User's favorite color is navy blue."},
        )
        assert upgrade_resp.status_code == 201
        upgraded_data = upgrade_resp.json()
        assert upgraded_data["parent_conversation_id"] == conv_id
        assert upgraded_data["agent_revision_id"] == rev2_id
        assert upgraded_data["upgrade_summary"] == "User's favorite color is navy blue."
