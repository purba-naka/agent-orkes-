"""Test bootstrap: redirect every test onto a dedicated ``*_test`` database.

Integration tests import ``orchestrator.main``, which builds its async engine
from ``settings.database_url`` at import time. Without this redirect they write
straight into the development database and leave seed rows behind.

pytest imports this conftest before any test module, so the redirect below
runs before the first ``orchestrator`` engine exists. The flow is:

1. Read the configured (dev) URLs from settings.
2. Derive test URLs (``<dev_db>_test``) unless ``TEST_DATABASE_URL`` /
   ``TEST_CHECKPOINTER_URL`` are set explicitly.
3. Refuse to run if the test target is the development database.
4. Mutate the settings singleton and env vars so every later import
   (engine, checkpointer, spike tests, alembic) sees the test URLs.
5. Create the test database if missing, run migrations, and truncate all
   application tables so every session starts from a clean slate.
"""

import asyncio
import os
import sys
from pathlib import Path

import pytest
from sqlalchemy.engine import make_url

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

REPO_ROOT = Path(__file__).resolve().parents[1]
TEST_DB_SUFFIX = "_test"

# Application tables in the public schema. TRUNCATE ... CASCADE handles FK
# ordering; checkpoint_* tables are managed by LangGraph and dropped separately.
APP_TABLES = (
    "messages",
    "run_interrupts",
    "runs",
    "conversations",
    "agent_revision_dependencies",
    "agent_revisions",
    "agent_drafts",
    "agents",
    "knowledge_chunks",
    "knowledge_documents",
    "knowledge_bases",
    "tool_revisions",
    "tools",
    "mcp_tool_snapshots",
    "mcp_connections",
    "model_revisions",
    "models",
    "credentials",
)


def _derive_test_url(url: str) -> str:
    parsed = make_url(url)
    # hide_password=False: str(URL) masks passwords as "***" since SQLAlchemy 2.0.
    return parsed.set(database=f"{parsed.database}{TEST_DB_SUFFIX}").render_as_string(
        hide_password=False
    )


def _redirect_to_test_database() -> str:
    from orchestrator.config import settings  # noqa: imported before any engine

    dev_db_url = settings.database_url
    dev_cp_url = settings.checkpointer_url

    test_db_url = os.environ.get("TEST_DATABASE_URL") or _derive_test_url(dev_db_url)
    test_cp_url = os.environ.get("TEST_CHECKPOINTER_URL") or _derive_test_url(dev_cp_url)

    test_db_name = make_url(test_db_url).database
    dev_db_name = make_url(dev_db_url).database
    if test_db_name == dev_db_name:
        raise RuntimeError(
            "Refusing to run tests against the development database "
            f"'{dev_db_name}'. Tests must target a '*{TEST_DB_SUFFIX}' database; "
            "set TEST_DATABASE_URL to override."
        )

    settings.database_url = test_db_url
    settings.checkpointer_url = test_cp_url
    # Env vars win over .env in pydantic-settings and are read directly by
    # spike tests and any alembic subprocess.
    os.environ["DATABASE_URL"] = test_db_url
    os.environ["CHECKPOINTER_URL"] = test_cp_url
    return test_db_url


def _sync_dsn(url: str) -> str:
    """Plain postgresql:// DSN usable by sync psycopg connections."""
    return (
        make_url(url)
        .set(drivername="postgresql")
        .render_as_string(hide_password=False)
    )


def _ensure_database_exists(test_db_url: str) -> None:
    import psycopg

    parsed = make_url(test_db_url)
    admin_dsn = parsed.set(drivername="postgresql", database="postgres").render_as_string(
        hide_password=False
    )
    with psycopg.connect(admin_dsn, autocommit=True) as conn:
        exists = conn.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", (parsed.database,)
        ).fetchone()
        if not exists:
            conn.execute(f'CREATE DATABASE "{parsed.database}"')


def _run_migrations() -> None:
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "backend" / "alembic"))
    command.upgrade(cfg, "head")


def _truncate(test_db_url: str) -> None:
    import psycopg

    with psycopg.connect(_sync_dsn(test_db_url), autocommit=True) as conn:
        app_tables = ", ".join(f'"{name}"' for name in APP_TABLES)
        conn.execute(f"TRUNCATE TABLE {app_tables} RESTART IDENTITY CASCADE")
        rows = conn.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name LIKE 'checkpoint%'"
        ).fetchall()
        for (table_name,) in rows:
            conn.execute(f'TRUNCATE TABLE "{table_name}" CASCADE')


_TEST_DB_URL = _redirect_to_test_database()
_ensure_database_exists(_TEST_DB_URL)
_run_migrations()
_truncate(_TEST_DB_URL)


@pytest.fixture(scope="session", autouse=True)
def _clean_test_database_after_session() -> None:
    """Session teardown: wipe test rows so the test database stays small."""
    yield
    _truncate(_TEST_DB_URL)
