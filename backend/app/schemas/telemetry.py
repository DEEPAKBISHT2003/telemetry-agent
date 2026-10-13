"""Pydantic schemas for telemetry batch ingestion, event validation, and responses."""

from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional
import uuid

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ClientRepositoryInfo(BaseModel):
    """Contextual Git repository metadata submitted by the client workstation."""

    model_config = ConfigDict(extra="ignore")

    name: Optional[str] = Field(default=None, max_length=255, description="Repository name")
    branch: Optional[str] = Field(default=None, max_length=255, description="Git branch name")
    remote_url: Optional[str] = Field(default=None, max_length=512, description="Git remote URL")


class ClientContextInfo(BaseModel):
    """Contextual metadata surrounding the execution event."""

    model_config = ConfigDict(extra="ignore")

    session_id: Optional[str] = Field(default=None, max_length=128, description="IDE or AI session ID")
    project_id: Optional[str] = Field(default=None, max_length=128, description="Project identifier")
    jira_task_id: Optional[str] = Field(default=None, max_length=128, description="Jira task identifier")
    repository: Optional[ClientRepositoryInfo] = Field(default=None, description="Repository metadata")


class ClientEventPayload(BaseModel):
    """Structured telemetry metrics payload submitted by client agent."""

    model_config = ConfigDict(extra="allow")

    provider: Optional[str] = Field(default=None, max_length=64, description="AI model provider")
    model: Optional[str] = Field(default=None, max_length=128, description="Model identifier")
    session_id: Optional[str] = Field(default=None, max_length=128, description="AI session ID")
    run_id: Optional[str] = Field(default=None, max_length=128, description="Run identifier")
    started_at: Optional[str] = Field(default=None, max_length=64, description="Run start timestamp")
    completed_at: Optional[str] = Field(default=None, max_length=64, description="Run completion timestamp")
    duration_ms: Optional[float] = Field(default=None, ge=0.0, description="Duration in milliseconds")
    input_tokens: Optional[int] = Field(default=None, ge=0, description="Uncached prompt token count")
    output_tokens: Optional[int] = Field(default=None, ge=0, description="Completion token count")
    cache_read_tokens: Optional[int] = Field(default=None, ge=0, description="Cached prompt token count")
    cache_write_tokens: Optional[int] = Field(default=None, ge=0, description="Tokens written to prompt cache")
    total_tokens: Optional[int] = Field(default=None, ge=0, description="Total tokens consumed")
    cost_usd: Optional[Decimal] = Field(default=None, ge=Decimal(0), description="Authoritative USD cost")
    turns: Optional[int] = Field(default=None, ge=0, description="Conversation turn index or count")
    tool_name: Optional[str] = Field(default=None, max_length=128, description="Tool name if tool call")
    agent_name: Optional[str] = Field(default=None, max_length=128, description="Agent name if multi-agent")
    capture_method: Optional[str] = Field(default=None, max_length=64, description="Telemetry capture mechanism")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Custom metric metadata")


class ClientTelemetryEvent(BaseModel):
    """Individual client telemetry event envelope submitted in a batch."""

    model_config = ConfigDict(extra="allow")

    event_id: uuid.UUID = Field(..., description="Unique event UUID")
    event_type: str = Field(..., min_length=1, max_length=64, description="Canonical event type")
    timestamp: datetime = Field(..., description="UTC ISO 8601 event timestamp")
    collector: Optional[Dict[str, Any]] = Field(default=None, description="Collector version info (ignored for attribution)")
    identity: Optional[Dict[str, Any]] = Field(default=None, description="Client identity claims (ignored for attribution)")
    context: Optional[ClientContextInfo] = Field(default=None, description="Contextual session and repository info")
    payload: Optional[ClientEventPayload] = Field(default=None, description="Telemetry metrics payload")

    @field_validator("event_type")
    @classmethod
    def validate_event_type(cls, v: str) -> str:
        cleaned = v.strip().lower()
        if not cleaned:
            raise ValueError("event_type cannot be empty")
        return cleaned


class TelemetryBatchRequest(BaseModel):
    """Batch ingestion request envelope submitted by enrolled developer device."""

    model_config = ConfigDict(extra="forbid")

    batch_id: uuid.UUID = Field(..., description="Client-generated unique batch UUID for idempotency")
    client_sent_at: datetime = Field(..., description="Client dispatch timestamp")
    events: List[ClientTelemetryEvent] = Field(
        ...,
        min_length=1,
        max_length=500,
        description="List of telemetry events (1 to 500 events per batch)",
    )


class TelemetryBatchResponse(BaseModel):
    """Structured response acknowledging telemetry batch processing."""

    model_config = ConfigDict(from_attributes=True)

    batch_id: uuid.UUID = Field(..., description="Batch UUID")
    status: str = Field(..., description="Batch processing status (PROCESSED or ALREADY_PROCESSED)")
    submitted_count: int = Field(..., description="Total number of events submitted in the batch")
    newly_persisted_count: int = Field(..., description="Number of events newly committed to the database")
    duplicate_count: int = Field(..., description="Number of events already existing in the database")
    persisted_event_ids: List[uuid.UUID] = Field(
        default_factory=list,
        description="UUIDs of newly persisted events",
    )
    message: Optional[str] = Field(default=None, description="Status detail message")
