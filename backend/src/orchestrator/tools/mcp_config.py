"""Shared bridge between an McpConnection row and ToolInvoker rpc configs.

Every path that talks to a connection's MCP server (tools listing, snapshot
refresh, bound-tool invocation) builds the same config dict here, so transport
and auth semantics stay in one place.
"""

from __future__ import annotations

import json
from typing import Any

from orchestrator.db.models import McpConnection
from orchestrator.security.encryption import vault


def _aad(connection: McpConnection) -> bytes:
    return f"mcp_connection:{connection.id}".encode()


def store_connection_env(connection: McpConnection, env: dict[str, str]) -> None:
    """Encrypt stdio env vars in place; values may carry secrets."""
    if not env:
        connection.env_ciphertext = connection.env_nonce = None
        return
    ciphertext, nonce, _ = vault.encrypt(json.dumps(env), aad=_aad(connection))
    connection.env_ciphertext, connection.env_nonce = ciphertext, nonce


def connection_env(connection: McpConnection) -> dict[str, str]:
    if connection.env_ciphertext is None or connection.env_nonce is None:
        return {}
    return json.loads(
        vault.decrypt(
            connection.env_ciphertext, connection.env_nonce, aad=_aad(connection)
        )
    )


def connection_rpc_config(connection: McpConnection) -> dict[str, Any]:
    """Build the ToolInvoker rpc config for one connection.

    `auth=none` tells the invoker to skip the OAuth token fetch; transport
    selects the wire protocol (streamable_http today, sse/stdio to follow).
    """
    config: dict[str, Any] = {
        "connection_id": str(connection.id),
        "transport": connection.transport or "streamable_http",
        "auth": connection.auth or "oauth",
    }
    if (connection.transport or "streamable_http") != "stdio":
        config["server_url"] = connection.server_url
    if connection.command:
        config["command"] = connection.command
        config["args"] = list(connection.args or [])
        config["env"] = connection_env(connection)
    return config
