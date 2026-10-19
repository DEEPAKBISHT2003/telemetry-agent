"""Canonical AI Operation Accounting Data Model.

Defines the normalized operation-level accounting record for tracking,
correlating, and reporting every observable AI credit expenditure.
"""

from dataclasses import asdict, dataclass, field
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import json
from typing import Any, Dict, List, Optional
import uuid


class OperationType:
    """Canonical AI Operation Types."""
    PROMPT = "prompt"
    QUERY = "query"
    AGENT_HOOK = "agent_hook"
    SCHEDULED_HOOK = "scheduled_hook"
    TOOL_TRIGGERED = "tool_triggered"
    SUBAGENT = "subagent"
    RETRY = "retry"
    WORKFLOW = "workflow"
    SESSION_SUMMARY = "session_summary"
    ACCOUNT_SUMMARY = "account_summary"
    OTHER = "other"

    @classmethod
    def all_types(cls) -> set:
        return {
            cls.PROMPT,
            cls.QUERY,
            cls.AGENT_HOOK,
            cls.SCHEDULED_HOOK,
            cls.TOOL_TRIGGERED,
            cls.SUBAGENT,
            cls.RETRY,
            cls.WORKFLOW,
            cls.SESSION_SUMMARY,
            cls.ACCOUNT_SUMMARY,
            cls.OTHER,
        }


class TriggerType:
    """Initiation trigger types for AI operations."""
    MANUAL = "manual"
    USER_INITIATED = "user_initiated"
    EVENT_TRIGGERED = "event_triggered"
    SCHEDULED = "scheduled"
    HOOK_SCHEDULED = "scheduled"
    HOOK_AUTOMATED = "event_triggered"
    AGENT_SPAWNED = "agent_spawned"
    AUTOMATED = "automated"
    UNKNOWN = "unknown"

    @classmethod
    def all_triggers(cls) -> set:
        return {
            cls.MANUAL,
            cls.USER_INITIATED,
            cls.EVENT_TRIGGERED,
            cls.SCHEDULED,
            cls.HOOK_SCHEDULED,
            cls.HOOK_AUTOMATED,
            cls.AGENT_SPAWNED,
            cls.AUTOMATED,
            cls.UNKNOWN,
        }


class MeasurementScope:
    """Scope level at which credit usage was measured."""
    OPERATION = "operation"
    REQUEST = "request"
    SESSION = "session"
    ACCOUNT = "account"


class MeasurementStatus:
    """Confidence and verification status of credit measurement."""
    MEASURED = "measured"
    PENDING = "pending"
    UNAVAILABLE = "unavailable"
    ESTIMATED = "estimated"
    RECONCILED = "reconciled"


class ExecutionStatus:
    """Outcome status of the underlying AI operation."""
    SUCCESS = "success"
    FAILURE = "failure"
    CANCELLED = "cancelled"
    INCOMPLETE = "incomplete"
    UNKNOWN = "unknown"


def to_decimal(val: Any) -> Optional[Decimal]:
    """Convert input to Decimal safely preserving precision without binary float distortion."""
    if val is None:
        return None
    if isinstance(val, Decimal):
        return val
    try:
        return Decimal(str(val).strip())
    except (InvalidOperation, ValueError, TypeError):
        return None


def quantize_credits(val: Optional[Decimal], places: int = 6) -> Optional[Decimal]:
    """Quantize credit value to specified decimal places using ROUND_HALF_UP."""
    if val is None:
        return None
    fmt = Decimal("10") ** -places
    return val.quantize(fmt, rounding=ROUND_HALF_UP)


def sum_credits(credits_list: List[Optional[Decimal]]) -> Decimal:
    """Sum a sequence of credit values in decimal-safe arithmetic."""
    total = Decimal("0")
    for c in credits_list:
        if c is not None:
            total += c
    return total


