"""Hook Execution Registry and Lifecycle Tracker.

Tracks installed and observed hooks, their trigger mechanisms (manual, scheduled,
event-driven, automatic), their execution outcomes, and their correlation to AI operations.
Distinguishes shell-only hooks from AI-initiating hooks and provides auditability.
"""

from dataclasses import asdict, dataclass, field
from decimal import Decimal
import json
import os
from pathlib import Path
import threading
import time
from typing import Any, Dict, List, Optional
import uuid

from ai_telemetry_agent.accounting.models import (
    AIOperationRecord,
    ExecutionStatus,
    MeasurementScope,
    MeasurementStatus,
    OperationType,
    TriggerType,
    to_decimal,
)


@dataclass
class HookRegistration:
    """Configured hook definition."""
    hook_id: str
    hook_name: str
    trigger_type: str = TriggerType.EVENT_TRIGGERED
    event_pattern: Optional[str] = None
    schedule_expression: Optional[str] = None
    command_or_script: Optional[str] = None
    is_ai_powered: bool = False
    provider: str = "kiro_ide"
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "HookRegistration":
        return cls(**data)


@dataclass
class HookExecutionRecord:
    """Record of an individual hook execution lifecycle."""
    execution_id: str
    hook_id: str
    hook_name: str
    executed_at: str
    trigger_type: str
    provider: str
    session_id: Optional[str] = None
    outcome: str = ExecutionStatus.SUCCESS
    is_shell_only: bool = False
    initiates_ai: bool = False
    resulting_operation_id: Optional[str] = None
    credits_used: Optional[Decimal] = None
    credit_source: Optional[str] = None
    measurement_status: str = MeasurementStatus.UNAVAILABLE
    schedule_expression: Optional[str] = None
    error_message: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "execution_id": self.execution_id,
            "hook_id": self.hook_id,
            "hook_name": self.hook_name,
            "executed_at": self.executed_at,
            "trigger_type": self.trigger_type,
            "provider": self.provider,
            "session_id": self.session_id,
            "outcome": self.outcome,
            "is_shell_only": self.is_shell_only,
            "initiates_ai": self.initiates_ai,
            "resulting_operation_id": self.resulting_operation_id,
            "credits_used": float(self.credits_used) if self.credits_used is not None else None,
            "credits_used_exact": str(self.credits_used) if self.credits_used is not None else None,
            "credit_source": self.credit_source,
            "measurement_status": self.measurement_status,
            "schedule_expression": self.schedule_expression,
            "error_message": self.error_message,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "HookExecutionRecord":
        creds = data.get("credits_used_exact")
        if creds is None:
            creds = data.get("credits_used")
        return cls(
            execution_id=data.get("execution_id", ""),
            hook_id=data.get("hook_id", ""),
            hook_name=data.get("hook_name", ""),
            executed_at=data.get("executed_at", ""),
            trigger_type=data.get("trigger_type", TriggerType.EVENT_TRIGGERED),
            provider=data.get("provider", "unknown"),
            session_id=data.get("session_id"),
            outcome=data.get("outcome", ExecutionStatus.SUCCESS),
            is_shell_only=bool(data.get("is_shell_only", False)),
            initiates_ai=bool(data.get("initiates_ai", False)),
            resulting_operation_id=data.get("resulting_operation_id"),
            credits_used=to_decimal(creds),
            credit_source=data.get("credit_source"),
            measurement_status=data.get("measurement_status", MeasurementStatus.UNAVAILABLE),
            schedule_expression=data.get("schedule_expression"),
            error_message=data.get("error_message"),
            metadata=data.get("metadata", {}),
        )


