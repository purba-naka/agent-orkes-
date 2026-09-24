import json
import re
from typing import Any
import uuid
from sqlalchemy.ext.asyncio import AsyncSession

from orchestrator.db.models import (
    AgentRevision,
    KnowledgeBase,
    McpConnection,
    ModelRevision,
    ToolRevision,
)
from orchestrator.domain.agent_schemas import Diagnostic
from orchestrator.tools.mcp_snapshots import McpSnapshotService
from orchestrator.tools.registry import code_tool_registry

NODE_ID_REGEX = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
MAX_CONTEXT_CHARS = 100_000
MAX_CALL_LIMIT = 1_000
MAX_MODEL_RETRIES = 10
SUPPORTED_PII_TYPES = {"email", "credit_card", "ip", "mac_address", "url"}
SUPPORTED_PII_STRATEGIES = {"block", "redact", "mask", "hash"}

VALID_MECHANICAL_OPS = {
    "eq",
    "ne",
    "lt",
    "lte",
    "gt",
    "gte",
    "in",
    "not_in",
    "is_null",
    "is_not_null",
}


def _policy_error(path: str, code: str, message: str) -> Diagnostic:
    return Diagnostic(code=code, path=path, message=message, severity="error")


def _validate_policies(
    context_policy: dict[str, Any], middleware_policy: dict[str, Any], path: str
) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    context_path = f"{path}context_policy"
    middleware_path = f"{path}middleware_policy"
    allowed_context_keys = {"memory", "knowledge_top_k", "upstream", "max_chars", "max_item_chars"}
    unknown_context = set(context_policy) - allowed_context_keys
    if unknown_context:
        diagnostics.append(_policy_error(context_path, "policy.unknown_context_field", f"Unsupported context policy fields: {', '.join(sorted(unknown_context))}"))
    if context_policy.get("memory", False) is not False:
        diagnostics.append(_policy_error(f"{context_path}/memory", "policy.memory_unsupported", "memory must remain false in schema version 1"))
    upstream = context_policy.get("upstream", [])
    if not isinstance(upstream, list) or not all(isinstance(item, str) and NODE_ID_REGEX.match(item) for item in upstream):
        diagnostics.append(_policy_error(f"{context_path}/upstream", "policy.invalid_upstream", "upstream must be a list of valid node IDs"))
    elif len(upstream) != len(set(upstream)):
        diagnostics.append(_policy_error(f"{context_path}/upstream", "policy.duplicate_upstream", "upstream node IDs must be unique"))
    for field in ("max_chars", "max_item_chars"):
        value = context_policy.get(field)
        if value is not None and (isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MAX_CONTEXT_CHARS):
            diagnostics.append(_policy_error(f"{context_path}/{field}", "policy.invalid_context_limit", f"{field} must be an integer between 1 and {MAX_CONTEXT_CHARS}"))
    top_k = context_policy.get("knowledge_top_k", 0)
    if isinstance(top_k, bool) or not isinstance(top_k, int) or not 0 <= top_k <= 10:
        diagnostics.append(_policy_error(f"{context_path}/knowledge_top_k", "policy.invalid_knowledge_top_k", "knowledge_top_k must be an integer between 0 and 10"))

    allowed_middleware_keys = {"summarization", "context_editing", "pii", "tool_approval", "tool_selection", "model_call_limit", "tool_call_limit", "model_retry"}
    unknown_middleware = set(middleware_policy) - allowed_middleware_keys
    if unknown_middleware:
        diagnostics.append(_policy_error(middleware_path, "policy.unknown_middleware", f"Unsupported middleware policies: {', '.join(sorted(unknown_middleware))}"))
    for name in allowed_middleware_keys:
        value = middleware_policy.get(name)
        if value is not None and not isinstance(value, dict):
            diagnostics.append(_policy_error(f"{middleware_path}/{name}", "policy.invalid_middleware_config", f"{name} must be an object"))

    approval = middleware_policy.get("tool_approval", {})
    if isinstance(approval, dict):
        risk_levels = approval.get("risk_levels", [])
        if not isinstance(risk_levels, list) or not all(
            level in {"medium", "high"} for level in risk_levels
        ):
            diagnostics.append(_policy_error(f"{middleware_path}/tool_approval/risk_levels", "policy.invalid_tool_approval", "risk_levels may contain only medium and high"))

    for name in ("model_call_limit", "tool_call_limit"):
        config = middleware_policy.get(name, {})
        if not isinstance(config, dict):
            continue
        for field in ("run_limit", "thread_limit"):
            value = config.get(field)
            if value is not None and (isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MAX_CALL_LIMIT):
                diagnostics.append(_policy_error(f"{middleware_path}/{name}/{field}", "policy.invalid_call_limit", f"{field} must be between 1 and {MAX_CALL_LIMIT}"))

    retry = middleware_policy.get("model_retry", {})
    if isinstance(retry, dict):
        value = retry.get("max_retries")
        if value is not None and (isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MAX_MODEL_RETRIES):
            diagnostics.append(_policy_error(f"{middleware_path}/model_retry/max_retries", "policy.invalid_model_retry", f"max_retries must be between 1 and {MAX_MODEL_RETRIES}"))

    summary = middleware_policy.get("summarization", {})
    if isinstance(summary, dict) and summary.get("enabled"):
        for field, default in (("trigger", {"type": "fraction", "value": 0.7}), ("keep", {"type": "fraction", "value": 0.3})):
            threshold = summary.get(field, default)
            if not isinstance(threshold, dict):
                diagnostics.append(_policy_error(f"{middleware_path}/summarization/{field}", f"policy.invalid_summary_{field}", f"{field} must be an object"))
                continue
            kind = threshold.get("type", threshold.get("kind"))
            value = threshold.get("value")
            valid = (
                kind in {"relative", "fraction", "absolute", "tokens", "messages"}
                and isinstance(value, (int, float))
                and not isinstance(value, bool)
                and value > 0
            )
            if kind in {"relative", "fraction"}:
                valid = valid and isinstance(value, (int, float)) and value <= 1
            elif kind in {"absolute", "tokens", "messages"}:
                valid = valid and isinstance(value, int)
            if not valid:
                diagnostics.append(_policy_error(f"{middleware_path}/summarization/{field}", f"policy.invalid_summary_{field}", f"{field} must be a positive integer threshold or a fraction in (0, 1]"))

    editing = middleware_policy.get("context_editing", {})
    if isinstance(editing, dict) and editing.get("enabled"):
        for field in ("trigger_tokens", "clear_at_least_tokens", "keep_tool_results"):
            value = editing.get(field)
            if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
                diagnostics.append(_policy_error(f"{middleware_path}/context_editing/{field}", "policy.invalid_context_editing", f"{field} must be a non-negative integer"))

    pii = middleware_policy.get("pii", {})
    if isinstance(pii, dict) and pii.get("enabled"):
        types = pii.get("types", ["email"])
        if not isinstance(types, list) or not types or not all(item in SUPPORTED_PII_TYPES for item in types):
            diagnostics.append(_policy_error(f"{middleware_path}/pii/types", "policy.invalid_pii_types", "types must contain supported built-in PII types"))
        if pii.get("strategy", "redact") not in SUPPORTED_PII_STRATEGIES:
            diagnostics.append(_policy_error(f"{middleware_path}/pii/strategy", "policy.invalid_pii_strategy", "Unsupported PII strategy"))

    selection = middleware_policy.get("tool_selection", {})
    if isinstance(selection, dict) and selection.get("enabled"):
        max_tools = selection.get("max_tools", 20)
        if isinstance(max_tools, bool) or not isinstance(max_tools, int) or not 1 <= max_tools <= 100:
            diagnostics.append(_policy_error(f"{middleware_path}/tool_selection/max_tools", "policy.invalid_tool_selection", "max_tools must be between 1 and 100"))
        always_include = selection.get("always_include", [])
        if not isinstance(always_include, list) or not all(isinstance(name, str) and name for name in always_include):
            diagnostics.append(_policy_error(f"{middleware_path}/tool_selection/always_include", "policy.invalid_tool_selection", "always_include must be a list of non-empty tool names"))
    return diagnostics


