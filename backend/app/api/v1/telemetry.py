"""Telemetry batch ingestion API endpoints."""

import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.dependencies import (
    AuthenticatedDeviceContext,
    get_app_settings,
    get_current_device_context,
    get_db,
)
from app.schemas.telemetry import TelemetryBatchRequest, TelemetryBatchResponse
from app.services.ingestion_service import IngestionService

logger = logging.getLogger("telemetry.api.ingestion")
router = APIRouter(prefix="/telemetry", tags=["Telemetry Ingestion"])


async def verify_payload_size_and_read(
    request: Request,
    settings: Settings = Depends(get_app_settings),
) -> bytes:
    """Enforce request body size limits before and during buffering.

    Checks:
    1. Content-Length header against max_request_body_bytes (2 MiB default).
    2. Actual read byte count to prevent buffer exhaustion attacks from chunked requests.

    Raises:
        HTTPException(413) if body size exceeds limit.
    """
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            length_int = int(content_length)
            if length_int > settings.max_request_body_bytes:
                raise HTTPException(
                    status_code=getattr(status, "HTTP_413_CONTENT_TOO_LARGE", 413),
                    detail=f"Request payload size ({length_int} bytes) exceeds limit of {settings.max_request_body_bytes} bytes.",
                )
        except ValueError:
            pass

    body = await request.body()
    if len(body) > settings.max_request_body_bytes:
        raise HTTPException(
            status_code=getattr(status, "HTTP_413_CONTENT_TOO_LARGE", 413),
            detail=f"Request payload size ({len(body)} bytes) exceeds limit of {settings.max_request_body_bytes} bytes.",
        )

    return body


@router.post(
    "/events/batch",
    response_model=TelemetryBatchResponse,
    summary="Ingest batch of telemetry events from authenticated workstation",
    description=(
        "Accepts a batch of up to 500 validated telemetry events from an enrolled workstation. "
        "Enforces server-side privacy filtering, batch and event idempotency, and transactional "
        "persistence. Attribution is derived exclusively from server-side device credentials."
    ),
)
async def ingest_telemetry_batch(
    response: Response,
    payload_bytes: bytes = Depends(verify_payload_size_and_read),
    device_ctx: AuthenticatedDeviceContext = Depends(get_current_device_context),
    session: AsyncSession = Depends(get_db),
) -> TelemetryBatchResponse:
    """Ingest, validate, sanitize, and persist telemetry batch transactionally."""
    try:
        batch_request = TelemetryBatchRequest.model_validate_json(payload_bytes)
    except ValidationError as val_err:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=val_err.errors(),
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Malformed request payload: {str(e)}",
        )

    result = await IngestionService.process_batch(
        session=session,
        request=batch_request,
        org_id=device_ctx.organization.id,
        dev_id=device_ctx.developer.id,
        device_id=device_ctx.device.id,
    )

    # Commit the transaction before returning acknowledgment
    await session.commit()

    # Differentiate 200 OK for replayed batches vs 201 Created for new batches
    if result.status == "ALREADY_PROCESSED":
        response.status_code = status.HTTP_200_OK
    else:
        response.status_code = status.HTTP_201_CREATED

    return result
