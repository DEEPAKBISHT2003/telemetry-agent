"""Accounting Aggregator for Comprehensive AI Credit Expenditure Analytics.

Computes decimal-safe aggregations across multiple operational dimensions:
provider, operation type, hook/trigger, session, model, repository, Jira ticket,
interactive vs automated, subagent, failed/retried, measurement status, and
reconciliation variance.
"""

from collections import defaultdict
from decimal import Decimal
from typing import Any, Dict, List, Optional, Set

from ai_telemetry_agent.accounting.models import (
    AIOperationRecord,
    ExecutionStatus,
    MeasurementScope,
    MeasurementStatus,
    OperationType,
    TriggerType,
    quantize_credits,
    sum_credits,
    to_decimal,
)
from ai_telemetry_agent.accounting.store import AccountingStore


class AccountingAggregator:
    """Aggregates operation accounting records with strict decimal-safe accounting."""

    def __init__(self, store: AccountingStore):
        self.store = store

    def get_summary(
        self,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        jira_key: Optional[str] = None,
        repository_name: Optional[str] = None,
        provider: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Compute full accounting summary across all dimensions."""
        ops = self.store.get_operations(
            jira_key=jira_key,
            provider=provider,
            date_from=date_from,
            date_to=date_to,
            limit=50000,
        )

        if repository_name:
            ops = [
                op for op in ops
                if op.repository and (op.repository.get("name") == repository_name
                                      or op.repository.get("root", "").endswith(repository_name))
            ]

        # Scope partitioning (Never additively combine operation scope with session or account scope!)
        operation_level_ops = [op for op in ops if op.measurement_scope in (MeasurementScope.OPERATION, MeasurementScope.REQUEST)]
        session_level_ops = [op for op in ops if op.measurement_scope == MeasurementScope.SESSION]
        account_level_ops = [op for op in ops if op.measurement_scope == MeasurementScope.ACCOUNT]

        # 1. Total Measured Operational Credits
        total_op_credits = sum_credits([op.credits_used for op in operation_level_ops])
        total_session_credits = sum_credits([op.credits_used for op in session_level_ops])
        total_account_credits = sum_credits([op.credits_used for op in account_level_ops])

        # 2. Credits by Provider
        by_provider: Dict[str, Dict[str, Any]] = defaultdict(lambda: {"credits": Decimal("0"), "count": 0, "unmeasured_count": 0})
        for op in operation_level_ops:
            entry = by_provider[op.provider]
            entry["count"] += 1
            if op.credits_used is not None:
                entry["credits"] += op.credits_used
            else:
                entry["unmeasured_count"] += 1

        # 3. Credits by Operation Type
        by_op_type: Dict[str, Dict[str, Any]] = defaultdict(lambda: {"credits": Decimal("0"), "count": 0})
        for op in operation_level_ops:
            entry = by_op_type[op.operation_type]
            entry["count"] += 1
            if op.credits_used is not None:
                entry["credits"] += op.credits_used

        # 4. Credits by Trigger Type
        by_trigger: Dict[str, Dict[str, Any]] = defaultdict(lambda: {"credits": Decimal("0"), "count": 0})
        for op in operation_level_ops:
            entry = by_trigger[op.trigger_type]
            entry["count"] += 1
            if op.credits_used is not None:
                entry["credits"] += op.credits_used

        # 5. Interactive vs Automated Expenditure
        interactive_credits = Decimal("0")
        interactive_count = 0
        automated_credits = Decimal("0")
        automated_count = 0

        for op in operation_level_ops:
            is_interactive = op.trigger_type in (TriggerType.MANUAL, TriggerType.USER_INITIATED)
            if is_interactive:
                interactive_count += 1
                if op.credits_used is not None:
                    interactive_credits += op.credits_used
            else:
                automated_count += 1
                if op.credits_used is not None:
                    automated_credits += op.credits_used

        # 6. Subagent and Background Agent Expenditure
        subagent_credits = Decimal("0")
        subagent_count = 0
        for op in operation_level_ops:
            if op.operation_type == OperationType.SUBAGENT or op.trigger_type == TriggerType.AGENT_SPAWNED or op.parent_session_id:
                subagent_count += 1
                if op.credits_used is not None:
                    subagent_credits += op.credits_used

        # 7. Failed and Retried Operations
        failed_credits = Decimal("0")
        failed_count = 0
        retry_credits = Decimal("0")
        retry_count = 0
        turns_with_retries = 0

        for op in operation_level_ops:
            if op.execution_status == ExecutionStatus.FAILURE:
                failed_count += 1
                if op.credits_used is not None:
                    failed_credits += op.credits_used
            if op.metadata.get("has_retries"):
                turns_with_retries += 1
            if op.operation_type == OperationType.RETRY:
                retry_count += 1
                if op.credits_used is not None:
                    retry_credits += op.credits_used
            elif op.metadata.get("retry_credits") is not None:
                retry_count += 1
                r_c = to_decimal(op.metadata.get("retry_credits"))
                if r_c is not None:
                    retry_credits += r_c
            elif op.metadata.get("is_retry"):
                retry_count += 1
                if op.credits_used is not None:
                    retry_credits += op.credits_used

        # 8. Measurement Status Breakdown
        by_status: Dict[str, Dict[str, Any]] = defaultdict(lambda: {"credits": Decimal("0"), "count": 0})
        for op in operation_level_ops:
            entry = by_status[op.measurement_status]
            entry["count"] += 1
            if op.credits_used is not None:
                entry["credits"] += op.credits_used

        # 9. Jira Attribution
        by_ticket: Dict[str, Dict[str, Any]] = defaultdict(lambda: {"credits": Decimal("0"), "count": 0, "sessions": set()})
        unassigned_credits = Decimal("0")
        unassigned_count = 0

        for op in operation_level_ops:
            ticket = op.jira_key
            if not ticket or ticket.upper() == "UNASSIGNED":
                unassigned_count += 1
                if op.credits_used is not None:
                    unassigned_credits += op.credits_used
            else:
                entry = by_ticket[ticket]
                entry["count"] += 1
                if op.session_id:
                    entry["sessions"].add(op.session_id)
                if op.credits_used is not None:
                    entry["credits"] += op.credits_used

        # 10. By Model
        by_model: Dict[str, Dict[str, Any]] = defaultdict(lambda: {"credits": Decimal("0"), "count": 0})
        for op in operation_level_ops:
            model = op.model_resolved or op.model_requested or "unknown"
            entry = by_model[model]
            entry["count"] += 1
            if op.credits_used is not None:
                entry["credits"] += op.credits_used

        # 11. By Hook
        by_hook: Dict[str, Dict[str, Any]] = defaultdict(lambda: {"credits": Decimal("0"), "count": 0, "trigger": "unknown"})
        for op in operation_level_ops:
            if op.hook_name or op.hook_id:
                name = op.hook_name or op.hook_id or "unnamed_hook"
                entry = by_hook[name]
                entry["count"] += 1
                entry["trigger"] = op.trigger_type
                if op.credits_used is not None:
                    entry["credits"] += op.credits_used

        # 12. Account Checkpoints & Reconciliation Discrepancy
        checkpoints = self.store.get_account_checkpoints(provider=provider)
        latest_checkpoint = checkpoints[-1] if checkpoints else None
        account_checkpoint_credits = to_decimal(latest_checkpoint.get("reported_credits_exact") or latest_checkpoint.get("reported_credits")) if latest_checkpoint else None

        reconciliation_discrepancy = None
        if account_checkpoint_credits is not None:
            reconciliation_discrepancy = account_checkpoint_credits - total_op_credits

        # Format helper
        def _fmt_dec(d: Optional[Decimal]) -> Optional[float]:
            return float(d) if d is not None else None

        measured_ops_cnt = len([op for op in operation_level_ops if op.credits_used is not None])
        unmeasured_ops_cnt = len([op for op in operation_level_ops if op.credits_used is None])

        return {
            "total_operations_count": len(operation_level_ops),
            "measured_operations_count": measured_ops_cnt,
            "unmeasured_operations_count": unmeasured_ops_cnt,
            "cross_provider_coverage_note": (
                "Total measured credits reflects observable expenditure for Kiro CLI and Kiro IDE only. "
                f"Antigravity operations ({by_provider.get('antigravity', {}).get('count', 0)} ops) remain unmeasured under credit accounting."
            ),
            "total_measured_credits": _fmt_dec(total_op_credits),
            "total_measured_credits_exact": str(total_op_credits),
            "total_session_scope_credits": _fmt_dec(total_session_credits),
            "total_session_scope_credits_exact": str(total_session_credits),
            "total_account_scope_credits": _fmt_dec(total_account_credits),
            "total_account_scope_credits_exact": str(total_account_credits),
            "by_provider": {
                k: {"credits": _fmt_dec(v["credits"]), "credits_exact": str(v["credits"]), "count": v["count"], "unmeasured_count": v["unmeasured_count"]}
                for k, v in by_provider.items()
            },
            "by_operation_type": {
                k: {"credits": _fmt_dec(v["credits"]), "credits_exact": str(v["credits"]), "count": v["count"]}
                for k, v in by_op_type.items()
            },
            "by_trigger_type": {
                k: {"credits": _fmt_dec(v["credits"]), "credits_exact": str(v["credits"]), "count": v["count"]}
                for k, v in by_trigger.items()
            },
            "interactive_vs_automated": {
                "interactive": {"credits": _fmt_dec(interactive_credits), "credits_exact": str(interactive_credits), "count": interactive_count},
                "automated": {"credits": _fmt_dec(automated_credits), "credits_exact": str(automated_credits), "count": automated_count},
            },
            "subagent_expenditure": {
                "credits": _fmt_dec(subagent_credits),
                "credits_exact": str(subagent_credits),
                "count": subagent_count,
            },
            "failed_and_retried": {
                "failed": {"credits": _fmt_dec(failed_credits), "credits_exact": str(failed_credits), "count": failed_count},
                "retried": {"credits": _fmt_dec(retry_credits), "credits_exact": str(retry_credits), "count": retry_count},
                "turns_with_retries_count": turns_with_retries,
            },
            "by_measurement_status": {
                k: {"credits": _fmt_dec(v["credits"]), "credits_exact": str(v["credits"]), "count": v["count"]}
                for k, v in by_status.items()
            },
            "jira_attribution": {
                "attributed_tickets": {
                    k: {
                        "credits": _fmt_dec(v["credits"]),
                        "credits_exact": str(v["credits"]),
                        "count": v["count"],
                        "sessions_count": len(v["sessions"]),
                    }
                    for k, v in by_ticket.items()
                },
                "unassigned": {
                    "credits": _fmt_dec(unassigned_credits),
                    "credits_exact": str(unassigned_credits),
                    "count": unassigned_count,
                },
            },
            "by_model": {
                k: {"credits": _fmt_dec(v["credits"]), "credits_exact": str(v["credits"]), "count": v["count"]}
                for k, v in by_model.items()
            },
            "by_hook": {
                k: {"credits": _fmt_dec(v["credits"]), "credits_exact": str(v["credits"]), "count": v["count"], "trigger": v["trigger"]}
                for k, v in by_hook.items()
            },
            "reconciliation": {
                "operation_sum_credits": _fmt_dec(total_op_credits),
                "operation_sum_credits_exact": str(total_op_credits),
                "session_sum_credits": _fmt_dec(total_session_credits),
                "session_sum_credits_exact": str(total_session_credits),
                "latest_account_checkpoint_credits": _fmt_dec(account_checkpoint_credits),
                "latest_account_checkpoint_credits_exact": str(account_checkpoint_credits) if account_checkpoint_credits is not None else None,
                "reconciliation_discrepancy": _fmt_dec(reconciliation_discrepancy),
                "reconciliation_discrepancy_exact": str(reconciliation_discrepancy) if reconciliation_discrepancy is not None else None,
            },
        }

    def reconcile_session(self, session_id: str) -> Dict[str, Any]:
        """Compute reconciliation between operation-level records and session-level totals for a single session."""
        ops = self.store.get_operations(session_id=session_id, limit=5000)
        op_level = [op for op in ops if op.measurement_scope in (MeasurementScope.OPERATION, MeasurementScope.REQUEST)]
        sess_level = [op for op in ops if op.measurement_scope == MeasurementScope.SESSION]

        op_credits = sum_credits([op.credits_used for op in op_level])
        sess_credits = sum_credits([op.credits_used for op in sess_level]) if sess_level else None

        discrepancy = None
        if sess_credits is not None:
            discrepancy = sess_credits - op_credits

        def _fmt(d: Optional[Decimal]) -> Optional[float]:
            return float(d) if d is not None else None

        return {
            "session_id": session_id,
            "operation_count": len(op_level),
            "operation_credits": _fmt(op_credits),
            "operation_credits_exact": str(op_credits),
            "session_scope_credits": _fmt(sess_credits),
            "session_scope_credits_exact": str(sess_credits) if sess_credits is not None else None,
            "discrepancy": _fmt(discrepancy),
            "discrepancy_exact": str(discrepancy) if discrepancy is not None else None,
            "reconciled": (discrepancy == Decimal("0")) if discrepancy is not None else (sess_credits is None),
        }