def _validate_mcp_bindings(agent_cfg: dict[str, Any], path: str) -> list[Diagnostic]:
    """Shape-check agent MCP bindings. Drafts pin connections, never snapshots."""
    diagnostics: list[Diagnostic] = []
    bindings = agent_cfg.get("mcp_bindings", [])
    base = f"{path}/mcp_bindings"
    if not isinstance(bindings, list):
        return [
            Diagnostic(
                code="node.invalid_mcp_bindings",
                path=base,
                message="mcp_bindings must be a list",
                severity="error",
            )
        ]
    for idx, binding in enumerate(bindings):
        binding_path = f"{base}/{idx}"
        if not isinstance(binding, dict):
            diagnostics.append(
                Diagnostic(
                    code="node.invalid_mcp_binding",
                    path=binding_path,
                    message="MCP binding must be a JSON object",
                    severity="error",
                )
            )
            continue
        if "snapshot_id" in binding:
            diagnostics.append(
                Diagnostic(
                    code="node.mcp_binding_snapshot_in_draft",
                    path=f"{binding_path}/snapshot_id",
                    message="Drafts bind a connection, not a snapshot; snapshot_id is frozen at publish time",
                    severity="error",
                )
            )
        try:
            uuid.UUID(str(binding.get("connection_id")))
        except (TypeError, ValueError, AttributeError):
            diagnostics.append(
                Diagnostic(
                    code="node.invalid_mcp_connection_uuid",
                    path=f"{binding_path}/connection_id",
                    message="MCP binding connection_id must be a UUID",
                    severity="error",
                )
            )
        tools = binding.get("tools")
        if not isinstance(tools, list) or not tools:
            diagnostics.append(
                Diagnostic(
                    code="node.invalid_mcp_binding_tools",
                    path=f"{binding_path}/tools",
                    message="MCP binding tools must be a non-empty list",
                    severity="error",
                )
            )
            continue
        for tool_idx, tool in enumerate(tools):
            tool_path = f"{binding_path}/tools/{tool_idx}"
            if not isinstance(tool, dict) or not isinstance(tool.get("name"), str) or not tool["name"].strip():
                diagnostics.append(
                    Diagnostic(
                        code="node.invalid_mcp_tool_name",
                        path=f"{tool_path}/name",
                        message="Bound MCP tool name must be a non-empty string",
                        severity="error",
                    )
                )
            approval = tool.get("approval", "never")
            if approval not in ("always", "never"):
                diagnostics.append(
                    Diagnostic(
                        code="node.invalid_mcp_tool_approval",
                        path=f"{tool_path}/approval",
                        message="Bound MCP tool approval must be 'always' or 'never'",
                        severity="error",
                    )
                )
    return diagnostics


