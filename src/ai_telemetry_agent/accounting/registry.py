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
from typing import Any, Dict, List, Optional, Set, Union
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
from ai_telemetry_agent.config.settings import resolve_data_dir


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
    parent_operation_id: Optional[str] = None
    jira_key: Optional[str] = None
    credits_used: Optional[Decimal] = None
    credit_source: Optional[str] = None
    measurement_status: str = MeasurementStatus.UNAVAILABLE
    estimated_credits: Optional[Decimal] = None
    allocation_method: Optional[str] = None
    correlation_status: Optional[str] = None
    correlation_reason: Optional[str] = None
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
            "parent_operation_id": self.parent_operation_id,
            "jira_key": self.jira_key,
            "credits_used": float(self.credits_used) if self.credits_used is not None else None,
            "credits_used_exact": str(self.credits_used) if self.credits_used is not None else None,
            "credit_source": self.credit_source,
            "measurement_status": self.measurement_status,
            "estimated_credits": float(self.estimated_credits) if self.estimated_credits is not None else None,
            "estimated_credits_exact": str(self.estimated_credits) if self.estimated_credits is not None else None,
            "allocation_method": self.allocation_method,
            "correlation_status": self.correlation_status,
            "correlation_reason": self.correlation_reason,
            "schedule_expression": self.schedule_expression,
            "error_message": self.error_message,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "HookExecutionRecord":
        creds = data.get("credits_used_exact")
        if creds is None:
            creds = data.get("credits_used")
        est_creds = data.get("estimated_credits_exact")
        if est_creds is None:
            est_creds = data.get("estimated_credits")
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
            parent_operation_id=data.get("parent_operation_id"),
            jira_key=data.get("jira_key"),
            credits_used=to_decimal(creds),
            credit_source=data.get("credit_source"),
            measurement_status=data.get("measurement_status", MeasurementStatus.UNAVAILABLE),
            estimated_credits=to_decimal(est_creds),
            allocation_method=data.get("allocation_method"),
            correlation_status=data.get("correlation_status"),
            correlation_reason=data.get("correlation_reason"),
            schedule_expression=data.get("schedule_expression"),
            error_message=data.get("error_message"),
            metadata=data.get("metadata", {}),
        )


