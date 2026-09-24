import json
from typing import Any
import uuid
from langchain.agents import create_agent
from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.func import task
from langgraph.graph import END, START, StateGraph
from langgraph.types import RetryPolicy, interrupt
from langgraph.graph.state import CompiledStateGraph
from sqlalchemy.ext.asyncio import AsyncSession

from orchestrator.config import settings
from orchestrator.db.models import AgentRevision, ModelRevision, ToolRevision
from orchestrator.db.session import async_session_factory
from orchestrator.domain.catalog import CatalogService
from orchestrator.runtime.policy import (
    build_agent_middleware,
    build_tool_approval_interrupts,
    resolve_agent_tools,
    resolve_mcp_bound_tools,
)
from orchestrator.runtime.state import RuntimeState
from orchestrator.tools.adapters import ToolInvoker, ToolInvocationError
from orchestrator.tools.network import NetworkPolicy


def evaluate_mechanical_condition(actual: Any, operator: str, expected: Any) -> bool:
    if operator == "is_null":
        return actual is None
    if operator == "is_not_null":
        return actual is not None
    if operator == "eq":
        return actual == expected
    if operator == "ne":
        return actual != expected
    if operator == "lt":
        return actual < expected
    if operator == "lte":
        return actual <= expected
    if operator == "gt":
        return actual > expected
    if operator == "gte":
        return actual >= expected
    if operator == "in":
        return actual in expected if expected is not None else False
    if operator == "not_in":
        return actual not in expected if expected is not None else True
    return False


async def build_agent_harness(
    session: AsyncSession,
    doc: dict[str, Any],
    agent_cfg: dict[str, Any],
    *,
    checkpointer: BaseCheckpointSaver | None = None,
) -> CompiledStateGraph:
    model_rev_id = uuid.UUID(str(agent_cfg["model_revision_id"]))
    model_revision = await session.get(ModelRevision, model_rev_id)
    if not model_revision or not model_revision.is_enabled:
        raise ValueError(f"Model revision {model_rev_id} is disabled or not found")
    chat_model = await CatalogService.resolve_chat_model(session, model_rev_id)
    system_prompt = agent_cfg.get("system_prompt") or doc.get("system_prompt") or ""
    context_policy = {
        **doc.get("context_policy", {}),
        **agent_cfg.get("context_policy", {}),
    }
    middleware_policy = {
        **doc.get("middleware_policy", {}),
        **agent_cfg.get("middleware_policy", {}),
    }
    tools, tool_revisions = await resolve_agent_tools(
        session, agent_cfg.get("tool_revision_ids", [])
    )
    bound_tools, bound_mcp_info = await resolve_mcp_bound_tools(
        session,
        agent_cfg.get("mcp_bindings", []),
        reserved_names={tool.name for tool in tools},
    )
    tools = [*tools, *bound_tools]
    interrupt_on = build_tool_approval_interrupts(
        tool_revisions, bound_mcp_info, middleware_policy
    )
    middleware = build_agent_middleware(
        system_prompt=system_prompt,
        context_policy=context_policy,
        middleware_policy=middleware_policy,
        model=chat_model,
        model_revision=model_revision,
        tool_approval=interrupt_on,
    )
    return create_agent(
        model=chat_model,
        tools=tools,
        system_prompt=system_prompt,
        middleware=middleware,
        state_schema=RuntimeState,
        checkpointer=checkpointer,
    )


def make_agent_node_runner(node_id: str, harness: CompiledStateGraph):
    async def agent_node(
        state: RuntimeState, config: RunnableConfig | None = None
    ) -> dict[str, Any]:
        msgs = state.get("messages", [])
        if not msgs:
            msgs = [HumanMessage(content="Start")]
        harness_state = dict(state)
        harness_state["messages"] = msgs
        res = await harness.ainvoke(harness_state, config=config)
        agent_msgs = res.get("messages", [])
        new_msgs = (
            agent_msgs[len(msgs):]
            if len(agent_msgs) > len(msgs)
            else (agent_msgs[-1:] if agent_msgs else [])
        )
        last_msg = agent_msgs[-1] if agent_msgs else None
        raw_content = last_msg.content if last_msg else ""
        output_data: Any = raw_content
        if isinstance(raw_content, str):
            trimmed = raw_content.strip()
            if (trimmed.startswith("{") and trimmed.endswith("}")) or (
                trimmed.startswith("[") and trimmed.endswith("]")
            ):
                try:
                    output_data = json.loads(trimmed)
                except Exception:
                    output_data = raw_content

        if not isinstance(output_data, dict):
            output_dict = {"output": output_data, "content": output_data}
        else:
            output_dict = dict(output_data)

        step = (config or {}).get("metadata", {}).get("langgraph_step", 1)
        node_output = {
            node_id: output_dict,
            "__step__": step,
            "__node__": node_id,
        }
        ret: dict[str, Any] = {
            "outputs": node_output,
            "messages": new_msgs,
        }
        if last_msg and getattr(last_msg, "usage_metadata", None):
            ret["usage"] = last_msg.usage_metadata
        return ret

    return agent_node


