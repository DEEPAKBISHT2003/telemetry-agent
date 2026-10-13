"""Device identity and self-attribution API endpoints."""

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import AuthenticatedDeviceContext, get_current_device_context, get_db
from app.models.base import utc_now
from app.schemas.device import DeviceIdentityResponse

router = APIRouter(prefix="/devices", tags=["Devices"])


@router.get(
    "/me",
    response_model=DeviceIdentityResponse,
    status_code=status.HTTP_200_OK,
    summary="Retrieve current authenticated device identity",
    description=(
        "Returns the verified device, developer, and organization identity derived strictly "
        "from the presented Bearer credential ('dvt_...'). Client-supplied identity headers "
        "or request payloads are never trusted."
    ),
)
async def get_device_identity(
    device_ctx: AuthenticatedDeviceContext = Depends(get_current_device_context),
    session: AsyncSession = Depends(get_db),
) -> DeviceIdentityResponse:
    """Return verified identity and organizational attribution for the authenticated device."""
    # Update device last_seen_at timestamp
    device = device_ctx.device
    device.last_seen_at = utc_now()
    await session.commit()

    return DeviceIdentityResponse(
        device_id=device.id,
        organization_id=device_ctx.organization.id,
        organization_slug=device_ctx.organization.slug,
        developer_id=device_ctx.developer.id,
        developer_email=device_ctx.developer.email,
        developer_name=device_ctx.developer.full_name,
        developer_role=device_ctx.developer.role.value,
        hardware_uuid=device.hardware_uuid,
        hostname=device.hostname,
        platform=device.platform,
        agent_version=device.agent_version,
        status=device.status.value,
        enrolled_at=device.enrolled_at,
        last_seen_at=device.last_seen_at,
    )
