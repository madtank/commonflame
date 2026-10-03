"""Waystation's self-hosted API, with cloud services disabled by default."""

from contextlib import asynccontextmanager
from importlib import import_module
import logging
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from sqlalchemy import text

from app.core.config import get_settings, validate_security_settings
from app.core.database import AsyncSessionLocal, engine

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
settings = get_settings()
validate_security_settings(settings)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Readiness must fail when the owning database is unavailable.
    async with AsyncSessionLocal() as db:
        await db.execute(text("SELECT 1"))
    yield
    await engine.dispose()


app = FastAPI(title="Waystation API", version="0.1.0", lifespan=lifespan,
              docs_url="/api/docs", redoc_url=None)
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins,
                   allow_credentials=True, allow_methods=["*"], allow_headers=["*"],
                   expose_headers=["WWW-Authenticate", "X-Correlation-Id"])
from app.middleware.correlation import CorrelationIdMiddleware
from app.middleware.rate_limiting_middleware import RateLimitingMiddleware

app.add_middleware(CorrelationIdMiddleware)
app.add_middleware(RateLimitingMiddleware, enabled=True)


@app.exception_handler(RequestValidationError)
async def validation_error_without_credentials(request, exc):
    # Pydantic includes the submitted value by default; never echo passwords,
    # setup/invite capabilities, or OAuth credentials in validation responses.
    return JSONResponse(status_code=422, content={"detail": [
        {"loc": error["loc"], "msg": error["msg"], "type": error["type"]}
        for error in exc.errors()
    ]})

# Imports intentionally fail startup instead of leaving a healthy-looking API
# with missing routes. Route dependencies own authentication and authorization.
for name in (
    "jwks", "oauth_as", "auth", "local_auth", "agent_management",
    "agents", "tasks", "messages", "summaries", "message_summaries", "metrics",
    "search", "sse", "spaces", "notifications", "config", "agent_templates",
    "notification_preferences", "agents_unified",
    "agent_groups", "context", "interactive_context", "api_v1", "uploads",
    "feature_flags", "guest_space", "fleet_control",
):
    module = import_module(f"app.api.v1.{name}")
    app.include_router(module.router)
    if name == "context":
        app.include_router(module.sse_router)

from app.api.v1.user_settings import router as user_settings_router
app.include_router(user_settings_router)
app.include_router(user_settings_router, prefix="/api/v1")


@app.get("/")
async def root():
    return {"service": "Waystation API", "version": "0.1.0", "health": "/health",
            "docs": "/api/docs", "agent_auth": "/auth.md"}


@app.get("/health")
async def health():
    try:
        async with AsyncSessionLocal() as db:
            await db.execute(text("SELECT 1"))
    except Exception:
        raise HTTPException(status_code=503, detail="Database unavailable")
    return {"status": "healthy", "service": "Waystation API", "database": "connected"}


@app.get("/auth.md", include_in_schema=False)
async def auth_md():
    path = Path(__file__).resolve().parents[1] / "auth.md"
    origin = (os.getenv("AX_AUTH_PUBLIC_BASE_URL") or os.getenv("PUBLIC_URL")
              or os.getenv("FRONTEND_URL") or "http://localhost:3000").rstrip("/")
    return PlainTextResponse(path.read_text().replace("{{ORIGIN}}", origin), media_type="text/markdown; charset=utf-8",
                             headers={"Cache-Control": "public, max-age=300"})
