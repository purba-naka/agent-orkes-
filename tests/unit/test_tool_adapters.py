import asyncio
from types import SimpleNamespace
import uuid

import httpx
import pytest

from orchestrator.tools.adapters import ToolInvocationError, ToolInvoker
from orchestrator.tools.network import NetworkPolicy, NetworkPolicyError
from orchestrator.tools.registry import CodeToolRegistry


async def resolves_to(address: str):
    async def resolver(host: str, port: int) -> list[str]:
        return [address]

    return resolver


def revision(**overrides):
    values = {
        "is_enabled": True,
        "kind": "http",
        "input_schema": {"type": "object"},
        "output_schema": {"type": "object"},
        "configuration": {
            "url": "http://example.test/echo",
            "method": "POST",
            "timeout_seconds": 1,
        },
        "is_mutating": False,
        "max_attempts": 1,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.mark.asyncio
async def test_network_policy_rejects_private_target_and_exact_allowlist_accepts() -> None:
    resolver = await resolves_to("127.0.0.1")
    with pytest.raises(NetworkPolicyError, match="prohibited"):
        await NetworkPolicy(resolver=resolver).validate_url("http://local.test/tool")

    await NetworkPolicy(
        local_allowlist={"local.test"}, resolver=resolver
    ).validate_url("http://local.test/tool")
    with pytest.raises(NetworkPolicyError, match="prohibited"):
        await NetworkPolicy(
            local_allowlist={"other.local.test"}, resolver=resolver
        ).validate_url("http://local.test/tool")


@pytest.mark.asyncio
async def test_network_policy_rejects_metadata_and_url_credentials() -> None:
    metadata_resolver = await resolves_to("169.254.169.254")
    with pytest.raises(NetworkPolicyError, match="prohibited"):
        await NetworkPolicy(resolver=metadata_resolver).validate_url(
            "http://metadata.test/latest"
        )
    with pytest.raises(NetworkPolicyError, match="credentials"):
        await NetworkPolicy().validate_url("http://user:secret@example.test/tool")


@pytest.mark.asyncio
async def test_http_tool_maps_json_and_sends_idempotency_header() -> None:
    seen: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["headers"] = dict(request.headers)
        seen["body"] = request.read().decode()
        return httpx.Response(200, json={"accepted": True})

    public_resolver = await resolves_to("8.8.8.8")
    invoker = ToolInvoker(
        session=SimpleNamespace(),
        network_policy=NetworkPolicy(resolver=public_resolver),
        transport=httpx.MockTransport(handler),
    )
    output = await invoker.invoke(
        revision(
            is_mutating=True,
            max_attempts=3,
            input_schema={
                "type": "object",
                "properties": {"value": {"type": "string"}},
                "required": ["value"],
            },
            output_schema={
                "type": "object",
                "properties": {"accepted": {"type": "boolean"}},
                "required": ["accepted"],
            },
            configuration={
                "url": "http://example.test/echo",
                "method": "POST",
                "idempotency_header": "Idempotency-Key",
            },
        ),
        {"value": "mapped"},
        idempotency_key="run:node:1",
    )

    assert output == {"accepted": True}
    assert seen["headers"]["idempotency-key"] == "run:node:1"
    assert seen["body"] == '{"value":"mapped"}'


@pytest.mark.asyncio
async def test_http_redirect_target_is_validated_per_hop() -> None:
    async def resolver(host: str, port: int) -> list[str]:
        return ["8.8.8.8"] if host == "public.test" else ["127.0.0.1"]

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "http://private.test/secret"})

    invoker = ToolInvoker(
        session=SimpleNamespace(),
        network_policy=NetworkPolicy(resolver=resolver),
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(NetworkPolicyError, match="prohibited"):
        await invoker.invoke(
            revision(
                configuration={
                    "url": "http://public.test/start",
                    "method": "GET",
                    "follow_redirects": True,
                }
            ),
            {},
        )


@pytest.mark.asyncio
async def test_exact_version_code_registry_and_output_validation() -> None:
    registry = CodeToolRegistry()
    registry.register("math.double", "1", lambda value: {"result": value["number"] * 2})
    invoker = ToolInvoker(session=SimpleNamespace(), code_registry=registry)
    code_revision = revision(
        kind="code",
        input_schema={
            "type": "object",
            "properties": {"number": {"type": "integer"}},
            "required": ["number"],
        },
        output_schema={
            "type": "object",
            "properties": {"result": {"type": "integer"}},
            "required": ["result"],
        },
        configuration={
            "implementation_key": "math.double",
            "implementation_version": "1",
        },
    )
    assert await invoker.invoke(code_revision, {"number": 4}) == {"result": 8}

    code_revision.configuration["implementation_version"] = "2"
    with pytest.raises(ToolInvocationError) as error:
        await invoker.invoke(code_revision, {"number": 4})
    assert error.value.code == "tool_implementation_unavailable"


@pytest.mark.asyncio
async def test_mcp_streamable_http_initializes_and_calls_pinned_tool() -> None:
    requests: list[dict] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        payload = __import__("json").loads(request.read())
        requests.append(payload)
        if payload["method"] == "initialize":
            return httpx.Response(200, headers={"mcp-session-id": "session-1"}, json={"jsonrpc": "2.0", "id": 1, "result": {}})
        assert request.headers["mcp-session-id"] == "session-1"
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 2, "result": {"structuredContent": {"answer": 42}}})

    public_resolver = await resolves_to("8.8.4.4")
    invoker = ToolInvoker(
        session=SimpleNamespace(),
        network_policy=NetworkPolicy(resolver=public_resolver),
        transport=httpx.MockTransport(handler),
    )
    output = await invoker.invoke(
        revision(
            kind="mcp",
            output_schema={
                "type": "object",
                "properties": {"answer": {"type": "integer"}},
                "required": ["answer"],
            },
            configuration={
                "server_url": "https://mcp.example.test/rpc",
                "remote_tool_name": "answer",
            },
        ),
        {"question": "life"},
    )
    assert output == {"answer": 42}
    assert [request["method"] for request in requests] == ["initialize", "tools/call"]
    assert requests[1]["params"] == {"name": "answer", "arguments": {"question": "life"}}