class HookRegistry:
    """Thread-safe registry for managing hook configurations and execution histories."""

    def __init__(self, data_dir: Optional[Path] = None):
        if data_dir:
            self.registry_file = Path(data_dir) / "hook_registry.json"
        else:
            home = os.environ.get("TELEMETRY_AGENT_HOME")
            base = Path(home).resolve() if home else (Path.home() / ".telemetry_agent").resolve()
            self.registry_file = base / "data" / "hook_registry.json"

        self._lock = threading.Lock()
        self._hooks: Dict[str, HookRegistration] = {}
        self._executions: List[HookExecutionRecord] = []
        self._load()

    def register_hook(
        self,
        hook_id: str,
        hook_name: str,
        trigger_type: str = TriggerType.EVENT_TRIGGERED,
        event_pattern: Optional[str] = None,
        schedule_expression: Optional[str] = None,
        command_or_script: Optional[str] = None,
        is_ai_powered: bool = False,
        provider: str = "kiro_ide",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> HookRegistration:
        """Register or update a configured hook."""
        with self._lock:
            reg = HookRegistration(
                hook_id=hook_id,
                hook_name=hook_name,
                trigger_type=trigger_type,
                event_pattern=event_pattern,
                schedule_expression=schedule_expression,
                command_or_script=command_or_script,
                is_ai_powered=is_ai_powered,
                provider=provider,
                metadata=metadata or {},
            )
            self._hooks[hook_id] = reg
            self._save()
            return reg

    def record_execution(
        self,
        hook_id: str,
        hook_name: str,
        trigger_type: str,
        provider: str,
        session_id: Optional[str] = None,
        outcome: str = ExecutionStatus.SUCCESS,
        is_shell_only: bool = False,
        initiates_ai: bool = False,
        resulting_operation_id: Optional[str] = None,
        credits_used: Optional[Any] = None,
        credit_source: Optional[str] = None,
        measurement_status: Optional[str] = None,
        schedule_expression: Optional[str] = None,
        executed_at: Optional[str] = None,
        error_message: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> HookExecutionRecord:
        """Record an individual execution of a hook."""
        with self._lock:
            exec_id = str(uuid.uuid4())
            ts = executed_at or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            dec_credits = to_decimal(credits_used)

            # Determine measurement status
            if dec_credits is not None:
                status = measurement_status or MeasurementStatus.MEASURED
            elif is_shell_only:
                # Shell-only tools don't consume AI credits
                dec_credits = Decimal("0")
                status = MeasurementStatus.MEASURED
            else:
                status = measurement_status or MeasurementStatus.UNAVAILABLE

            rec = HookExecutionRecord(
                execution_id=exec_id,
                hook_id=hook_id,
                hook_name=hook_name,
                executed_at=ts,
                trigger_type=trigger_type,
                provider=provider,
                session_id=session_id,
                outcome=outcome,
                is_shell_only=is_shell_only,
                initiates_ai=initiates_ai,
                resulting_operation_id=resulting_operation_id,
                credits_used=dec_credits,
                credit_source=credit_source,
                measurement_status=status,
                schedule_expression=schedule_expression,
                error_message=error_message,
                metadata=metadata or {},
            )
            self._executions.append(rec)
            self._save()
            return rec

    def get_registered_hooks(self) -> List[HookRegistration]:
        with self._lock:
            return list(self._hooks.values())

    def get_executions(
        self,
        hook_id: Optional[str] = None,
        session_id: Optional[str] = None,
        limit: int = 100,
    ) -> List[HookExecutionRecord]:
        with self._lock:
            res = self._executions
            if hook_id:
                res = [e for e in res if e.hook_id == hook_id]
            if session_id:
                res = [e for e in res if e.session_id == session_id]
            return list(reversed(res[-limit:]))

    def _save(self) -> None:
        try:
            self.registry_file.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "version": 1,
                "hooks": {k: v.to_dict() for k, v in self._hooks.items()},
                "executions": [e.to_dict() for e in self._executions[-1000:]],
            }
            tmp = self.registry_file.parent / f"{self.registry_file.name}.tmp"
            tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            tmp.replace(self.registry_file)
        except Exception:
            pass

    def _load(self) -> None:
        if not self.registry_file.is_file():
            return
        try:
            data = json.loads(self.registry_file.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                hooks = data.get("hooks", {})
                for k, v in hooks.items():
                    if isinstance(v, dict):
                        self._hooks[k] = HookRegistration.from_dict(v)
                execs = data.get("executions", [])
                for e in execs:
                    if isinstance(e, dict):
                        self._executions.append(HookExecutionRecord.from_dict(e))
        except Exception:
            pass