def validate_draft_document(doc: dict[str, Any]) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []

    if not isinstance(doc, dict):
        return [
            Diagnostic(
                code="document.invalid_type",
                path="/",
                message="Document root must be a JSON object",
                severity="error",
            )
        ]

    if doc.get("schema_version") != 1:
        diagnostics.append(
            Diagnostic(
                code="document.unsupported_schema_version",
                path="/schema_version",
                message="schema_version must be 1",
                severity="error",
            )
        )

    entry_node_id = doc.get("entry_node_id")
    if not isinstance(entry_node_id, str) or not NODE_ID_REGEX.match(entry_node_id):
        diagnostics.append(
            Diagnostic(
                code="document.invalid_entry_node_id",
                path="/entry_node_id",
                message="entry_node_id must match ^[a-z][a-z0-9_]{0,63}$",
                severity="error",
            )
        )

    rec_limit = doc.get("recursion_limit", 25)
    if not isinstance(rec_limit, int) or rec_limit < 1 or rec_limit > 100:
        diagnostics.append(
            Diagnostic(
                code="document.invalid_recursion_limit",
                path="/recursion_limit",
                message="recursion_limit must be an integer between 1 and 100",
                severity="error",
            )
        )

    context_policy = doc.get("context_policy", {})
    middleware_policy = doc.get("middleware_policy", {})
    if not isinstance(context_policy, dict):
        diagnostics.append(Diagnostic(code="policy.invalid_context", path="/context_policy", message="context_policy must be an object"))
        context_policy = {}
    if not isinstance(middleware_policy, dict):
        diagnostics.append(Diagnostic(code="policy.invalid_middleware", path="/middleware_policy", message="middleware_policy must be an object"))
        middleware_policy = {}
    diagnostics.extend(_validate_policies(context_policy, middleware_policy, "/"))

    named_exits = doc.get("named_exits", ["success"])
    if not isinstance(named_exits, list) or not all(isinstance(x, str) and x for x in named_exits):
        diagnostics.append(
            Diagnostic(
                code="document.invalid_named_exits",
                path="/named_exits",
                message="named_exits must be a list of non-empty strings",
                severity="error",
            )
        )

    nodes = doc.get("nodes")
    if not isinstance(nodes, list):
        diagnostics.append(
            Diagnostic(
                code="document.invalid_nodes",
                path="/nodes",
                message="nodes must be a list",
                severity="error",
            )
        )
        return diagnostics

    node_ids: set[str] = set()
    for idx, node in enumerate(nodes):
        if not isinstance(node, dict):
            diagnostics.append(
                Diagnostic(
                    code="node.invalid_type",
                    path=f"/nodes/{idx}",
                    message="Node must be a JSON object",
                    severity="error",
                )
            )
            continue

        n_id = node.get("id")
        if not isinstance(n_id, str) or not NODE_ID_REGEX.match(n_id):
            diagnostics.append(
                Diagnostic(
                    code="node.invalid_id",
                    path=f"/nodes/{idx}/id",
                    message="Node id must match ^[a-z][a-z0-9_]{0,63}$",
                    severity="error",
                )
            )
        elif n_id in node_ids:
            diagnostics.append(
                Diagnostic(
                    code="node.duplicate_id",
                    path=f"/nodes/{idx}/id",
                    message=f"Duplicate node id '{n_id}'",
                    severity="error",
                )
            )
        else:
            node_ids.add(n_id)

        kind = node.get("kind")
        if kind not in ("agent", "tool"):
            diagnostics.append(
                Diagnostic(
                    code="node.invalid_kind",
                    path=f"/nodes/{idx}/kind",
                    message="Node kind must be 'agent' or 'tool'",
                    severity="error",
                )
            )

        if kind == "agent":
            agent_cfg = node.get("agent")
            if not isinstance(agent_cfg, dict):
                diagnostics.append(
                    Diagnostic(
                        code="node.missing_agent_config",
                        path=f"/nodes/{idx}/agent",
                        message="Agent node must contain agent configuration object",
                        severity="error",
                    )
                )
            else:
                mode = agent_cfg.get("mode")
                if mode not in ("inline", "ref"):
                    diagnostics.append(
                        Diagnostic(
                            code="node.invalid_agent_mode",
                            path=f"/nodes/{idx}/agent/mode",
                            message="Agent mode must be 'inline' or 'ref'",
                            severity="error",
                        )
                    )
                if mode == "inline":
                    if not isinstance(agent_cfg.get("review_output", False), bool):
                        diagnostics.append(_policy_error(f"/nodes/{idx}/agent/review_output", "policy.invalid_review_output", "review_output must be a boolean"))
                    node_context_policy = agent_cfg.get("context_policy", {})
                    node_middleware_policy = agent_cfg.get("middleware_policy", {})
                    if not isinstance(node_context_policy, dict):
                        diagnostics.append(Diagnostic(code="policy.invalid_context", path=f"/nodes/{idx}/agent/context_policy", message="context_policy must be an object"))
                        node_context_policy = {}
                    if not isinstance(node_middleware_policy, dict):
                        diagnostics.append(Diagnostic(code="policy.invalid_middleware", path=f"/nodes/{idx}/agent/middleware_policy", message="middleware_policy must be an object"))
                        node_middleware_policy = {}
                    diagnostics.extend(_validate_policies(node_context_policy, node_middleware_policy, f"/nodes/{idx}/agent/"))
                    for tool_idx, tool_revision_id in enumerate(agent_cfg.get("tool_revision_ids", [])):
                        try:
                            uuid.UUID(str(tool_revision_id))
                        except (TypeError, ValueError, AttributeError):
                            diagnostics.append(Diagnostic(code="node.invalid_tool_revision_uuid", path=f"{path}/agent/tool_revision_ids/{tool_idx}", message="tool_revision_id must be an immutable revision UUID", severity="error"))
                    diagnostics.extend(
                        _validate_mcp_bindings(agent_cfg, f"/nodes/{idx}/agent")
                    )
                    m_rev = agent_cfg.get("model_revision_id")
                    if not m_rev:
                        diagnostics.append(
                            Diagnostic(
                                code="node.missing_model_revision",
                                path=f"/nodes/{idx}/agent/model_revision_id",
                                message="Model revision is not yet assigned",
                                severity="warning",
                            )
                        )
                elif mode == "ref":
                    revision_id = agent_cfg.get("agent_revision_id")
                    try:
                        uuid.UUID(str(revision_id))
                    except (TypeError, ValueError, AttributeError):
                        diagnostics.append(
                            Diagnostic(
                                code="node.invalid_agent_revision_uuid",
                                path=f"/nodes/{idx}/agent/agent_revision_id",
                                message="Referenced agent_revision_id must be an immutable revision UUID",
                                severity="error",
                            )
                        )

                    for mapping_name in ("input_mapping", "output_mapping"):
                        mapping = agent_cfg.get(mapping_name)
                        if mapping_name == "input_mapping" and mapping is None:
                            diagnostics.append(
                                Diagnostic(
                                    code="node.missing_input_mapping",
                                    path=f"/nodes/{idx}/agent/input_mapping",
                                    message="Referenced agent input_mapping is required",
                                    severity="error",
                                )
                            )
                            continue
                        if mapping is None:
                            continue
                        if not isinstance(mapping, dict) or not all(
                            isinstance(key, str)
                            and key
                            and isinstance(path, list)
                            and bool(path)
                            and all(isinstance(part, str) and part for part in path)
                            for key, path in mapping.items()
                        ):
                            diagnostics.append(
                                Diagnostic(
                                    code="node.invalid_mapping",
                                    path=f"/nodes/{idx}/agent/{mapping_name}",
                                    message=f"{mapping_name} must map field names to non-empty field-path arrays",
                                    severity="error",
                                )
                            )

                    result_name = agent_cfg.get("result_name")
                    if result_name is not None and (
                        not isinstance(result_name, str) or not result_name
                    ):
                        diagnostics.append(
                            Diagnostic(
                                code="node.invalid_result_name",
                                path=f"/nodes/{idx}/agent/result_name",
                                message="Referenced agent result_name must be a non-empty string",
                                severity="error",
                            )
                        )
        elif kind == "tool":
            try:
                uuid.UUID(str(node.get("tool_revision_id")))
            except (TypeError, ValueError, AttributeError):
                diagnostics.append(
                    Diagnostic(
                        code="node.invalid_tool_revision_uuid",
                        path=f"/nodes/{idx}/tool_revision_id",
                        message="tool_revision_id must be an immutable revision UUID",
                        severity="error",
                    )
                )
            mapping = node.get("input_mapping")
            if not isinstance(mapping, dict) or not all(
                isinstance(key, str)
                and key
                and isinstance(path, list)
                and bool(path)
                and all(isinstance(part, str) and part for part in path)
                for key, path in mapping.items()
            ):
                diagnostics.append(
                    Diagnostic(
                        code="node.invalid_mapping",
                        path=f"/nodes/{idx}/input_mapping",
                        message="input_mapping must map field names to non-empty field-path arrays",
                        severity="error",
                    )
                )

    # Validate edges
    edges = doc.get("edges", [])
    if not isinstance(edges, list):
        diagnostics.append(
            Diagnostic(
                code="document.invalid_edges",
                path="/edges",
                message="edges must be a list",
                severity="error",
            )
        )
        return diagnostics

    for idx, edge in enumerate(edges):
        if not isinstance(edge, dict):
            diagnostics.append(
                Diagnostic(
                    code="edge.invalid_type",
                    path=f"/edges/{idx}",
                    message="Edge must be a JSON object",
                    severity="error",
                )
            )
            continue

        e_kind = edge.get("kind")
        if e_kind not in ("direct", "exit", "semantic", "mechanical", "join"):
            diagnostics.append(
                Diagnostic(
                    code="edge.invalid_kind",
                    path=f"/edges/{idx}/kind",
                    message="Edge kind must be one of: direct, exit, semantic, mechanical, join",
                    severity="error",
                )
            )
            continue

        if e_kind == "direct":
            if not isinstance(edge.get("source"), str):
                diagnostics.append(
                    Diagnostic(
                        code="edge.invalid_source",
                        path=f"/edges/{idx}/source",
                        message="Direct edge source must be a string node ID",
                        severity="error",
                    )
                )
            if not isinstance(edge.get("target"), str):
                diagnostics.append(
                    Diagnostic(
                        code="edge.invalid_target",
                        path=f"/edges/{idx}/target",
                        message="Direct edge target must be a string node ID",
                        severity="error",
                    )
                )

        elif e_kind == "exit":
            if not isinstance(edge.get("source"), str):
                diagnostics.append(
                    Diagnostic(
                        code="edge.invalid_source",
                        path=f"/edges/{idx}/source",
                        message="Exit edge source must be a string node ID",
                        severity="error",
                    )
                )
            res_name = edge.get("result_name")
            if not isinstance(res_name, str) or not res_name:
                diagnostics.append(
                    Diagnostic(
                        code="edge.invalid_result_name",
                        path=f"/edges/{idx}/result_name",
                        message="Exit edge result_name must be a non-empty string",
                        severity="error",
                    )
                )

        elif e_kind == "join":
            sources = edge.get("sources")
            if (
                not isinstance(sources, list)
                or len(sources) < 2
                or not all(isinstance(s, str) for s in sources)
            ):
                diagnostics.append(
                    Diagnostic(
                        code="edge.invalid_sources",
                        path=f"/edges/{idx}/sources",
                        message="Join edge sources must be a list of at least 2 string node IDs",
                        severity="error",
                    )
                )
            elif len(sources) != len(set(sources)):
                diagnostics.append(
                    Diagnostic(
                        code="edge.duplicate_sources",
                        path=f"/edges/{idx}/sources",
                        message="Join edge sources must contain distinct node IDs",
                        severity="error",
                    )
                )
            if not isinstance(edge.get("target"), str):
                diagnostics.append(
                    Diagnostic(
                        code="edge.invalid_target",
                        path=f"/edges/{idx}/target",
                        message="Join edge target must be a string node ID",
                        severity="error",
                    )
                )
            if edge.get("join") != "all":
                diagnostics.append(
                    Diagnostic(
                        code="edge.invalid_join_mode",
                        path=f"/edges/{idx}/join",
                        message="Fan-in only supports 'join: all'",
                        severity="error",
                    )
                )

        elif e_kind == "semantic":
            source = edge.get("source")
            if (
                not isinstance(source, list)
                or len(source) != 2
                or not isinstance(source[0], str)
                or not isinstance(source[1], str)
            ):
                diagnostics.append(
                    Diagnostic(
                        code="edge.invalid_source",
                        path=f"/edges/{idx}/source",
                        message="Semantic edge source must be a [node_id, field_name] pair",
                        severity="error",
                    )
                )
            routes = edge.get("routes")
            if not isinstance(routes, dict) or not routes:
                diagnostics.append(
                    Diagnostic(
                        code="edge.invalid_routes",
                        path=f"/edges/{idx}/routes",
                        message="Semantic edge routes must be a non-empty dictionary mapping enum values to targets",
                        severity="error",
                    )
                )
            default = edge.get("default")
            if default is not None and not isinstance(default, str):
                diagnostics.append(
                    Diagnostic(
                        code="edge.invalid_default",
                        path=f"/edges/{idx}/default",
                        message="Semantic edge default must be a string target or null",
                        severity="error",
                    )
                )

        elif e_kind == "mechanical":
            source = edge.get("source")
            if (
                not isinstance(source, list)
                or len(source) != 2
                or not isinstance(source[0], str)
                or not isinstance(source[1], str)
            ):
                diagnostics.append(
                    Diagnostic(
                        code="edge.invalid_source",
                        path=f"/edges/{idx}/source",
                        message="Mechanical edge source must be a [node_id, field_name] pair",
                        severity="error",
                    )
                )
            op = edge.get("operator")
            if op not in VALID_MECHANICAL_OPS:
                diagnostics.append(
                    Diagnostic(
                        code="edge.invalid_operator",
                        path=f"/edges/{idx}/operator",
                        message=f"Mechanical edge operator must be one of: {', '.join(sorted(VALID_MECHANICAL_OPS))}",
                        severity="error",
                    )
                )
            if not isinstance(edge.get("then"), str):
                diagnostics.append(
                    Diagnostic(
                        code="edge.invalid_then",
                        path=f"/edges/{idx}/then",
                        message="Mechanical edge 'then' target must be a string node ID",
                        severity="error",
                    )
                )
            if not isinstance(edge.get("else"), str):
                diagnostics.append(
                    Diagnostic(
                        code="edge.invalid_else",
                        path=f"/edges/{idx}/else",
                        message="Mechanical edge 'else' target must be a string node ID",
                        severity="error",
                    )
                )

    return diagnostics


