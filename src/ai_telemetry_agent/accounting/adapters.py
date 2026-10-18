"""Accounting Adapters for Cross-Provider AI Credit Extraction.

Normalizes raw provider artifacts (Kiro CLI JSON, Kiro IDE messages.jsonl,
Antigravity SQLite, Lifecycle Hooks) into canonical AIOperationRecord models.
"""

from decimal import Decimal
import os
from pathlib import Path
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


class KiroCLIAccountingAdapter:
    """Adapter for transforming Kiro CLI session turns into operation records."""

    @staticmethod
    def turn_to_operation_record(
        session_id: str,
        session_data: Dict[str, Any],
        raw_turn: Dict[str, Any],
        turn_idx: int,
        repo_dict: Optional[Dict[str, Any]] = None,
        jira_key: Optional[str] = None,
        developer_id: Optional[str] = None,
        device_id: Optional[str] = None,
    ) -> AIOperationRecord:
        """Map a Kiro CLI user_turn_metadata dictionary to an AIOperationRecord."""
        result_ok = raw_turn.get("result", {}).get("Ok", {})
        if isinstance(result_ok, dict) and "user_turn_metadata" in result_ok:
            turn = result_ok["user_turn_metadata"]
            explicit_id = result_ok.get("id")
        elif "user_turn_metadata" in raw_turn and isinstance(raw_turn["user_turn_metadata"], dict):
            turn = raw_turn["user_turn_metadata"]
            explicit_id = raw_turn.get("id")
        else:
            turn = raw_turn
            explicit_id = raw_turn.get("id")

        turn_id = explicit_id or f"{session_id}_turn_{turn_idx}"
        dedup_key = f"kiro_cli:{session_id}:{turn_id}"

        # Subagent detection
        session_reason = session_data.get("session_created_reason")
        loop_id = turn.get("loop_id", {})
        agent_id_info = loop_id.get("agent_id", {}) if isinstance(loop_id, dict) else {}
        parent_agent_id = agent_id_info.get("parent_id") if isinstance(agent_id_info, dict) else None

        is_subagent = (session_reason == "subagent") or (parent_agent_id is not None)

        if is_subagent:
            operation_type = OperationType.SUBAGENT
            trigger_type = TriggerType.AGENT_SPAWNED
        elif turn.get("builtin_tool_uses", 0) > 0 and turn.get("total_request_count", 1) == 0:
            operation_type = OperationType.TOOL_TRIGGERED
            trigger_type = TriggerType.AGENT_SPAWNED
        else:
            operation_type = OperationType.PROMPT
            trigger_type = TriggerType.USER_INITIATED

        # Model resolution
        raw_model = raw_turn.get("selected_model") or raw_turn.get("model") or turn.get("model")
        if raw_model == "auto":
            model_requested = "auto"
            model_resolved = None
        else:
            model_requested = raw_model
            model_resolved = raw_model

        # Extract authoritative credits
        metering = turn.get("metering_usage", [])
        credits_dec: Optional[Decimal] = None
        credit_source = None
        if isinstance(metering, (int, float)):
            credits_dec = to_decimal(metering)
            credit_source = "kiro_cli_session_json:metering_usage"
        elif isinstance(metering, list) and metering:
            sum_c = Decimal("0")
            has_val = False
            for m in metering:
                if isinstance(m, dict):
                    v = m.get("value")
                    d_v = to_decimal(v)
                    if d_v is not None:
                        sum_c += d_v
                        has_val = True
                else:
                    d_v = to_decimal(m)
                    if d_v is not None:
                        sum_c += d_v
                        has_val = True
            if has_val:
                credits_dec = sum_c
                credit_source = "kiro_cli_session_json:metering_usage"

        measurement_status = MeasurementStatus.MEASURED if credits_dec is not None else MeasurementStatus.UNAVAILABLE

        # Request attempts & retry detection
        request_attempts = turn.get("request_attempts", 1)
        total_requests = turn.get("total_request_count", 1)
        has_retries = bool(
            turn.get("has_retries")
            or (total_requests is not None and request_attempts is not None and request_attempts > total_requests)
        )
        retry_attempts = max(0, request_attempts - total_requests) if (total_requests is not None and request_attempts is not None) else 0

        # Status
        end_reason = turn.get("end_reason", "UserTurnEnd")
        exec_status = ExecutionStatus.SUCCESS if end_reason in ("UserTurnEnd", "success") else ExecutionStatus.FAILURE

        # Message IDs
        message_ids = turn.get("message_ids") or []
        request_id = message_ids[0] if message_ids else None

        # Timestamps
        completed_at = turn.get("end_timestamp") or session_data.get("updated_at")

        metadata: Dict[str, Any] = {
            "turn_index": turn_idx,
            "end_reason": end_reason,
            "total_request_count": total_requests,
            "request_attempts": request_attempts,
            "has_retries": has_retries,
            "retry_attempts": retry_attempts,
            "is_retry": False,
            "retry_credits": None,
            "builtin_tool_uses": turn.get("builtin_tool_uses", 0),
            "context_usage_percentage": turn.get("final_context_usage_percentage") or turn.get("context_usage_percentage"),
        }

        return AIOperationRecord(
            operation_id=turn_id,
            parent_operation_id=parent_agent_id,
            parent_session_id=session_data.get("parent_session_id"),
            provider="kiro_cli",
            source="session_json",
            operation_type=operation_type,
            trigger_type=trigger_type,
            session_id=session_id,
            request_id=request_id,
            model_requested=model_requested,
            model_resolved=model_resolved,
            started_at=session_data.get("created_at"),
            completed_at=completed_at,
            credits_used=credits_dec,
            credit_source=credit_source,
            measurement_scope=MeasurementScope.OPERATION,
            measurement_status=measurement_status,
            token_usage=None,  # Kiro backend does not meter tokens in CLI
            jira_key=jira_key,
            repository=repo_dict,
            branch=repo_dict.get("branch") if repo_dict else None,
            developer_id=developer_id,
            device_id=device_id,
            execution_status=exec_status,
            deduplication_key=dedup_key,
            metadata=metadata,
        )


