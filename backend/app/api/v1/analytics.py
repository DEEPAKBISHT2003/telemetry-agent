"""Read-only Analytics API endpoints under /api/v1/analytics.

Strict privacy, authorization, and tenant isolation enforced:
- Zero raw prompts, source code, transcripts, diffs, tool args, or filesystem paths.
- Token metrics are exact integers; never fabricated.
- Scoped to organization and authorized role.
"""

from datetime import datetime
from typing import Optional
import uuid

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import AnalyticsAuthContext, get_db, require_analytics_auth
from app.schemas.analytics import (
    AnalyticsOverviewResponse,
    DeveloperDetailResponse,
    DeveloperListResponse,
    DeviceListResponse,
    ModelAnalyticsResponse,
    SessionListResponse,
    UsageTimeseriesResponse,
)
from app.services.analytics_service import AnalyticsService

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get(
    "/overview",
    response_model=AnalyticsOverviewResponse,
    status_code=status.HTTP_200_OK,
    summary="Get aggregated high-level telemetry and usage KPIs",
)
async def get_overview(
    start_date: Optional[datetime] = Query(None, description="Start timestamp (UTC)"),
    end_date: Optional[datetime] = Query(None, description="End timestamp (UTC)"),
    organization_id: Optional[uuid.UUID] = Query(None, description="Tenant ID (bootstrap only)"),
    team_id: Optional[uuid.UUID] = Query(None, description="Filter by team ID"),
    auth: AnalyticsAuthContext = Depends(require_analytics_auth),
    session: AsyncSession = Depends(get_db),
) -> AnalyticsOverviewResponse:
    """Retrieve high-level KPI cards and overview token consumption metrics."""
    return await AnalyticsService.get_overview(
        session=session,
        auth=auth,
        start_date=start_date,
        end_date=end_date,
        organization_id=organization_id,
        team_id=team_id,
    )


@router.get(
    "/usage-timeseries",
    response_model=UsageTimeseriesResponse,
    status_code=status.HTTP_200_OK,
    summary="Get chronological daily telemetry usage buckets",
)
async def get_usage_timeseries(
    start_date: Optional[datetime] = Query(None, description="Start timestamp (UTC)"),
    end_date: Optional[datetime] = Query(None, description="End timestamp (UTC)"),
    organization_id: Optional[uuid.UUID] = Query(None, description="Tenant ID (bootstrap only)"),
    developer_id: Optional[uuid.UUID] = Query(None, description="Filter by developer ID"),
    model: Optional[str] = Query(None, description="Filter by AI model name"),
    team_id: Optional[uuid.UUID] = Query(None, description="Filter by team ID"),
    auth: AnalyticsAuthContext = Depends(require_analytics_auth),
    session: AsyncSession = Depends(get_db),
) -> UsageTimeseriesResponse:
    """Retrieve time-series bucket data for charts and trends."""
    return await AnalyticsService.get_usage_timeseries(
        session=session,
        auth=auth,
        start_date=start_date,
        end_date=end_date,
        organization_id=organization_id,
        developer_id=developer_id,
        model_name=model,
        team_id=team_id,
    )


