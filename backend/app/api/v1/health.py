"""Health and readiness check endpoints."""

import logging
from typing import Literal

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel, Field

from app.config import Settings
from app.database import check_database_connectivity
from app.dependencies import get_app_settings

logger = logging.getLogger("telemetry.backend.health")

router = APIRouter(tags=["Health"])


class HealthResponse(BaseModel):
    """Documented health check response schema."""

    status: Literal["healthy", "degraded", "unhealthy"] = Field(
        description="Overall service operational health status"
    )
    app: Literal["healthy"] = Field(
        default="healthy", description="Application runtime health"
    )
    database: Literal["connected", "disconnected", "not_configured"] = Field(
        description="Database connectivity status"
    )
    version: str = Field(description="Backend application version")
    environment: str = Field(description="Current deployment environment")


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Service Health Check",
    description=(
        "Returns the operational health of the application and verifies "
        "connectivity to the backend database when configured."
    ),
    responses={
        200: {
            "description": "Service is fully operational or operating without database.",
            "model": HealthResponse,
        },
        503: {
            "description": "Database connectivity check failed.",
            "model": HealthResponse,
        },
    },
)
async def get_health(
    response: Response,
    settings: Settings = Depends(get_app_settings),
) -> HealthResponse:
    """Evaluate application and database health without leaking infrastructure details."""
    db_status: Literal["connected", "disconnected", "not_configured"]

    if not settings.database_url:
        # Operating without configured database (e.g. gateway-only mode or initial setup)
        db_status = "not_configured"
        overall_status: Literal["healthy", "degraded", "unhealthy"] = "healthy"
        response.status_code = status.HTTP_200_OK
    else:
        # Check database connectivity
        is_connected = await check_database_connectivity(timeout=2.5)
        if is_connected:
            db_status = "connected"
            overall_status = "healthy"
            response.status_code = status.HTTP_200_OK
        else:
            db_status = "disconnected"
            overall_status = "unhealthy"
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return HealthResponse(
        status=overall_status,
        app="healthy",
        database=db_status,
        version=settings.app_version,
        environment=settings.app_env,
    )
