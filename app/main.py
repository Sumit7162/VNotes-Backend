import os
os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"

import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import get_settings
from app.utils.logger import setup_logging, get_logger

settings = get_settings()
setup_logging()
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup and shutdown events."""
    # Ensure storage directories exist
    for d in [settings.upload_dir, settings.transcript_dir, settings.notes_dir]:
        Path(d).mkdir(parents=True, exist_ok=True)

    # Auto-create database tables as fallback if alembic migrations not applied
    try:
        from app.core.database import engine, Base
        from app.models import User, Video, Note, UsageTracking  # noqa: F401
        Base.metadata.create_all(bind=engine)
        logger.info("database_tables_verified")
    except Exception as e:
        logger.error("database_init_failed", error=str(e))

    logger.info("app_started", env=settings.log_level)
    yield
    logger.info("app_stopped")


app = FastAPI(
    title="Video Notes AI",
    description="AI-powered video note-taking SaaS",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_origin_regex=settings.cors_origin_regex,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Import and register routers
from app.api.auth import router as auth_router
from app.api.videos import router as videos_router
from app.api.notes import router as notes_router
from app.api.usage import router as usage_router

app.include_router(auth_router)
app.include_router(videos_router)
app.include_router(notes_router)
app.include_router(usage_router)


@app.get("/api/health")
def health_check():
    return {"status": "healthy", "service": "video-notes-ai"}
