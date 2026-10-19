"""Data models for Jira ticket attribution."""

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
from typing import Any, Dict, List, Optional


def now_utc_iso() -> str:
    """Return current UTC time as ISO-8601 string."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class JiraAttributionError(ValueError):
    """Base exception for Jira attribution failures."""
    pass


class JiraIssueNotFoundError(JiraAttributionError):
    """Raised when an issue is verified to not exist in Jira (HTTP 404)."""

    def __init__(self, key: str, message: str):
        super().__init__(message)
        self.key = key
        self.message = message


class JiraUnreachableError(JiraAttributionError):
    """Raised when Jira is configured but cannot be reached or authenticated, and unverified selection is not explicitly permitted."""

    def __init__(self, key: str, reason: str, status: str):
        super().__init__(reason)
        self.key = key
        self.reason = reason
        self.status = status


class JiraUnconfiguredError(JiraAttributionError):
    """Raised when Jira is unconfigured and unverified selection is not explicitly permitted."""

    def __init__(self, key: str, message: Optional[str] = None):
        msg = message or f"Jira integration is unconfigured (JIRA_BASE_URL is not set). Ticket existence for '{key}' cannot be verified."
        super().__init__(msg)
        self.key = key
        self.message = msg
        self.status = "unconfigured"


@dataclass
class TicketAttribution:
    """Represents an association between a Jira issue and a repository working context."""
    id: str
    issue_key: str
    repository_name: Optional[str] = None
    repository_root: Optional[str] = None
    branch: Optional[str] = None
    source: str = "manual"  # "manual", "branch", "remembered"
    start_time: str = field(default_factory=now_utc_iso)
    end_time: Optional[str] = None
    confirmation_state: str = "confirmed"  # "confirmed", "proposed", "unassigned" (user confirmation)
    validation_status: str = "unverified"   # "verified", "unverified", "rejected" (Jira existence validation)
    validation_message: Optional[str] = None
    issue_title: Optional[str] = None
    issue_status: Optional[str] = None
    session_ids: List[str] = field(default_factory=list)
    created_at: str = field(default_factory=now_utc_iso)
    updated_at: str = field(default_factory=now_utc_iso)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TicketAttribution":
        return cls(
            id=data["id"],
            issue_key=data["issue_key"].upper(),
            repository_name=data.get("repository_name"),
            repository_root=data.get("repository_root"),
            branch=data.get("branch"),
            source=data.get("source", "manual"),
            start_time=data.get("start_time", now_utc_iso()),
            end_time=data.get("end_time"),
            confirmation_state=data.get("confirmation_state", "confirmed"),
            validation_status=data.get("validation_status", "unverified"),
            validation_message=data.get("validation_message"),
            issue_title=data.get("issue_title"),
            issue_status=data.get("issue_status"),
            session_ids=list(data.get("session_ids", [])),
            created_at=data.get("created_at", now_utc_iso()),
            updated_at=data.get("updated_at", now_utc_iso()),
            metadata=dict(data.get("metadata", {})),
        )


@dataclass
class BranchDetectionResult:
    """Result of inspecting a branch name for Jira issue keys."""
    keys: List[str]
    suggested_key: Optional[str] = None
    status: str = "none"  # "single", "none", "conflict", "error"
    branch: Optional[str] = None
    error: Optional[str] = None


@dataclass
class TicketSummary:
    """Aggregated telemetry usage summary for a Jira ticket or unassigned work."""
    issue_key: str
    title: Optional[str] = None
    status: Optional[str] = None
    repositories: List[str] = field(default_factory=list)
    branches: List[str] = field(default_factory=list)
    sessions_count: int = 0
    ai_runs_count: int = 0
    total_credits: float = 0.0
    has_credit_data: bool = False
    credit_unit: Optional[str] = "credit"
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    cache_read_tokens: Optional[int] = None
    total_tokens: Optional[int] = None
    has_token_data: bool = False
    duration_seconds: float = 0.0
    source: str = "none"
    confirmation_state: str = "confirmed"
    session_ids: List[str] = field(default_factory=list)
    hook_executions_count: int = 0
    has_estimated_credits: bool = False
    estimated_credits: float = 0.0
    estimated_credits_exact: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
