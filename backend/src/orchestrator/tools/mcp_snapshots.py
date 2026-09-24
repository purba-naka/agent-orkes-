"""Content-addressed snapshots of an MCP server's tools/list listing.

Publishing an agent freezes a snapshot ID into the revision so published
agents stay reproducible; drafts keep tracking the server's latest listing.
Snapshots are immutable: a refresh that sees new content stores a new row
and leaves previous rows untouched.
"""

from __future__ import annotations

from typing import Any
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from orchestrator.db.models import McpConnection, McpToolSnapshot
from orchestrator.domain.canonical import compute_content_hash
from orchestrator.tools.adapters import ToolInvoker, ToolInvocationError
from orchestrator.tools.network import NetworkPolicy


def normalize_tool_entry(raw: dict[str, Any]) -> dict[str, Any]:
    """Project a raw tools/list entry onto the fields a snapshot pins."""
    name = str(raw.get("name", "")).strip()
    if not name:
        raise ToolInvocationError("mcp_protocol_error", "Tool entry is missing a name")
    input_schema = raw.get("inputSchema")
    if not isinstance(input_schema, dict):
        input_schema = {"type": "object"}
    output_schema = raw.get("outputSchema")
    title = raw.get("title")
    description = raw.get("description")
    annotations = raw.get("annotations")
    return {
        "name": name,
        "title": str(title) if isinstance(title, str) and title.strip() else None,
        "description": description if isinstance(description, str) else None,
        "input_schema": input_schema,
        "output_schema": output_schema if isinstance(output_schema, dict) else None,
        "annotations": annotations if isinstance(annotations, dict) else None,
    }


def snapshot_tools_hash(tools: list[dict[str, Any]]) -> str:
    """Hash a normalized listing deterministically (order-independent)."""
    return compute_content_hash(sorted(tools, key=lambda tool: tool["name"]))


class McpSnapshotService:
    def __init__(
        self,
        network_policy: NetworkPolicy | None = None,
        transport: Any | None = None,
    ) -> None:
        self.network_policy = network_policy or NetworkPolicy()
        self.transport = transport

    async def refresh(
        self, session: AsyncSession, connection: McpConnection
    ) -> McpToolSnapshot:
        """Fetch the live listing and store it unless the content already exists."""
        if connection.status != "connected":
            raise ToolInvocationError(
                "mcp_auth_required", "MCP server is not connected"
            )
        invoker = ToolInvoker(
            session,
            network_policy=self.network_policy,
            transport=self.transport,
        )
        raw_tools = await invoker.list_mcp_tools(
            {"server_url": connection.server_url, "connection_id": str(connection.id)}
        )
        tools = [normalize_tool_entry(tool) for tool in raw_tools]
        tools_hash = snapshot_tools_hash(tools)

        existing = await session.scalar(
            select(McpToolSnapshot).where(
                McpToolSnapshot.connection_id == connection.id,
                McpToolSnapshot.tools_hash == tools_hash,
            )
        )
        if existing:
            return existing

        snapshot = McpToolSnapshot(
            id=uuid.uuid4(),
            connection_id=connection.id,
            tools_hash=tools_hash,
            tools=tools,
        )
        session.add(snapshot)
        await session.commit()
        await session.refresh(snapshot)
        return snapshot

    @staticmethod
    async def latest(
        session: AsyncSession, connection_id: uuid.UUID
    ) -> McpToolSnapshot | None:
        stmt = (
            select(McpToolSnapshot)
            .where(McpToolSnapshot.connection_id == connection_id)
            .order_by(McpToolSnapshot.created_at.desc())
            .limit(1)
        )
        return await session.scalar(stmt)
