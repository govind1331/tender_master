"""
FastAPI Application Entry Point
================================
Run via: uvicorn app.main:app --host 0.0.0.0 --port 8000
"""
import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager

from app.core.config import get_settings
from app.db.session import init_db
from app.api.routes_ingest import router as ingest_router
from app.api.routes_tender import router as tender_router

logger = structlog.get_logger()
settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("app.startup", env=settings.app_env)
    init_db()
    yield
    logger.info("app.shutdown")


app = FastAPI(
    title="Pan Software — Tender Response Automation API",
    description=(
        "Multi-agent LangGraph service that generates structured tender responses "
        "grounded in historical QA knowledge bank."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(ingest_router)
app.include_router(tender_router)


@app.get("/health", tags=["Health"])
def health():
    return {"status": "ok", "service": "tender-automation-api"}
