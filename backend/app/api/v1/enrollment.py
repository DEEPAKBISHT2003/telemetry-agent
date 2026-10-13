"""Enrollment API endpoints for developer workstation onboarding."""

import logging

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.dependencies import get_app_settings, get_db
from app.schemas.enrollment import EnrollmentClaimRequest, EnrollmentClaimResponse
from app.services.enrollment_service import EnrollmentService

logger = logging.getLogger("telemetry.api.enrollment")
router = APIRouter(prefix="/enrollment", tags=["Enrollment"])


@router.post(
    "/claim",
    response_model=EnrollmentClaimResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Claim single-use enrollment token and register device",
    description=(
        "Redeems a single-use developer enrollment token ('dve_...'), binds the physical or virtual "
        "workstation to the organization and developer, and generates a one-time device Bearer credential ('dvt_...'). "
        "The operation is atomic and protected against concurrent double redemption."
    ),
)
async def claim_enrollment(
    payload: EnrollmentClaimRequest,
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> EnrollmentClaimResponse:
    """Atomically redeem enrollment invitation and register workstation."""
    device, credential, plaintext_cred = await EnrollmentService.claim_enrollment_token(
        session=session,
        plaintext_token=payload.enrollment_token,
        hardware_uuid=payload.hardware_uuid,
        hostname=payload.hostname,
        platform=payload.platform,
        agent_version=payload.agent_version,
        credential_expiry_days=settings.device_credential_expiry_days,
    )

    # Commit the transaction atomically
    await session.commit()

    return EnrollmentClaimResponse(
        device_id=device.id,
        device_credential=plaintext_cred,
        token_prefix=credential.token_prefix,
        organization_id=device.organization_id,
        developer_id=device.developer_id,
        hardware_uuid=device.hardware_uuid,
        enrolled_at=device.enrolled_at,
        expires_at=credential.expires_at,
    )
