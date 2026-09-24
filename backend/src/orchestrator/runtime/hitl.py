from __future__ import annotations

from typing import Any
import uuid

from jsonschema import Draft202012Validator, ValidationError
from langgraph.types import Interrupt
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from orchestrator.db.models import (
    AgentRevision,
    McpConnection,
    McpToolSnapshot,
    Run,
    RunInterrupt,
    Tool,
    ToolRevision,
    utcnow,
)
from orchestrator.tools.mcp_snapshots import mcp_bound_tool_name


class InterruptDecisionError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def interrupt_kind(value: Any) -> str:
    if isinstance(value, dict) and value.get("kind") == "node_review":
        return "node_review"
    return "tool_approval"


def extract_interrupts(payload: Any) -> list[Interrupt]:
    if not isinstance(payload, dict):
        return []
    raw = payload.get("__interrupt__", ())
    return [item for item in raw if isinstance(item, Interrupt)]


async def project_interrupts(
    session: AsyncSession,
    run_id: uuid.UUID,
    namespace: tuple[str, ...],
    interrupts: list[Interrupt],
) -> None:
    for item in interrupts:
        payload = {
            "namespace": list(namespace),
            "value": item.value,
        }
        statement = insert(RunInterrupt).values(
            id=uuid.uuid4(),
            run_id=run_id,
            interrupt_id=item.id,
            kind=interrupt_kind(item.value),
            payload=payload,
            status="pending",
            decision=None,
            created_at=utcnow(),
            resolved_at=None,
        )
        statement = statement.on_conflict_do_nothing(
            index_elements=["run_id", "interrupt_id"]
        )
        await session.execute(statement)


def _validate_schema(schema: dict[str, Any], value: Any, field: str) -> None:
    try:
        Draft202012Validator(schema).validate(value)
    except ValidationError as exc:
        raise InterruptDecisionError(
            "invalid_interrupt_decision",
            f"{field} does not match the pinned schema",
        ) from exc


def _find_node(revision: AgentRevision, node_id: str) -> dict[str, Any] | None:
    return next(
        (node for node in revision.document.get("nodes", []) if node.get("id") == node_id),
        None,
    )


async def _pinned_input_schema(
    session: AsyncSession, revision: AgentRevision, tool_name: str
) -> dict[str, Any] | None:
    """Resolve the pinned input schema for a runtime tool name.

    Native tools resolve through tool_revision_ids; MCP-bound tools resolve
    through the frozen snapshot pinned in each binding.
    """
    revision_ids = {
        uuid.UUID(str(raw_id))
        for node in revision.document.get("nodes", [])
        if node.get("kind") == "agent" and node.get("agent", {}).get("mode") == "inline"
        for raw_id in node.get("agent", {}).get("tool_revision_ids", [])
    }
    if revision_ids:
        rows = await session.execute(
            select(ToolRevision, Tool.name)
            .join(Tool, Tool.id == ToolRevision.tool_id)
            .where(ToolRevision.id.in_(revision_ids))
        )
        for tool_revision, catalog_name in rows.all():
            if _runtime_tool_name(catalog_name, tool_revision.id) == tool_name:
                return tool_revision.input_schema

    for node in revision.document.get("nodes", []):
        agent_cfg = node.get("agent", {}) if isinstance(node, dict) else {}
        for binding in agent_cfg.get("mcp_bindings", []) or []:
            if not isinstance(binding, dict) or not binding.get("snapshot_id"):
                continue
            snapshot = await session.get(
                McpToolSnapshot, uuid.UUID(str(binding["snapshot_id"]))
            )
            connection = await session.get(
                McpConnection, uuid.UUID(str(binding["connection_id"]))
            )
            if not snapshot or not connection:
                continue
            for entry in snapshot.tools:
                if not isinstance(entry, dict) or not entry.get("name"):
                    continue
                if mcp_bound_tool_name(connection.name, entry["name"]) == tool_name:
                    return entry["input_schema"]
    return None