def resolve_field_path(state: RuntimeState | dict[str, Any], path: list[str]) -> Any:
    current: Any = state
    if path and path[0] not in state and "outputs" in state:
        current = state.get("outputs", {})
    for part in path:
        if not isinstance(current, dict) or part not in current:
            raise ValueError(f"Field path {path!r} is not present in runtime state")
        current = current[part]
    return current


def make_referenced_agent_runner(
    node_id: str,
    child_graph: CompiledStateGraph,
    input_mapping: dict[str, list[str]],
    output_mapping: dict[str, list[str]],
    selected_result: str | None,
):
    async def referenced_agent_node(
        state: RuntimeState, config: RunnableConfig | None = None
    ) -> dict[str, Any]:
        child_input = {
            target: resolve_field_path(state, source_path)
            for target, source_path in input_mapping.items()
        }
        child_state: RuntimeState = {
            "input": child_input,
            "messages": [HumanMessage(content=json.dumps(child_input, sort_keys=True))],
            "outputs": {},
            "result_name": None,
            "run_id": state.get("run_id", ""),
            "usage": {},
        }
        child_result = await child_graph.ainvoke(child_state, config=config)
        actual_result = child_result.get("result_name") or "success"
        if selected_result and actual_result != selected_result:
            raise ValueError(
                f"Referenced node '{node_id}' returned named result "
                f"'{actual_result}', expected '{selected_result}'"
            )

        if output_mapping:
            projected = {
                target: resolve_field_path(child_result, source_path)
                for target, source_path in output_mapping.items()
            }
        else:
            outputs = child_result.get("outputs", {})
            projected = next(reversed(outputs.values()), {}) if outputs else {}
            if not isinstance(projected, dict):
                projected = {"output": projected}

        step = (config or {}).get("metadata", {}).get("langgraph_step", 1)
        result: dict[str, Any] = {
            "outputs": {
                node_id: projected,
                "__step__": step,
                "__node__": node_id,
            }
        }
        if child_result.get("usage"):
            result["usage"] = child_result["usage"]
        return result

    return referenced_agent_node


def _retryable_tool_error(exc: Exception) -> bool:
    return isinstance(exc, ToolInvocationError) and exc.code == "tool_network_error"


@task(name="invoke_tool")
async def invoke_tool_task(
    revision_id: str,
    input_data: dict[str, Any],
    idempotency_key: str,
) -> Any:
    async with async_session_factory() as session:
        revision = await session.get(ToolRevision, uuid.UUID(revision_id))
        if not revision or not revision.is_enabled:
            raise ToolInvocationError("tool_unavailable", "Tool revision is disabled or missing")
        invoker = ToolInvoker(
            session,
            network_policy=NetworkPolicy(set(settings.tool_local_allowlist)),
        )
        return await invoker.invoke(
            revision,
            input_data,
            idempotency_key=idempotency_key,
        )


def make_tool_node_runner(
    node_id: str,
    revision: ToolRevision,
    input_mapping: dict[str, list[str]],
):
    async def tool_node(
        state: RuntimeState, config: RunnableConfig | None = None
    ) -> dict[str, Any]:
        tool_input = {
            target: resolve_field_path(state, source_path)
            for target, source_path in input_mapping.items()
        }
        sequence = (config or {}).get("metadata", {}).get("langgraph_step", 1)
        idempotency_key = f"{state.get('run_id', '')}:{node_id}:{sequence}"
        future = invoke_tool_task(str(revision.id), tool_input, idempotency_key)
        output = await future
        return {
            "outputs": {
                node_id: output,
                "__step__": sequence,
                "__node__": node_id,
            }
        }

    return tool_node


def make_review_node(node_id: str, output_schema: dict[str, Any]):
    async def review_node(state: RuntimeState) -> dict[str, Any]:
        output = state.get("outputs", {}).get(node_id, {})
        decision = interrupt(
            {
                "kind": "node_review",
                "node_id": node_id,
                "output": output,
                "output_schema": output_schema,
                "allowed_decisions": ["accept", "revise", "abort"],
            }
        )
        action = decision.get("action") if isinstance(decision, dict) else None
        if action == "abort":
            return {"result_name": "__cancelled__"}
        if action == "revise":
            revised = decision.get("output", {})
            return {
                "outputs": {
                    node_id: revised,
                    "__node__": node_id,
                }
            }
        return {}

    return review_node


