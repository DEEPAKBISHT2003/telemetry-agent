"""Persistent Accounting Store for AI Operation Records and Reconciliation Ledgers.

Provides thread-safe, atomic, append-only JSONL storage for normalized
operation-level AI accounting records, with deduplication, late-arriving measurement
updates, and account-level reconciliation checkpoints.
"""

from datetime import datetime, timezone
from decimal import Decimal
import json
import os
from pathlib import Path
import threading
from typing import Any, Dict, List, Optional, Set

from ai_telemetry_agent.accounting.models import (
    AIOperationRecord,
    MeasurementScope,
    MeasurementStatus,
    to_decimal,
)
from ai_telemetry_agent.core.validation import is_valid_iso8601
from ai_telemetry_agent.logging.structured import get_logger

logger = get_logger("telemetry.accounting.store")


class AccountingStore:
    """Thread-safe store for AI operation records and reconciliation ledgers."""

    def __init__(self, data_dir: Optional[Path] = None):
        if data_dir:
            self.data_dir = Path(data_dir)
        else:
            home = os.environ.get("TELEMETRY_AGENT_HOME")
            base = Path(home).resolve() if home else (Path.home() / ".telemetry_agent").resolve()
            self.data_dir = base / "data"

        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.reconciliation_file = self.data_dir / "account_reconciliation.json"
        if not self.reconciliation_file.exists():
            try:
                self.reconciliation_file.write_text("[]\n", encoding="utf-8")
            except Exception:
                pass
        self._lock = threading.Lock()
        
        # In-memory index: deduplication_key -> (file_path, operation_id)
        self._dedup_index: Dict[str, str] = {}
        # operation_id -> AIOperationRecord (cached recent records)
        self._op_cache: Dict[str, AIOperationRecord] = {}
        
        self._preload_recent_index()

    def _get_file_path_for_timestamp(self, ts: Optional[str]) -> Path:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if ts and is_valid_iso8601(ts):
            try:
                date_str = ts[:10]
            except Exception:
                pass
        return self.data_dir / f"accounting-{date_str}.jsonl"

    def _preload_recent_index(self) -> None:
        """Scan accounting JSONL files to populate dedup index and cache."""
        try:
            files = sorted(self.data_dir.glob("accounting-*.jsonl"), reverse=True)
            for f in files:
                with open(f, "r", encoding="utf-8", errors="replace") as fp:
                    for line in fp:
                        line_str = line.strip()
                        if not line_str:
                            continue
                        try:
                            d = json.loads(line_str)
                            rec = AIOperationRecord.from_dict(d)
                            if rec.deduplication_key:
                                self._dedup_index[rec.deduplication_key] = rec.operation_id
                            self._op_cache[rec.operation_id] = rec
                        except Exception:
                            continue
        except Exception as e:
            logger.debug("accounting_index_preload_error", error=str(e))

    def save_operation(self, record: AIOperationRecord) -> bool:
        """Save or update an AI operation record with deduplication and late-arriving updates.

        Returns True if newly added or updated, False if duplicate already present.
        """
        if not record or not record.operation_id:
            logger.warning("accounting_save_rejected_invalid_record", record=repr(record))
            return False

        with self._lock:
            existing_op_id = self._dedup_index.get(record.deduplication_key)
            if existing_op_id:
                existing = self._op_cache.get(existing_op_id)
                if existing:
                    # Check if this is a late-arriving measurement update
                    can_update = (
                        (existing.credits_used is None and record.credits_used is not None)
                        or (existing.measurement_status in (MeasurementStatus.PENDING, MeasurementStatus.UNAVAILABLE)
                            and record.measurement_status == MeasurementStatus.MEASURED)
                        or (existing.execution_status == "incomplete" and record.execution_status != "incomplete")
                    )
                    if not can_update:
                        logger.debug("accounting_duplicate_ignored", dedup_key=record.deduplication_key, operation_id=record.operation_id)
                        return False
                    # Preserve original operation ID when updating
                    record.operation_id = existing.operation_id

            # Save to date-partitioned JSONL file
            file_path = self._get_file_path_for_timestamp(record.completed_at or record.started_at)
            try:
                dict_repr = record.to_dict()
                json_line = json.dumps(dict_repr, ensure_ascii=False)
                with open(file_path, "a", encoding="utf-8") as fp:
                    fp.write(json_line + "\n")
                    fp.flush()

                self._dedup_index[record.deduplication_key] = record.operation_id
                self._op_cache[record.operation_id] = record
                return True
            except Exception as e:
                logger.error("accounting_save_failed", operation_id=record.operation_id, error=str(e))
                raise

    def get_operation_by_id(self, operation_id: str) -> Optional[AIOperationRecord]:
        """Retrieve operation record by ID."""
        with self._lock:
            if operation_id in self._op_cache:
                return self._op_cache[operation_id]

        # Scan files if not in cache
        for f in sorted(self.data_dir.glob("accounting-*.jsonl"), reverse=True):
            try:
                with open(f, "r", encoding="utf-8", errors="replace") as fp:
                    for line in fp:
                        if f'"{operation_id}"' in line:
                            try:
                                d = json.loads(line)
                                if d.get("operation_id") == operation_id:
                                    rec = AIOperationRecord.from_dict(d)
                                    with self._lock:
                                        self._op_cache[operation_id] = rec
                                    return rec
                            except Exception:
                                continue
            except Exception:
                continue
        return None

    def get_operations(
        self,
        session_id: Optional[str] = None,
        jira_key: Optional[str] = None,
        provider: Optional[str] = None,
        operation_type: Optional[str] = None,
        trigger_type: Optional[str] = None,
        measurement_status: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        limit: int = 1000,
    ) -> List[AIOperationRecord]:
        """Query operation records with comprehensive filtering."""
        records: List[AIOperationRecord] = []
        seen_ops: Set[str] = set()

        files = sorted(self.data_dir.glob("accounting-*.jsonl"), reverse=True)
        for f in files:
            # Filter by date prefix if provided
            file_date = f.stem.replace("accounting-", "")
            if date_from and file_date < date_from:
                continue
            if date_to and file_date > date_to:
                continue

            try:
                with open(f, "r", encoding="utf-8", errors="replace") as fp:
                    for line in fp:
                        line_str = line.strip()
                        if not line_str:
                            continue
                        try:
                            d = json.loads(line_str)
                            rec = AIOperationRecord.from_dict(d)
                            if not rec.operation_id or rec.operation_id in seen_ops:
                                continue

                            # Apply filters on normalized record
                            if session_id and rec.session_id != session_id:
                                continue
                            if jira_key and rec.jira_key != jira_key:
                                continue
                            if provider and rec.provider != provider:
                                continue
                            if operation_type and rec.operation_type != operation_type:
                                continue
                            if trigger_type and rec.trigger_type != trigger_type:
                                continue
                            if measurement_status and rec.measurement_status != measurement_status:
                                continue

                            records.append(rec)
                            seen_ops.add(rec.operation_id)

                            if len(records) >= limit:
                                break
                        except Exception:
                            continue
            except Exception:
                continue

            if len(records) >= limit:
                break

        return records

    def get_operation(self, operation_id: str) -> Optional[AIOperationRecord]:
        """Retrieve a single operation record by its operation_id."""
        with self._lock:
            if operation_id in self._op_cache:
                return self._op_cache[operation_id]

        ops = self.get_operations(limit=10000)
        for op in ops:
            if op.operation_id == operation_id:
                return op
        return None

    def record_account_checkpoint(
        self,
        provider: str,
        reported_credits: Any,
        account_id: Optional[str] = "default",
        as_of: Optional[str] = None,
        source: str = "account_billing_api",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Record an authoritative account-level expenditure checkpoint for reconciliation."""
        with self._lock:
            ts = as_of or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            dec_credits = to_decimal(reported_credits)

            entry = {
                "checkpoint_id": str(datetime.now(timezone.utc).timestamp()),
                "provider": provider,
                "account_id": account_id or "default",
                "reported_credits": float(dec_credits) if dec_credits is not None else None,
                "reported_credits_exact": str(dec_credits) if dec_credits is not None else None,
                "as_of": ts,
                "source": source,
                "metadata": metadata or {},
            }

            checkpoints = self._load_account_checkpoints()
            checkpoints.append(entry)
            self._save_account_checkpoints(checkpoints)
            return entry

    def get_account_checkpoints(self, provider: Optional[str] = None) -> List[Dict[str, Any]]:
        with self._lock:
            checkpoints = self._load_account_checkpoints()
            if provider:
                checkpoints = [c for c in checkpoints if c.get("provider") == provider]
            return checkpoints

    def _load_account_checkpoints(self) -> List[Dict[str, Any]]:
        if not self.reconciliation_file.is_file():
            return []
        try:
            data = json.loads(self.reconciliation_file.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return data
            if isinstance(data, dict) and "checkpoints" in data:
                return data["checkpoints"]
        except Exception:
            pass
        return []

    def _save_account_checkpoints(self, checkpoints: List[Dict[str, Any]]) -> None:
        try:
            tmp = self.reconciliation_file.parent / f"{self.reconciliation_file.name}.tmp"
            tmp.write_text(json.dumps(checkpoints, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            tmp.replace(self.reconciliation_file)
        except Exception as e:
            logger.error("account_checkpoints_save_failed", error=str(e))

    def backfill_from_event_store(self, converter_fn: Any) -> Dict[str, int]:
        """Safely import historical telemetry events into the accounting ledger.

        Reads existing telemetry-*.jsonl files, converts applicable AI events
        using converter_fn, and writes them with strict deduplication.
        Does not consume any AI credits or mutate original event logs.
        """
        from ai_telemetry_agent.core.event import TelemetryEvent

        stats = {
            "files_scanned": 0,
            "events_scanned": 0,
            "records_created": 0,
            "records_saved": 0,
            "records_skipped": 0,
            "conversion_errors": 0,
        }

        files = sorted(self.data_dir.glob("telemetry-*.jsonl"))
        stats["files_scanned"] = len(files)

        for f in files:
            try:
                with open(f, "r", encoding="utf-8", errors="replace") as fp:
                    for line in fp:
                        line_str = line.strip()
                        if not line_str:
                            continue
                        stats["events_scanned"] += 1
                        try:
                            d = json.loads(line_str)
                            ev = TelemetryEvent.from_dict(d)
                            rec = converter_fn(ev)
                            if rec:
                                stats["records_created"] += 1
                                if self.save_operation(rec):
                                    stats["records_saved"] += 1
                                else:
                                    stats["records_skipped"] += 1
                        except Exception as e:
                            stats["conversion_errors"] += 1
                            logger.debug("accounting_backfill_event_error", file=str(f), error=str(e))
            except Exception as e:
                logger.error("accounting_backfill_file_error", file=str(f), error=str(e))

        logger.info("accounting_backfill_completed", **stats)
        return stats