def find_directed_cycles_and_conditional_exits(
    nodes: list[str], edges: list[dict[str, Any]], named_exits: set[str]
) -> list[tuple[list[str], bool]]:
    """
    Find non-trivial Strongly Connected Components (SCCs) and check if each has a conditional exit.
    Returns a list of (scc_nodes, has_conditional_exit).
    """
    adj: dict[str, list[tuple[str, bool]]] = {n: [] for n in nodes}

    for e in edges:
        kind = e.get("kind", "direct")
        if kind == "direct":
            src = e.get("source")
            tgt = e.get("target")
            if src in adj and tgt:
                adj[src].append((tgt, False))
        elif kind == "join":
            tgt = e.get("target")
            for s in e.get("sources", []):
                if s in adj and tgt:
                    adj[s].append((tgt, False))
        elif kind == "semantic":
            s_pair = e.get("source", [])
            s_node = s_pair[0] if isinstance(s_pair, list) and len(s_pair) > 0 else None
            if s_node in adj:
                for t in e.get("routes", {}).values():
                    if t:
                        adj[s_node].append((t, True))
                if e.get("default"):
                    adj[s_node].append((e["default"], True))
        elif kind == "mechanical":
            s_pair = e.get("source", [])
            s_node = s_pair[0] if isinstance(s_pair, list) and len(s_pair) > 0 else None
            if s_node in adj:
                if e.get("then"):
                    adj[s_node].append((e["then"], True))
                if e.get("else"):
                    adj[s_node].append((e["else"], True))

    # Tarjan's SCC algorithm
    index = 0
    indices: dict[str, int] = {}
    lowlinks: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    sccs: list[list[str]] = []

    def strongconnect(v: str) -> None:
        nonlocal index
        indices[v] = index
        lowlinks[v] = index
        index += 1
        stack.append(v)
        on_stack.add(v)

        for w, _ in adj.get(v, []):
            if w in adj:  # only internal nodes
                if w not in indices:
                    strongconnect(w)
                    lowlinks[v] = min(lowlinks[v], lowlinks[w])
                elif w in on_stack:
                    lowlinks[v] = min(lowlinks[v], indices[w])

        if lowlinks[v] == indices[v]:
            scc: list[str] = []
            while True:
                w = stack.pop()
                on_stack.remove(w)
                scc.append(w)
                if w == v:
                    break
            sccs.append(scc)

    for n in nodes:
        if n not in indices:
            strongconnect(n)

    results: list[tuple[list[str], bool]] = []
    for scc in sccs:
        scc_set = set(scc)
        is_cycle = False
        if len(scc) > 1:
            is_cycle = True
        elif len(scc) == 1:
            # check self-loop
            node = scc[0]
            if any(w == node for w, _ in adj.get(node, [])):
                is_cycle = True

        if is_cycle:
            has_conditional_exit = False
            for v in scc:
                for w, is_cond in adj.get(v, []):
                    if is_cond and (w not in scc_set or w in named_exits):
                        has_conditional_exit = True
                        break
                if has_conditional_exit:
                    break
            results.append((scc, has_conditional_exit))

    return results


