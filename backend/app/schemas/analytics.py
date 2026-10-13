"""Pydantic schemas for read-only analytics endpoints.

Adheres to:
1. Strict privacy invariants: zero prompts, transcripts, code, diffs, tool args, or filesystem paths.
2. Exact token accounting without fabrication.
3. Multi-tenant and role-scoped analytics views.
"""

from datetime import datetime
from decimal import Decimal
from typing import List, Optional
import uuid

from pydantic import BaseModel, Field


class TokenUsageBreakdown(BaseModel):
    """Token consumption categorized by cache status and direction."""

    input_tokens: int = Field(0, description="Uncached prompt / input tokens")
    cached_input_tokens: int = Field(0, description="Cached prompt / input tokens read from context cache")
    output_tokens: int = Field(0, description="Generated completion / output tokens")
    thinking_tokens: Optional[int] = Field(None, description="Reasoning / thinking tokens if reported")
    total_tokens: int = Field(0, description="Total tokens consumed across all categories")


class AnalyticsOverviewResponse(BaseModel):
    """High-level organization or team operations summary KPI metrics."""

    organization_id: Optional[uuid.UUID] = None
    start_date: datetime
    end_date: datetime
    total_developers: int = Field(0, description="Total enrolled developer accounts")
    active_developers: int = Field(0, description="Developers with active telemetry in requested range")
    enrolled_devices: int = Field(0, description="Total registered workstations")
    active_devices: int = Field(0, description="Devices reporting events in requested range")
    total_events: int = Field(0, description="Total ingested telemetry events")
    total_ai_runs: int = Field(0, description="Total AI invocation runs (ai_run events)")
    total_tool_calls: int = Field(0, description="Total tool call events")
    total_sessions: int = Field(0, description="Total unique developer AI sessions")
    tokens: TokenUsageBreakdown
    total_cost_usd: Optional[Decimal] = Field(None, description="Total cost in USD if authoritative billing available")
    first_event_at: Optional[datetime] = None
    last_event_at: Optional[datetime] = None


class TimeseriesBucket(BaseModel):
    """Time-series usage point aggregated by date."""

    timestamp: str = Field(..., description="Bucket date (YYYY-MM-DD)")
    events_count: int = 0
    ai_runs_count: int = 0
    tool_calls_count: int = 0
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cost_usd: Optional[Decimal] = None


class UsageTimeseriesResponse(BaseModel):
    """Time-series telemetry response for trend charting."""

    interval: str = "day"
    start_date: datetime
    end_date: datetime
    buckets: List[TimeseriesBucket]


class DeveloperSummary(BaseModel):
    """Aggregated operational summary for an individual developer."""

    developer_id: uuid.UUID
    email: str
    full_name: str
    role: str
    status: str
    teams: List[str] = []
    event_count: int = 0
    ai_run_count: int = 0
    total_tokens: int = 0
    input_tokens: int = 0
    cached_tokens: int = 0
    output_tokens: int = 0
    first_seen_at: Optional[datetime] = None
    last_seen_at: Optional[datetime] = None
    is_active: bool = False


class DeveloperListResponse(BaseModel):
    """Paginated developer list response."""

    items: List[DeveloperSummary]
    total_count: int
    page: int
    page_size: int
    total_pages: int


class ModelUsageSummary(BaseModel):
    """Model usage breakdown for a developer or team."""

    model_name: str
    run_count: int = 0
    total_tokens: int = 0
    last_used_at: Optional[datetime] = None


class SessionBrief(BaseModel):
    """Brief summary of a developer session."""

    session_id: str
    repository_name: Optional[str] = None
    branch: Optional[str] = None
    event_count: int = 0
    ai_run_count: int = 0
    total_tokens: int = 0
    started_at: Optional[datetime] = None
    last_activity_at: Optional[datetime] = None


class DeveloperDetailResponse(BaseModel):
    """In-depth developer profile and telemetry activity breakdown."""

    developer: DeveloperSummary
    models: List[ModelUsageSummary] = []
    recent_sessions: List[SessionBrief] = []
    enrolled_devices_count: int = 0


class ModelAnalyticsSummary(BaseModel):
    """Aggregated token and execution metrics by AI model."""

    model_name: str
    ai_run_count: int = 0
    total_events: int = 0
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    thinking_tokens: Optional[int] = None
    total_tokens: int = 0
    cost_usd: Optional[Decimal] = None
    last_used_at: Optional[datetime] = None


class ModelAnalyticsResponse(BaseModel):
    """Model comparison and usage overview response."""

    items: List[ModelAnalyticsSummary]
    total_models: int


class SessionAnalyticsSummary(BaseModel):
    """Session telemetry summary with developer and repository attribution."""

    session_id: str
    developer_id: uuid.UUID
    developer_name: str
    repository_name: Optional[str] = None
    branch: Optional[str] = None
    event_count: int = 0
    ai_run_count: int = 0
    input_tokens: int = 0
    cached_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    started_at: Optional[datetime] = None
    last_activity_at: Optional[datetime] = None
    primary_model: Optional[str] = None


class SessionListResponse(BaseModel):
    """Paginated session list response."""

    items: List[SessionAnalyticsSummary]
    total_count: int
    page: int
    page_size: int
    total_pages: int


class DeviceAnalyticsSummary(BaseModel):
    """Registered developer device telemetry activity status."""

    device_id: uuid.UUID
    developer_id: uuid.UUID
    developer_name: str
    hostname: str
    platform: str
    agent_version: str
    status: str
    total_events: int = 0
    last_seen_at: Optional[datetime] = None


class DeviceListResponse(BaseModel):
    """Registered devices response."""

    items: List[DeviceAnalyticsSummary]
    total_count: int
