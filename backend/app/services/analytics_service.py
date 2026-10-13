"""Domain service for read-only analytics aggregation and reporting.

Guarantees:
1. SQL-level aggregations without loading raw event tables into Python memory.
2. Tenant and role-based scoping using authoritative authorization context.
3. UTC-normalized date filtering with bounded defaults.
4. Privacy compliance: zero raw prompts, code, transcripts, diffs, tool args, or paths returned.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import math
from typing import List, Optional, Set, Tuple
import uuid

from fastapi import HTTPException, status
from sqlalchemy import (
    Date,
    String,
    case,
    cast,
    distinct,
    func,
    or_,
    select,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.dependencies import AnalyticsAuthContext
from app.models.base import ensure_utc, utc_now
from app.models.developer import Developer
from app.models.device import Device
from app.models.event import TelemetryEvent
from app.models.organization import TeamMembership
from app.schemas.analytics import (
    AnalyticsOverviewResponse,
    DeveloperDetailResponse,
    DeveloperListResponse,
    DeveloperSummary,
    DeviceAnalyticsSummary,
    DeviceListResponse,
    ModelAnalyticsResponse,
    ModelAnalyticsSummary,
    ModelUsageSummary,
    SessionAnalyticsSummary,
    SessionBrief,
    SessionListResponse,
    TimeseriesBucket,
    TokenUsageBreakdown,
    UsageTimeseriesResponse,
)

DEFAULT_RANGE_DAYS = 30
MAX_RANGE_DAYS = 365


class AnalyticsService:
    """Core domain service executing optimized SQL queries for analytics dashboards."""

    @staticmethod
    def normalize_date_range(
        start_date: Optional[datetime],
        end_date: Optional[datetime],
    ) -> Tuple[datetime, datetime]:
        """Validate and normalize requested date range into UTC bounds."""
        now = utc_now()
        normalized_end = ensure_utc(end_date) if end_date else now
        normalized_start = (
            ensure_utc(start_date)
            if start_date
            else (normalized_end - timedelta(days=DEFAULT_RANGE_DAYS))
        )

        if normalized_start > normalized_end:
            raise HTTPException(
                status_code=getattr(status, "HTTP_422_UNPROCESSABLE_CONTENT", 422),
                detail="start_date cannot be greater than end_date.",
            )

        if (normalized_end - normalized_start).days > MAX_RANGE_DAYS:
            raise HTTPException(
                status_code=getattr(status, "HTTP_422_UNPROCESSABLE_CONTENT", 422),
                detail=f"Requested date range exceeds maximum allowed window of {MAX_RANGE_DAYS} days.",
            )

        return normalized_start, normalized_end

    @staticmethod
    def _build_event_filters(
        auth: AnalyticsAuthContext,
        start_date: datetime,
        end_date: datetime,
        target_org_id: Optional[uuid.UUID] = None,
        developer_id: Optional[uuid.UUID] = None,
        model_name: Optional[str] = None,
        team_id: Optional[uuid.UUID] = None,
    ) -> list:
        """Construct parameterized SQLAlchemy filter expressions honoring auth boundaries."""
        effective_org_id = auth.check_organization_access(target_org_id)
        filters = [
            TelemetryEvent.event_timestamp >= start_date,
            TelemetryEvent.event_timestamp <= end_date,
        ]

        if effective_org_id is not None:
            filters.append(TelemetryEvent.organization_id == effective_org_id)

        # Scoping based on caller's role
        if auth.allowed_developer_ids is not None:
            if developer_id is not None:
                if developer_id not in auth.allowed_developer_ids:
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail="Access to specified developer analytics is not authorized.",
                    )
                filters.append(TelemetryEvent.developer_id == developer_id)
            else:
                filters.append(TelemetryEvent.developer_id.in_(auth.allowed_developer_ids))
        elif developer_id is not None:
            filters.append(TelemetryEvent.developer_id == developer_id)

        if model_name:
            filters.append(TelemetryEvent.model_name == model_name)

        if team_id is not None:
            team_devs_subq = select(TeamMembership.developer_id).where(TeamMembership.team_id == team_id)
            filters.append(TelemetryEvent.developer_id.in_(team_devs_subq))

        return filters

    @classmethod
    async def get_overview(
        cls,
        session: AsyncSession,
        auth: AnalyticsAuthContext,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        organization_id: Optional[uuid.UUID] = None,
        team_id: Optional[uuid.UUID] = None,
    ) -> AnalyticsOverviewResponse:
        """Compute organization/team-level KPI metrics aggregated inside the database engine."""
        s_date, e_date = cls.normalize_date_range(start_date, end_date)
        filters = cls._build_event_filters(
            auth=auth,
            start_date=s_date,
            end_date=e_date,
            target_org_id=organization_id,
            team_id=team_id,
        )

        effective_org_id = auth.check_organization_access(organization_id)

        # Single aggregated query for telemetry events
        stmt = select(
            func.count(TelemetryEvent.id).label("total_events"),
            func.count(case((TelemetryEvent.event_type == "ai_run", 1))).label("total_ai_runs"),
            func.count(
                case(
                    (
                        or_(
                            TelemetryEvent.event_type.in_(["tool_call", "ai_tool_call"]),
                            TelemetryEvent.tool_name.is_not(None),
                        ),
                        1,
                    )
                )
            ).label("total_tool_calls"),
            func.count(distinct(TelemetryEvent.session_id)).label("total_sessions"),
            func.coalesce(func.sum(TelemetryEvent.tokens_input_uncached), 0).label("input_tokens"),
            func.coalesce(func.sum(TelemetryEvent.tokens_input_cached), 0).label("cached_input_tokens"),
            func.coalesce(func.sum(TelemetryEvent.tokens_output), 0).label("output_tokens"),
            func.coalesce(func.sum(TelemetryEvent.tokens_total), 0).label("total_tokens"),
            func.sum(TelemetryEvent.cost_usd).label("total_cost_usd"),
            func.min(TelemetryEvent.event_timestamp).label("first_event_at"),
            func.max(TelemetryEvent.event_timestamp).label("last_event_at"),
            func.count(distinct(TelemetryEvent.developer_id)).label("active_developers"),
            func.count(distinct(TelemetryEvent.device_id)).label("active_devices"),
        ).where(*filters)

        res = (await session.execute(stmt)).first()

        # Count total registered developers and devices in tenant scope
        dev_stmt = select(func.count(Developer.id))
        dev_stmt = dev_stmt.where(Developer.organization_id == effective_org_id) if effective_org_id else dev_stmt
        if auth.allowed_developer_ids is not None:
            dev_stmt = dev_stmt.where(Developer.id.in_(auth.allowed_developer_ids))
        total_devs = (await session.execute(dev_stmt)).scalar() or 0

        device_stmt = select(func.count(Device.id))
        device_stmt = device_stmt.where(Device.organization_id == effective_org_id) if effective_org_id else device_stmt
        if auth.allowed_developer_ids is not None:
            device_stmt = device_stmt.where(Device.developer_id.in_(auth.allowed_developer_ids))
        total_devices = (await session.execute(device_stmt)).scalar() or 0

        # Build response
        tokens = TokenUsageBreakdown(
            input_tokens=res.input_tokens if res else 0,
            cached_input_tokens=res.cached_input_tokens if res else 0,
            output_tokens=res.output_tokens if res else 0,
            thinking_tokens=None,
            total_tokens=res.total_tokens if res else 0,
        )

        return AnalyticsOverviewResponse(
            organization_id=effective_org_id,
            start_date=s_date,
            end_date=e_date,
            total_developers=total_devs,
            active_developers=res.active_developers if res else 0,
            enrolled_devices=total_devices,
            active_devices=res.active_devices if res else 0,
            total_events=res.total_events if res else 0,
            total_ai_runs=res.total_ai_runs if res else 0,
            total_tool_calls=res.total_tool_calls if res else 0,
            total_sessions=res.total_sessions if res else 0,
            tokens=tokens,
            total_cost_usd=res.total_cost_usd if res and res.total_cost_usd is not None else None,
            first_event_at=res.first_event_at if res else None,
            last_event_at=res.last_event_at if res else None,
        )

    @classmethod
    async def get_usage_timeseries(
        cls,
        session: AsyncSession,
        auth: AnalyticsAuthContext,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        organization_id: Optional[uuid.UUID] = None,
        developer_id: Optional[uuid.UUID] = None,
        model_name: Optional[str] = None,
        team_id: Optional[uuid.UUID] = None,
    ) -> UsageTimeseriesResponse:
        """Compute chronological daily usage buckets grouped in SQL."""
        s_date, e_date = cls.normalize_date_range(start_date, end_date)
        filters = cls._build_event_filters(
            auth=auth,
            start_date=s_date,
            end_date=e_date,
            target_org_id=organization_id,
            developer_id=developer_id,
            model_name=model_name,
            team_id=team_id,
        )

        bucket_date = func.substr(cast(TelemetryEvent.event_timestamp, String), 1, 10).label("bucket_date")

        stmt = (
            select(
                bucket_date,
                func.count(TelemetryEvent.id).label("events_count"),
                func.count(case((TelemetryEvent.event_type == "ai_run", 1))).label("ai_runs_count"),
                func.count(
                    case(
                        (
                            or_(
                                TelemetryEvent.event_type.in_(["tool_call", "ai_tool_call"]),
                                TelemetryEvent.tool_name.is_not(None),
                            ),
                            1,
                        )
                    )
                ).label("tool_calls_count"),
                func.coalesce(func.sum(TelemetryEvent.tokens_input_uncached), 0).label("input_tokens"),
                func.coalesce(func.sum(TelemetryEvent.tokens_input_cached), 0).label("cached_input_tokens"),
                func.coalesce(func.sum(TelemetryEvent.tokens_output), 0).label("output_tokens"),
                func.coalesce(func.sum(TelemetryEvent.tokens_total), 0).label("total_tokens"),
                func.sum(TelemetryEvent.cost_usd).label("cost_usd"),
            )
            .where(*filters)
            .group_by(bucket_date)
            .order_by(bucket_date.asc())
        )

        rows = (await session.execute(stmt)).all()

        buckets = [
            TimeseriesBucket(
                timestamp=str(r.bucket_date),
                events_count=r.events_count,
                ai_runs_count=r.ai_runs_count,
                tool_calls_count=r.tool_calls_count,
                input_tokens=r.input_tokens,
                cached_input_tokens=r.cached_input_tokens,
                output_tokens=r.output_tokens,
                total_tokens=r.total_tokens,
                cost_usd=r.cost_usd,
            )
            for r in rows
        ]

        return UsageTimeseriesResponse(
            interval="day",
            start_date=s_date,
            end_date=e_date,
            buckets=buckets,
        )

    @classmethod
    async def get_developers(
        cls,
        session: AsyncSession,
        auth: AnalyticsAuthContext,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        organization_id: Optional[uuid.UUID] = None,
        search: Optional[str] = None,
        team_id: Optional[uuid.UUID] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> DeveloperListResponse:
        """Retrieve paginated developer list with aggregated usage in the requested window."""
        s_date, e_date = cls.normalize_date_range(start_date, end_date)
        effective_org_id = auth.check_organization_access(organization_id)
        page = max(1, page)
        page_size = min(max(1, page_size), 100)

        # Telemetry event aggregation subquery
        event_filters = [
            TelemetryEvent.event_timestamp >= s_date,
            TelemetryEvent.event_timestamp <= e_date,
        ]
        if effective_org_id:
            event_filters.append(TelemetryEvent.organization_id == effective_org_id)

        evt_subq = (
            select(
                TelemetryEvent.developer_id,
                func.count(TelemetryEvent.id).label("event_count"),
                func.count(case((TelemetryEvent.event_type == "ai_run", 1))).label("ai_run_count"),
                func.coalesce(func.sum(TelemetryEvent.tokens_total), 0).label("total_tokens"),
                func.coalesce(func.sum(TelemetryEvent.tokens_input_uncached), 0).label("input_tokens"),
                func.coalesce(func.sum(TelemetryEvent.tokens_input_cached), 0).label("cached_tokens"),
                func.coalesce(func.sum(TelemetryEvent.tokens_output), 0).label("output_tokens"),
                func.min(TelemetryEvent.event_timestamp).label("first_seen_at"),
                func.max(TelemetryEvent.event_timestamp).label("last_seen_at"),
            )
            .where(*event_filters)
            .group_by(TelemetryEvent.developer_id)
            .subquery()
        )

        # Base developer query
        dev_filters = []
        if effective_org_id:
            dev_filters.append(Developer.organization_id == effective_org_id)
        if auth.allowed_developer_ids is not None:
            dev_filters.append(Developer.id.in_(auth.allowed_developer_ids))
        if search:
            search_pattern = f"%{search.strip()}%"
            dev_filters.append(
                or_(
                    Developer.email.ilike(search_pattern),
                    Developer.full_name.ilike(search_pattern),
                )
            )
        if team_id is not None:
            team_subq = select(TeamMembership.developer_id).where(TeamMembership.team_id == team_id)
            dev_filters.append(Developer.id.in_(team_subq))

        # Total count query
        count_stmt = select(func.count(Developer.id)).where(*dev_filters)
        total_count = (await session.execute(count_stmt)).scalar() or 0

        # Query developer records left-joined with metrics
        stmt = (
            select(
                Developer,
                evt_subq.c.event_count,
                evt_subq.c.ai_run_count,
                evt_subq.c.total_tokens,
                evt_subq.c.input_tokens,
                evt_subq.c.cached_tokens,
                evt_subq.c.output_tokens,
                evt_subq.c.first_seen_at,
                evt_subq.c.last_seen_at,
            )
            .outerjoin(evt_subq, Developer.id == evt_subq.c.developer_id)
            .where(*dev_filters)
            .options(
                selectinload(Developer.team_memberships).selectinload(TeamMembership.team)
            )
            .order_by(evt_subq.c.last_seen_at.desc().nullslast(), Developer.full_name.asc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )

        rows = (await session.execute(stmt)).all()

        items = []
        for r in rows:
            dev = r[0]
            team_names = [m.team.name for m in dev.team_memberships if m.team]
            last_seen = r.last_seen_at
            # Active rule: developer emitted telemetry in requested date range
            is_active = last_seen is not None

            items.append(
                DeveloperSummary(
                    developer_id=dev.id,
                    email=dev.email,
                    full_name=dev.full_name,
                    role=dev.role.value if hasattr(dev.role, "value") else str(dev.role),
                    status=dev.status.value if hasattr(dev.status, "value") else str(dev.status),
                    teams=team_names,
                    event_count=r.event_count or 0,
                    ai_run_count=r.ai_run_count or 0,
                    total_tokens=r.total_tokens or 0,
                    input_tokens=r.input_tokens or 0,
                    cached_tokens=r.cached_tokens or 0,
                    output_tokens=r.output_tokens or 0,
                    first_seen_at=r.first_seen_at,
                    last_seen_at=last_seen,
                    is_active=is_active,
                )
            )

        total_pages = math.ceil(total_count / page_size) if total_count > 0 else 1

        return DeveloperListResponse(
            items=items,
            total_count=total_count,
            page=page,
            page_size=page_size,
            total_pages=total_pages,
        )

    @classmethod
    async def get_developer_detail(
        cls,
        session: AsyncSession,
        auth: AnalyticsAuthContext,
        developer_id: uuid.UUID,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
    ) -> DeveloperDetailResponse:
        """Retrieve single developer profile, model breakdown, and recent sessions."""
        if not auth.can_access_developer(developer_id):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not authorized to access analytics for this developer.",
            )

        s_date, e_date = cls.normalize_date_range(start_date, end_date)

        # Developer record
        dev_stmt = (
            select(Developer)
            .where(Developer.id == developer_id)
            .options(selectinload(Developer.team_memberships).selectinload(TeamMembership.team))
        )
        dev = (await session.execute(dev_stmt)).scalars().first()
        if not dev:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Developer not found.")

        # Overall summary for developer in date range
        filters = [
            TelemetryEvent.developer_id == developer_id,
            TelemetryEvent.event_timestamp >= s_date,
            TelemetryEvent.event_timestamp <= e_date,
        ]
        sum_stmt = select(
            func.count(TelemetryEvent.id).label("event_count"),
            func.count(case((TelemetryEvent.event_type == "ai_run", 1))).label("ai_run_count"),
            func.coalesce(func.sum(TelemetryEvent.tokens_total), 0).label("total_tokens"),
            func.coalesce(func.sum(TelemetryEvent.tokens_input_uncached), 0).label("input_tokens"),
            func.coalesce(func.sum(TelemetryEvent.tokens_input_cached), 0).label("cached_tokens"),
            func.coalesce(func.sum(TelemetryEvent.tokens_output), 0).label("output_tokens"),
            func.min(TelemetryEvent.event_timestamp).label("first_seen_at"),
            func.max(TelemetryEvent.event_timestamp).label("last_seen_at"),
        ).where(*filters)
        metrics = (await session.execute(sum_stmt)).first()

        team_names = [m.team.name for m in dev.team_memberships if m.team]
        last_seen = metrics.last_seen_at if metrics else None

        dev_summary = DeveloperSummary(
            developer_id=dev.id,
            email=dev.email,
            full_name=dev.full_name,
            role=dev.role.value if hasattr(dev.role, "value") else str(dev.role),
            status=dev.status.value if hasattr(dev.status, "value") else str(dev.status),
            teams=team_names,
            event_count=metrics.event_count if metrics else 0,
            ai_run_count=metrics.ai_run_count if metrics else 0,
            total_tokens=metrics.total_tokens if metrics else 0,
            input_tokens=metrics.input_tokens if metrics else 0,
            cached_tokens=metrics.cached_tokens if metrics else 0,
            output_tokens=metrics.output_tokens if metrics else 0,
            first_seen_at=metrics.first_seen_at if metrics else None,
            last_seen_at=last_seen,
            is_active=last_seen is not None,
        )

        # Model breakdown
        model_stmt = (
            select(
                TelemetryEvent.model_name,
                func.count(case((TelemetryEvent.event_type == "ai_run", 1))).label("run_count"),
                func.coalesce(func.sum(TelemetryEvent.tokens_total), 0).label("total_tokens"),
                func.max(TelemetryEvent.event_timestamp).label("last_used_at"),
            )
            .where(*filters, TelemetryEvent.model_name.is_not(None))
            .group_by(TelemetryEvent.model_name)
            .order_by(func.coalesce(func.sum(TelemetryEvent.tokens_total), 0).desc())
        )
        model_rows = (await session.execute(model_stmt)).all()
        models = [
            ModelUsageSummary(
                model_name=r.model_name or "unknown",
                run_count=r.run_count,
                total_tokens=r.total_tokens,
                last_used_at=r.last_used_at,
            )
            for r in model_rows
        ]

        # Recent sessions
        sess_stmt = (
            select(
                TelemetryEvent.session_id,
                TelemetryEvent.repository_name,
                TelemetryEvent.branch,
                func.count(TelemetryEvent.id).label("event_count"),
                func.count(case((TelemetryEvent.event_type == "ai_run", 1))).label("ai_run_count"),
                func.coalesce(func.sum(TelemetryEvent.tokens_total), 0).label("total_tokens"),
                func.min(TelemetryEvent.event_timestamp).label("started_at"),
                func.max(TelemetryEvent.event_timestamp).label("last_activity_at"),
            )
            .where(*filters, TelemetryEvent.session_id.is_not(None))
            .group_by(
                TelemetryEvent.session_id,
                TelemetryEvent.repository_name,
                TelemetryEvent.branch,
            )
            .order_by(func.max(TelemetryEvent.event_timestamp).desc())
            .limit(10)
        )
        sess_rows = (await session.execute(sess_stmt)).all()
        sessions = [
            SessionBrief(
                session_id=r.session_id or "",
                repository_name=r.repository_name,
                branch=r.branch,
                event_count=r.event_count,
                ai_run_count=r.ai_run_count,
                total_tokens=r.total_tokens,
                started_at=r.started_at,
                last_activity_at=r.last_activity_at,
            )
            for r in sess_rows
        ]

        # Device count
        dev_devices_stmt = select(func.count(Device.id)).where(Device.developer_id == developer_id)
        dev_devices_count = (await session.execute(dev_devices_stmt)).scalar() or 0

        return DeveloperDetailResponse(
            developer=dev_summary,
            models=models,
            recent_sessions=sessions,
            enrolled_devices_count=dev_devices_count,
        )

    @classmethod
    async def get_models(
        cls,
        session: AsyncSession,
        auth: AnalyticsAuthContext,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        organization_id: Optional[uuid.UUID] = None,
        team_id: Optional[uuid.UUID] = None,
    ) -> ModelAnalyticsResponse:
        """Aggregate token consumption and run count grouped by AI model."""
        s_date, e_date = cls.normalize_date_range(start_date, end_date)
        filters = cls._build_event_filters(
            auth=auth,
            start_date=s_date,
            end_date=e_date,
            target_org_id=organization_id,
            team_id=team_id,
        )

        stmt = (
            select(
                TelemetryEvent.model_name,
                func.count(case((TelemetryEvent.event_type == "ai_run", 1))).label("ai_run_count"),
                func.count(TelemetryEvent.id).label("total_events"),
                func.coalesce(func.sum(TelemetryEvent.tokens_input_uncached), 0).label("input_tokens"),
                func.coalesce(func.sum(TelemetryEvent.tokens_input_cached), 0).label("cached_input_tokens"),
                func.coalesce(func.sum(TelemetryEvent.tokens_output), 0).label("output_tokens"),
                func.coalesce(func.sum(TelemetryEvent.tokens_total), 0).label("total_tokens"),
                func.sum(TelemetryEvent.cost_usd).label("cost_usd"),
                func.max(TelemetryEvent.event_timestamp).label("last_used_at"),
            )
            .where(*filters, TelemetryEvent.model_name.is_not(None))
            .group_by(TelemetryEvent.model_name)
            .order_by(func.coalesce(func.sum(TelemetryEvent.tokens_total), 0).desc())
        )

        rows = (await session.execute(stmt)).all()
        items = [
            ModelAnalyticsSummary(
                model_name=r.model_name or "unknown",
                ai_run_count=r.ai_run_count,
                total_events=r.total_events,
                input_tokens=r.input_tokens,
                cached_input_tokens=r.cached_input_tokens,
                output_tokens=r.output_tokens,
                thinking_tokens=None,
                total_tokens=r.total_tokens,
                cost_usd=r.cost_usd,
                last_used_at=r.last_used_at,
            )
            for r in rows
        ]

        return ModelAnalyticsResponse(
            items=items,
            total_models=len(items),
        )

    @classmethod
    async def get_sessions(
        cls,
        session: AsyncSession,
        auth: AnalyticsAuthContext,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        organization_id: Optional[uuid.UUID] = None,
        developer_id: Optional[uuid.UUID] = None,
        team_id: Optional[uuid.UUID] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> SessionListResponse:
        """Paginated list of developer sessions and aggregated token consumption."""
        s_date, e_date = cls.normalize_date_range(start_date, end_date)
        page = max(1, page)
        page_size = min(max(1, page_size), 100)

        filters = cls._build_event_filters(
            auth=auth,
            start_date=s_date,
            end_date=e_date,
            target_org_id=organization_id,
            developer_id=developer_id,
            team_id=team_id,
        )
        filters.append(TelemetryEvent.session_id.is_not(None))

        # Session summary subquery
        sess_subq = (
            select(
                TelemetryEvent.session_id,
                TelemetryEvent.developer_id,
                TelemetryEvent.repository_name,
                TelemetryEvent.branch,
                func.count(TelemetryEvent.id).label("event_count"),
                func.count(case((TelemetryEvent.event_type == "ai_run", 1))).label("ai_run_count"),
                func.coalesce(func.sum(TelemetryEvent.tokens_input_uncached), 0).label("input_tokens"),
                func.coalesce(func.sum(TelemetryEvent.tokens_input_cached), 0).label("cached_tokens"),
                func.coalesce(func.sum(TelemetryEvent.tokens_output), 0).label("output_tokens"),
                func.coalesce(func.sum(TelemetryEvent.tokens_total), 0).label("total_tokens"),
                func.min(TelemetryEvent.event_timestamp).label("started_at"),
                func.max(TelemetryEvent.event_timestamp).label("last_activity_at"),
                func.max(TelemetryEvent.model_name).label("primary_model"),
            )
            .where(*filters)
            .group_by(
                TelemetryEvent.session_id,
                TelemetryEvent.developer_id,
                TelemetryEvent.repository_name,
                TelemetryEvent.branch,
            )
            .subquery()
        )

        count_stmt = select(func.count()).select_from(sess_subq)
        total_count = (await session.execute(count_stmt)).scalar() or 0

        stmt = (
            select(sess_subq, Developer.full_name)
            .join(Developer, Developer.id == sess_subq.c.developer_id)
            .order_by(sess_subq.c.last_activity_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )

        rows = (await session.execute(stmt)).all()
        items = [
            SessionAnalyticsSummary(
                session_id=r.session_id,
                developer_id=r.developer_id,
                developer_name=r.full_name,
                repository_name=r.repository_name,
                branch=r.branch,
                event_count=r.event_count,
                ai_run_count=r.ai_run_count,
                input_tokens=r.input_tokens,
                cached_tokens=r.cached_tokens,
                output_tokens=r.output_tokens,
                total_tokens=r.total_tokens,
                started_at=r.started_at,
                last_activity_at=r.last_activity_at,
                primary_model=r.primary_model,
            )
            for r in rows
        ]

        total_pages = math.ceil(total_count / page_size) if total_count > 0 else 1

        return SessionListResponse(
            items=items,
            total_count=total_count,
            page=page,
            page_size=page_size,
            total_pages=total_pages,
        )

    @classmethod
    async def get_devices(
        cls,
        session: AsyncSession,
        auth: AnalyticsAuthContext,
        organization_id: Optional[uuid.UUID] = None,
    ) -> DeviceListResponse:
        """List registered developer devices with their latest activity timestamps."""
        effective_org_id = auth.check_organization_access(organization_id)

        # Device activity subquery
        dev_subq = (
            select(
                TelemetryEvent.device_id,
                func.count(TelemetryEvent.id).label("total_events"),
                func.max(TelemetryEvent.event_timestamp).label("last_seen_at"),
            )
            .group_by(TelemetryEvent.device_id)
            .subquery()
        )

        device_filters = []
        if effective_org_id:
            device_filters.append(Device.organization_id == effective_org_id)
        if auth.allowed_developer_ids is not None:
            device_filters.append(Device.developer_id.in_(auth.allowed_developer_ids))

        stmt = (
            select(
                Device,
                Developer.full_name,
                dev_subq.c.total_events,
                dev_subq.c.last_seen_at,
            )
            .join(Developer, Developer.id == Device.developer_id)
            .outerjoin(dev_subq, Device.id == dev_subq.c.device_id)
            .where(*device_filters)
            .order_by(dev_subq.c.last_seen_at.desc().nullslast(), Device.hostname.asc())
        )

        rows = (await session.execute(stmt)).all()
        items = [
            DeviceAnalyticsSummary(
                device_id=r[0].id,
                developer_id=r[0].developer_id,
                developer_name=r.full_name,
                hostname=r[0].hostname,
                platform=r[0].platform,
                agent_version=r[0].agent_version,
                status=r[0].status.value if hasattr(r[0].status, "value") else str(r[0].status),
                total_events=r.total_events or 0,
                last_seen_at=r.last_seen_at,
            )
            for r in rows
        ]

        return DeviceListResponse(items=items, total_count=len(items))