class KiroIDEAccountingAdapter:
    """Adapter for transforming Kiro IDE messages.jsonl entries into operation records."""

    @staticmethod
    def entry_to_operation_record(
        entry: Dict[str, Any],
        session_id: str,
        session_data: Dict[str, Any],
        repo_dict: Optional[Dict[str, Any]] = None,
        jira_key: Optional[str] = None,
        developer_id: Optional[str] = None,
        device_id: Optional[str] = None,
    ) -> Optional[AIOperationRecord]:
        """Convert a message entry from messages.jsonl into an operation record if relevant."""
        payload = entry.get("payload", entry)
        if not isinstance(payload, dict):
            return None

        msg_type = payload.get("type") or entry.get("type")
        timestamp = entry.get("timestamp")
        entry_id = entry.get("id", str(uuid.uuid4()))

        # Sub-agent execution
        if msg_type in ("sub_agent_start", "sub_agent_complete"):
            sub_id = payload.get("subSessionId") or entry_id
            parent_exec = payload.get("parentExecutionId")
            return AIOperationRecord(
                operation_id=f"{session_id}_sub_{sub_id}",
                parent_operation_id=parent_exec,
                parent_session_id=session_id,
                provider="kiro_ide",
                source="messages_jsonl",
                operation_type=OperationType.SUBAGENT,
                trigger_type=TriggerType.AGENT_SPAWNED,
                session_id=sub_id,
                request_id=entry_id,
                model_requested=session_data.get("modelId", "auto"),
                started_at=timestamp,
                completed_at=timestamp if msg_type == "sub_agent_complete" else None,
                credits_used=None,  # Measured when turn completion or usage summary arrives
                measurement_scope=MeasurementScope.OPERATION,
                measurement_status=MeasurementStatus.PENDING,
                jira_key=jira_key,
                repository=repo_dict,
                branch=repo_dict.get("branch") if repo_dict else None,
                developer_id=developer_id,
                device_id=device_id,
                execution_status=ExecutionStatus.SUCCESS if msg_type == "sub_agent_complete" else ExecutionStatus.INCOMPLETE,
                deduplication_key=f"kiro_ide:{session_id}:subagent:{sub_id}",
                metadata={"sub_agent_name": payload.get("subAgentName")},
            )

        # Usage summary with authoritative credits
        if msg_type in ("usage_summary", "turn_completed"):
            exec_id = payload.get("executionId") or entry_id
            summaries = payload.get("promptTurnSummaries", [])
            credits_dec: Optional[Decimal] = None
            credit_source = "kiro_ide_messages_jsonl:usage_summary"

            if isinstance(payload.get("credits"), (int, float, str)):
                credits_dec = to_decimal(payload.get("credits"))
            elif isinstance(summaries, list) and summaries:
                s_sum = Decimal("0")
                has_s = False
                for s in summaries:
                    if isinstance(s, dict):
                        d_u = to_decimal(s.get("usage"))
                        if d_u is not None:
                            s_sum += d_u
                            has_s = True
                if has_s:
                    credits_dec = s_sum

            status = payload.get("status", "success")
            exec_status = ExecutionStatus.SUCCESS if status in ("success", "completed") else ExecutionStatus.FAILURE

            return AIOperationRecord(
                operation_id=f"{session_id}_{exec_id}",
                parent_operation_id=None,
                provider="kiro_ide",
                source="messages_jsonl",
                operation_type=OperationType.PROMPT,
                trigger_type=TriggerType.USER_INITIATED,
                session_id=session_id,
                request_id=exec_id,
                model_requested=session_data.get("modelId", "auto"),
                started_at=timestamp,
                completed_at=timestamp,
                credits_used=credits_dec,
                credit_source=credit_source if credits_dec is not None else None,
                measurement_scope=MeasurementScope.OPERATION,
                measurement_status=MeasurementStatus.MEASURED if credits_dec is not None else MeasurementStatus.UNAVAILABLE,
                jira_key=jira_key,
                repository=repo_dict,
                branch=repo_dict.get("branch") if repo_dict else None,
                developer_id=developer_id,
                device_id=device_id,
                execution_status=exec_status,
                deduplication_key=f"kiro_ide:{session_id}:{exec_id}",
                metadata={"elapsed_time": payload.get("elapsedTime")},
            )

        return None


