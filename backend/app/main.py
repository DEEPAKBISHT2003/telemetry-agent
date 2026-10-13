"""FastAPI application factory, lifespan management, and security middleware."""

from contextlib import asynccontextmanager
import logging
import sys
from typing import AsyncGenerator, Optional

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

from app.api.v1.admin import router as admin_router
from app.api.v1.analytics import router as analytics_router
from app.api.v1.devices import router as devices_router
from app.api.v1.enrollment import router as enrollment_router
from app.api.v1.health import router as health_router
from app.api.v1.telemetry import router as telemetry_router
from app.config import Settings, get_settings
from app.database import dispose_engine


def configure_logging(log_level: str) -> None:
    """Configure structured logging for backend application."""
    numeric_level = getattr(logging, log_level.upper(), logging.INFO)
    logging.basicConfig(
        level=numeric_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%SZ",
        handlers=[logging.StreamHandler(sys.stdout)],
        force=True,
    )


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Enforce standard enterprise security headers on all HTTP responses."""

    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        return response


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Manage application startup and shutdown lifecycles cleanly."""
    settings = app.state.settings
    logger = logging.getLogger("telemetry.backend")

    # Startup logic
    configure_logging(settings.log_level)
    logger.info(
        "Starting %s v%s [env=%s]",
        settings.app_name,
        settings.app_version,
        settings.app_env,
    )
    logger.info("Database configured: %s", bool(settings.database_url))

    yield

    # Shutdown logic
    logger.info("Initiating graceful shutdown...")
    await dispose_engine()
    logger.info("Application shutdown complete.")


def create_app(settings: Optional[Settings] = None) -> FastAPI:
    """Create and configure an instance of the FastAPI application."""
    cfg = settings or get_settings()

    app = FastAPI(
        title=cfg.app_name,
        version=cfg.app_version,
        docs_url="/docs" if cfg.debug or cfg.app_env != "production" else None,
        redoc_url="/redoc" if cfg.debug or cfg.app_env != "production" else None,
        openapi_url="/openapi.json" if cfg.debug or cfg.app_env != "production" else None,
        lifespan=lifespan,
    )

    # Attach settings to application state
    app.state.settings = cfg

    # 1. Security Headers Middleware
    app.add_middleware(SecurityHeadersMiddleware)

    # 2. CORS Middleware (strictly restrictive by default)
    if cfg.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=cfg.cors_origins,
            allow_credentials=cfg.cors_allow_credentials,
            allow_methods=cfg.cors_allow_methods,
            allow_headers=cfg.cors_allow_headers,
        )

    # 3. Register API Routers
    app.include_router(health_router, prefix=cfg.api_v1_prefix)
    app.include_router(admin_router, prefix=cfg.api_v1_prefix)
    app.include_router(enrollment_router, prefix=cfg.api_v1_prefix)
    app.include_router(devices_router, prefix=cfg.api_v1_prefix)
    app.include_router(telemetry_router, prefix=cfg.api_v1_prefix)
    app.include_router(analytics_router, prefix=cfg.api_v1_prefix)

    return app


# Default ASGI application instance for standard runners (e.g. uvicorn app.main:app)
app = create_app()
