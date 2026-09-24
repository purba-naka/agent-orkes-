import json
from datetime import datetime
from typing import Any
import uuid
from langchain_core.messages import BaseMessage
from langgraph.types import Interrupt


def serialize_native(obj: Any) -> Any:
    """Convert LangChain messages, chunks, UUIDs, datetimes to JSON-safe primitives."""
    if isinstance(obj, Interrupt):
        return {"id": obj.id, "value": serialize_native(obj.value)}
    if isinstance(obj, BaseMessage):
        return {
            "type": getattr(obj, "type", "message"),
            "content": serialize_native(obj.content),
            "name": getattr(obj, "name", None),
            "tool_calls": serialize_native(getattr(obj, "tool_calls", [])),
            "id": getattr(obj, "id", None),
            "response_metadata": serialize_native(getattr(obj, "response_metadata", {})),
        }
    if isinstance(obj, (uuid.UUID,)):
        return str(obj)
    if isinstance(obj, (datetime,)):
        return obj.isoformat()
    if hasattr(obj, "model_dump") and callable(obj.model_dump):
        return serialize_native(obj.model_dump())
    if hasattr(obj, "to_dict") and callable(obj.to_dict):
        return serialize_native(obj.to_dict())
    if isinstance(obj, dict):
        return {str(k): serialize_native(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [serialize_native(i) for i in obj]
    if isinstance(obj, (str, int, float, bool, type(None))):
        return obj
    return str(obj)


def format_sse(event: str, data: dict[str, Any]) -> str:
    """Format an SSE message with UTF-8 JSON data."""
    serialized = serialize_native(data)
    json_str = json.dumps(serialized, ensure_ascii=False)
    return f"event: {event}\ndata: {json_str}\n\n"
