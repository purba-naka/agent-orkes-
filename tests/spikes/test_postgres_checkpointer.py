from __future__ import annotations

from typing import Any
import os
import uuid

import pytest
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

DB_URI = os.environ["CHECKPOINTER_URL"]


@pytest.mark.asyncio
async def test_postgres_checkpointer_setup_and_cross_process_resume() -> None:
    # 1. Run setup once
    async with AsyncPostgresSaver.from_conn_string(DB_URI) as initial_saver:
        await initial_saver.setup()

    thread_id = f"cross-process-{uuid.uuid4()}"
    config = {"configurable": {"thread_id": thread_id}}

    def make_graph(checkpointer: AsyncPostgresSaver) -> Any:
        def step_one(state: dict[str, Any]) -> dict[str, Any]:
            decision = interrupt({"action": "human_approval", "val": state["num"]})
            return {"num": state["num"] * 10, "decision": decision}

        def step_two(state: dict[str, Any]) -> dict[str, Any]:
            return {"final_val": state["num"] + 5, "decision": state["decision"]}

        builder = StateGraph(dict)
        builder.add_node("step_one", step_one)
        builder.add_node("step_two", step_two)
        builder.add_edge(START, "step_one")
        builder.add_edge("step_one", "step_two")
        builder.add_edge("step_two", END)
        return builder.compile(checkpointer=checkpointer)

    # 2. Process A: Run until interrupt, then close connection
    async with AsyncPostgresSaver.from_conn_string(DB_URI) as saver_proc_a:
        graph_a = make_graph(saver_proc_a)
        res_a = await graph_a.ainvoke({"num": 7}, config=config)
        assert "__interrupt__" in res_a
        assert res_a["__interrupt__"][0].value == {
            "action": "human_approval",
            "val": 7,
        }

    # 3. Process B: Completely new connection and checkpointer instance
    async with AsyncPostgresSaver.from_conn_string(DB_URI) as saver_proc_b:
        graph_b = make_graph(saver_proc_b)
        res_b = await graph_b.ainvoke(Command(resume="approved_by_boss"), config=config)
        assert res_b["decision"] == "approved_by_boss"
        assert res_b["final_val"] == 75
