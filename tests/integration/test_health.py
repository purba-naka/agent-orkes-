import pytest
from httpx import ASGITransport, AsyncClient
from orchestrator.main import app


@pytest.mark.asyncio
async def test_health_endpoint() -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_ready_endpoint_with_live_db() -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/ready")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ready", "database": "connected"}