class HookRegistry:
    """Thread-safe registry for managing hook configurations and execution histories."""

    def __init__(self, data_dir: Optional[Union[str, Path]] = None):
        self.data_dir = resolve_data_dir(data_dir)
        self.registry_file = self.data_dir / "hook_registry.json"

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
        parent_operation_id: Optional[str] = None,
        jira_key: Optional[str] = None,
        credits_used: Optional[Any] = None,
        credit_source: Optional[str] = None,
        measurement_status: Optional[str] = None,
        estimated_credits: Optional[Any] = None,
        allocation_method: Optional[str] = None,
        correlation_status: Optional[str] = None,
        correlation_reason: Optional[str] = None,
        schedule_expression: Optional[str] = None,
        executed_at: Optional[str] = None,
        error_message: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        deduplication_key: Optional[str] = None,
    ) -> HookExecutionRecord:
        """Record an individual execution of a hook with duplicate prevention."""
        with self._lock:
            meta = dict(metadata or {})
            dedup_key = deduplication_key or meta.get("deduplication_key")

            # Duplicate-event prevention across repeated hook calls
            if dedup_key:
                for existing in self._executions:
                    if existing.metadata.get("deduplication_key") == dedup_key:
                        if jira_key and not existing.jira_key:
                            existing.jira_key = jira_key
                        if parent_operation_id and not existing.parent_operation_id:
                            existing.parent_operation_id = parent_operation_id
                        if correlation_status and not existing.correlation_status:
                            existing.correlation_status = correlation_status
                        if correlation_reason and not existing.correlation_reason:
                            existing.correlation_reason = correlation_reason
                        self._save()
                        return existing

            if resulting_operation_id:
                for existing in self._executions:
                    if (
                        existing.resulting_operation_id == resulting_operation_id
                        and existing.hook_id == hook_id
                    ):
                        if jira_key and not existing.jira_key:
                            existing.jira_key = jira_key
                        if parent_operation_id and not existing.parent_operation_id:
                            existing.parent_operation_id = parent_operation_id
                        if correlation_status and not existing.correlation_status:
                            existing.correlation_status = correlation_status
                        if correlation_reason and not existing.correlation_reason:
                            existing.correlation_reason = correlation_reason
                        self._save()
                        return existing

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

            if dedup_key:
                meta["deduplication_key"] = dedup_key

            # Explicit dynamic registration for observed lifecycle hooks
            if hook_id not in self._hooks:
                self._hooks[hook_id] = HookRegistration(
                    hook_id=hook_id,
                    hook_name=hook_name,
                    trigger_type=trigger_type,
                    schedule_expression=schedule_expression,
                    is_ai_powered=initiates_ai or not is_shell_only,
                    provider=provider,
                    metadata={"dynamically_registered": True, "first_observed_at": ts},
                )

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
                parent_operation_id=parent_operation_id,
                jira_key=jira_key,
                credits_used=dec_credits,
                credit_source=credit_source,
                measurement_status=status,
                estimated_credits=to_decimal(estimated_credits),
                allocation_method=allocation_method,
                correlation_status=correlation_status,
                correlation_reason=correlation_reason,
                schedule_expression=schedule_expression,
                error_message=error_message,
                metadata=meta,
            )
            self._executions.append(rec)
            self._save()
            return rec

    def allocate_estimated_credits(
        self,
        accounting_store: Optional[Any] = None,
        allocation_method: str = "even_split_by_run",
    ) -> int:
        """Allocate estimated credits to unmeasured hook executions correlated to measured parent runs.

        Guarantees:
        - Keeps actual measured credits separate from hook estimates (credits_used is NOT modified).
        - Requires a reliably correlated parent operation with measured credits.
        - Parent operations with missing credits (None) do NOT produce fabricated estimates.
        - Multiple hooks on one run divide the parent credits so total estimates do NOT multiply actual credits.
        - Documents allocation_method on each record.
        - Never infers parent relationships from timestamp proximity, directory names, or unrelated session IDs.
        - Preserves unassigned / ineligible / ambiguous states with explicit correlation_reason.
        - Updates persisted registry records and matching accounting store operation records safely.

        Returns:
            Number of hook executions allocated estimates.
        """
        if not accounting_store:
            return 0

        from ai_telemetry_agent.accounting.models import quantize_credits

        with self._lock:
            target_hooks = [e for e in self._executions if not e.is_shell_only and e.credits_used is None]
            if not target_hooks:
                return 0

            # Eligible hooks grouped by verified parent operation ID
            eligible_by_parent: Dict[str, List[HookExecutionRecord]] = {}

            for h in target_hooks:
                # 1. Resolve candidate parent operation ID
                parent_id = h.parent_operation_id
                if not parent_id and h.metadata:
                    parent_id = h.metadata.get("parent_operation_id") or h.metadata.get("parent_run_id")
                    if parent_id:
                        h.parent_operation_id = parent_id

                if not parent_id and h.resulting_operation_id:
                    # Check if the hook's operation record has a parent_operation_id or is an explicitly linked measured parent run
                    hook_op = None
                    if hasattr(accounting_store, "get_operation_by_id"):
                        hook_op = accounting_store.get_operation_by_id(h.resulting_operation_id)
                    elif hasattr(accounting_store, "get_operation"):
                        hook_op = accounting_store.get_operation(h.resulting_operation_id)
                    if hook_op:
                        if hook_op.parent_operation_id:
                            parent_id = hook_op.parent_operation_id
                            h.parent_operation_id = parent_id
                        elif hook_op.has_measured_credits and hook_op.operation_type not in (
                            OperationType.TOOL_TRIGGERED,
                            OperationType.AGENT_HOOK,
                            OperationType.SCHEDULED_HOOK,
                        ):
                            parent_id = hook_op.operation_id
                            h.parent_operation_id = parent_id

                # Ambiguous correlation check
                if h.metadata.get("ambiguous_correlation") or h.metadata.get("ambiguous") or h.correlation_status == "ambiguous":
                    h.estimated_credits = None
                    h.allocation_method = None
                    h.correlation_status = "ambiguous"
                    h.correlation_reason = "ambiguous_correlation"
                    self._sync_hook_to_store(h, accounting_store)
                    continue

                # Missing parent operation ID
                if not parent_id:
                    h.estimated_credits = None
                    h.allocation_method = None
                    h.correlation_status = "ineligible"
                    h.correlation_reason = "missing_parent_operation"
                    self._sync_hook_to_store(h, accounting_store)
                    continue

                # 2. Look up parent operation
                parent_op = None
                if hasattr(accounting_store, "get_operation_by_id"):
                    parent_op = accounting_store.get_operation_by_id(parent_id)
                elif hasattr(accounting_store, "get_operation"):
                    parent_op = accounting_store.get_operation(parent_id)

                if not parent_op:
                    h.estimated_credits = None
                    h.allocation_method = None
                    h.correlation_status = "ineligible"
                    h.correlation_reason = "missing_parent_operation"
                    self._sync_hook_to_store(h, accounting_store)
                    continue

                # Check if hook attempts to use an unmeasured hook operation as its parent (self-referential)
                if parent_id == h.resulting_operation_id and parent_op.operation_type in (
                    OperationType.TOOL_TRIGGERED,
                    OperationType.AGENT_HOOK,
                    OperationType.SCHEDULED_HOOK,
                ):
                    h.estimated_credits = None
                    h.allocation_method = None
                    h.correlation_status = "ineligible"
                    h.correlation_reason = "missing_parent_operation"
                    self._sync_hook_to_store(h, accounting_store)
                    continue

                # 3. Check measured credits on parent
                if (
                    not parent_op.has_measured_credits
                    or parent_op.credits_used is None
                    or parent_op.credits_used <= Decimal("0")
                ):
                    h.estimated_credits = None
                    h.allocation_method = None
                    h.correlation_status = "ineligible"
                    h.correlation_reason = "parent_unmeasured"
                    self._sync_hook_to_store(h, accounting_store)
                    continue

                # 4. Check context compatibility (session & repository)
                if h.session_id and parent_op.session_id and h.session_id != parent_op.session_id:
                    h.estimated_credits = None
                    h.allocation_method = None
                    h.correlation_status = "ineligible"
                    h.correlation_reason = "incompatible_context"
                    self._sync_hook_to_store(h, accounting_store)
                    continue

                h_repo = h.metadata.get("repository")
                p_repo = parent_op.repository
                if h_repo and p_repo:
                    h_name = h_repo.get("name") if isinstance(h_repo, dict) else str(h_repo)
                    p_name = p_repo.get("name") if isinstance(p_repo, dict) else str(p_repo)
                    if h_name and p_name and h_name != p_name:
                        h.estimated_credits = None
                        h.allocation_method = None
                        h.correlation_status = "ineligible"
                        h.correlation_reason = "incompatible_context"
                        self._sync_hook_to_store(h, accounting_store)
                        continue

                # 5. Check Jira ticket attribution consistency
                h_tkt = (h.jira_key or "").strip().upper()
                p_tkt = (parent_op.jira_key or "").strip().upper()
                if h_tkt and h_tkt != "UNASSIGNED" and p_tkt and p_tkt != "UNASSIGNED":
                    if h_tkt != p_tkt:
                        h.estimated_credits = None
                        h.allocation_method = None
                        h.correlation_status = "ineligible"
                        h.correlation_reason = "ticket_mismatch"
                        self._sync_hook_to_store(h, accounting_store)
                        continue
                elif (not h_tkt or h_tkt == "UNASSIGNED") and p_tkt and p_tkt != "UNASSIGNED":
                    # Inherit verified parent ticket
                    h.jira_key = parent_op.jira_key

                eligible_by_parent.setdefault(parent_id, []).append(h)

            allocated_count = 0
            for parent_id, hooks_list in eligible_by_parent.items():
                parent_op = None
                if hasattr(accounting_store, "get_operation_by_id"):
                    parent_op = accounting_store.get_operation_by_id(parent_id)
                elif hasattr(accounting_store, "get_operation"):
                    parent_op = accounting_store.get_operation(parent_id)

                if not parent_op or parent_op.credits_used is None:
                    continue

                count = len(hooks_list)
                if allocation_method == "even_split_by_run":
                    split_credit = quantize_credits(parent_op.credits_used / Decimal(str(count)))
                    for h in hooks_list:
                        h.estimated_credits = split_credit
                        h.allocation_method = allocation_method
                        h.correlation_status = "correlated"
                        h.correlation_reason = "verified_parent"
                        self._sync_hook_to_store(h, accounting_store, split_credit=split_credit)
                        allocated_count += 1

            self._save()
            return allocated_count

    def _sync_hook_to_store(
        self,
        h: HookExecutionRecord,
        accounting_store: Any,
        split_credit: Optional[Decimal] = None,
    ) -> None:
        """Safely synchronize hook execution status and estimated credits back to the accounting store."""
        if not h.resulting_operation_id:
            return
        try:
            op_rec = None
            if hasattr(accounting_store, "get_operation_by_id"):
                op_rec = accounting_store.get_operation_by_id(h.resulting_operation_id)
            elif hasattr(accounting_store, "get_operation"):
                op_rec = accounting_store.get_operation(h.resulting_operation_id)

            if op_rec:
                op_rec.estimated_credits = split_credit
                op_rec.allocation_method = h.allocation_method
                op_rec.parent_operation_id = h.parent_operation_id
                op_rec.correlation_status = h.correlation_status
                op_rec.correlation_reason = h.correlation_reason
                if h.jira_key and (not op_rec.jira_key or op_rec.jira_key == "UNASSIGNED"):
                    op_rec.jira_key = h.jira_key
                accounting_store.save_operation(op_rec)
        except Exception:
            pass

    def get_diagnostics(self) -> Dict[str, Any]:
        """Compute diagnostic counts across hook executions."""
        with self._lock:
            eligible_cnt = 0
            ineligible_cnt = 0
            ambiguous_cnt = 0
            total_est = Decimal("0")
            by_reason: Dict[str, int] = {}
            parent_ids: Set[str] = set()

            for e in self._executions:
                if e.is_shell_only:
                    continue
                if e.credits_used is not None:
                    continue
                if e.correlation_status == "correlated":
                    eligible_cnt += 1
                    if e.estimated_credits is not None:
                        total_est += e.estimated_credits
                    if e.parent_operation_id:
                        parent_ids.add(e.parent_operation_id)
                elif e.correlation_status == "ambiguous":
                    ambiguous_cnt += 1
                    reason = e.correlation_reason or "ambiguous_correlation"
                    by_reason[reason] = by_reason.get(reason, 0) + 1
                else:
                    ineligible_cnt += 1
                    reason = e.correlation_reason or "missing_parent_operation"
                    by_reason[reason] = by_reason.get(reason, 0) + 1

            return {
                "eligible_count": eligible_cnt,
                "ineligible_count": ineligible_cnt,
                "ambiguous_count": ambiguous_cnt,
                "total_estimated_credits": float(total_est) if total_est > 0 else 0.0,
                "total_estimated_credits_exact": str(total_est),
                "by_reason": by_reason,
                "parent_operation_ids": sorted(list(parent_ids)),
            }

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

                # Backfill dynamic registrations for observed hooks in existing execution histories
                for rec in self._executions:
                    if rec.hook_id and rec.hook_id not in self._hooks:
                        self._hooks[rec.hook_id] = HookRegistration(
                            hook_id=rec.hook_id,
                            hook_name=rec.hook_name or rec.hook_id,
                            trigger_type=rec.trigger_type,
                            schedule_expression=rec.schedule_expression,
                            is_ai_powered=rec.initiates_ai or not rec.is_shell_only,
                            provider=rec.provider or "kiro_ide",
                            metadata={"dynamically_registered": True, "first_observed_at": rec.executed_at},
                        )
        except Exception:
            pass
