import json
import re
from typing import Any
from urllib.parse import quote, urljoin
import uuid

import httpx
from jsonschema import Draft202012Validator, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from orchestrator.db.models import ToolRevision
from orchestrator.domain.catalog import CatalogService
from orchestrator.retrieval.service import KnowledgeError, KnowledgeService
from orchestrator.tools.mcp_oauth import McpOAuthError, McpOAuthService
from orchestrator.tools.network import NetworkPolicy
from orchestrator.tools.registry import CodeToolRegistry, code_tool_registry

_TEMPLATE_FIELD = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
_REDIRECT_STATUSES = {301, 302, 303, 307, 308}


def parse_jsonrpc_response(response: httpx.Response, request_id: int) -> dict[str, Any]:
    """Streamable HTTP servers answer with plain JSON or an SSE stream."""
    if not response.headers.get("content-type", "").startswith("text/event-stream"):
        return response.json()
    # ponytail: buffers the whole stream; fine for request/response tool calls.
    for event in response.text.replace("\r\n", "\n").split("\n\n"):
        data = "\n".join(
            line[5:].removeprefix(" ") for line in event.split("\n") if line.startswith("data:")
        )
        if not data:
            continue
        message = json.loads(data)
        if isinstance(message, dict) and message.get("id") == request_id:
            return message
    raise json.JSONDecodeError("No JSON-RPC response in event stream", response.text, 0)


class ToolInvocationError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def validate_schema(schema: dict[str, Any], value: Any, code: str) -> None:
    try:
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(value)
    except ValidationError as exc:
        path = "/" + "/".join(str(part) for part in exc.absolute_path)
        raise ToolInvocationError(code, f"JSON Schema validation failed at {path}") from exc
    except Exception as exc:
        raise ToolInvocationError(code, "The configured JSON Schema is invalid") from exc


def ensure_json_value(value: Any) -> Any:
    try:
        return json.loads(json.dumps(value, allow_nan=False))
    except (TypeError, ValueError) as exc:
        raise ToolInvocationError(
            "tool_output_not_serializable", "Tool output is not JSON-serializable"
        ) from exc


