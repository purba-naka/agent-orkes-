from __future__ import annotations

import asyncio
from typing import Annotated, Any, Sequence, TypedDict

import pytest
from langchain.agents import create_agent
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AnyMessage, BaseMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph, add_messages
from langgraph.types import Command, RetryPolicy, interrupt


class ScriptedTestChatModel(BaseChatModel):
    scripted_responses: list[AIMessage] = []
    index: int = 0
    behavior: str = "default"

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        if self.behavior == "memory_echo" or not self.scripted_responses:
            last_user = next((m for m in reversed(messages) if m.type in ("human", "user")), None)
            last_text = str(last_user.content) if last_user else ""
            if "favorite color" in last_text.lower():
                found_color = "navy blue" if any("navy blue" in str(m.content).lower() for m in messages) else "unknown"
                msg = AIMessage(content=f"Your favorite color is {found_color}.")
            else:
                msg = AIMessage(content=f"Noted: {last_text}")
            return ChatResult(generations=[ChatGeneration(message=msg)])

        if self.index < len(self.scripted_responses):
            msg = self.scripted_responses[self.index]
            self.index += 1
            return ChatResult(generations=[ChatGeneration(message=msg)])

        # Fallback when scripted responses run out
        last_user = next((m for m in reversed(messages) if m.type in ("human", "user")), None)
        last_text = str(last_user.content) if last_user else ""
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=f"Noted: {last_text}"))])

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        return self._generate(messages, stop=stop, run_manager=run_manager, **kwargs)

    def bind_tools(
        self,
        tools: Sequence[Any],
        *,
        tool_choice: str | None = None,
        **kwargs: Any,
    ) -> ScriptedTestChatModel:
        return self

    @property
    def _llm_type(self) -> str:
        return "scripted-test"


class UniversalState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
    outputs: dict[str, Any]
    result_name: str | None
    run_id: str


@pytest.mark.asyncio
async def test_create_agent_with_custom_test_model_and_tool_execution() -> None:
    @tool
    def ping(text: str) -> str:
        """Return pong response."""
        return f"pong:{text}"

    model = ScriptedTestChatModel(
        scripted_responses=[
            AIMessage(
                content="",
                tool_calls=[{"name": "ping", "args": {"text": "hello"}, "id": "call-1"}],
            ),
            AIMessage(content="final answer from agent"),
        ]
    )

    checkpointer = InMemorySaver()
    agent = create_agent(
        model=model,
        tools=[ping],
        system_prompt="You are a test agent.",
        checkpointer=checkpointer,
    )

    config = {"configurable": {"thread_id": "test-thread-1"}}
    result = await agent.ainvoke(
        {"messages": [{"role": "user", "content": "run ping"}]},
        config=config,
    )

    messages = result["messages"]
    tool_messages = [m for m in messages if isinstance(m, ToolMessage)]
    assert len(tool_messages) == 1
    assert tool_messages[0].content == "pong:hello"
    assert messages[-1].content == "final answer from agent"


@pytest.mark.asyncio
async def test_subgraph_interrupt_and_resume_with_command() -> None:
    def inner_node(state: dict[str, Any]) -> dict[str, Any]:
        decision = interrupt({"review": "approve_needed", "payload": state["value"]})
        return {"value": f"approved:{decision}"}

    inner_builder = StateGraph(dict)
    inner_builder.add_node("review_step", inner_node)
    inner_builder.add_edge(START, "review_step")
    inner_builder.add_edge("review_step", END)
    inner_subgraph = inner_builder.compile()

    def parent_node(state: dict[str, Any]) -> dict[str, Any]:
        sub_result = inner_subgraph.invoke({"value": state["input_val"]})
        return {"output_val": sub_result["value"]}

    parent_builder = StateGraph(dict)
    parent_builder.add_node("sub_agent", parent_node)
    parent_builder.add_edge(START, "sub_agent")
    parent_builder.add_edge("sub_agent", END)

    checkpointer = InMemorySaver()
    parent_graph = parent_builder.compile(checkpointer=checkpointer)

    thread_config = {"configurable": {"thread_id": "parent-thread-1"}}

    first_run = await parent_graph.ainvoke({"input_val": "initial"}, config=thread_config)
    assert "__interrupt__" in first_run
    interrupt_payload = first_run["__interrupt__"][0].value
    assert interrupt_payload["review"] == "approve_needed"
    assert interrupt_payload["payload"] == "initial"

    resumed_run = await parent_graph.ainvoke(
        Command(resume="user_ok"),
        config=thread_config,
    )
    assert resumed_run["output_val"] == "approved:user_ok"


@pytest.mark.asyncio
async def test_upstream_node_is_not_rerun_on_retry_or_resume() -> None:
    upstream_runs: list[str] = []
    downstream_runs: list[str] = []

    def upstream_node(state: dict[str, Any]) -> dict[str, Any]:
        upstream_runs.append("ran_upstream")
        return {"step1": "done"}

    def flaky_downstream(state: dict[str, Any]) -> dict[str, Any]:
        downstream_runs.append("attempt_downstream")
        if len(downstream_runs) < 2:
            raise ConnectionError("temporary failure")
        return {"step2": f"{state.get('step1')}_and_step2_done"}

    builder = StateGraph(dict)
    builder.add_node("upstream", upstream_node)
    builder.add_node(
        "downstream",
        flaky_downstream,
        retry_policy=RetryPolicy(
            max_attempts=3,
            initial_interval=0.01,
            jitter=False,
            retry_on=(ConnectionError,),
        ),
    )
    builder.add_edge(START, "upstream")
    builder.add_edge("upstream", "downstream")
    builder.add_edge("downstream", END)

    checkpointer = InMemorySaver()
    graph = builder.compile(checkpointer=checkpointer)

    res = await graph.ainvoke({"init": True}, config={"configurable": {"thread_id": "superstep-thread"}})
    assert res["step2"] == "done_and_step2_done"
    assert len(upstream_runs) == 1, "Upstream node must not re-run when downstream node retries"
    assert len(downstream_runs) == 2


@pytest.mark.asyncio
async def test_native_streaming_with_subgraphs_flag() -> None:
    def sub_node(state: dict[str, Any]) -> dict[str, Any]:
        return {"sub_val": "from_sub"}

    sub_builder = StateGraph(dict)
    sub_builder.add_node("worker", sub_node)
    sub_builder.add_edge(START, "worker")
    sub_builder.add_edge("worker", END)
    sub = sub_builder.compile()

    def parent_node(state: dict[str, Any]) -> dict[str, Any]:
        res = sub.invoke({})
        return {"parent_val": res["sub_val"]}

    parent_builder = StateGraph(dict)
    parent_builder.add_node("parent_worker", parent_node)
    parent_builder.add_edge(START, "parent_worker")
    parent_builder.add_edge("parent_worker", END)

    graph = parent_builder.compile()

    namespaces: list[tuple[str, ...]] = []
    modes: list[str] = []

    async for chunk in graph.astream(
        {"parent_val": "start"},
        stream_mode=["updates"],
        subgraphs=True,
    ):
        ns, mode, payload = chunk
        namespaces.append(ns)
        modes.append(mode)

    assert any(ns == () for ns in namespaces)
    assert any(m == "updates" for m in modes)