def make_review_router(next_target: str, cancelled_target: str = "__exit_cancelled"):
    def router(state: RuntimeState) -> str:
        if state.get("result_name") == "__cancelled__":
            return cancelled_target
        return next_target

    return router


def make_passthrough_node():
    async def passthrough_node(state: RuntimeState) -> dict[str, Any]:
        return {}

    return passthrough_node


def make_exit_node(result_name: str):
    async def exit_node(
        state: RuntimeState, config: RunnableConfig | None = None
    ) -> dict[str, Any]:
        return {"result_name": result_name}

    return exit_node


def make_semantic_router(
    source_node: str,
    field_name: str,
    path_map: dict[str, str],
    default_target: str | None,
):
    def router(state: RuntimeState) -> str:
        node_out = state.get("outputs", {}).get(source_node, {})
        if isinstance(node_out, dict):
            val = str(node_out.get(field_name, ""))
        else:
            val = str(node_out or "")
        if val in path_map:
            return path_map[val]
        if default_target:
            return default_target
        raise ValueError(
            f"No route for semantic value '{val}' from node '{source_node}.{field_name}'"
        )

    return router


def make_mechanical_router(
    source_node: str,
    field_name: str,
    operator: str,
    expected: Any,
    then_target: str,
    else_target: str,
):
    def router(state: RuntimeState) -> str:
        node_out = state.get("outputs", {}).get(source_node, {})
        if isinstance(node_out, dict):
            actual = node_out.get(field_name)
        else:
            actual = node_out
        if evaluate_mechanical_condition(actual, operator, expected):
            return then_target
        return else_target

    return router


