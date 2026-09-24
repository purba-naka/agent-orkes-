"""Projected messages must keep what the chat UI renders as agent steps."""

from langchain_core.messages import AIMessage, ToolMessage

from orchestrator.domain.conversations import parse_checkpoint_message


def test_ai_message_keeps_reasoning_and_tool_calls() -> None:
    msg = AIMessage(
        id="a1",
        content="Sebentar.",
        additional_kwargs={"reasoning_content": "list pages first"},
        tool_calls=[{"id": "c1", "name": "notion_notion-search", "args": {"query": "x"}}],
    )
    _, role, content = parse_checkpoint_message(msg)
    assert role == "assistant"
    assert content == [
        {"type": "reasoning", "reasoning": "list pages first"},
        {"type": "text", "text": "Sebentar."},
        {"type": "tool_call", "id": "c1", "name": "notion_notion-search", "args": {"query": "x"}},
    ]


def test_tool_message_links_to_its_call() -> None:
    msg = ToolMessage(id="t1", content='{"results": []}', tool_call_id="c1", name="notion_notion-search")
    _, role, content = parse_checkpoint_message(msg)
    assert role == "tool"
    assert content == [
        {
            "type": "tool_result",
            "tool_call_id": "c1",
            "name": "notion_notion-search",
            "status": "success",
            "content": '{"results": []}',
        }
    ]


def test_empty_ai_text_is_dropped() -> None:
    _, _, content = parse_checkpoint_message(AIMessage(id="a2", content=""))
    assert content == []
