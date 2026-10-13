"""Enrollment and device lifecycle business logic service.

Handles:
- Single-use enrollment token generation, cryptographic hashing, and lifecycle tracking.
- Atomic token redemption with concurrency control and rollback guarantees.
- Device workstation registration and hardware binding.
- High-entropy device credential generation and server-side hash persistence.
- Device credential and enrollment token revocation.
"""

from datetime import datetime, timedelta
import logging
from typing import Optional, Tuple
import uuid

from fastapi import HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.base import ensure_utc, utc_now
from app.models.developer import AccountStatus, Developer, DeveloperRole
from app.models.device import Device, DeviceCredential, DeviceStatus
from app.models.organization import Organization
from app.models.token import EnrollmentToken, TokenStatus
from app.services import security

logger = logging.getLogger("telemetry.enrollment")


class EnrollmentService:
    """Service encapsulating enrollment workflows and device credential administration."""

    @staticmethod
    async def issue_enrollment_invitation(
        session: AsyncSession,
        organization_id: uuid.UUID,
        email: str,
        full_name: str,
        role: DeveloperRole = DeveloperRole.DEVELOPER,
        expires_in_hours: int = 24,
    ) -> Tuple[EnrollmentToken, str]:
        """Issue a single-use enrollment token for an active organization and developer.

        Args:
            session: Async database session.
            organization_id: Target organization UUID.
            email: Developer email.
            full_name: Developer display name.
            role: Developer organization role.
            expires_in_hours: Token validity duration in hours.

        Returns:
            Tuple of (EnrollmentToken model, plaintext_token string).
            The plaintext token must be delivered to caller once and discarded.
        """
        # 1. Validate Organization exists and is active
        org_stmt = select(Organization).where(Organization.id == organization_id)
        org = (await session.execute(org_stmt)).scalars().first()
        if not org or not org.is_active:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Organization does not exist or is inactive.",
            )

        # 2. Retrieve or create developer account
        dev_stmt = select(Developer).where(
            Developer.organization_id == organization_id,
            Developer.email == email,
        )
        developer = (await session.execute(dev_stmt)).scalars().first()

        if developer is None:
            developer = Developer(
                organization_id=organization_id,
                email=email,
                full_name=full_name,
                role=role,
                status=AccountStatus.ACTIVE,
            )
            session.add(developer)
            await session.flush()
        else:
            if developer.status != AccountStatus.ACTIVE:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Developer account is suspended or deactivated.",
                )

        # 3. Generate high-entropy enrollment token (>= 256 bits entropy)
        plaintext_token = security.generate_enrollment_token()
        token_hash = security.hash_secret(plaintext_token)
        expires_at = utc_now() + timedelta(hours=expires_in_hours)

        token_record = EnrollmentToken(
            organization_id=organization_id,
            developer_id=developer.id,
            token_hash=token_hash,
            status=TokenStatus.PENDING,
            expires_at=expires_at,
        )
        session.add(token_record)
        await session.flush()

        logger.info(
            "Issued enrollment token id=%s for developer_id=%s in org_id=%s (expires=%s)",
            token_record.id,
            developer.id,
            organization_id,
            expires_at.isoformat(),
        )

        return token_record, plaintext_token

    @staticmethod
    async def claim_enrollment_token(
        session: AsyncSession,
        plaintext_token: str,
        hardware_uuid: str,
        hostname: str,
        platform: str,
        agent_version: str,
        credential_expiry_days: Optional[int] = None,
    ) -> Tuple[Device, DeviceCredential, str]:
        """Atomically claim an enrollment token and register/credential a developer device.

        Uses row-level locking (with_for_update) to guarantee zero double-spend or
        concurrent replay races. If device registration fails, the transaction rolls back,
        leaving the token unconsumed.

        Args:
            session: Async database session.
            plaintext_token: The plaintext 'dve_...' token presented by the workstation.
            hardware_uuid: Unique hardware identifier.
            hostname: Hostname of workstation.
            platform: OS platform.
            agent_version: Telemetry agent software version.
            credential_expiry_days: Optional credential expiration in days.

        Returns:
            Tuple of (Device model, DeviceCredential model, plaintext_credential string).
        """
        token_hash = security.hash_secret(plaintext_token.strip())

        # 1. Row-level locked query on enrollment token to prevent concurrent redemption
        token_stmt = (
            select(EnrollmentToken)
            .where(EnrollmentToken.token_hash == token_hash)
            .with_for_update()
        )
        token_record = (await session.execute(token_stmt)).scalars().first()

        # Generic reject if token not found, already consumed, or revoked
        if token_record is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid, expired, or already consumed enrollment token.",
            )

        if token_record.status != TokenStatus.PENDING:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid, expired, or already consumed enrollment token.",
            )

        # Check expiration
        now = utc_now()
        if ensure_utc(token_record.expires_at) <= now:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid, expired, or already consumed enrollment token.",
            )

        # 2. Verify bound developer and organization are active
        dev_stmt = select(Developer).where(Developer.id == token_record.developer_id)
        developer = (await session.execute(dev_stmt)).scalars().first()
        if not developer or developer.status != AccountStatus.ACTIVE:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Bound developer account is inactive or not found.",
            )

        org_stmt = select(Organization).where(Organization.id == token_record.organization_id)
        org = (await session.execute(org_stmt)).scalars().first()
        if not org or not org.is_active:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Bound organization is inactive or not found.",
            )

        # 3. Locate or create device for (developer_id, hardware_uuid)
        device_stmt = (
            select(Device)
            .where(
                Device.developer_id == developer.id,
                Device.hardware_uuid == hardware_uuid,
            )
            .with_for_update()
        )
        device = (await session.execute(device_stmt)).scalars().first()

        if device is None:
            device = Device(
                organization_id=org.id,
                developer_id=developer.id,
                hardware_uuid=hardware_uuid,
                hostname=hostname,
                platform=platform,
                agent_version=agent_version,
                status=DeviceStatus.ACTIVE,
                enrolled_at=now,
                last_seen_at=now,
            )
            session.add(device)
            await session.flush()
        else:
            # Re-enrollment or hardware update
            device.hostname = hostname
            device.platform = platform
            device.agent_version = agent_version
            device.status = DeviceStatus.ACTIVE
            device.last_seen_at = now
            await session.flush()

        # 4. Generate high-entropy device credential (>= 256 bits entropy)
        plaintext_cred = security.generate_device_credential()
        cred_hash = security.hash_secret(plaintext_cred)
        token_prefix = security.extract_token_prefix(plaintext_cred)
        cred_expires_at = (
            now + timedelta(days=credential_expiry_days)
            if credential_expiry_days
            else None
        )

        credential = DeviceCredential(
            device_id=device.id,
            credential_hash=cred_hash,
            token_prefix=token_prefix,
            is_active=True,
            created_at=now,
            expires_at=cred_expires_at,
        )
        session.add(credential)
        await session.flush()

        # 5. Mark enrollment token as CONSUMED in the same atomic transaction
        token_record.status = TokenStatus.CONSUMED
        token_record.consumed_at = now
        token_record.consumed_by_device_id = device.id
        await session.flush()

        logger.info(
            "Device enrolled successfully: device_id=%s (hardware=%s, developer_id=%s, org_id=%s)",
            device.id,
            hardware_uuid[:8] + "...",
            developer.id,
            org.id,
        )

        return device, credential, plaintext_cred

    @staticmethod
    async def revoke_credential(
        session: AsyncSession,
        credential_id: uuid.UUID,
    ) -> DeviceCredential:
        """Revoke a specific device credential immediately.

        Args:
            session: Async database session.
            credential_id: UUID of the DeviceCredential to revoke.

        Returns:
            Updated DeviceCredential.
        """
        stmt = select(DeviceCredential).where(DeviceCredential.id == credential_id)
        cred = (await session.execute(stmt)).scalars().first()
        if not cred:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Device credential not found.",
            )

        if cred.is_active:
            cred.is_active = False
            cred.revoked_at = utc_now()
            await session.flush()
            logger.info("Device credential revoked: credential_id=%s device_id=%s", cred.id, cred.device_id)

        return cred

    @staticmethod
    async def revoke_device(
        session: AsyncSession,
        device_id: uuid.UUID,
    ) -> Tuple[Device, int]:
        """Revoke a device and deactivate all associated credentials immediately.

        Args:
            session: Async database session.
            device_id: UUID of Device to revoke.

        Returns:
            Tuple of (Device model, revoked_credentials_count).
        """
        stmt = select(Device).where(Device.id == device_id)
        device = (await session.execute(stmt)).scalars().first()
        if not device:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Device not found.",
            )

        now = utc_now()
        device.status = DeviceStatus.REVOKED

        # Deactivate all active credentials for this device
        cred_update_stmt = (
            update(DeviceCredential)
            .where(
                DeviceCredential.device_id == device_id,
                DeviceCredential.is_active == True,  # noqa: E712
            )
            .values(is_active=False, revoked_at=now)
        )
        res = await session.execute(cred_update_stmt)
        revoked_count = res.rowcount
        await session.flush()

        logger.info("Device revoked: device_id=%s with %d credentials deactivated", device_id, revoked_count)
        return device, revoked_count

    @staticmethod
    async def revoke_enrollment_token(
        session: AsyncSession,
        token_id: uuid.UUID,
    ) -> EnrollmentToken:
        """Revoke an unconsumed enrollment invitation token.

        Args:
            session: Async database session.
            token_id: UUID of EnrollmentToken to revoke.

        Returns:
            Updated EnrollmentToken.
        """
        stmt = select(EnrollmentToken).where(EnrollmentToken.id == token_id)
        token_record = (await session.execute(stmt)).scalars().first()
        if not token_record:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Enrollment token not found.",
            )

        if token_record.status == TokenStatus.PENDING:
            token_record.status = TokenStatus.REVOKED
            token_record.revoked_at = utc_now()
            await session.flush()
            logger.info("Enrollment token revoked: token_id=%s", token_record.id)

        return token_record
