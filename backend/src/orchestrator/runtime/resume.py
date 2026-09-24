from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any
import uuid

from langchain_core.messages import AIMessage, BaseMessage
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.types import Command

from orchestrator.config import settings
from orchestrator.db.models import AgentRevision, Conversation, Run, utcnow
from orchestrator.db.session import async_session_factory
from orchestrator.domain.conversations import ConversationService
from orchestrator.runtime.compiler import SingleNodeCompiler
from orchestrator.runtime.hitl import extract_interrupts, project_interrupts
from orchestrator.runtime.policy import sanitize_runtime_error
from orchestrator.runtime.sse import format_sse


async def resume_run_stream(
    run_id: uuid.UUID,
    decision: dict[str, Any],
    *,
    revision: AgentRevision,
    thread_id: uuid.UUID,
    conversation_id: uuid.UUID | None,
    native_value: dict[str, Any],
) -> AsyncGenerator[str, None]:

    yield format_sse(
        "open",
        {
            "run_id": str(run_id),
            "thread_id": str(thread_id),
            "revision_id": str(revision.id),
            "resumed": True,
        },
    )

    final_content = ""
    final_outputs: dict[str, Any] = {}
    final_usage: dict[str, Any] = {}
    final_result_name = "success"
    status = "completed"
    error_data: dict[str, Any] | None = None

    try:
        async with AsyncPostgresSaver.from_conn_string(settings.checkpointer_url) as checkpointer:
            async with async_session_factory() as session:
                graph = await SingleNodeCompiler.compile(
                    session=session,
                    revision=revision,
                    checkpointer=checkpointer,
                )
            config = {
                "configurable": {"thread_id": str(thread_id)},
                "recursion_limit": revision.document.get("recursion_limit", 25),
            }
            command = Command(resume={decision["interrupt_id"]: native_value})
            async for namespace, mode, payload in graph.astream(
                command,
                stream_mode=["messages", "updates"],
                subgraphs=True,
                config=config,
            ):
                if mode == "messages":
                    message = payload[0] if isinstance(payload, tuple) and payload else payload
                    if isinstance(message, AIMessage) and message.content:
                        final_content = str(message.content)
                elif mode == "updates" and isinstance(payload, dict):
                    native_interrupts = extract_interrupts(payload)
                    if native_interrupts:
                        status = "interrupted"
                        async with async_session_factory() as projection_session:
                            await project_interrupts(
                                projection_session, run_id, namespace, native_interrupts
                            )
                            await projection_session.commit()
                    for node_name, node_data in payload.items():
                        if not isinstance(node_data, dict):
                            continue
                        if node_data.get("result_name"):
                            final_result_name = str(node_data["result_name"])
                        if isinstance(node_data.get("outputs"), dict):
                            final_outputs.update(
                                {
                                    key: value
                                    for key, value in node_data["outputs"].items()
                                    if not key.startswith("__")
                                }
                            )
                        if isinstance(node_data.get("usage"), dict):
                            final_usage.update(node_data["usage"])
                        if node_name == "model" and node_data.get("messages"):
                            messages = node_data["messages"]
                            if isinstance(messages[-1], BaseMessage):
                                final_content = str(messages[-1].content)

                yield format_sse(
                    "native",
                    {
                        "namespace": list(namespace),
                        "mode": mode,
                        "chunk": payload,
                    },
                )

        if final_result_name == "__cancelled__":
            status = "cancelled"
        if conversation_id and status in {"completed", "cancelled"}:
            async with async_session_factory() as session:
                await ConversationService.project_messages_from_checkpoint(
                    session=session,
                    conversation_id=conversation_id,
                    run_id=run_id,
                )

    except Exception as exc:
        status = "failed"
        error_data = sanitize_runtime_error(exc)
        yield format_sse("error", error_data)

    output: dict[str, Any] | None = None
    if status == "completed":
        output = {"content": final_content}
        if final_outputs:
            output["outputs"] = final_outputs

    async with async_session_factory() as session:
        db_run = await session.get(Run, run_id)
        if db_run:
            db_run.status = status
            db_run.finished_at = utcnow() if status != "interrupted" else None
            if status == "completed":
                db_run.result_name = final_result_name
                db_run.result = output
                db_run.usage = final_usage
            elif status == "failed":
                db_run.sanitized_error = error_data
            if conversation_id:
                conversation = await session.get(Conversation, conversation_id)
                if conversation:
                    conversation.updated_at = utcnow()
            await session.commit()

    yield format_sse(
        "close",
        {
            "status": status,
            "result_name": final_result_name if status == "completed" else None,
            "output": output,
            "usage": final_usage,
        },
    )
