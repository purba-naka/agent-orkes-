import asyncio
import sys
import traceback
import uuid

sys.path.insert(0, "backend/src")

from sqlalchemy import select

from orchestrator.db.session import async_session_factory
from orchestrator.db.models import Agent, AgentRevision
from orchestrator.runtime.runner import run_agent_stream

AGENT_ID = uuid.UUID(sys.argv[1])
PROMPT = sys.argv[2]


async def main() -> None:
    async with async_session_factory() as session:
        agent = (
            await session.execute(select(Agent).where(Agent.id == AGENT_ID))
        ).scalar_one()
        revision = (
            await session.execute(
                select(AgentRevision).where(
                    AgentRevision.id == agent.active_revision_id
                )
            )
        ).scalar_one()

        try:
            async for event in run_agent_stream(
                agent=agent,
                revision=revision,
                input_data={"prompt": PROMPT},
            ):
                print(repr(event)[:400])
        except Exception:
            traceback.print_exc()


asyncio.run(main())
