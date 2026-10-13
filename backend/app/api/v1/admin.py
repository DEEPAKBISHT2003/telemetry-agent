"""Administrative API endpoints for developer management, invitations, and device revocation."""

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import joinedload
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.dependencies import AdminAuthContext, get_app_settings, get_db, require_admin_auth
from app.models.base import utc_now
from app.models.device import Device, DeviceCredential
from app.models.token import EnrollmentToken
from app.schemas.device import CredentialRevokeResponse, DeviceRevokeResponse
from app.schemas.enrollment import (
    EnrollmentInviteRequest,
    EnrollmentInviteResponse,
    TokenRevokeResponse,
)
from app.services.enrollment_service import EnrollmentService

logger = logging.getLogger("telemetry.api.admin")
router = APIRouter(prefix="/admin", tags=["Administration"])


@router.post(
    "/enrollment/invite",
    response_model=EnrollmentInviteResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Issue a single-use developer enrollment invitation",
    description=(
        "Administrative endpoint to register/find a developer and issue a single-use "
        "cryptographic enrollment token ('dve_...'). The plaintext token is returned only once."
    ),
)
async def invite_developer(
    payload: EnrollmentInviteRequest,
    admin: AdminAuthContext = Depends(require_admin_auth),
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> EnrollmentInviteResponse:
    """Create or retrieve a developer account and issue an enrollment invitation token."""
    # Enforce organization boundaries
    admin.check_organization_access(payload.organization_id)

    expires_hours = payload.expires_in_hours or settings.enrollment_token_expiry_hours

    token_record, plaintext_token = await EnrollmentService.issue_enrollment_invitation(
        session=session,
        organization_id=payload.organization_id,
        email=payload.email,
        full_name=payload.full_name,
        role=payload.role,
        expires_in_hours=expires_hours,
    )

    await session.commit()

    return EnrollmentInviteResponse(
        token_id=token_record.id,
        enrollment_token=plaintext_token,
        organization_id=token_record.organization_id,
        developer_id=token_record.developer_id,
        email=payload.email,
        expires_at=token_record.expires_at,
        created_at=token_record.created_at,
    )


@router.post(
    "/credentials/{credential_id}/revoke",
    response_model=CredentialRevokeResponse,
    status_code=status.HTTP_200_OK,
    summary="Revoke a specific device credential",
    description="Immediately deactivates a device credential so future authentication attempts are rejected.",
)
async def revoke_credential(
    credential_id: uuid.UUID,
    admin: AdminAuthContext = Depends(require_admin_auth),
    session: AsyncSession = Depends(get_db),
) -> CredentialRevokeResponse:
    """Deactivate a specific device credential."""
    stmt = (
        select(DeviceCredential)
        .options(joinedload(DeviceCredential.device))
        .where(DeviceCredential.id == credential_id)
    )
    cred = (await session.execute(stmt)).scalars().first()
    if not cred:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Device credential not found.",
        )

    # Check tenancy
    admin.check_organization_access(cred.device.organization_id)

    revoked_cred = await EnrollmentService.revoke_credential(session=session, credential_id=credential_id)
    await session.commit()

    return CredentialRevokeResponse(
        credential_id=revoked_cred.id,
        device_id=revoked_cred.device_id,
        is_active=revoked_cred.is_active,
        revoked_at=revoked_cred.revoked_at or utc_now(),
    )


@router.post(
    "/devices/{device_id}/revoke",
    response_model=DeviceRevokeResponse,
    status_code=status.HTTP_200_OK,
    summary="Revoke a device and all its active credentials",
    description="Marks the device as REVOKED and deactivates all associated active credentials immediately.",
)
async def revoke_device(
    device_id: uuid.UUID,
    admin: AdminAuthContext = Depends(require_admin_auth),
    session: AsyncSession = Depends(get_db),
) -> DeviceRevokeResponse:
    """Mark device revoked and deactivate all active credentials."""
    stmt = select(Device).where(Device.id == device_id)
    device = (await session.execute(stmt)).scalars().first()
    if not device:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Device not found.",
        )

    # Check tenancy
    admin.check_organization_access(device.organization_id)

    revoked_device, count = await EnrollmentService.revoke_device(session=session, device_id=device_id)
    await session.commit()

    return DeviceRevokeResponse(
        device_id=revoked_device.id,
        status=revoked_device.status.value,
        revoked_credentials_count=count,
        revoked_at=utc_now(),
    )


@router.post(
    "/enrollment/tokens/{token_id}/revoke",
    response_model=TokenRevokeResponse,
    status_code=status.HTTP_200_OK,
    summary="Revoke an unconsumed enrollment invitation token",
    description="Cancels an outstanding invitation so it cannot be claimed.",
)
async def revoke_enrollment_token(
    token_id: uuid.UUID,
    admin: AdminAuthContext = Depends(require_admin_auth),
    session: AsyncSession = Depends(get_db),
) -> TokenRevokeResponse:
    """Revoke an outstanding enrollment invitation token."""
    stmt = select(EnrollmentToken).where(EnrollmentToken.id == token_id)
    token_record = (await session.execute(stmt)).scalars().first()
    if not token_record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Enrollment token not found.",
        )

    # Check tenancy
    admin.check_organization_access(token_record.organization_id)

    revoked_token = await EnrollmentService.revoke_enrollment_token(session=session, token_id=token_id)
    await session.commit()

    return TokenRevokeResponse(
        token_id=revoked_token.id,
        status=revoked_token.status.value,
        revoked_at=revoked_token.revoked_at or utc_now(),
    )