async def _resolve_referenced_dependencies(
    session: AsyncSession,
    revision: AgentRevision,
    path: str,
    ancestors: list[uuid.UUID],
    diagnostics: list[Diagnostic],
    manifests: dict[str, set[str]],
    owner_agent_id: uuid.UUID | None,
) -> None:
    if revision.agent_id == owner_agent_id or revision.id in ancestors:
        cycle = (
            ancestors[ancestors.index(revision.id):] + [revision.id]
            if revision.id in ancestors
            else [*ancestors, revision.id]
        )
        diagnostics.append(
            Diagnostic(
                code="publish.agent_ref_cycle",
                path=path,
                message="Referenced agent cycle detected: " + " -> ".join(map(str, cycle)),
                severity="error",
            )
        )
        return

    manifests["agents"].add(str(revision.id))
    child_manifest = revision.dependency_manifest or {}
    for kind in ("models", "tools", "agents", "mcp_servers", "mcp_snapshots"):
        manifests[kind].update(str(value) for value in child_manifest.get(kind, []))
    for code_tool in child_manifest.get("code_tools", []):
        manifests["code_tools"].add(json.dumps(code_tool, sort_keys=True))

    next_ancestors = [*ancestors, revision.id]
    for node_idx, node in enumerate(revision.document.get("nodes", [])):
        agent_cfg = node.get("agent", {}) if isinstance(node, dict) else {}
        if agent_cfg.get("mode") != "ref":
            continue
        child_id_value = agent_cfg.get("agent_revision_id")
        try:
            child_id = uuid.UUID(str(child_id_value))
        except (TypeError, ValueError, AttributeError):
            continue
        child = await session.get(AgentRevision, child_id)
        child_path = f"{path}/document/nodes/{node_idx}/agent/agent_revision_id"
        if child:
            await _resolve_referenced_dependencies(
                session,
                child,
                child_path,
                next_ancestors,
                diagnostics,
                manifests,
                owner_agent_id,
            )


def _mapping_source_exists(
    path: list[str], node_ids: set[str], entry_node_id: str
) -> bool:
    return path[0] in node_ids or path[0] in {"input", "messages", "outputs", "run_id"} or (
        path[0] == entry_node_id
    )


