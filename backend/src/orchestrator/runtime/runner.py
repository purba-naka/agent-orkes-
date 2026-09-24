from collections.abc import AsyncGenerator
from datetime import datetime, timezone
import logging
from typing import Any
import uuid
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.types import Command
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from orchestrator.config import settings
from orchestrator.db.models import Agent, AgentRevision, Conversation, Run, utcnow
from orchestrator.db.session import async_session_factory
from orchestrator.runtime.compiler import SingleNodeCompiler
from orchestrator.runtime.hitl import extract_interrupts, project_interrupts
from orchestrator.runtime.policy import sanitize_runtime_error
from orchestrator.runtime.sse import format_sse

logger = logging.getLogger(__name__)


def normalize_input(input_data: dict[str, Any], run_id: uuid.UUID) -> dict[str, Any]:
    if "messages" in input_data and isinstance(input_data["messages"], list):
        msgs = input_data["messages"]
    elif "prompt" in input_data:
        msgs = [{"role": "user", "content": str(input_data["prompt"])}]
    elif "input" in input_data and isinstance(input_data["input"], str):
        msgs = [{"role": "user", "content": input_data["input"]}]
    else:
        msgs = [{"role": "user", "content": str(input_data) if input_data else "Hello"}]
    return {
        "input": input_data,
        "messages": msgs,
        "outputs": {},
        "result_name": None,
        "run_id": str(run_id),
        "usage": {},
    }


async def run_agent_stream(
    agent: Agent,
    revision: AgentRevision,
    input_data: dict[str, Any],
) -> AsyncGenerator[str, None]:
    run_id = uuid.uuid4()
    thread_id = run_id

    # 1. Create Run in DB
    async with async_session_factory() as session:
        run = Run(
            id=run_id,
            agent_revision_id=revision.id,
            conversation_id=None,
            thread_id=thread_id,
            mode="orchestration",
            status="running",
            usage={},
            started_at=utcnow(),
        )
        session.add(run)
        await session.commit()

    # 2. Emit opening event
    yield format_sse(
        "open",
        {
            "run_id": str(run_id),
            "thread_id": str(thread_id),
            "revision_id": str(revision.id),
        },
    )

    final_content = ""
    final_result_name = "success"
    final_outputs: dict[str, Any] = {}
    final_usage: dict[str, Any] = {}
    status = "completed"
    interrupted = False
    error_data: dict[str, Any] | None = None

    try:
        # 3. Connect to checkpointer & compile graph
        async with AsyncPostgresSaver.from_conn_string(settings.checkpointer_url) as checkpointer:
            await checkpointer.setup()
            async with async_session_factory() as session:
                graph = await SingleNodeCompiler.compile(
                    session=session,
                    revision=revision,
                    checkpointer=checkpointer,
                )

            initial_state = normalize_input(input_data, run_id)
            rec_limit = revision.document.get("recursion_limit", 25)
            config = {
                "configurable": {"thread_id": str(thread_id)},
                "recursion_limit": rec_limit,
            }

            # 4. Stream native chunks
            async for chunk in graph.astream(
                initial_state,
                stream_mode=["messages", "updates"],
                subgraphs=True,
                config=config,
            ):
                ns, mode, payload = chunk

                # Extract text if available for the final result
                if mode == "messages":
                    msg = payload[0] if isinstance(payload, tuple) and payload else payload
                    if isinstance(msg, AIMessage) and msg.content:
                        final_content = str(msg.content)
                elif mode == "updates" and isinstance(payload, dict):
                    native_interrupts = extract_interrupts(payload)
                    if native_interrupts:
                        interrupted = True
                        status = "interrupted"
                        async with async_session_factory() as projection_session:
                            await project_interrupts(
                                projection_session, run_id, ns, native_interrupts
                            )
                            await projection_session.commit()
                    for node_name, node_data in payload.items():
                        if isinstance(node_data, dict):
                            if "result_name" in node_data and node_data["result_name"]:
                                final_result_name = node_data["result_name"]
                            if "outputs" in node_data and isinstance(node_data["outputs"], dict):
                                for k, v in node_data["outputs"].items():
                                    if not k.startswith("__"):
                                        final_outputs[k] = v
                            if "usage" in node_data and isinstance(node_data["usage"], dict):
                                final_usage.update(node_data["usage"])
                            if "messages" in node_data and node_name == "model":
                                msgs = node_data["messages"]
                                if msgs and isinstance(msgs[-1], BaseMessage):
                                    final_content = str(msgs[-1].content)

                yield format_sse(
                    "native",
                    {
                        "namespace": list(ns),
                        "mode": mode,
                        "chunk": payload,
                    },
                )

        output_payload: dict[str, Any] = {"content": final_content}
        if final_outputs:
            output_payload["outputs"] = final_outputs

        # 5. Emit terminal or interrupted status.
        yield format_sse(
            "close",
            {
                "status": status,
                "result_name": final_result_name if not interrupted else None,
                "output": output_payload if not interrupted else None,
                "usage": final_usage,
            },
        )

    except Exception as exc:
        logger.exception("Run %s failed with exception: %s", run_id, exc)
        status = "failed"
        error_data = sanitize_runtime_error(exc)
        yield format_sse("error", error_data)
        yield format_sse(
            "close",
            {
                "status": "failed",
                "result_name": None,
                "output": None,
                "usage": {},
            },
        )

    # 6. Update Run row in DB
    async with async_session_factory() as session:
        db_run = await session.get(Run, run_id)
        if db_run:
            db_run.status = status
            db_run.finished_at = utcnow() if status != "interrupted" else None
            if status == "completed":
                output_payload = {"content": final_content}
                if final_outputs:
                    output_payload["outputs"] = final_outputs
                db_run.result_name = final_result_name
                db_run.result = output_payload
                db_run.usage = final_usage
            elif status == "failed":
                db_run.sanitized_error = error_data
            await session.commit()


