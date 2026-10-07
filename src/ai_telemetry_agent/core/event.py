"""Standardized Telemetry Event Model and AI Telemetry Schema for AI Telemetry Agent."""

import datetime
import json
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


# Canonical Event Types
class EventType:
    # Collector events
    COLLECTOR_STARTED = "collector_started"
    COLLECTOR_STOPPED = "collector_stopped"
    COLLECTOR_ERROR = "collector_error"
    COLLECTOR_HEARTBEAT = "collector_heartbeat"

    # Developer / session events
    DEVELOPER_DETECTED = "developer_detected"
    SESSION_STARTED = "session_started"
    SESSION_ENDED = "session_ended"

    # Git events
    GIT_REPOSITORY_DETECTED = "git_repository_detected"
    GIT_BRANCH_CHANGED = "git_branch_changed"
    GIT_COMMIT_DETECTED = "git_commit_detected"

    # AI events (Phase 1, Phase 2, Phase 2.5, Phase 2.7)
    AI_REQUEST = "ai_request"
    AI_RESPONSE = "ai_response"
    AI_RUN = "ai_run"
    AI_RUN_STARTED = "ai_run_started"
    AI_RUN_COMPLETED = "ai_run_completed"
    AI_RUN_FAILED = "ai_run_failed"
    AI_TURN = "ai_turn"
    AI_TOOL_CALL = "ai_tool_call"
    TOOL_CALL = "tool_call"
    AGENT_STARTED = "agent_started"
    AGENT_COMPLETED = "agent_completed"
    ANTIGRAVITY_DETECTED = "antigravity_detected"

    @classmethod
    def all_types(cls) -> set:
        return {
            cls.COLLECTOR_STARTED, cls.COLLECTOR_STOPPED, cls.COLLECTOR_ERROR, cls.COLLECTOR_HEARTBEAT,
            cls.DEVELOPER_DETECTED, cls.SESSION_STARTED, cls.SESSION_ENDED,
            cls.GIT_REPOSITORY_DETECTED, cls.GIT_BRANCH_CHANGED, cls.GIT_COMMIT_DETECTED,
            cls.AI_REQUEST, cls.AI_RESPONSE, cls.AI_RUN,
            cls.AI_RUN_STARTED, cls.AI_RUN_COMPLETED, cls.AI_RUN_FAILED,
            cls.AI_TURN, cls.AI_TOOL_CALL, cls.TOOL_CALL,
            cls.AGENT_STARTED, cls.AGENT_COMPLETED, cls.ANTIGRAVITY_DETECTED,
        }


@dataclass
class CollectorInfo:
    version: str
    device_id: str


@dataclass
class IdentityInfo:
    member_id: str


@dataclass
class RepositoryInfo:
    name: Optional[str] = None
    branch: Optional[str] = None
    root: Optional[str] = None
    remote_url: Optional[str] = None
    is_git_repo: bool = True

    def to_dict(self, include_local_root: bool = True) -> Dict[str, Any]:
        d = {
            "name": self.name,
            "branch": self.branch,
            "remote_url": self.remote_url,
            "is_git_repo": self.is_git_repo,
        }
        if include_local_root:
            d["root"] = self.root
        return {k: v for k, v in d.items() if v is not None}


@dataclass
class ContextInfo:
    session_id: Optional[str] = None
    project_id: Optional[str] = None
    jira_task_id: Optional[str] = None
    repository: Optional[RepositoryInfo] = None

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "session_id": self.session_id,
            "project_id": self.project_id,
            "jira_task_id": self.jira_task_id,
        }
        if self.repository:
            d["repository"] = self.repository.to_dict() if isinstance(self.repository, RepositoryInfo) else self.repository
        return d


@dataclass
class AITelemetryPayload:
    """Standard AI event payload.

    IMPORTANT: If the source does not expose a value, store None (null in JSON).
    Never estimate, fabricate, or infer token counts or cost.
    """
    provider: Optional[str] = None
    model: Optional[str] = None
    session_id: Optional[str] = None
    run_id: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    duration_ms: Optional[float] = None
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    cache_read_tokens: Optional[int] = None
    cache_write_tokens: Optional[int] = None
    cache_tokens: Optional[int] = None
    total_tokens: Optional[int] = None
    cost_usd: Optional[float] = None
    turns: Optional[int] = None
    tool_name: Optional[str] = None
    agent_name: Optional[str] = None
    capture_method: Optional[str] = None
    tools: List[Dict[str, Any]] = field(default_factory=list)
    agents: List[Dict[str, Any]] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class TelemetryEvent:
    """Standard standardized event envelope."""
    event_id: str
    event_type: str
    timestamp: str
    collector: CollectorInfo
    identity: IdentityInfo
    context: ContextInfo = field(default_factory=ContextInfo)
    payload: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "timestamp": self.timestamp,
            "collector": asdict(self.collector),
            "identity": asdict(self.identity),
            "context": self.context.to_dict(),
            "payload": self.payload,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TelemetryEvent":
        collector_data = data.get("collector", {})
        identity_data = data.get("identity", {})
        context_data = data.get("context", {})

        repo_data = context_data.get("repository")
        repo_info = None
        if isinstance(repo_data, dict):
            repo_info = RepositoryInfo(
                name=repo_data.get("name"),
                branch=repo_data.get("branch"),
                root=repo_data.get("root"),
                remote_url=repo_data.get("remote_url"),
                is_git_repo=repo_data.get("is_git_repo", True),
            )
        elif isinstance(repo_data, RepositoryInfo):
            repo_info = repo_data

        return cls(
            event_id=data.get("event_id", ""),
            event_type=data.get("event_type", ""),
            timestamp=data.get("timestamp", ""),
            collector=CollectorInfo(
                version=collector_data.get("version", "0.1.0"),
                device_id=collector_data.get("device_id", "")
            ),
            identity=IdentityInfo(
                member_id=identity_data.get("member_id", "")
            ),
            context=ContextInfo(
                session_id=context_data.get("session_id"),
                project_id=context_data.get("project_id"),
                jira_task_id=context_data.get("jira_task_id"),
                repository=repo_info,
            ),
            payload=data.get("payload", {}) or {}
        )
