import io
import json
import logging
import uuid
import pytest
from httpx import ASGITransport, AsyncClient
from langchain_core.messages import HumanMessage

from orchestrator.db.session import async_session_factory
from orchestrator.domain.catalog import CatalogService
from orchestrator.main import app
from orchestrator.security.encryption import vault
from orchestrator.security.logging import SafeJsonFormatter


def test_safe_json_formatter_redacts_keys() -> None:
    formatter = SafeJsonFormatter()
    logger = logging.getLogger("test_redact")
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

    record = logger.makeRecord(
        name="test_redact",
        level=logging.INFO,
        fn="",
        lno=0,
        msg="Logging test with secret",
        args=(),
        exc_info=None,
        extra={"secret": "sk-secret-1234", "run_id": "run-xyz"},
    )
    handler.emit(record)
    log_output = stream.getvalue().strip()
    data = json.loads(log_output)
    assert data["run_id"] == "run-xyz"
    assert "sk-secret-1234" not in log_output


@pytest.mark.asyncio
async def test_credential_encryption_and_tamper_detection() -> None:
    secret = "sk-test-super-secret-key-12345"
    aad = b"credential:test:v1"
    ciphertext, nonce, key_version = vault.encrypt(secret, aad=aad)

    assert ciphertext != secret.encode()
    assert key_version == 1

    decrypted = vault.decrypt(ciphertext, nonce, aad=aad)
    assert decrypted == secret

    # Tampered ciphertext must fail
    tampered = bytearray(ciphertext)
    tampered[0] ^= 0xFF
    with pytest.raises(Exception):
        vault.decrypt(bytes(tampered), nonce, aad=aad)


@pytest.mark.asyncio
async def test_credentials_api_crud_and_redaction() -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        unique_name = f"openai-key-{uuid.uuid4().hex[:8]}"
        secret_value = "sk-proj-verysecrettoken9876"

        # 1. Create credential
        create_resp = await client.post(
            "/api/v1/credentials",
            json={
                "name": unique_name,
                "kind": "api_key",
                "secret": secret_value,
            },
        )
        assert create_resp.status_code == 201, create_resp.text
        data = create_resp.json()
        cred_id = data["id"]
        assert data["name"] == unique_name
        assert data["kind"] == "api_key"
        assert data["last_four"] == "9876"
        assert data["is_enabled"] is True
        # Plaintext secret MUST NOT appear anywhere in response JSON
        assert secret_value not in str(data)

        # 2. List credentials
        list_resp = await client.get("/api/v1/credentials")
        assert list_resp.status_code == 200
        items = list_resp.json()
        matched = [i for i in items if i["id"] == cred_id]
        assert len(matched) == 1
        assert matched[0]["last_four"] == "9876"
        assert secret_value not in str(list_resp.json())

        # 3. Update / rotate credential secret
        new_secret = "sk-proj-rotatedtoken1122"
        update_resp = await client.put(
            f"/api/v1/credentials/{cred_id}",
            json={"secret": new_secret},
        )
        assert update_resp.status_code == 200
        up_data = update_resp.json()
        assert up_data["last_four"] == "1122"
        assert new_secret not in str(up_data)

        # 4. Disable credential
        disable_resp = await client.post(f"/api/v1/credentials/{cred_id}/disable")
        assert disable_resp.status_code == 200
        assert disable_resp.json()["is_enabled"] is False

        # Verify disabled credential cannot be decrypted via catalog service
        async with async_session_factory() as session:
            with pytest.raises(ValueError, match="disabled or does not exist"):
                await CatalogService.get_decrypted_credential(
                    session, uuid.UUID(cred_id)
                )


@pytest.mark.asyncio
async def test_models_and_revisions_api_and_resolution() -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        # Create a test credential first
        cred_resp = await client.post(
            "/api/v1/credentials",
            json={
                "name": f"cred-for-model-{uuid.uuid4().hex[:8]}",
                "kind": "api_key",
                "secret": "sk-fake-secret-9999",
            },
        )
        cred_id = cred_resp.json()["id"]

        model_name = f"gpt-4o-mini-test-{uuid.uuid4().hex[:8]}"

        # 1. Create model with initial revision
        create_resp = await client.post(
            "/api/v1/models",
            json={
                "name": model_name,
                "revision": {
                    "provider": "openai",
                    "model_name": "gpt-4o-mini",
                    "api_key_id": cred_id,
                    "parameters": {"temperature": 0.2},
                },
            },
        )
        assert create_resp.status_code == 201, create_resp.text
        model_data = create_resp.json()
        model_id = model_data["id"]
        rev1_id = model_data["active_revision_id"]
        assert model_data["name"] == model_name
        assert rev1_id is not None
        assert model_data["active_revision"]["revision_number"] == 1
        assert model_data["active_revision"]["provider"] == "openai"

        # 2. Get model detail
        detail_resp = await client.get(f"/api/v1/models/{model_id}")
        assert detail_resp.status_code == 200
        detail_data = detail_resp.json()
        assert len(detail_data["revisions"]) == 1

        # 3. Create revision 2
        rev2_resp = await client.post(
            f"/api/v1/models/{model_id}/revisions",
            json={
                "provider": "openai",
                "model_name": "gpt-4o",
                "api_key_id": cred_id,
                "parameters": {"temperature": 0.7},
            },
        )
        assert rev2_resp.status_code == 201
        rev2_data = rev2_resp.json()
        rev2_id = rev2_data["id"]
        assert rev2_data["revision_number"] == 2
        assert rev2_data["model_name"] == "gpt-4o"

        # 4. Verify updated model has revision 2 as active
        detail2_resp = await client.get(f"/api/v1/models/{model_id}")
        assert detail2_resp.status_code == 200
        detail2_data = detail2_resp.json()
        assert detail2_data["active_revision_id"] == rev2_id
        assert detail2_data["active_revision"]["revision_number"] == 2
        assert len(detail2_data["revisions"]) == 2

        # 5. Create a fake model for test execution resolution
        fake_model_resp = await client.post(
            "/api/v1/models",
            json={
                "name": f"fake-test-model-{uuid.uuid4().hex[:8]}",
                "revision": {
                    "provider": "fake",
                    "model_name": "scripted-fake",
                    "parameters": {},
                },
            },
        )
        assert fake_model_resp.status_code == 201
        fake_rev_id = fake_model_resp.json()["active_revision_id"]

        # Resolve fake model
        async with async_session_factory() as session:
            fake_chat_model = await CatalogService.resolve_chat_model(
                session, uuid.UUID(fake_rev_id)
            )
            res = await fake_chat_model.ainvoke(
                [HumanMessage(content="Hello test model")]
            )
            assert res.content == "Test response from fake model"

            # Resolve real/litellm model with decrypted credential
            litellm_model = await CatalogService.resolve_chat_model(
                session, uuid.UUID(rev2_id)
            )
            assert litellm_model.model == "openai/gpt-4o"
            assert litellm_model.api_key == "sk-fake-secret-9999"

            # Verify model revision record in DB only has api_key_id, never plaintext secret
            rev_db = await CatalogService.get_model_revision(session, uuid.UUID(rev2_id))
            assert rev_db is not None
            assert rev_db.api_key_id == uuid.UUID(cred_id)
            assert "sk-fake-secret-9999" not in str(rev_db.__dict__)
