"""Remove test seed data from the development database.

Integration tests used to run against the development database and left
hundreds of seed rows behind (agents, models, tools, credentials, runs, ...).
This script deletes every test artifact while preserving records that look
like genuine user data.

Keep-lists below are names that did not match any test pattern in
tests/integration/* at the time of writing. Adjust them if needed.

Usage (from the repo root):
    .venv/Scripts/python.exe scripts/cleanup_test_data.py
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend" / "src"))

from sqlalchemy import text  # noqa: E402

from orchestrator.db.session import async_session_factory  # noqa: E402

# Records that do NOT match test patterns and are preserved.
KEEP = {
    "models": ["cek-glm3", "local-fast-model"],
    "tools": ["system-health-checker", "public-http-probe"],
    "credentials": ["cosmos"],
    "mcp_connections": ["notion"],
}

# Every agent in the dev database is test seed (verified against
# tests/integration/* name patterns), so agents are deleted wholesale.
# (label, sql, keep-list table or None)
STATEMENTS = (
    # Runs and conversations: runs.agent_revision_id and runs.conversation_id
    # are both ON DELETE RESTRICT, so runs must go first.
    ("messages", "DELETE FROM messages", None),
    ("run_interrupts", "DELETE FROM run_interrupts", None),
    ("runs", "DELETE FROM runs", None),
    ("conversations", "DELETE FROM conversations", None),
    # Agents and their revisions/drafts/dependencies. agent_revisions and
    # tool_revisions carry immutability triggers (prevent_revision_mutation);
    # disable them for this admin cleanup and re-enable afterwards.
    ("agent_revision_dependencies", "DELETE FROM agent_revision_dependencies", None),
    ("disable agent_revisions trigger", "ALTER TABLE agent_revisions DISABLE TRIGGER trg_agent_revisions_immutable", None),
    ("agent_revisions", "DELETE FROM agent_revisions", None),
    ("enable agent_revisions trigger", "ALTER TABLE agent_revisions ENABLE TRIGGER trg_agent_revisions_immutable", None),
    ("agent_drafts", "DELETE FROM agent_drafts", None),
    ("agents", "DELETE FROM agents", None),
    # Knowledge bases block model_revision deletion (RESTRICT).
    ("knowledge_chunks", "DELETE FROM knowledge_chunks", None),
    ("knowledge_documents", "DELETE FROM knowledge_documents", None),
    ("knowledge_bases", "DELETE FROM knowledge_bases", None),
    # Models except the keep-list. model_revisions.api_key_id is RESTRICT,
    # so revisions must go before credentials.
    (
        "model_revisions (test)",
        "DELETE FROM model_revisions WHERE model_id IN "
        "(SELECT id FROM models WHERE name != ALL(:keep))",
        "models",
    ),
    ("models (test)", "DELETE FROM models WHERE name != ALL(:keep)", "models"),
    # tool_revisions is trigger-protected (immutable) and RESTRICTs tool
    # deletion, so: disable trigger -> delete seed revisions + tools -> re-enable.
    ("disable tool_revisions trigger", "ALTER TABLE tool_revisions DISABLE TRIGGER trg_tool_revisions_immutable", None),
    (
        "tool_revisions (test)",
        "DELETE FROM tool_revisions WHERE tool_id IN "
        "(SELECT id FROM tools WHERE name != ALL(:keep))",
        "tools",
    ),
    ("tools (test)", "DELETE FROM tools WHERE name != ALL(:keep)", "tools"),
    ("enable tool_revisions trigger", "ALTER TABLE tool_revisions ENABLE TRIGGER trg_tool_revisions_immutable", None),
    ("credentials (test)", "DELETE FROM credentials WHERE name != ALL(:keep)", "credentials"),
    (
        "mcp_tool_snapshots (test)",
        "DELETE FROM mcp_tool_snapshots WHERE connection_id IN "
        "(SELECT id FROM mcp_connections WHERE name != ALL(:keep))",
        "mcp_connections",
    ),
    ("mcp_connections (test)", "DELETE FROM mcp_connections WHERE name != ALL(:keep)", "mcp_connections"),
    # LangGraph checkpoints: all threads belonged to deleted runs/conversations.
    ("checkpoints", "DELETE FROM checkpoints", None),
    ("checkpoint_blobs", "DELETE FROM checkpoint_blobs", None),
    ("checkpoint_writes", "DELETE FROM checkpoint_writes", None),
)


async def main() -> None:
    async with async_session_factory() as session:
        for label, sql, keep_table in STATEMENTS:
            params = {"keep": KEEP[keep_table]} if keep_table else {}
            result = await session.execute(text(sql), params)
            print(f"{label:28s} deleted={result.rowcount}")
        await session.commit()

        for table in (
            "agents", "models", "tools", "knowledge_bases", "credentials",
            "mcp_connections", "conversations", "runs",
        ):
            count = (await session.execute(text(f"SELECT count(*) FROM {table}"))).scalar()
            print(f"remaining {table:18s} {count}")


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