class ToolInvoker:
    def __init__(
        self,
        session: AsyncSession,
        *,
        network_policy: NetworkPolicy | None = None,
        code_registry: CodeToolRegistry | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.session = session
        self.network_policy = network_policy or NetworkPolicy()
        self.code_registry = code_registry or code_tool_registry
        self.transport = transport

    async def invoke(
        self,
        revision: ToolRevision,
        input_data: dict[str, Any],
        *,
        idempotency_key: str | None = None,
    ) -> Any:
        if not revision.is_enabled:
            raise ToolInvocationError("tool_unavailable", "Tool revision is disabled")
        validate_schema(revision.input_schema, input_data, "tool_input_invalid")

        if revision.kind == "code":
            config = revision.configuration
            try:
                output = await self.code_registry.invoke(
                    str(config["implementation_key"]),
                    str(config["implementation_version"]),
                    input_data,
                )
            except KeyError as exc:
                raise ToolInvocationError("tool_implementation_unavailable", str(exc)) from exc
        elif revision.kind == "http":
            output = await self._invoke_http(revision, input_data, idempotency_key)
        elif revision.kind == "mcp":
            output = await self._invoke_mcp(revision, input_data, idempotency_key)
        elif revision.kind == "retrieval":
            config = revision.configuration
            try:
                output = await KnowledgeService.retrieve(
                    self.session,
                    knowledge_base_id=uuid.UUID(str(config["knowledge_base_id"])),
                    embedding_model_revision_id=uuid.UUID(
                        str(config["embedding_model_revision_id"])
                    ),
                    query=str(input_data["query"]),
                    top_k=int(config.get("top_k", 5)),
                )
            except (KeyError, ValueError, KnowledgeError) as exc:
                raise ToolInvocationError(
                    "tool_configuration_invalid", str(exc)
                ) from exc
        else:
            raise ToolInvocationError("tool_kind_unsupported", "Tool kind is not executable")

        output = ensure_json_value(output)
        validate_schema(revision.output_schema, output, "tool_output_invalid")
        return output

    async def _credential_headers(self, config: dict[str, Any]) -> dict[str, str]:
        headers = {str(key): str(value) for key, value in config.get("headers", {}).items()}
        credential_headers = config.get("credential_headers", {})
        if not isinstance(credential_headers, dict):
            raise ToolInvocationError(
                "tool_configuration_invalid", "credential_headers must be an object"
            )
        for header, credential_id in credential_headers.items():
            try:
                parsed_id = uuid.UUID(str(credential_id))
            except ValueError as exc:
                raise ToolInvocationError(
                    "tool_configuration_invalid", "Credential reference is invalid"
                ) from exc
            headers[str(header)] = await CatalogService.get_decrypted_credential(
                self.session, parsed_id
            )
        return headers

    @staticmethod
    def _render_url(config: dict[str, Any], input_data: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        template = str(config["url"])
        fields = _TEMPLATE_FIELD.findall(template)
        declared = config.get("path_parameters", fields)
        if sorted(fields) != sorted(declared) or len(fields) != len(set(fields)):
            raise ToolInvocationError(
                "tool_configuration_invalid",
                "URL template fields must exactly match declared path_parameters",
            )
        body = dict(input_data)
        for field in fields:
            if field not in input_data:
                raise ToolInvocationError(
                    "tool_input_invalid", f"Missing URL path parameter '{field}'"
                )
            template = template.replace("{" + field + "}", quote(str(input_data[field]), safe=""))
            body.pop(field, None)
        return template, body

    async def _send_json(
        self,
        *,
        method: str,
        url: str,
        headers: dict[str, str],
        body: Any,
        timeout: float,
        follow_redirects: bool,
        max_redirects: int,
    ) -> httpx.Response:
        current_method = method
        current_url = url
        current_body = body
        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=False,
            trust_env=False,
            transport=self.transport,
        ) as client:
            for redirect_count in range(max_redirects + 1):
                await self.network_policy.validate_url(current_url)
                response = await client.request(
                    current_method,
                    current_url,
                    headers=headers,
                    json=current_body,
                )
                if response.status_code not in _REDIRECT_STATUSES:
                    return response
                if not follow_redirects:
                    raise ToolInvocationError("tool_redirect_rejected", "Redirects are disabled")
                location = response.headers.get("location")
                if not location or redirect_count == max_redirects:
                    raise ToolInvocationError(
                        "tool_redirect_rejected", "Redirect limit reached or location missing"
                    )
                current_url = urljoin(str(response.url), location)
                if response.status_code == 303:
                    current_method, current_body = "GET", None
        raise ToolInvocationError("tool_redirect_rejected", "Redirect limit reached")

    async def _invoke_http(
        self,
        revision: ToolRevision,
        input_data: dict[str, Any],
        idempotency_key: str | None,
    ) -> Any:
        config = revision.configuration
        method = str(config.get("method", "POST")).upper()
        url, body = self._render_url(config, input_data)
        headers = await self._credential_headers(config)
        idempotency_header = config.get("idempotency_header")
        if idempotency_header and idempotency_key:
            headers[str(idempotency_header)] = idempotency_key
        if revision.is_mutating and revision.max_attempts > 1 and not (
            idempotency_header and idempotency_key
        ):
            raise ToolInvocationError(
                "tool_idempotency_required",
                "A mutating retryable tool requires an idempotency header",
            )
        try:
            response = await self._send_json(
                method=method,
                url=url,
                headers=headers,
                body=body,
                timeout=float(config.get("timeout_seconds", 30)),
                follow_redirects=bool(config.get("follow_redirects", False)),
                max_redirects=int(config.get("max_redirects", 5)),
            )
            response.raise_for_status()
            return response.json()
        except ToolInvocationError:
            raise
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise ToolInvocationError("tool_network_error", "Tool request failed") from exc
        except (httpx.HTTPStatusError, json.JSONDecodeError) as exc:
            raise ToolInvocationError("tool_http_error", "Tool returned an invalid HTTP response") from exc

    async def invoke_mcp_tool(
        self,
        config: dict[str, Any],
        remote_tool_name: str,
        input_data: dict[str, Any],
    ) -> Any:
        """Call a remote tool on a connected MCP server by name.

        Used by agent bindings pinned to a snapshot: the schema is validated
        by the caller against the snapshot entry, this only performs the call.
        """
        headers = await self._credential_headers(config)
        payload = await self._mcp_rpc(
            config,
            headers,
            [("tools/call", {"name": remote_tool_name, "arguments": input_data})],
        )
        return self._mcp_call_result(payload[0])

    @staticmethod
    def _mcp_call_result(payload: dict[str, Any]) -> Any:
        if "error" in payload:
            raise ToolInvocationError("mcp_tool_error", "Remote MCP tool returned an error")
        result = payload.get("result", {})
        if result.get("isError"):
            raise ToolInvocationError("mcp_tool_error", "Remote MCP tool returned an error")
        if "structuredContent" in result:
            return result["structuredContent"]
        content = result.get("content", [])
        if len(content) == 1 and content[0].get("type") == "text":
            text = content[0].get("text", "")
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return {"content": text}
        return {"content": content}

    async def _invoke_mcp(
        self,
        revision: ToolRevision,
        input_data: dict[str, Any],
        idempotency_key: str | None,
    ) -> Any:
        config = revision.configuration
        headers = await self._credential_headers(config)
        idempotency_header = config.get("idempotency_header")
        if idempotency_header and idempotency_key:
            headers[str(idempotency_header)] = idempotency_key
        if revision.is_mutating and revision.max_attempts > 1 and not (
            idempotency_header and idempotency_key
        ):
            raise ToolInvocationError(
                "tool_idempotency_required",
                "A mutating retryable tool requires an idempotency header",
            )
        payload = await self._mcp_rpc(
            config,
            headers,
            [("tools/call", {"name": config["remote_tool_name"], "arguments": input_data})],
        )
        return self._mcp_call_result(payload[0])

    async def list_mcp_tools(self, config: dict[str, Any]) -> list[dict[str, Any]]:
        """All tools the server advertises, following `nextCursor` pagination."""
        headers = await self._credential_headers(config)
        tools: list[dict[str, Any]] = []
        cursor: str | None = None
        # ponytail: one MCP session per page; servers rarely paginate tool lists.
        for _ in range(20):
            params = {"cursor": cursor} if cursor else {}
            payload = (await self._mcp_rpc(config, dict(headers), [("tools/list", params)]))[0]
            if "error" in payload:
                raise ToolInvocationError("mcp_protocol_error", "MCP tools/list failed")
            result = payload.get("result", {})
            tools.extend(t for t in result.get("tools", []) if isinstance(t, dict) and t.get("name"))
            cursor = result.get("nextCursor")
            if not cursor:
                return tools
        raise ToolInvocationError("mcp_protocol_error", "MCP tools/list did not terminate")

    async def _mcp_rpc(
        self,
        config: dict[str, Any],
        headers: dict[str, str],
        calls: list[tuple[str, dict[str, Any]]],
    ) -> list[dict[str, Any]]:
        """Open a Streamable HTTP session, then send each (method, params) in order."""
        headers.setdefault("Accept", "application/json, text/event-stream")
        server_url = str(config["server_url"])
        timeout = float(config.get("timeout_seconds", 30))
        if config.get("connection_id"):
            try:
                token = await McpOAuthService(
                    self.network_policy, self.transport
                ).access_token(self.session, uuid.UUID(str(config["connection_id"])))
            except (McpOAuthError, ValueError) as exc:
                raise ToolInvocationError("mcp_auth_required", str(exc)) from exc
            headers["Authorization"] = f"Bearer {token}"
        redirect_options = {
            "timeout": timeout,
            "follow_redirects": bool(config.get("follow_redirects", False)),
            "max_redirects": int(config.get("max_redirects", 5)),
        }

        try:
            initialize = await self._send_json(
                method="POST",
                url=server_url,
                headers=headers,
                body={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": str(
                            config.get("protocol_version", "2025-03-26")
                        ),
                        "capabilities": {},
                        "clientInfo": {
                            "name": "agent-orchestrator",
                            "version": "1",
                        },
                    },
                },
                **redirect_options,
            )
            if initialize.status_code == 401:
                raise ToolInvocationError(
                    "mcp_auth_required", "MCP server rejected the credentials"
                )
            initialize.raise_for_status()
            init_payload = parse_jsonrpc_response(initialize, 1)
            if "error" in init_payload:
                raise ToolInvocationError("mcp_protocol_error", "MCP initialize failed")
            session_id = initialize.headers.get("mcp-session-id")
            call_headers = dict(headers)
            if session_id:
                call_headers["Mcp-Session-Id"] = session_id
            negotiated = init_payload.get("result", {}).get("protocolVersion")
            if negotiated:
                call_headers["MCP-Protocol-Version"] = str(negotiated)
            initialized = await self._send_json(
                method="POST",
                url=server_url,
                headers=call_headers,
                body={"jsonrpc": "2.0", "method": "notifications/initialized"},
                **redirect_options,
            )
            initialized.raise_for_status()
            payloads = []
            for request_id, (method, params) in enumerate(calls, start=2):
                response = await self._send_json(
                    method="POST",
                    url=server_url,
                    headers=call_headers,
                    body={"jsonrpc": "2.0", "id": request_id, "method": method, "params": params},
                    **redirect_options,
                )
                response.raise_for_status()
                payloads.append(parse_jsonrpc_response(response, request_id))
            return payloads
        except ToolInvocationError:
            raise
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise ToolInvocationError("tool_network_error", "MCP request failed") from exc
        except (httpx.HTTPStatusError, json.JSONDecodeError) as exc:
            raise ToolInvocationError("mcp_protocol_error", "MCP server returned an invalid response") from exc

