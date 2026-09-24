from __future__ import annotations

from types import SimpleNamespace
import json
import uuid

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, Interrupt

from orchestrator.runtime.compiler import (
    make_passthrough_node,
    make_review_node,
    make_review_router,
)
from orchestrator.runtime.hitl import (
    InterruptDecisionError,
    build_resume_value,
    extract_interrupts,
    interrupt_kind,
)
from orchestrator.runtime.sse import format_sse, serialize_native
from orchestrator.runtime.state import RuntimeState


def _revision(*, node: dict | None = None) -> SimpleNamespace:
    return SimpleNamespace(document={"nodes": [node] if node else []})


def _interrupt_row(kind: str, value: dict) -> SimpleNamespace:
    return SimpleNamespace(kind=kind, payload={"namespace": [], "value": value})


def test_interrupt_sse_serialization_is_json_safe() -> None:
    native = Interrupt(value={"kind": "node_review", "output": {"ok": True}}, id="int-1")

    assert serialize_native(native) == {
        "id": "int-1",
        "value": {"kind": "node_review", "output": {"ok": True}},
    }
    event = format_sse("native", {"chunk": {"__interrupt__": (native,)}})
    encoded = event.removeprefix("event: native\ndata: ").removesuffix("\n\n")
    assert json.loads(encoded)["chunk"]["__interrupt__"][0]["id"] == "int-1"


def test_extract_interrupts_ignores_non_native_values() -> None:
    native = Interrupt(value={"action_requests": []}, id="int-1")

    assert extract_interrupts({"__interrupt__": (native, {"id": "fake"})}) == [native]
    assert extract_interrupts({"other": native}) == []
    assert interrupt_kind(native.value) == "tool_approval"
    assert interrupt_kind({"kind": "node_review"}) == "node_review"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("action", "extra", "expected"),
    [
        ("approve", {}, {"decisions": [{"type": "approve"}]}),
        (
            "reject",
            {"reason": "No"},
            {"decisions": [{"type": "reject", "message": "No"}]},
        ),
    ],
)
async def test_tool_decisions_translate_to_native_values(
    action: str, extra: dict, expected: dict
) -> None:
    row = _interrupt_row(
        "tool_approval",
        {"action_requests": [{"name": "danger", "args": {"amount": 7}}]},
    )

    result = await build_resume_value(
        SimpleNamespace(), _revision(), row, {"action": action, **extra}
    )

    assert result == expected


@pytest.mark.asyncio
async def test_grouped_tool_approval_applies_to_every_native_action() -> None:
    row = _interrupt_row(
        "tool_approval",
        {
            "action_requests": [
                {"name": "first", "args": {}},
                {"name": "second", "args": {}},
            ]
        },
    )

    result = await build_resume_value(
        SimpleNamespace(), _revision(), row, {"action": "approve"}
    )

    assert result == {"decisions": [{"type": "approve"}, {"type": "approve"}]}


@pytest.mark.asyncio
async def test_grouped_tool_edit_is_rejected() -> None:
    row = _interrupt_row(
        "tool_approval",
        {"action_requests": [{"name": "first"}, {"name": "second"}]},
    )

    with pytest.raises(InterruptDecisionError, match="exactly one"):
        await build_resume_value(
            SimpleNamespace(),
            _revision(),
            row,
            {"action": "edit", "input": {}},
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("action", "extra", "expected"),
    [
        ("accept", {}, {"action": "accept"}),
        ("abort", {"reason": "stop"}, {"action": "abort", "reason": "stop"}),
        ("revise", {"output": {"answer": "fixed"}}, {"action": "revise", "output": {"answer": "fixed"}}),
    ],
)
async def test_node_review_decisions_validate_and_translate(
    action: str, extra: dict, expected: dict
) -> None:
    node = {
        "id": "writer",
        "agent": {
            "output_schema": {
                "type": "object",
                "properties": {"answer": {"type": "string"}},
                "required": ["answer"],
            }
        },
    }
    row = _interrupt_row("node_review", {"node_id": "writer"})

    result = await build_resume_value(
        SimpleNamespace(), _revision(node=node), row, {"action": action, **extra}
    )

    assert result == expected


@pytest.mark.asyncio
async def test_node_review_resumes_without_rerunning_producer() -> None:
    producer_calls: list[str] = []

    async def producer(state: RuntimeState) -> dict:
        producer_calls.append("called")
        return {"outputs": {"writer": {"answer": "draft"}, "__node__": "writer"}}

    builder = StateGraph(RuntimeState)
    builder.add_node("writer", producer)
    builder.add_node("review", make_review_node("writer", {"type": "object"}))
    builder.add_node("continue", make_passthrough_node())
    builder.add_node("cancelled", lambda state: {"result_name": "__cancelled__"})
    builder.add_edge(START, "writer")
    builder.add_edge("writer", "review")
    builder.add_conditional_edges(
        "review",
        make_review_router("continue", "cancelled"),
        ["continue", "cancelled"],
    )
    builder.add_edge("continue", END)
    builder.add_edge("cancelled", END)
    graph = builder.compile(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "review-thread"}}

    paused = await graph.ainvoke({"outputs": {}}, config=config)
    pending = paused["__interrupt__"][0]
    resumed = await graph.ainvoke(
        Command(resume={pending.id: {"action": "revise", "output": {"answer": "fixed"}}}),
        config=config,
    )

    assert producer_calls == ["called"]
    assert resumed["outputs"]["writer"] == {"answer": "fixed"}
    assert resumed.get("result_name") != "__cancelled__"


@pytest.mark.asyncio
async def test_node_review_abort_routes_to_cancelled_exit() -> None:
    builder = StateGraph(RuntimeState)
    builder.add_node(
        "writer",
        lambda state: {"outputs": {"writer": {"answer": "draft"}, "__node__": "writer"}},
    )
    builder.add_node("review", make_review_node("writer", {"type": "object"}))
    builder.add_node("continue", make_passthrough_node())
    builder.add_node("cancelled", lambda state: {"result_name": "__cancelled__"})
    builder.add_edge(START, "writer")
    builder.add_edge("writer", "review")
    builder.add_conditional_edges(
        "review",
        make_review_router("continue", "cancelled"),
        ["continue", "cancelled"],
    )
    builder.add_edge("continue", END)
    builder.add_edge("cancelled", END)
    graph = builder.compile(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "abort-thread"}}

    paused = await graph.ainvoke({"outputs": {}}, config=config)
    pending = paused["__interrupt__"][0]
    resumed = await graph.ainvoke(
        Command(resume={pending.id: {"action": "abort", "reason": "stop"}}),
        config=config,
    )

    assert resumed["result_name"] == "__cancelled__"


@pytest.mark.asyncio
async def test_invalid_revised_node_output_is_rejected() -> None:
    node = {
        "id": "writer",
        "agent": {
            "output_schema": {
                "type": "object",
                "properties": {"answer": {"type": "string"}},
                "required": ["answer"],
            }
        },
    }
    row = _interrupt_row("node_review", {"node_id": "writer"})

    with pytest.raises(InterruptDecisionError, match="pinned schema"):
        await build_resume_value(
            SimpleNamespace(),
            _revision(node=node),
            row,
            {"action": "revise", "output": {"answer": 42}},
        )
