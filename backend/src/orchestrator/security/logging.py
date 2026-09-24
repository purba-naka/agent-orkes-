import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any


class SafeJsonFormatter(logging.Formatter):
    """Structured JSON formatter that redacts sensitive payload keys."""

    REDACTED_KEYS = {
        "password",
        "secret",
        "token",
        "api_key",
        "ciphertext",
        "prompt",
        "messages",
        "payload",
    }

    def format(self, record: logging.LogRecord) -> str:
        log_entry: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Include standard extra attributes if attached
        for attr in ("run_id", "thread_id", "node_id", "status", "error_code"):
            if hasattr(record, attr):
                log_entry[attr] = getattr(record, attr)

        if record.exc_info and record.exc_text:
            log_entry["exception"] = record.exc_text

        return json.dumps(log_entry, default=str)


def setup_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(SafeJsonFormatter())

    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    root_logger.handlers = [handler]