async def validate_publish_document(
    session: AsyncSession,
    doc: dict[str, Any],
    owner_agent_id: uuid.UUID | None = None,
    snapshot_pins: dict[str, str] | None = None,
) -> tuple[bool, list[Diagnostic], dict[str, Any]]:
    diagnostics = validate_draft_document(doc)
    has_errors = any(d.severity == "error" for d in diagnostics)
    if has_errors:
        return False, diagnostics, {}

    entry_node_id = str(doc.get("entry_node_id"))
    nodes = doc.get("nodes", [])
    edges = doc.get("edges", [])
    named_exits = set(doc.get("named_exits", ["success"]))
    node_ids: set[str] = {
        n["id"]
        for n in nodes
        if isinstance(n, dict) and isinstance(n.get("id"), str)
    }

    # 1. Check entry node existence
    if entry_node_id not in node_ids:
        diagnostics.append(
            Diagnostic(
                code="publish.entry_node_not_found",
                path="/entry_node_id",
                message=f"entry_node_id '{entry_node_id}' does not exist in nodes",
                severity="error",
            )
        )
        return False, diagnostics, {}

    # 2. Validate all inline agent nodes & resolve models
    model_ids: set[str] = set()
    manifests: dict[str, set[str]] = {
        "models": model_ids,
        "tools": set(),
        "agents": set(),
        "code_tools": set(),
        "mcp_servers": set(),
        "mcp_snapshots": set(),
    }
    for idx, node in enumerate(nodes):
        n_id = node.get("id")
        kind = node.get("kind")
        if kind == "agent":
            agent_cfg = node.get("agent", {})
            mode = agent_cfg.get("mode")
            if mode == "inline":
                m_rev_id_str = agent_cfg.get("model_revision_id")
                if not m_rev_id_str:
                    diagnostics.append(
                        Diagnostic(
                            code="publish.model_revision_required",
                            path=f"/nodes/{idx}/agent/model_revision_id",
                            message=f"Model revision ID is required for node '{n_id}'",
                            severity="error",
                        )
                    )
                    continue

                try:
                    model_rev_uuid = uuid.UUID(str(m_rev_id_str))
                except ValueError:
                    diagnostics.append(
                        Diagnostic(
                            code="publish.invalid_model_revision_uuid",
                            path=f"/nodes/{idx}/agent/model_revision_id",
                            message=f"Model revision ID for node '{n_id}' is not a valid UUID",
                            severity="error",
                        )
                    )
                    continue

                model_rev = await session.get(ModelRevision, model_rev_uuid)
                if not model_rev or not model_rev.is_enabled:
                    diagnostics.append(
                        Diagnostic(
                            code="publish.model_revision_unavailable",
                            path=f"/nodes/{idx}/agent/model_revision_id",
                            message=f"Model revision {model_rev_uuid} for node '{n_id}' does not exist or is disabled",
                            severity="error",
                        )
                    )
                    continue

                model_ids.add(str(model_rev.id))

                effective_middleware = {
                    **doc.get("middleware_policy", {}),
                    **agent_cfg.get("middleware_policy", {}),
                }
                summary = effective_middleware.get("summarization", {})
                if summary.get("enabled") and model_rev.context_window is None:
                    for field, default in (
                        ("trigger", {"type": "fraction", "value": 0.7}),
                        ("keep", {"type": "fraction", "value": 0.3}),
                    ):
                        threshold = summary.get(field, default)
                        kind = threshold.get("type", threshold.get("kind")) if isinstance(threshold, dict) else None
                        if kind in {"relative", "fraction"}:
                            diagnostics.append(
                                Diagnostic(
                                    code="publish.summarization_context_window_required",
                                    path=f"/nodes/{idx}/agent/middleware_policy/summarization/{field}",
                                    message=f"Relative summarization {field} requires model context_window; configure an absolute threshold instead",
                                    severity="error",
                                )
                            )

                for tool_idx, raw_tool_id in enumerate(agent_cfg.get("tool_revision_ids", [])):
                    tool_path = f"/nodes/{idx}/agent/tool_revision_ids/{tool_idx}"
                    try:
                        agent_tool_id = uuid.UUID(str(raw_tool_id))
                    except (TypeError, ValueError, AttributeError):
                        diagnostics.append(Diagnostic(code="publish.invalid_tool_revision_uuid", path=tool_path, message="Agent tool revision ID must be a valid UUID", severity="error"))
                        continue
                    agent_tool = await session.get(ToolRevision, agent_tool_id)
                    if not agent_tool or not agent_tool.is_enabled:
                        diagnostics.append(Diagnostic(code="publish.tool_revision_unavailable", path=tool_path, message=f"Tool revision {agent_tool_id} does not exist or is disabled", severity="error"))
                        continue
                    manifests["tools"].add(str(agent_tool.id))

                for b_idx, binding in enumerate(agent_cfg.get("mcp_bindings", [])):
                    binding_path = f"/nodes/{idx}/agent/mcp_bindings/{b_idx}"
                    if not isinstance(binding, dict):
                        continue
                    try:
                        connection_id = uuid.UUID(str(binding.get("connection_id")))
                    except (TypeError, ValueError, AttributeError):
                        continue
                    connection = await session.get(McpConnection, connection_id)
                    if not connection:
                        diagnostics.append(
                            Diagnostic(
                                code="publish.mcp_connection_unavailable",
                                path=binding_path,
                                message=f"MCP connection {connection_id} does not exist",
                                severity="error",
                            )
                        )
                        continue
                    if connection.status != "connected":
                        diagnostics.append(
                            Diagnostic(
                                code="publish.mcp_connection_not_connected",
                                path=binding_path,
                                message=f"MCP connection '{connection.name}' is not connected (status: {connection.status})",
                                severity="error",
                            )
                        )
                        continue
                    snapshot = await McpSnapshotService.latest(session, connection_id)
                    if not snapshot:
                        diagnostics.append(
                            Diagnostic(
                                code="publish.mcp_snapshot_missing",
                                path=binding_path,
                                message=f"MCP connection '{connection.name}' has no tool snapshot; refresh it before publishing",
                                severity="error",
                            )
                        )
                        continue
                    manifests["mcp_servers"].add(str(connection.id))
                    manifests["mcp_snapshots"].add(str(snapshot.id))
                    if snapshot_pins is not None:
                        snapshot_pins[str(connection.id)] = str(snapshot.id)
                    snapshot_names = {
                        tool.get("name") for tool in snapshot.tools if isinstance(tool, dict)
                    }
                    for t_idx, bound_tool in enumerate(binding.get("tools", [])):
                        if not isinstance(bound_tool, dict):
                            continue
                        name = bound_tool.get("name")
                        if name not in snapshot_names:
                            diagnostics.append(
                                Diagnostic(
                                    code="publish.mcp_tool_not_in_snapshot",
                                    path=f"{binding_path}/tools/{t_idx}/name",
                                    message=f"MCP tool '{name}' is not in the current snapshot of '{connection.name}'",
                                    severity="error",
                                )
                            )

                system_prompt = (
                    agent_cfg.get("system_prompt")
                    or doc.get("system_prompt")
                    or ""
                ).strip()
                if not system_prompt:
                    diagnostics.append(
                        Diagnostic(
                            code="publish.system_prompt_empty",
                            path=f"/nodes/{idx}/agent/system_prompt",
                            message=f"System prompt cannot be empty for node '{n_id}'",
                            severity="error",
                        )
                    )
            elif mode == "ref":
                ref_path = f"/nodes/{idx}/agent/agent_revision_id"
                ref_value = agent_cfg.get("agent_revision_id")
                try:
                    ref_id = uuid.UUID(str(ref_value))
                except (TypeError, ValueError, AttributeError):
                    continue
                ref_revision = await session.get(AgentRevision, ref_id)
                if not ref_revision:
                    diagnostics.append(
                        Diagnostic(
                            code="publish.agent_revision_unavailable",
                            path=ref_path,
                            message=f"Referenced agent revision {ref_id} does not exist",
                            severity="error",
                        )
                    )
                    continue
                if owner_agent_id is not None and ref_revision.agent_id == owner_agent_id:
                    diagnostics.append(
                        Diagnostic(
                            code="publish.agent_ref_cycle",
                            path=ref_path,
                            message=f"Referenced agent cycle detected through revision {ref_id}",
                            severity="error",
                        )
                    )
                    continue

                input_mapping = agent_cfg.get("input_mapping", {})
                child_input_schema = ref_revision.document.get("input_schema", {})
                child_input_properties = child_input_schema.get("properties", {})
                child_required = set(child_input_schema.get("required", []))
                for target, source_path in input_mapping.items():
                    mapping_path = f"/nodes/{idx}/agent/input_mapping/{target}"
                    if child_input_properties and target not in child_input_properties:
                        diagnostics.append(
                            Diagnostic(
                                code="publish.mapping_target_not_found",
                                path=mapping_path,
                                message=f"Mapping target '{target}' is not declared by revision {ref_id}",
                                severity="error",
                            )
                        )
                    if not _mapping_source_exists(source_path, node_ids, entry_node_id):
                        diagnostics.append(
                            Diagnostic(
                                code="publish.mapping_source_not_found",
                                path=mapping_path,
                                message=f"Mapping source '{source_path[0]}' is not a node or runtime input",
                                severity="error",
                            )
                        )
                for missing_target in sorted(child_required - input_mapping.keys()):
                    diagnostics.append(
                        Diagnostic(
                            code="publish.mapping_required_target_missing",
                            path=f"/nodes/{idx}/agent/input_mapping",
                            message=f"Required child input '{missing_target}' is not mapped",
                            severity="error",
                        )
                    )

                child_node_ids = {
                    child_node["id"]
                    for child_node in ref_revision.document.get("nodes", [])
                    if isinstance(child_node, dict)
                    and isinstance(child_node.get("id"), str)
                }
                for target, output_path in agent_cfg.get("output_mapping", {}).items():
                    mapping_path = f"/nodes/{idx}/agent/output_mapping/{target}"
                    valid_root = output_path[0] in {"outputs", "result_name"}
                    valid_output_node = (
                        output_path[0] != "outputs"
                        or len(output_path) > 1
                        and output_path[1] in child_node_ids
                    )
                    if not valid_root or not valid_output_node:
                        diagnostics.append(
                            Diagnostic(
                                code="publish.invalid_output_mapping_source",
                                path=mapping_path,
                                message="Output mapping must select result_name or an output from a child node",
                                severity="error",
                            )
                        )

                selected_result = agent_cfg.get("result_name")
                child_exits = set(ref_revision.document.get("named_exits", ["success"]))
                if selected_result is not None and selected_result not in child_exits:
                    diagnostics.append(
                        Diagnostic(
                            code="publish.unknown_referenced_result",
                            path=f"/nodes/{idx}/agent/result_name",
                            message=f"Referenced result '{selected_result}' is not declared by revision {ref_id}",
                            severity="error",
                        )
                    )

                await _resolve_referenced_dependencies(
                    session,
                    ref_revision,
                    ref_path,
                    [],
                    diagnostics,
                    manifests,
                    owner_agent_id,
                )
        elif kind == "tool":
            revision_path = f"/nodes/{idx}/tool_revision_id"
            try:
                tool_revision_id = uuid.UUID(str(node.get("tool_revision_id")))
            except (TypeError, ValueError, AttributeError):
                continue
            tool_revision = await session.get(ToolRevision, tool_revision_id)
            if not tool_revision or not tool_revision.is_enabled:
                diagnostics.append(
                    Diagnostic(
                        code="publish.tool_revision_unavailable",
                        path=revision_path,
                        message=f"Tool revision {tool_revision_id} does not exist or is disabled",
                        severity="error",
                    )
                )
                continue
            manifests["tools"].add(str(tool_revision.id))
            mapping = node.get("input_mapping", {})
            properties = tool_revision.input_schema.get("properties", {})
            required = set(tool_revision.input_schema.get("required", []))
            for target, source_path in mapping.items():
                mapping_path = f"/nodes/{idx}/input_mapping/{target}"
                if properties and target not in properties:
                    diagnostics.append(
                        Diagnostic(
                            code="publish.mapping_target_not_found",
                            path=mapping_path,
                            message=f"Mapping target '{target}' is not declared by tool revision",
                            severity="error",
                        )
                    )
                if not _mapping_source_exists(source_path, node_ids, entry_node_id):
                    diagnostics.append(
                        Diagnostic(
                            code="publish.mapping_source_not_found",
                            path=mapping_path,
                            message=f"Mapping source '{source_path[0]}' is not a node or runtime input",
                            severity="error",
                        )
                    )
            for missing_target in sorted(required - mapping.keys()):
                diagnostics.append(
                    Diagnostic(
                        code="publish.mapping_required_target_missing",
                        path=f"/nodes/{idx}/input_mapping",
                        message=f"Required tool input '{missing_target}' is not mapped",
                        severity="error",
                    )
                )
            if tool_revision.kind == "retrieval":
                config = tool_revision.configuration
                try:
                    knowledge_base_id = uuid.UUID(str(config["knowledge_base_id"]))
                    embedding_revision_id = uuid.UUID(
                        str(config["embedding_model_revision_id"])
                    )
                except (KeyError, ValueError):
                    diagnostics.append(
                        Diagnostic(
                            code="publish.retrieval_configuration_invalid",
                            path=revision_path,
                            message="Retrieval tool revision has invalid pinned references",
                            severity="error",
                        )
                    )
                else:
                    knowledge_base = await session.get(KnowledgeBase, knowledge_base_id)
                    if (
                        not knowledge_base
                        or knowledge_base.embedding_model_revision_id
                        != embedding_revision_id
                    ):
                        diagnostics.append(
                            Diagnostic(
                                code="publish.retrieval_dependency_unavailable",
                                path=revision_path,
                                message="Retrieval knowledge base or embedding model revision is unavailable",
                                severity="error",
                            )
                        )
                    else:
                        manifests["models"].add(str(embedding_revision_id))
            if tool_revision.kind == "code":
                config = tool_revision.configuration
                key = str(config.get("implementation_key", ""))
                version = str(config.get("implementation_version", ""))
                if not code_tool_registry.contains(key, version):
                    diagnostics.append(
                        Diagnostic(
                            code="publish.code_tool_unavailable",
                            path=revision_path,
                            message=f"Code tool {key}@{version} is not registered",
                            severity="error",
                        )
                    )
                else:
                    manifests["code_tools"].add(
                        json.dumps(
                            {
                                "implementation_key": key,
                                "implementation_version": version,
                            },
                            sort_keys=True,
                        )
                    )

    # If errors in node model resolution, return
    if any(d.severity == "error" for d in diagnostics):
        return False, diagnostics, {}

    # 3. If single node with no edges: valid
    if len(nodes) == 1 and not edges:
        dependency_manifest = {
            "models": sorted(manifests["models"]),
            "tools": sorted(manifests["tools"]),
            "agents": sorted(manifests["agents"]),
            "code_tools": [json.loads(value) for value in sorted(manifests["code_tools"])],
            "mcp_servers": sorted(manifests["mcp_servers"]),
            "mcp_snapshots": sorted(manifests["mcp_snapshots"]),
        }
        return True, diagnostics, dependency_manifest

    # 4. Multi-node graph validation
    # Validate edge targets, sources, and semantic coverage
    exit_sources: set[str] = set()
    for idx, edge in enumerate(edges):
        kind = edge.get("kind")
        if kind == "direct":
            s = edge.get("source")
            t = edge.get("target")
            if s not in node_ids:
                diagnostics.append(
                    Diagnostic(
                        code="publish.edge_source_not_found",
                        path=f"/edges/{idx}/source",
                        message=f"Direct edge source '{s}' not found in nodes",
                        severity="error",
                    )
                )
            if t not in node_ids:
                diagnostics.append(
                    Diagnostic(
                        code="publish.edge_target_not_found",
                        path=f"/edges/{idx}/target",
                        message=f"Direct edge target '{t}' not found in nodes",
                        severity="error",
                    )
                )

        elif kind == "exit":
            s = edge.get("source")
            r = edge.get("result_name")
            if s not in node_ids:
                diagnostics.append(
                    Diagnostic(
                        code="publish.edge_source_not_found",
                        path=f"/edges/{idx}/source",
                        message=f"Exit edge source '{s}' not found in nodes",
                        severity="error",
                    )
                )
            else:
                exit_sources.add(s)
            if r not in named_exits:
                diagnostics.append(
                    Diagnostic(
                        code="publish.unknown_named_exit",
                        path=f"/edges/{idx}/result_name",
                        message=f"Exit result_name '{r}' is not in document named_exits: {sorted(list(named_exits))}",
                        severity="error",
                    )
                )

        elif kind == "join":
            sources = edge.get("sources", [])
            t = edge.get("target")
            for s in sources:
                if s not in node_ids:
                    diagnostics.append(
                        Diagnostic(
                            code="publish.edge_source_not_found",
                            path=f"/edges/{idx}/sources",
                            message=f"Join edge source '{s}' not found in nodes",
                            severity="error",
                        )
                    )
            if t not in node_ids:
                diagnostics.append(
                    Diagnostic(
                        code="publish.edge_target_not_found",
                        path=f"/edges/{idx}/target",
                        message=f"Join edge target '{t}' not found in nodes",
                        severity="error",
                    )
                )
            if edge.get("join") != "all":
                diagnostics.append(
                    Diagnostic(
                        code="publish.invalid_join_mode",
                        path=f"/edges/{idx}/join",
                        message="Fan-in only supports 'join: all'",
                        severity="error",
                    )
                )

        elif kind == "semantic":
            s_pair = edge.get("source", [])
            s_node = s_pair[0] if isinstance(s_pair, list) and len(s_pair) > 0 else None
            f_name = s_pair[1] if isinstance(s_pair, list) and len(s_pair) > 1 else None
            if s_node not in node_ids:
                diagnostics.append(
                    Diagnostic(
                        code="publish.edge_source_not_found",
                        path=f"/edges/{idx}/source",
                        message=f"Semantic edge source '{s_node}' not found in nodes",
                        severity="error",
                    )
                )
            routes = edge.get("routes", {})
            for val, t in routes.items():
                if t not in node_ids and t not in named_exits:
                    diagnostics.append(
                        Diagnostic(
                            code="publish.edge_target_not_found",
                            path=f"/edges/{idx}/routes/{val}",
                            message=f"Semantic edge target '{t}' not found in nodes or named_exits",
                            severity="error",
                        )
                    )
            def_target = edge.get("default")
            if def_target and def_target not in node_ids and def_target not in named_exits:
                diagnostics.append(
                    Diagnostic(
                        code="publish.edge_target_not_found",
                        path=f"/edges/{idx}/default",
                        message=f"Semantic edge default target '{def_target}' not found in nodes or named_exits",
                        severity="error",
                    )
                )

            # Route coverage check against node output schema enum
            src_node_obj = next((n for n in nodes if n.get("id") == s_node), None)
            if src_node_obj and f_name:
                out_schema = src_node_obj.get("agent", {}).get("output_schema", {})
                props = out_schema.get("properties", {}) if isinstance(out_schema, dict) else {}
                prop_info = props.get(f_name, {}) if isinstance(props, dict) else {}
                enum_vals = prop_info.get("enum")
                if isinstance(enum_vals, list):
                    routes_keys = set(routes.keys())
                    unhandled = [str(ev) for ev in enum_vals if str(ev) not in routes_keys]
                    if unhandled and not def_target:
                        diagnostics.append(
                            Diagnostic(
                                code="publish.unhandled_semantic_route",
                                path=f"/edges/{idx}/routes",
                                message=f"Missing route for enum values {unhandled} and no default is set",
                                severity="error",
                            )
                        )

        elif kind == "mechanical":
            s_pair = edge.get("source", [])
            s_node = s_pair[0] if isinstance(s_pair, list) and len(s_pair) > 0 else None
            if s_node not in node_ids:
                diagnostics.append(
                    Diagnostic(
                        code="publish.edge_source_not_found",
                        path=f"/edges/{idx}/source",
                        message=f"Mechanical edge source '{s_node}' not found in nodes",
                        severity="error",
                    )
                )
            t_then = edge.get("then")
            t_else = edge.get("else")
            if t_then not in node_ids and t_then not in named_exits:
                diagnostics.append(
                    Diagnostic(
                        code="publish.edge_target_not_found",
                        path=f"/edges/{idx}/then",
                        message=f"Mechanical edge 'then' target '{t_then}' not found in nodes or named_exits",
                        severity="error",
                    )
                )
            if t_else not in node_ids and t_else not in named_exits:
                diagnostics.append(
                    Diagnostic(
                        code="publish.edge_target_not_found",
                        path=f"/edges/{idx}/else",
                        message=f"Mechanical edge 'else' target '{t_else}' not found in nodes or named_exits",
                        severity="error",
                    )
                )

    if any(d.severity == "error" for d in diagnostics):
        return False, diagnostics, {}

    # 5. Reachability from START (entry_node_id)
    reachable: set[str] = {entry_node_id}
    changed = True
    while changed:
        changed = False
        for edge in edges:
            k = edge.get("kind")
            if k == "direct":
                s = edge.get("source")
                t = edge.get("target")
                if s in reachable and t in node_ids and t not in reachable:
                    reachable.add(t)
                    changed = True
            elif k == "join":
                sources = edge.get("sources", [])
                t = edge.get("target")
                if all(s in reachable for s in sources) and t in node_ids and t not in reachable:
                    reachable.add(t)
                    changed = True
            elif k == "semantic":
                s_pair = edge.get("source", [])
                s_node = s_pair[0] if isinstance(s_pair, list) and len(s_pair) > 0 else None
                if s_node in reachable:
                    for t in edge.get("routes", {}).values():
                        if t in node_ids and t not in reachable:
                            reachable.add(t)
                            changed = True
                    def_t = edge.get("default")
                    if def_t in node_ids and def_t not in reachable:
                        reachable.add(def_t)
                        changed = True
            elif k == "mechanical":
                s_pair = edge.get("source", [])
                s_node = s_pair[0] if isinstance(s_pair, list) and len(s_pair) > 0 else None
                if s_node in reachable:
                    for t in (edge.get("then"), edge.get("else")):
                        if t in node_ids and t not in reachable:
                            reachable.add(t)
                            changed = True

    for idx, node in enumerate(nodes):
        nid = node.get("id")
        if nid not in reachable:
            diagnostics.append(
                Diagnostic(
                    code="publish.unreachable_node",
                    path=f"/nodes/{idx}/id",
                    message=f"Node '{nid}' is not reachable from START",
                    severity="error",
                )
            )

    # 6. Path to exit / END
    can_reach_end: set[str] = set(exit_sources)
    # Also check if any conditional edge directly targets a named exit
    for edge in edges:
        k = edge.get("kind")
        if k == "semantic":
            s_pair = edge.get("source", [])
            s_node = s_pair[0] if isinstance(s_pair, list) and len(s_pair) > 0 else None
            if s_node:
                for t in edge.get("routes", {}).values():
                    if t in named_exits:
                        can_reach_end.add(s_node)
                if edge.get("default") in named_exits:
                    can_reach_end.add(s_node)
        elif k == "mechanical":
            s_pair = edge.get("source", [])
            s_node = s_pair[0] if isinstance(s_pair, list) and len(s_pair) > 0 else None
            if s_node:
                if edge.get("then") in named_exits or edge.get("else") in named_exits:
                    can_reach_end.add(s_node)

    changed = True
    while changed:
        changed = False
        for edge in edges:
            k = edge.get("kind")
            if k == "direct":
                s = edge.get("source")
                t = edge.get("target")
                if t in can_reach_end and s in node_ids and s not in can_reach_end:
                    can_reach_end.add(s)
                    changed = True
            elif k == "join":
                t = edge.get("target")
                if t in can_reach_end:
                    for s in edge.get("sources", []):
                        if s in node_ids and s not in can_reach_end:
                            can_reach_end.add(s)
                            changed = True
            elif k == "semantic":
                s_pair = edge.get("source", [])
                s_node = s_pair[0] if isinstance(s_pair, list) and len(s_pair) > 0 else None
                if s_node in node_ids and s_node not in can_reach_end:
                    targets = list(edge.get("routes", {}).values())
                    if edge.get("default"):
                        targets.append(edge["default"])
                    if any(t in can_reach_end or t in named_exits for t in targets):
                        can_reach_end.add(s_node)
                        changed = True
            elif k == "mechanical":
                s_pair = edge.get("source", [])
                s_node = s_pair[0] if isinstance(s_pair, list) and len(s_pair) > 0 else None
                if s_node in node_ids and s_node not in can_reach_end:
                    if any(
                        t in can_reach_end or t in named_exits
                        for t in (edge.get("then"), edge.get("else"))
                    ):
                        can_reach_end.add(s_node)
                        changed = True

    for idx, node in enumerate(nodes):
        nid = node.get("id")
        if nid not in can_reach_end:
            diagnostics.append(
                Diagnostic(
                    code="publish.no_path_to_end",
                    path=f"/nodes/{idx}/id",
                    message=f"Node '{nid}' has no path to an exit/END",
                    severity="error",
                )
            )

    # 7. Cycle detection & conditional exit check
    node_id_list = [
        n["id"]
        for n in nodes
        if isinstance(n, dict) and isinstance(n.get("id"), str)
    ]
    cycles = find_directed_cycles_and_conditional_exits(node_id_list, edges, named_exits)
    for scc_nodes, has_cond_exit in cycles:
        if not has_cond_exit:
            diagnostics.append(
                Diagnostic(
                    code="publish.loop_without_conditional_exit",
                    path="/edges",
                    message=f"Loop without conditional exit detected in nodes: {scc_nodes}",
                    severity="error",
                )
            )

    has_errors = any(d.severity == "error" for d in diagnostics)
    if has_errors:
        return False, diagnostics, {}

    dependency_manifest = {
        "models": sorted(manifests["models"]),
        "tools": sorted(manifests["tools"]),
        "agents": sorted(manifests["agents"]),
        "code_tools": [json.loads(value) for value in sorted(manifests["code_tools"])],
        "mcp_servers": sorted(manifests["mcp_servers"]),
        "mcp_snapshots": sorted(manifests["mcp_snapshots"]),
    }
    return True, diagnostics, dependency_manifest
