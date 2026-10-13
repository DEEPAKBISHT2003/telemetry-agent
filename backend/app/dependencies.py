"""FastAPI application dependencies for database sessions, security, and authentication."""

from dataclasses import dataclass
from typing import AsyncGenerator, Optional, Set
import uuid

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import joinedload
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.database import get_session_factory
from app.models.base import ensure_utc, utc_now
from app.models.developer import AccountStatus, Developer, DeveloperRole
from app.models.device import Device, DeviceCredential, DeviceStatus
from app.models.organization import Organization
from app.services import security


@dataclass
class AuthenticatedDeviceContext:
    """Strongly typed authentication context resolved from a verified device credential."""

    device: Device
    developer: Developer
    organization: Organization
    credential: DeviceCredential


@dataclass
class AdminAuthContext:
    """Strongly typed authorization context for administrative operations."""

    is_bootstrap: bool
    organization_id: Optional[uuid.UUID] = None
    device_context: Optional[AuthenticatedDeviceContext] = None

    def check_organization_access(self, target_org_id: uuid.UUID) -> None:
        """Enforce strict cross-organization isolation boundary."""
        if not self.is_bootstrap:
            if self.organization_id is None or self.organization_id != target_org_id:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Cross-organization administration is not permitted.",
                )


async def get_app_settings(request: Request) -> Settings:
    """Dependency for injecting application settings from application state."""
    if hasattr(request.app.state, "settings") and request.app.state.settings is not None:
        return request.app.state.settings
    return get_settings()


async def get_db(
    settings: Settings = Depends(get_app_settings),
) -> AsyncGenerator[AsyncSession, None]:
    """Dependency for acquiring an isolated asynchronous database session.

    Guarantees:
    - Session is not shared across concurrent requests.
    - Automatic rollback on unhandled exceptions.
    - Clean session closure upon request completion.
    """
    factory = get_session_factory(settings)
    if factory is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database service is not configured.",
        )

    async with factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def get_current_device_context(
    authorization: Optional[str] = Header(None, alias="Authorization"),
    session: AsyncSession = Depends(get_db),
) -> AuthenticatedDeviceContext:
    """Authentication dependency for protected telemetry and device endpoints.

    Validates:
    - Bearer credential format and recognizable 'dvt_' prefix.
    - Matches active cryptographic hash in device_credentials.
    - Credential is active, not revoked, and not expired.
    - Device is ACTIVE.
    - Developer account is ACTIVE.
    - Organization is active.

    Returns:
        AuthenticatedDeviceContext with server-verified device, developer, and organization.
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or malformed Authorization header.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = authorization[7:].strip()
    if not token.startswith("dvt_"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid, expired, or revoked device credential.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    cred_hash = security.hash_secret(token)

    stmt = (
        select(DeviceCredential)
        .options(
            joinedload(DeviceCredential.device)
            .joinedload(Device.developer),
            joinedload(DeviceCredential.device)
            .joinedload(Device.organization),
        )
        .where(DeviceCredential.credential_hash == cred_hash)
    )
    result = await session.execute(stmt)
    cred = result.scalars().first()

    if not cred or not cred.is_active or cred.revoked_at is not None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid, expired, or revoked device credential.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    now = utc_now()
    if cred.expires_at is not None and ensure_utc(cred.expires_at) <= now:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid, expired, or revoked device credential.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    device = cred.device
    if not device or device.status != DeviceStatus.ACTIVE:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Device is deactivated or suspended.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    developer = device.developer
    if not developer or developer.status != AccountStatus.ACTIVE:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Developer account is deactivated or suspended.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    org = device.organization
    if not org or not org.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Organization is inactive.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return AuthenticatedDeviceContext(
        device=device,
        developer=developer,
        organization=org,
        credential=cred,
    )


async def require_admin_auth(
    authorization: Optional[str] = Header(None, alias="Authorization"),
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
    settings: Settings = Depends(get_app_settings),
    session: AsyncSession = Depends(get_db),
) -> AdminAuthContext:
    """Authorization dependency for administrative endpoints.

    Supports:
    1. Explicit bootstrap administrator key (via X-Admin-Key or non-device Bearer token).
    2. Active enrolled device with ORG_ADMIN developer role (strictly scoped to own organization).

    Rejects:
    - Unauthenticated callers.
    - Callers with invalid keys.
    - Enrolled devices with non-admin roles (e.g. DEVELOPER, TEAM_LEAD, AUDITOR).
    """
    # 1. Check bootstrap administrator key
    candidate_key = None
    if x_admin_key:
        candidate_key = x_admin_key.strip()
    elif authorization and authorization.startswith("Bearer "):
        token = authorization[7:].strip()
        if not token.startswith("dvt_") and not token.startswith("dve_"):
            candidate_key = token

    if candidate_key and settings.admin_api_key:
        if security.verify_admin_key(candidate_key, settings.admin_api_key):
            return AdminAuthContext(is_bootstrap=True, organization_id=None)

    # 2. Check enrolled device with ORG_ADMIN role
    if authorization and authorization.startswith("Bearer dvt_"):
        device_ctx = await get_current_device_context(authorization=authorization, session=session)
        if device_ctx.developer.role == DeveloperRole.ORG_ADMIN:
            return AdminAuthContext(
                is_bootstrap=False,
                organization_id=device_ctx.organization.id,
                device_context=device_ctx,
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Administrative role required for this operation.",
            )

    # 3. Reject unauthenticated caller
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Valid administrative authentication required.",
        headers={"WWW-Authenticate": "Bearer"},
    )


@dataclass
class AnalyticsAuthContext:
    """Strongly typed authorization context for read-only analytics operations."""

    is_bootstrap: bool
    role: Optional[DeveloperRole] = None
    organization_id: Optional[uuid.UUID] = None
    developer_id: Optional[uuid.UUID] = None
    allowed_developer_ids: Optional[Set[uuid.UUID]] = None
    device_context: Optional[AuthenticatedDeviceContext] = None

    def check_organization_access(self, target_org_id: Optional[uuid.UUID]) -> Optional[uuid.UUID]:
        """Enforce strict cross-organization tenant isolation boundary.

        Returns authoritative organization_id to use in query filters, or None if global bootstrap.
        """
        if self.is_bootstrap:
            return target_org_id

        if target_org_id is not None and target_org_id != self.organization_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Cross-organization analytics access is forbidden.",
            )
        return self.organization_id

    def can_access_developer(self, target_dev_id: uuid.UUID) -> bool:
        """Check if caller is authorized to view a specific developer's analytics."""
        if self.is_bootstrap:
            return True
        if self.role in (DeveloperRole.ORG_ADMIN, DeveloperRole.AUDITOR):
            return True
        if self.role == DeveloperRole.TEAM_LEAD:
            return self.allowed_developer_ids is not None and target_dev_id in self.allowed_developer_ids
        if self.role == DeveloperRole.DEVELOPER:
            return self.developer_id == target_dev_id
        return False