@dataclass
class AIOperationRecord:
    """Canonical operation-level accounting record for observable AI credit expenditures."""

    # Stable unique identifier for this operation
    operation_id: str
    
    # Provider identity (e.g. kiro_cli, kiro_ide, antigravity)
    provider: str
    
    # Source integration that emitted or extracted the record (e.g. session_json, messages_jsonl, hook)
    source: str
    
    # Type of operation: prompt, query, agent_hook, scheduled_hook, tool_triggered, subagent, retry, workflow, other
    operation_type: str = OperationType.PROMPT
    
    # Trigger mechanism: manual, user_initiated, event_triggered, scheduled, agent_spawned, unknown
    trigger_type: str = TriggerType.USER_INITIATED
    
    # Stable source-derived key preventing duplicate credit counting
    deduplication_key: str = ""
    
    # Scope at which credit usage was measured: operation, request, session, account
    measurement_scope: str = MeasurementScope.OPERATION
    
    # Measurement confidence: measured, pending, unavailable, estimated, reconciled
    measurement_status: str = MeasurementStatus.MEASURED
    
    # Execution outcome: success, failure, cancelled, incomplete, unknown
    execution_status: str = ExecutionStatus.SUCCESS

    # Parent correlation
    parent_operation_id: Optional[str] = None
    parent_session_id: Optional[str] = None

    # Source identifiers
    session_id: Optional[str] = None
    request_id: Optional[str] = None
    trace_id: Optional[str] = None

    # Hook metadata
    hook_id: Optional[str] = None
    hook_name: Optional[str] = None

    # Model specifications
    model_requested: Optional[str] = None
    model_resolved: Optional[str] = None

    # Timezone-aware UTC ISO-8601 timestamps
    started_at: Optional[str] = None
    completed_at: Optional[str] = None

    # Authoritative credit expenditure (decimal-safe)
    credits_used: Optional[Decimal] = None
    credit_source: Optional[str] = None

    # Token counts: real reported values only, unknown must remain None
    token_usage: Optional[Dict[str, Optional[int]]] = None

    # Optional monetary cost estimate (clearly distinct from authoritative credits)
    cost_estimate_usd: Optional[Decimal] = None

    # Estimated credit allocation for unmeasured hooks correlated with measured runs
    estimated_credits: Optional[Decimal] = None
    allocation_method: Optional[str] = None
    correlation_status: Optional[str] = None
    correlation_reason: Optional[str] = None

    # Attribution & Context
    jira_key: Optional[str] = None
    repository: Optional[Dict[str, Any]] = None
    branch: Optional[str] = None
    developer_id: Optional[str] = None
    device_id: Optional[str] = None

    # Additional provenance / diagnostic metadata
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not self.operation_id:
            self.operation_id = str(uuid.uuid4())
        if not self.deduplication_key:
            self.deduplication_key = f"{self.provider}:{self.source}:{self.operation_id}"
        if self.credits_used is not None and not isinstance(self.credits_used, Decimal):
            self.credits_used = to_decimal(self.credits_used)
        if self.cost_estimate_usd is not None and not isinstance(self.cost_estimate_usd, Decimal):
            self.cost_estimate_usd = to_decimal(self.cost_estimate_usd)
        if self.estimated_credits is not None and not isinstance(self.estimated_credits, Decimal):
            self.estimated_credits = to_decimal(self.estimated_credits)

    @property
    def has_measured_credits(self) -> bool:
        """Returns True if authoritative numeric credits are present."""
        return self.credits_used is not None and self.measurement_status in (
            MeasurementStatus.MEASURED,
            MeasurementStatus.RECONCILED,
        )

    def to_dict(self) -> Dict[str, Any]:
        """Convert to JSON-serializable dictionary with decimal preservation."""
        res: Dict[str, Any] = {
            "operation_id": self.operation_id,
            "parent_operation_id": self.parent_operation_id,
            "provider": self.provider,
            "source": self.source,
            "operation_type": self.operation_type,
            "trigger_type": self.trigger_type,
            "hook_id": self.hook_id,
            "hook_name": self.hook_name,
            "session_id": self.session_id,
            "request_id": self.request_id,
            "trace_id": self.trace_id,
            "parent_session_id": self.parent_session_id,
            "model_requested": self.model_requested,
            "model_resolved": self.model_resolved,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "credits_used": float(self.credits_used) if self.credits_used is not None else None,
            "credits_used_exact": str(self.credits_used) if self.credits_used is not None else None,
            "credit_source": self.credit_source,
            "measurement_scope": self.measurement_scope,
            "measurement_status": self.measurement_status,
            "token_usage": self.token_usage,
            "cost_estimate_usd": float(self.cost_estimate_usd) if self.cost_estimate_usd is not None else None,
            "estimated_credits": float(self.estimated_credits) if self.estimated_credits is not None else None,
            "estimated_credits_exact": str(self.estimated_credits) if self.estimated_credits is not None else None,
            "allocation_method": self.allocation_method,
            "correlation_status": self.correlation_status,
            "correlation_reason": self.correlation_reason,
            "jira_key": self.jira_key,
            "repository": self.repository,
            "branch": self.branch,
            "developer_id": self.developer_id,
            "device_id": self.device_id,
            "execution_status": self.execution_status,
            "deduplication_key": self.deduplication_key,
            "metadata": self.metadata,
        }
        return res

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AIOperationRecord":
        """Deserialize from dictionary, restoring exact Decimal credit value."""
        credits_raw = data.get("credits_used_exact")
        if credits_raw is None:
            credits_raw = data.get("credits_used")
        credits_decimal = to_decimal(credits_raw)

        cost_raw = data.get("cost_estimate_usd")
        cost_decimal = to_decimal(cost_raw)

        est_raw = data.get("estimated_credits_exact")
        if est_raw is None:
            est_raw = data.get("estimated_credits")
        est_decimal = to_decimal(est_raw)

        op_type = data.get("operation_type", OperationType.PROMPT)
        metadata = dict(data.get("metadata") or {})

        # Read-compatibility normalization for legacy whole-turn retry records:
        # If an operation is marked as 'retry' but was actually a prompt turn from session_json
        # (identified by turn metadata like total_request_count, turn_index, or _turn_ in operation_id),
        # normalize operation_type to PROMPT and represent retry activity cleanly in metadata.
        t_reqs = metadata.get("total_request_count")
        r_attempts = metadata.get("request_attempts")
        has_turn_evidence = (
            metadata.get("turn_index") is not None
            or t_reqs is not None
            or "_turn_" in data.get("operation_id", "")
        )
        has_retries = bool(
            metadata.get("has_retries")
            or (t_reqs is not None and r_attempts is not None and r_attempts > t_reqs)
        )
        if (op_type == OperationType.RETRY or op_type == "retry") and has_turn_evidence:
            op_type = OperationType.PROMPT
            metadata["has_retries"] = has_retries
            if t_reqs is not None and r_attempts is not None and "retry_attempts" not in metadata:
                metadata["retry_attempts"] = max(0, r_attempts - t_reqs)
            metadata["is_retry"] = False
            metadata["retry_credits"] = None
        elif has_retries:
            metadata["has_retries"] = True
            if t_reqs is not None and r_attempts is not None and "retry_attempts" not in metadata:
                metadata["retry_attempts"] = max(0, r_attempts - t_reqs)
            metadata["is_retry"] = False
            if "retry_credits" not in metadata:
                metadata["retry_credits"] = None

        return cls(
            operation_id=data.get("operation_id", ""),
            parent_operation_id=data.get("parent_operation_id"),
            provider=data.get("provider", "unknown"),
            source=data.get("source", "unknown"),
            operation_type=op_type,
            trigger_type=data.get("trigger_type", TriggerType.UNKNOWN),
            hook_id=data.get("hook_id"),
            hook_name=data.get("hook_name"),
            session_id=data.get("session_id"),
            request_id=data.get("request_id"),
            trace_id=data.get("trace_id"),
            parent_session_id=data.get("parent_session_id"),
            model_requested=data.get("model_requested"),
            model_resolved=data.get("model_resolved"),
            started_at=data.get("started_at"),
            completed_at=data.get("completed_at"),
            credits_used=credits_decimal,
            credit_source=data.get("credit_source"),
            measurement_scope=data.get("measurement_scope", MeasurementScope.OPERATION),
            measurement_status=data.get("measurement_status", MeasurementStatus.MEASURED),
            token_usage=data.get("token_usage"),
            cost_estimate_usd=cost_decimal,
            estimated_credits=est_decimal,
            allocation_method=data.get("allocation_method"),
            correlation_status=data.get("correlation_status"),
            correlation_reason=data.get("correlation_reason"),
            jira_key=data.get("jira_key"),
            repository=data.get("repository"),
            branch=data.get("branch"),
            developer_id=data.get("developer_id"),
            device_id=data.get("device_id"),
            execution_status=data.get("execution_status", ExecutionStatus.SUCCESS),
            deduplication_key=data.get("deduplication_key", ""),
            metadata=metadata,
        )
