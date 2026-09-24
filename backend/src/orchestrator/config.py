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

    # Async SQLAlchemy uses postgresql+psycopg
    database_url: str = (
        "postgresql+psycopg://orchestrator:orchestrator_dev_password@127.0.0.1:5433/orchestrator_dev"
    )

    # LangGraph PostgresSaver uses raw postgresql:// URI
    checkpointer_url: str = (
        "postgresql://orchestrator:orchestrator_dev_password@127.0.0.1:5433/orchestrator_dev"
    )

    # 32 bytes base64-encoded AES-256 master key for dev
    app_encryption_key: str = "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY="

    cors_origins: list[str] = [
        "http://127.0.0.1:5173",
        "http://localhost:5173",
    ]
    tool_local_allowlist: list[str] = []

    retrieval_embedding_dimensions: int = 64
    retrieval_max_document_bytes: int = 100_000
    retrieval_chunk_size: int = 1_000
    retrieval_chunk_overlap: int = 100
    retrieval_max_top_k: int = 10


settings = Settings()

# Dev defaults above are public (see docker-compose.yml). Refuse to boot with them
# outside development so prod never silently encrypts credentials with a repo key.
if settings.environment != "development":
    if settings.app_encryption_key == Settings.model_fields["app_encryption_key"].default:
        raise RuntimeError("APP_ENCRYPTION_KEY must be set outside development")
    if "orchestrator_dev_password" in settings.database_url:
        raise RuntimeError("DATABASE_URL must be set outside development")

# Ensure Windows uses SelectorEventLoop for psycopg compatibility
if sys.platform == "win32":
    import asyncio
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