@router.get(
    "/developers",
    response_model=DeveloperListResponse,
    status_code=status.HTTP_200_OK,
    summary="Get paginated list of developers with usage metrics",
)
async def get_developers(
    start_date: Optional[datetime] = Query(None, description="Start timestamp (UTC)"),
    end_date: Optional[datetime] = Query(None, description="End timestamp (UTC)"),
    organization_id: Optional[uuid.UUID] = Query(None, description="Tenant ID (bootstrap only)"),
    search: Optional[str] = Query(None, description="Search by name or email"),
    team_id: Optional[uuid.UUID] = Query(None, description="Filter by team ID"),
    page: int = Query(1, ge=1, description="Page number (1-indexed)"),
    page_size: int = Query(20, ge=1, le=100, description="Results per page (max 100)"),
    auth: AnalyticsAuthContext = Depends(require_analytics_auth),
    session: AsyncSession = Depends(get_db),
) -> DeveloperListResponse:
    """Retrieve developer directory with activity and token counts."""
    return await AnalyticsService.get_developers(
        session=session,
        auth=auth,
        start_date=start_date,
        end_date=end_date,
        organization_id=organization_id,
        search=search,
        team_id=team_id,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/developers/{developer_id}",
    response_model=DeveloperDetailResponse,
    status_code=status.HTTP_200_OK,
    summary="Get detailed developer profile and usage breakdown",
)
async def get_developer_detail(
    developer_id: uuid.UUID,
    start_date: Optional[datetime] = Query(None, description="Start timestamp (UTC)"),
    end_date: Optional[datetime] = Query(None, description="End timestamp (UTC)"),
    auth: AnalyticsAuthContext = Depends(require_analytics_auth),
    session: AsyncSession = Depends(get_db),
) -> DeveloperDetailResponse:
    """Retrieve developer details including models used and recent sessions."""
    return await AnalyticsService.get_developer_detail(
        session=session,
        auth=auth,
        developer_id=developer_id,
        start_date=start_date,
        end_date=end_date,
    )


@router.get(
    "/models",
    response_model=ModelAnalyticsResponse,
    status_code=status.HTTP_200_OK,
    summary="Get token consumption and run count grouped by AI model",
)
async def get_models(
    start_date: Optional[datetime] = Query(None, description="Start timestamp (UTC)"),
    end_date: Optional[datetime] = Query(None, description="End timestamp (UTC)"),
    organization_id: Optional[uuid.UUID] = Query(None, description="Tenant ID (bootstrap only)"),
    team_id: Optional[uuid.UUID] = Query(None, description="Filter by team ID"),
    auth: AnalyticsAuthContext = Depends(require_analytics_auth),
    session: AsyncSession = Depends(get_db),
) -> ModelAnalyticsResponse:
    """Retrieve comparative AI model metrics."""
    return await AnalyticsService.get_models(
        session=session,
        auth=auth,
        start_date=start_date,
        end_date=end_date,
        organization_id=organization_id,
        team_id=team_id,
    )


@router.get(
    "/sessions",
    response_model=SessionListResponse,
    status_code=status.HTTP_200_OK,
    summary="Get paginated list of AI sessions with token usage",
)
async def get_sessions(
    start_date: Optional[datetime] = Query(None, description="Start timestamp (UTC)"),
    end_date: Optional[datetime] = Query(None, description="End timestamp (UTC)"),
    organization_id: Optional[uuid.UUID] = Query(None, description="Tenant ID (bootstrap only)"),
    developer_id: Optional[uuid.UUID] = Query(None, description="Filter by developer ID"),
    team_id: Optional[uuid.UUID] = Query(None, description="Filter by team ID"),
    page: int = Query(1, ge=1, description="Page number (1-indexed)"),
    page_size: int = Query(20, ge=1, le=100, description="Results per page (max 100)"),
    auth: AnalyticsAuthContext = Depends(require_analytics_auth),
    session: AsyncSession = Depends(get_db),
) -> SessionListResponse:
    """Retrieve session listing with repository attribution and token totals."""
    return await AnalyticsService.get_sessions(
        session=session,
        auth=auth,
        start_date=start_date,
        end_date=end_date,
        organization_id=organization_id,
        developer_id=developer_id,
        team_id=team_id,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/devices",
    response_model=DeviceListResponse,
    status_code=status.HTTP_200_OK,
    summary="Get registered developer devices and last activity",
)
async def get_devices(
    organization_id: Optional[uuid.UUID] = Query(None, description="Tenant ID (bootstrap only)"),
    auth: AnalyticsAuthContext = Depends(require_analytics_auth),
    session: AsyncSession = Depends(get_db),
) -> DeviceListResponse:
    """Retrieve enrolled workstations with telemetry heartbeat status."""
    return await AnalyticsService.get_devices(
        session=session,
        auth=auth,
        organization_id=organization_id,
    )
