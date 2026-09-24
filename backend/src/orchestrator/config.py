import base64
import os
import sys
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    host: str = "127.0.0.1"
    port: int = 8000
    environment: str = "development"
    log_level: str = "INFO"

    # Required. No defaults: secrets must never live in source.
    # Async SQLAlchemy uses postgresql+psycopg
    database_url: str

    # LangGraph PostgresSaver uses raw postgresql:// URI
    checkpointer_url: str

    # 32 bytes base64-encoded AES-256 master key
    app_encryption_key: str


    cors_origins: list[str] = [
        "http://127.0.0.1:5173",
        "http://localhost:5173",
    ]
    tool_local_allowlist: list[str] = []
    # Executables a stdio MCP connection may spawn. Empty = stdio transport
    # disabled: an exposed API must not allow arbitrary command execution.
    mcp_stdio_command_allowlist: list[str] = []

    retrieval_embedding_dimensions: int = 64
    retrieval_max_document_bytes: int = 100_000
    retrieval_chunk_size: int = 1_000
    retrieval_chunk_overlap: int = 100
    retrieval_max_top_k: int = 10


settings = Settings()

if len(base64.b64decode(settings.app_encryption_key)) != 32:
    raise RuntimeError("APP_ENCRYPTION_KEY must decode to exactly 32 bytes")

# Ensure Windows uses SelectorEventLoop for psycopg compatibility
if sys.platform == "win32":
    import asyncio
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