async def build_resume_value(
    session: AsyncSession,
    revision: AgentRevision,
    interrupt_row: RunInterrupt,
    decision: dict[str, Any],
) -> dict[str, Any]:
    action = decision["action"]
    value = interrupt_row.payload.get("value", {})

    if interrupt_row.kind == "node_review":
        if action not in {"accept", "revise", "abort"}:
            raise InterruptDecisionError("invalid_interrupt_decision", "Invalid node review action")
        node_id = str(value.get("node_id", ""))
        node = _find_node(revision, node_id)
        if not node:
            raise InterruptDecisionError("invalid_interrupt_decision", "Reviewed node is not pinned")
        if action == "revise":
            output = decision.get("output")
            if not isinstance(output, dict):
                raise InterruptDecisionError("invalid_interrupt_decision", "Revised output is required")
            schema = node.get("agent", {}).get("output_schema", {"type": "object"})
            _validate_schema(schema, output, "output")
            return {"action": "revise", "output": output}
        if action == "abort":
            return {"action": "abort", "reason": decision.get("reason") or "Cancelled"}
        return {"action": "accept"}

    if action not in {"approve", "edit", "reject"}:
        raise InterruptDecisionError("invalid_interrupt_decision", "Invalid tool approval action")
    requests = value.get("action_requests", []) if isinstance(value, dict) else []
    if not requests:
        raise InterruptDecisionError(
            "invalid_interrupt_decision",
            "The pending interrupt contains no tool actions",
        )
    if action == "edit" and len(requests) != 1:
        raise InterruptDecisionError(
            "invalid_interrupt_decision",
            "Editing requires an interrupt with exactly one tool action",
        )

    tool_name = str(requests[0].get("name", ""))
    native: dict[str, Any] = {"type": action}
    if action == "edit":
        edited_input = decision.get("input")
        if not isinstance(edited_input, dict):
            raise InterruptDecisionError("invalid_interrupt_decision", "Edited input is required")
        pinned_schema = await _pinned_input_schema(session, revision, tool_name)
        if pinned_schema is None:
            raise InterruptDecisionError("invalid_interrupt_decision", "Tool is not pinned")
        _validate_schema(pinned_schema, edited_input, "input")
        native["edited_action"] = {"name": tool_name, "args": edited_input}
    elif action == "reject":
        native["message"] = decision.get("reason") or "Tool call rejected"

    # Native HITL can group several tool calls in one interrupt. A single public
    # approve/reject decision applies to the complete reviewed group; edit stays
    # intentionally single-action so no unmatched call can be silently accepted.
    if action in {"approve", "reject"}:
        return {"decisions": [dict(native) for _ in requests]}
    return {"decisions": [native]}


async def prepare_resume(
    session: AsyncSession,
    run_id: uuid.UUID,
    decision: dict[str, Any],
) -> tuple[Run, AgentRevision, RunInterrupt, dict[str, Any]]:
    interrupt_id = str(decision["interrupt_id"])
    row = await session.execute(
        select(RunInterrupt)
        .where(
            RunInterrupt.run_id == run_id,
            RunInterrupt.interrupt_id == interrupt_id,
        )
        .with_for_update()
    )
    interrupt_row = row.scalar_one_or_none()
    if not interrupt_row:
        raise InterruptDecisionError("interrupt_not_found", "Pending interrupt not found")
    if interrupt_row.status != "pending":
        raise InterruptDecisionError(
            "interrupt_already_resolved", "Interrupt has already been resolved"
        )
    run = await session.get(Run, run_id)
    if not run or run.status != "interrupted":
        raise InterruptDecisionError("interrupt_not_found", "Run is not interrupted")
    revision = await session.get(AgentRevision, run.agent_revision_id)
    if not revision:
        raise InterruptDecisionError("interrupt_not_found", "Pinned revision not found")

    native_value = await build_resume_value(session, revision, interrupt_row, decision)
    interrupt_row.status = "resolved"
    interrupt_row.decision = decision
    interrupt_row.resolved_at = utcnow()
    run.status = "running"
    await session.commit()
    return run, revision, interrupt_row, native_value


def _runtime_tool_name(name: str, revision_id: uuid.UUID) -> str:
    import re

    normalized = re.sub(r"[^a-zA-Z0-9_-]", "_", name).strip("_")
    return (normalized or f"tool_{revision_id.hex[:8]}")[:64]
