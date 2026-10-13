"""Cryptographic security utilities for enrollment tokens and device credentials.

Security Invariants:
1. All tokens are generated using secrets.token_urlsafe(32), providing >= 256 bits of cryptographic entropy.
2. Plaintext secrets are never stored server-side, never logged, and never included in error messages.
3. Cryptographic comparison uses hmac.compare_digest to prevent timing attacks.
4. Persistent token/credential records store strictly SHA-256 hex digests (64 chars).
"""

import hashlib
import hmac
import secrets


ENROLLMENT_TOKEN_PREFIX = "dve_"
DEVICE_CREDENTIAL_PREFIX = "dvt_"
TOKEN_PREFIX_STORED_LENGTH = 10  # Length of prefix saved in device_credentials table


def generate_enrollment_token() -> str:
    """Generate a high-entropy developer enrollment invitation token.

    Returns:
        str: Plaintext token starting with 'dve_' followed by 32 bytes (256 bits)
             of cryptographically secure URL-safe base64 data.
    """
    raw_entropy = secrets.token_urlsafe(32)
    return f"{ENROLLMENT_TOKEN_PREFIX}{raw_entropy}"


def generate_device_credential() -> str:
    """Generate a high-entropy device authentication Bearer token.

    Returns:
        str: Plaintext token starting with 'dvt_' followed by 32 bytes (256 bits)
             of cryptographically secure URL-safe base64 data.
    """
    raw_entropy = secrets.token_urlsafe(32)
    return f"{DEVICE_CREDENTIAL_PREFIX}{raw_entropy}"


def hash_secret(secret: str) -> str:
    """Compute the SHA-256 cryptographic hash of a token or credential.

    Args:
        secret: Plaintext token or secret string.

    Returns:
        str: 64-character lowercase hexadecimal SHA-256 digest.
    """
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def extract_token_prefix(token: str, max_chars: int = TOKEN_PREFIX_STORED_LENGTH) -> str:
    """Extract a short recognizable prefix for device credential identification.

    Args:
        token: Plaintext token string.
        max_chars: Maximum prefix characters to return.

    Returns:
        str: Substring of prefix characters (e.g. 'dvt_abcdef').
    """
    return token[:max_chars]


def verify_secret_hash(secret: str, expected_hash: str) -> bool:
    """Verify a secret against an expected SHA-256 hex digest using constant-time comparison.

    Args:
        secret: Candidate plaintext secret.
        expected_hash: Stored 64-character hex digest.

    Returns:
        bool: True if hashes match, False otherwise.
    """
    computed = hash_secret(secret)
    return hmac.compare_digest(computed, expected_hash)


def verify_admin_key(candidate: str, expected: str) -> bool:
    """Verify administrator API key in constant time.

    Args:
        candidate: Candidate admin key provided by caller.
        expected: Configured admin key.

    Returns:
        bool: True if strings match, False otherwise.
    """
    if not candidate or not expected:
        return False
    return hmac.compare_digest(candidate, expected)