def extract_user_text(input_data: dict[str, Any]) -> str:
    raw_content = input_data.get("content")
    raw_text = input_data.get("text")
    if raw_text:
        return str(raw_text)
    if isinstance(raw_content, str):
        return raw_content
    if isinstance(raw_content, list):
        parts = [
            p.get("text", "")
            for p in raw_content
            if isinstance(p, dict) and p.get("type") == "text"
        ]
        return " ".join(parts) if parts else str(raw_content)
    if "prompt" in input_data:
        return str(input_data["prompt"])
    if "input" in input_data:
        return str(input_data["input"])
    return "Hello"


async def run_conversation_stream(
    conversation_id: uuid.UUID,
    input_data: dict[str, Any],
) -> AsyncGenerator[str, None]:
    async with async_session_factory() as session:
        conv = await session.get(
            Conversation,
            conversation_id,
            options=[
                selectinload(Conversation.agent_revision).selectinload(AgentRevision.agent)
            ],
        )
        if not conv:
            raise ValueError(f"Conversation {conversation_id} not found")
        revision = conv.agent_revision
        if not revision:
            raise ValueError(f"Agent revision {conv.agent_revision_id} not found")

    run_id = uuid.uuid4()
    thread_id = conversation_id

    # 1. Create Run in DB
    async with async_session_factory() as session:
        run = Run(
            id=run_id,
            agent_revision_id=revision.id,
            conversation_id=conversation_id,
            thread_id=thread_id,
            mode="conversation",
            status="running",
            usage={},
            started_at=utcnow(),
        )
        session.add(run)
        await session.commit()

    # 2. Emit opening event
    yield format_sse(
        "open",
        {
            "run_id": str(run_id),
            "thread_id": str(thread_id),
            "conversation_id": str(conversation_id),
            "revision_id": str(revision.id),
        },
    )

    final_content = ""
    status = "completed"
    interrupted = False
    error_data: dict[str, Any] | None = None

    try:
        user_text = extract_user_text(input_data)
        human_msg = HumanMessage(content=user_text, id=str(uuid.uuid4()))

        # 3. Connect to checkpointer & compile graph
        async with AsyncPostgresSaver.from_conn_string(settings.checkpointer_url) as checkpointer:
            await checkpointer.setup()
            async with async_session_factory() as session:
                graph = await SingleNodeCompiler.compile(
                    session=session,
                    revision=revision,
                    checkpointer=checkpointer,
                )

            initial_state = {
                "input": input_data,
                "messages": [human_msg],
                "outputs": {},
                "result_name": None,
                "run_id": str(run_id),
                "usage": {},
            }
            rec_limit = revision.document.get("recursion_limit", 25)
            config = {
                "configurable": {"thread_id": str(thread_id)},
                "recursion_limit": rec_limit,
            }

            # 4. Stream native chunks
            async for chunk in graph.astream(
                initial_state,
                stream_mode=["messages", "updates"],
                subgraphs=True,
                config=config,
            ):
                ns, mode, payload = chunk

                if mode == "messages":
                    msg = payload[0] if isinstance(payload, tuple) and payload else payload
                    if isinstance(msg, AIMessage) and msg.content:
                        final_content = str(msg.content)
                elif mode == "updates" and isinstance(payload, dict):
                    native_interrupts = extract_interrupts(payload)
                    if native_interrupts:
                        interrupted = True
                        status = "interrupted"
                        async with async_session_factory() as projection_session:
                            await project_interrupts(
                                projection_session, run_id, ns, native_interrupts
                            )
                            await projection_session.commit()
                    model_node = payload.get("model")
                    if isinstance(model_node, dict) and "messages" in model_node:
                        msgs = model_node["messages"]
                        if msgs and isinstance(msgs[-1], BaseMessage):
                            final_content = str(msgs[-1].content)

                yield format_sse(
                    "native",
                    {
                        "namespace": list(ns),
                        "mode": mode,
                        "chunk": payload,
                    },
                )

        # 5. Project messages from checkpoint only after a completed turn.
        try:
            from orchestrator.domain.conversations import ConversationService

            async with async_session_factory() as session:
                if not interrupted:
                    await ConversationService.project_messages_from_checkpoint(
                        session=session,
                        conversation_id=conversation_id,
                        run_id=run_id,
                    )
        except Exception as proj_err:
            logger.error(
                "Projection write failed for conversation %s run %s: %s",
                conversation_id,
                run_id,
                proj_err,
            )
            status = "failed"
            error_data = {
                "code": "projection_failed",
                "message": "Projection write failed",
            }
            yield format_sse("error", error_data)

        # 6. Emit close event on completion
        yield format_sse(
            "close",
            {
                "status": status,
                "result_name": "success" if status == "completed" else None,
                "output": {"content": final_content} if status == "completed" else None,
                "usage": {},
            },
        )

    except Exception as exc:
        logger.exception("Conversation Run %s failed with exception: %s", run_id, exc)
        status = "failed"
        error_data = sanitize_runtime_error(exc)
        yield format_sse("error", error_data)
        yield format_sse(
            "close",
            {
                "status": "failed",
                "result_name": None,
                "output": None,
                "usage": {},
            },
        )

    # 7. Update Run and Conversation rows in DB
    async with async_session_factory() as session:
        db_run = await session.get(Run, run_id)
        if db_run:
            db_run.status = status
            db_run.finished_at = utcnow() if status != "interrupted" else None
            if status == "completed":
                db_run.result_name = "success"
                db_run.result = {"content": final_content}
            elif status == "failed":
                db_run.sanitized_error = error_data

        db_conv = await session.get(Conversation, conversation_id)
        if db_conv:
            db_conv.updated_at = utcnow()

        await session.commit()