async def require_analytics_auth(
    authorization: Optional[str] = Header(None, alias="Authorization"),
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
    settings: Settings = Depends(get_app_settings),
    session: AsyncSession = Depends(get_db),
) -> AnalyticsAuthContext:
    """Authorization dependency for analytics endpoints.

    Supports:
    1. Bootstrap Admin Key (global or organization-scoped visibility).
    2. Active enrolled device with ORG_ADMIN, AUDITOR, TEAM_LEAD, or DEVELOPER role:
       - ORG_ADMIN / AUDITOR: complete organization scope.
       - TEAM_LEAD: scoped exclusively to developers within their team(s).
       - DEVELOPER: scoped strictly to their own developer identity.

    Rejects unauthenticated callers or invalid credentials with 401 Unauthorized.
    """
    # 1. Check bootstrap administrator key
    candidate_key = None
    if x_admin_key:
        candidate_key = x_admin_key.strip()
    elif authorization and authorization.startswith("Bearer "):
        token = authorization[7:].strip()
        if not token.startswith("dvt_") and not token.startswith("dve_"):
            candidate_key = token

    if candidate_key and settings.admin_api_key:
        if security.verify_admin_key(candidate_key, settings.admin_api_key):
            return AnalyticsAuthContext(is_bootstrap=True)

    # 2. Check enrolled device Bearer token
    if authorization and authorization.startswith("Bearer dvt_"):
        device_ctx = await get_current_device_context(authorization=authorization, session=session)
        dev = device_ctx.developer
        org_id = device_ctx.organization.id
        role = dev.role

        allowed_dev_ids: Optional[Set[uuid.UUID]] = None

        if role == DeveloperRole.DEVELOPER:
            allowed_dev_ids = {dev.id}
        elif role == DeveloperRole.TEAM_LEAD:
            from app.models.organization import TeamMembership
            # Query all teams where this team lead is a member
            lead_teams_stmt = select(TeamMembership.team_id).where(TeamMembership.developer_id == dev.id)
            lead_team_ids = (await session.execute(lead_teams_stmt)).scalars().all()

            if lead_team_ids:
                devs_stmt = select(TeamMembership.developer_id).where(TeamMembership.team_id.in_(lead_team_ids))
                team_dev_ids = set((await session.execute(devs_stmt)).scalars().all())
                team_dev_ids.add(dev.id)
                allowed_dev_ids = team_dev_ids
            else:
                allowed_dev_ids = {dev.id}
        elif role in (DeveloperRole.ORG_ADMIN, DeveloperRole.AUDITOR):
            allowed_dev_ids = None

        return AnalyticsAuthContext(
            is_bootstrap=False,
            role=role,
            organization_id=org_id,
            developer_id=dev.id,
            allowed_developer_ids=allowed_dev_ids,
            device_context=device_ctx,
        )

    # 3. Reject unauthenticated caller
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Valid authentication credentials required for analytics access.",
        headers={"WWW-Authenticate": "Bearer"},
    )