class GraphCompiler:
    @classmethod
    async def compile(
        cls,
        session: AsyncSession,
        revision: AgentRevision,
        checkpointer: BaseCheckpointSaver | None = None,
        *,
        private: bool = False,
    ) -> CompiledStateGraph:
        doc = revision.document
        entry_node_id = doc.get("entry_node_id", "main")
        nodes = doc.get("nodes", [])
        edges = doc.get("edges", [])
        named_exits = set(doc.get("named_exits", ["success"]))

        # Fast path for a pure inline single-node agent with no edges.
        only_node = nodes[0] if len(nodes) == 1 else None
        if (
            only_node
            and not edges
            and only_node.get("kind") == "agent"
            and only_node.get("agent", {}).get("mode", "inline") == "inline"
            and not only_node.get("agent", {}).get("review_output", False)
            and not private
        ):
            entry_node = next((n for n in nodes if n.get("id") == entry_node_id), None)
            if not entry_node:
                raise ValueError(
                    f"Entry node '{entry_node_id}' not found in revision document"
                )

            agent_cfg = entry_node.get("agent", {})
            model_rev_id_str = agent_cfg.get("model_revision_id")
            if not model_rev_id_str:
                raise ValueError(
                    f"Node '{entry_node_id}' does not specify a model_revision_id"
                )

            return await build_agent_harness(
                session,
                doc,
                agent_cfg,
                checkpointer=checkpointer,
            )

        # Multi-node StateGraph
        builder = StateGraph(RuntimeState)

        # 1. Add exit nodes for all named exits
        builder.add_node("__exit_cancelled", make_exit_node("__cancelled__"))
        builder.add_edge("__exit_cancelled", END)
        reviewed_nodes: dict[str, str] = {}
        reviewed_continuations: dict[str, str] = {}
        for node in nodes:
            if node.get("kind") == "agent" and node.get("agent", {}).get("review_output"):
                node_id = str(node["id"])
                review_id = f"__review_{node_id}"
                continuation_id = f"__review_pass_{node_id}"
                reviewed_nodes[node_id] = review_id
                reviewed_continuations[node_id] = continuation_id
                output_schema = node.get("agent", {}).get("output_schema", {"type": "object"})
                builder.add_node(review_id, make_review_node(node_id, output_schema))
                builder.add_node(continuation_id, make_passthrough_node())
                builder.add_edge(node_id, review_id)
                builder.add_conditional_edges(
                    review_id,
                    make_review_router(continuation_id),
                    [continuation_id, "__exit_cancelled"],
                )
        for exit_name in named_exits:
            exit_node_id = f"__exit_{exit_name}"
            builder.add_node(exit_node_id, make_exit_node(exit_name))
            builder.add_edge(exit_node_id, END)

        # 2. Add all agent nodes. Referenced revisions are compiled first and
        # invoked through adapters so each child receives fresh private state.
        for node in nodes:
            n_id = node.get("id")
            kind = node.get("kind")
            if kind == "agent":
                agent_cfg = node.get("agent", {})
                mode = agent_cfg.get("mode", "inline")
                if mode == "inline":
                    m_rev_str = agent_cfg.get("model_revision_id")
                    if not m_rev_str:
                        raise ValueError(
                            f"Node '{n_id}' does not specify model_revision_id"
                        )
                    harness = await build_agent_harness(session, doc, agent_cfg)
                    builder.add_node(n_id, make_agent_node_runner(n_id, harness))
                elif mode == "ref":
                    ref_id = uuid.UUID(str(agent_cfg["agent_revision_id"]))
                    child_revision = await session.get(AgentRevision, ref_id)
                    if not child_revision:
                        raise ValueError(f"Referenced agent revision {ref_id} not found")
                    child_graph = await cls.compile(session, child_revision, private=True)
                    builder.add_node(
                        n_id,
                        make_referenced_agent_runner(
                            n_id,
                            child_graph,
                            agent_cfg.get("input_mapping", {}),
                            agent_cfg.get("output_mapping", {}),
                            agent_cfg.get("result_name"),
                        ),
                    )
            elif kind == "tool":
                tool_revision_id = uuid.UUID(str(node["tool_revision_id"]))
                tool_revision = await session.get(ToolRevision, tool_revision_id)
                if not tool_revision or not tool_revision.is_enabled:
                    raise ValueError(f"Tool revision {tool_revision_id} not found or disabled")
                retry_attempts = int(node.get("retry", {}).get("max_attempts", tool_revision.max_attempts))
                if tool_revision.is_mutating and not tool_revision.configuration.get(
                    "idempotency_header"
                ):
                    retry_attempts = 1
                builder.add_node(
                    n_id,
                    make_tool_node_runner(
                        n_id,
                        tool_revision,
                        node.get("input_mapping", {}),
                    ),
                    retry_policy=RetryPolicy(
                        max_attempts=max(1, retry_attempts),
                        retry_on=_retryable_tool_error,
                    ),
                )

        # 3. Add START edge to entry node
        builder.add_edge(START, entry_node_id)

        # 4. Add edges
        for edge in edges:
            e_kind = edge.get("kind")
            if e_kind == "direct":
                raw_source = str(edge["source"])
                source = reviewed_continuations.get(raw_source, raw_source)
                target = edge["target"]
                builder.add_edge(source, target)

            elif e_kind == "exit":
                r_name = edge.get("result_name", "success")
                raw_source = str(edge["source"])
                source = reviewed_continuations.get(raw_source, raw_source)
                target = f"__exit_{r_name}"
                builder.add_edge(source, target)

            elif e_kind == "join":
                sources = [reviewed_continuations.get(str(source), str(source)) for source in edge["sources"]]
                builder.add_edge(sources, edge["target"])

            elif e_kind == "semantic":
                raw_node, f_name = edge["source"]
                s_node = str(raw_node)
                graph_source = reviewed_continuations.get(s_node, s_node)
                routes = edge.get("routes", {})
                default = edge.get("default")
                routes_resolved = {
                    val: (f"__exit_{tgt}" if tgt in named_exits else tgt)
                    for val, tgt in routes.items()
                }
                def_tgt = (
                    (f"__exit_{default}" if default in named_exits else default)
                    if default
                    else None
                )
                router = make_semantic_router(s_node, f_name, routes_resolved, def_tgt)
                all_targets = set(routes_resolved.values())
                if def_tgt:
                    all_targets.add(def_tgt)
                semantic_paths = [str(target) for target in all_targets]
                builder.add_conditional_edges(graph_source, router, semantic_paths)

            elif e_kind == "mechanical":
                raw_node, f_name = edge["source"]
                s_node = str(raw_node)
                graph_source = reviewed_continuations.get(s_node, s_node)
                op = edge.get("operator", "eq")
                val = edge.get("value")
                t_then = (
                    f"__exit_{edge['then']}"
                    if edge["then"] in named_exits
                    else edge["then"]
                )
                t_else = (
                    f"__exit_{edge['else']}"
                    if edge["else"] in named_exits
                    else edge["else"]
                )
                mechanical_paths = [str(t_then), str(t_else)]
                router = make_mechanical_router(
                    s_node, f_name, op, val, t_then, t_else
                )
                builder.add_conditional_edges(graph_source, router, mechanical_paths)

        edge_sources = {
            source
            for edge in edges
            for source in (
                edge.get("sources", [])
                if edge.get("kind") == "join"
                else [edge.get("source", [None])[0] if isinstance(edge.get("source"), list) else edge.get("source")]
            )
        }
        for node_id, continuation_id in reviewed_continuations.items():
            if node_id not in edge_sources:
                builder.add_edge(continuation_id, "__exit_success")

        if private:
            return builder.compile()
        return builder.compile(checkpointer=checkpointer)


class SingleNodeCompiler:
    @staticmethod
    async def compile(
        session: AsyncSession,
        revision: AgentRevision,
        checkpointer: BaseCheckpointSaver | None = None,
    ) -> CompiledStateGraph:
        return await GraphCompiler.compile(session, revision, checkpointer)
