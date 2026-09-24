from contextlib import asynccontextmanager
import logging
from collections.abc import AsyncGenerator

from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware

from orchestrator.api.agents import router as agents_router
from orchestrator.api.conversations import router as conversations_router
from orchestrator.api.credentials import router as credentials_router
from orchestrator.api.models import router as models_router
from orchestrator.api.knowledge import router as knowledge_router
from orchestrator.api.runs import router as runs_router
from orchestrator.api.tools import router as tools_router
from orchestrator.config import settings
from orchestrator.db.session import check_db_health, engine
from orchestrator.security.logging import setup_logging

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    setup_logging(settings.log_level)
    logger.info("Starting Agent Orchestrator backend...")
    db_ok = await check_db_health()
    if db_ok:
        logger.info("Database connection established successfully.")
    else:
        logger.error("Initial database connection failed!")
    yield
    logger.info("Shutting down Agent Orchestrator backend...")
    await engine.dispose()


app = FastAPI(
    title="Agent Orchestrator API",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(credentials_router)
app.include_router(models_router)
app.include_router(knowledge_router)
app.include_router(tools_router)
app.include_router(agents_router)
app.include_router(conversations_router)
app.include_router(runs_router)


@app.get("/health", status_code=status.HTTP_200_OK)
async def health_check() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/ready", status_code=status.HTTP_200_OK)
async def readiness_check() -> dict[str, str]:
    db_healthy = await check_db_health()
    if not db_healthy:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database unavailable",
        )
    return {"status": "ready", "database": "connected"}


if __name__ == "__main__":
    import sys
    from pathlib import Path

    import uvicorn

    # Running this file directly puts its own directory on sys.path, not
    # backend/src, so the `orchestrator` package would not be importable.
    src_dir = str(Path(__file__).resolve().parents[1])
    if src_dir not in sys.path:
        sys.path.insert(0, src_dir)

    uvicorn.run(
        "orchestrator.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.environment == "development",
        reload_dirs=[src_dir],
    )
