"""Batch ingestion service for telemetry events.

Guarantees:
1. Batch-level idempotency via stable payload fingerprinting (detects identical re-acks and rejects conflicting alterations).
2. Event-level idempotency via atomic ON CONFLICT (id) DO NOTHING RETURNING id.
3. Intra-batch event deduplication handling repeated event IDs within the same payload deterministically.
4. Transactional persistence: batch metadata and newly accepted events commit atomically.
5. Exact persistent accounting: reports precise count of newly inserted events without duplicate inflation.
"""

import hashlib
import json
import logging
from decimal import Decimal
from typing import Any, Dict, List, Set, Tuple
import uuid

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.base import ensure_utc, utc_now
from app.models.batch import BatchStatus, UploadBatch
from app.models.event import TelemetryEvent
from app.schemas.telemetry import (
    ClientTelemetryEvent,
    TelemetryBatchRequest,
    TelemetryBatchResponse,
)
from app.services.sanitizer import SanitizedTelemetryEvent, validate_and_sanitize_event

logger = logging.getLogger("telemetry.ingestion")


class IngestionService:
    """Core domain service for batch event ingestion and transactional persistence."""

    @classmethod
    def canonical_event_repr(cls, event: SanitizedTelemetryEvent) -> Dict[str, Any]:
        """Produce a deterministic, canonical dictionary of a sanitized event."""
        event_time_utc = ensure_utc(event.event_timestamp)
        ts_str = event_time_utc.isoformat()
        cost_str = f"{event.cost_usd:.6f}" if event.cost_usd is not None else None

        return {
            "id": str(event.id),
            "event_type": event.event_type,
            "event_timestamp": ts_str,
            "session_id": event.session_id,
            "repository_name": event.repository_name,
            "branch": event.branch,
            "model_name": event.model_name,
            "tool_name": event.tool_name,
            "tokens_input_uncached": event.tokens_input_uncached,
            "tokens_input_cached": event.tokens_input_cached,
            "tokens_output": event.tokens_output,
            "tokens_total": event.tokens_total,
            "cost_usd": cost_str,
            "raw_payload": event.raw_payload,
        }

    @classmethod
    def compute_batch_fingerprint(
        cls,
        batch_id: uuid.UUID,
        sanitized_events: List[SanitizedTelemetryEvent],
    ) -> str:
        """Compute a deterministic SHA-256 fingerprint for canonical batch identification.

        Serializes sorted, canonicalized representations of all sanitized events.
        Key ordering is stabilized via json.dumps(sort_keys=True, separators=(',', ':')).
        """
        canonical_list = [cls.canonical_event_repr(e) for e in sanitized_events]
        # Sort canonically by event ID, then serialized content
        canonical_list.sort(
            key=lambda d: (
                d["id"],
                json.dumps(d, sort_keys=True, separators=(",", ":")),
            )
        )
        canonical_json = json.dumps(canonical_list, sort_keys=True, separators=(",", ":"))
        payload_data = f"{batch_id}:{canonical_json}"
        return hashlib.sha256(payload_data.encode("utf-8")).hexdigest()

    @staticmethod
    def _is_materially_different(
        stored: TelemetryEvent,
        incoming: SanitizedTelemetryEvent,
    ) -> bool:
        """Check if an incoming sanitized event differs materially from an existing stored event with the same ID."""
        if stored.event_type != incoming.event_type:
            return True

        # Timestamp comparison (allow up to 60s tolerance for clock jitter/re-serialization)
        stored_ts = ensure_utc(stored.event_timestamp)
        incoming_ts = ensure_utc(incoming.event_timestamp)
        if abs((stored_ts - incoming_ts).total_seconds()) > 60.0:
            return True

        if stored.session_id != incoming.session_id:
            return True
        if stored.repository_name != incoming.repository_name:
            return True
        if stored.branch != incoming.branch:
            return True
        if stored.model_name != incoming.model_name:
            return True
        if stored.tool_name != incoming.tool_name:
            return True
        if stored.tokens_input_uncached != incoming.tokens_input_uncached:
            return True
        if stored.tokens_input_cached != incoming.tokens_input_cached:
            return True
        if stored.tokens_output != incoming.tokens_output:
            return True
        if stored.tokens_total != incoming.tokens_total:
            return True

        # Cost comparison
        if (stored.cost_usd is None) != (incoming.cost_usd is None):
            return True
        if stored.cost_usd is not None and incoming.cost_usd is not None:
            if round(Decimal(str(stored.cost_usd)), 6) != round(Decimal(str(incoming.cost_usd)), 6):
                return True

        # Attribution comparison
        if stored.developer_id != incoming.developer_id:
            return True
        if stored.organization_id != incoming.organization_id:
            return True

        return False

    @classmethod
    async def process_batch(
        cls,
        session: AsyncSession,
        request: TelemetryBatchRequest,
        org_id: uuid.UUID,
        dev_id: uuid.UUID,
        device_id: uuid.UUID,
    ) -> TelemetryBatchResponse:
        """Process, sanitize, and persist a telemetry event batch transactionally.

        Args:
            session: Async database session.
            request: Validated TelemetryBatchRequest from client.
            org_id: Server-side authenticated organization UUID.
            dev_id: Server-side authenticated developer UUID.
            device_id: Server-side authenticated device UUID.

        Returns:
            TelemetryBatchResponse with processing status and persisted counts.
        """
        batch_id = request.batch_id
        submitted_count = len(request.events)
        now = utc_now()

        # 1. Sanitize and validate all events under server-side privacy allowlist
        sanitized_events: List[SanitizedTelemetryEvent] = []
        for raw_event in request.events:
            sanitized = validate_and_sanitize_event(
                event=raw_event,
                batch_id=batch_id,
                org_id=org_id,
                dev_id=dev_id,
                device_id=device_id,
                now=now,
            )
            sanitized_events.append(sanitized)

        # 2. Compute stable payload fingerprint across canonical sanitized events
        payload_fingerprint = cls.compute_batch_fingerprint(batch_id, sanitized_events)

        # 3. Check Batch-level idempotency
        batch_stmt = select(UploadBatch).where(UploadBatch.id == batch_id)
        existing_batch = (await session.execute(batch_stmt)).scalars().first()

        if existing_batch is not None:
            # If batch already committed with identical fingerprint, return idempotent success
            if existing_batch.payload_fingerprint == payload_fingerprint:
                logger.info(
                    "Idempotent batch retry acknowledged: batch_id=%s (already processed)",
                    batch_id,
                )
                return TelemetryBatchResponse(
                    batch_id=batch_id,
                    status="ALREADY_PROCESSED",
                    submitted_count=existing_batch.event_count,
                    newly_persisted_count=0,
                    duplicate_count=existing_batch.event_count,
                    persisted_event_ids=[],
                    message="Batch was previously committed to database.",
                )
            else:
                # Same batch UUID reused with altered event contents -> 409 Conflict
                logger.warning(
                    "Batch UUID collision with conflicting payload: batch_id=%s",
                    batch_id,
                )
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Batch UUID already exists with different payload contents.",
                )

        # 4. Intra-batch event deduplication (preserve first occurrence deterministically)
        unique_events: List[SanitizedTelemetryEvent] = []
        seen_in_batch: Set[uuid.UUID] = set()
        intra_batch_duplicates = 0

        for event in sanitized_events:
            if event.id in seen_in_batch:
                intra_batch_duplicates += 1
            else:
                seen_in_batch.add(event.id)
                unique_events.append(event)

        # 5. Check cross-batch event ID collisions against existing events in database
        if unique_events:
            unique_ids = [e.id for e in unique_events]
            existing_events_stmt = select(TelemetryEvent).where(
                TelemetryEvent.id.in_(unique_ids)
            )
            existing_events = (await session.execute(existing_events_stmt)).scalars().all()

            if existing_events:
                incoming_by_id = {e.id: e for e in unique_events}
                for stored in existing_events:
                    incoming = incoming_by_id[stored.id]
                    if cls._is_materially_different(stored, incoming):
                        logger.warning(
                            "Event ID collision with conflicting content: event_id=%s, batch_id=%s",
                            stored.id,
                            batch_id,
                        )
                        raise HTTPException(
                            status_code=status.HTTP_409_CONFLICT,
                            detail=(
                                f"Event ID collision: event {stored.id} already exists with conflicting content. "
                                f"Existing events cannot be overwritten or altered."
                            ),
                        )

        # 5. Record UploadBatch row
        batch_record = UploadBatch(
            id=batch_id,
            organization_id=org_id,
            device_id=device_id,
            developer_id=dev_id,
            event_count=submitted_count,
            payload_fingerprint=payload_fingerprint,
            status=BatchStatus.PROCESSED,
            client_sent_at=request.client_sent_at,
            server_received_at=now,
            processed_at=now,
        )
        session.add(batch_record)
        await session.flush()

        # 6. Bulk insert unique events using dialect-specific ON CONFLICT DO NOTHING RETURNING id
        persisted_ids: List[uuid.UUID] = []

        if unique_events:
            # Build insert values dictionary list
            values_list = [
                {
                    "id": e.id,
                    "organization_id": e.organization_id,
                    "developer_id": e.developer_id,
                    "device_id": e.device_id,
                    "batch_id": e.batch_id,
                    "event_type": e.event_type,
                    "event_timestamp": e.event_timestamp,
                    "session_id": e.session_id,
                    "repository_name": e.repository_name,
                    "branch": e.branch,
                    "model_name": e.model_name,
                    "tool_name": e.tool_name,
                    "tokens_input_uncached": e.tokens_input_uncached,
                    "tokens_input_cached": e.tokens_input_cached,
                    "tokens_output": e.tokens_output,
                    "tokens_total": e.tokens_total,
                    "cost_usd": e.cost_usd,
                    "raw_payload": e.raw_payload,
                    "created_at": now,
                }
                for e in unique_events
            ]

            # Detect dialect (PostgreSQL in production, SQLite in unit tests)
            dialect_name = session.bind.dialect.name if session.bind else "postgresql"
            if dialect_name == "sqlite":
                from sqlalchemy.dialects.sqlite import insert as dialect_insert
            else:
                from sqlalchemy.dialects.postgresql import insert as dialect_insert

            insert_query = (
                dialect_insert(TelemetryEvent)
                .values(values_list)
                .on_conflict_do_nothing(index_elements=["id"])
                .returning(TelemetryEvent.id)
            )

            result = await session.execute(insert_query)
            persisted_ids = [row[0] for row in result.all()]

        newly_persisted_count = len(persisted_ids)
        duplicate_count = submitted_count - newly_persisted_count

        logger.info(
            "Batch processed: batch_id=%s, submitted=%d, newly_persisted=%d, duplicates=%d (org_id=%s, dev_id=%s)",
            batch_id,
            submitted_count,
            newly_persisted_count,
            duplicate_count,
            org_id,
            dev_id,
        )

        return TelemetryBatchResponse(
            batch_id=batch_id,
            status="PROCESSED",
            submitted_count=submitted_count,
            newly_persisted_count=newly_persisted_count,
            duplicate_count=duplicate_count,
            persisted_event_ids=persisted_ids,
            message="Batch successfully processed and committed.",
        )