class AntigravityAccountingAdapter:
    """Adapter for transforming Antigravity SQLite generation records into operation records."""

    @staticmethod
    def generation_to_operation_record(
        conv_id: str,
        idx: int,
        timestamp: str,
        model_id: str,
        non_cached_input: int,
        cached_input: int,
        total_output: int,
        thinking_tokens: int,
        repo_dict: Optional[Dict[str, Any]] = None,
        jira_key: Optional[str] = None,
        developer_id: Optional[str] = None,
        device_id: Optional[str] = None,
    ) -> AIOperationRecord:
        """Map an Antigravity GenerationRecord to an AIOperationRecord with authoritative tokens."""
        dedup_key = f"antigravity:{conv_id}:{idx}"
        tokens = {
            "input_tokens": non_cached_input,
            "cache_read_tokens": cached_input,
            "output_tokens": total_output,
            "thinking_tokens": thinking_tokens,
            "total_tokens": non_cached_input + cached_input + total_output,
        }

        return AIOperationRecord(
            operation_id=f"antigravity_{conv_id}_{idx}",
            parent_operation_id=None,
            provider="antigravity",
            source="sqlite_gen_metadata",
            operation_type=OperationType.QUERY,
            trigger_type=TriggerType.USER_INITIATED,
            session_id=conv_id,
            request_id=str(idx),
            model_requested=model_id,
            model_resolved=model_id,
            started_at=timestamp,
            completed_at=timestamp,
            credits_used=None,  # Antigravity is token-metered, not credit-metered
            credit_source="antigravity_sqlite:gen_metadata",
            measurement_scope=MeasurementScope.OPERATION,
            measurement_status=MeasurementStatus.UNAVAILABLE,  # Credits unavailable; tokens measured
            token_usage=tokens,
            jira_key=jira_key,
            repository=repo_dict,
            branch=repo_dict.get("branch") if repo_dict else None,
            developer_id=developer_id,
            device_id=device_id,
            execution_status=ExecutionStatus.SUCCESS,
            deduplication_key=dedup_key,
            metadata={"sqlite_idx": idx},
        )
