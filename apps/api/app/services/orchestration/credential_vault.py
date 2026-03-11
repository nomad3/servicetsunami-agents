"""
Credential Vault Service - AES-256 encrypted credential storage.

Provides encryption/decryption for integration credentials with full
multi-tenant isolation. Never logs plaintext credential values.
"""

import uuid
import logging
from datetime import datetime
from typing import Dict, Optional

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.integration_credential import IntegrationCredential

logger = logging.getLogger(__name__)


class CredentialVault:
    """AES-256 encrypted credential storage using Fernet symmetric encryption."""

    def __init__(self):
        key = settings.ENCRYPTION_KEY
        if not key:
            raise ValueError("ENCRYPTION_KEY must be set in environment configuration")
        self.fernet = Fernet(key.encode() if isinstance(key, str) else key)

    def encrypt(self, plaintext: str) -> str:
        """Encrypt a plaintext value. Never log the input."""
        return self.fernet.encrypt(plaintext.encode()).decode()

    def decrypt(self, encrypted_value: str) -> str:
        """Decrypt an encrypted value. Never log the output."""
        return self.fernet.decrypt(encrypted_value.encode()).decode()


# ---------------------------------------------------------------------------
# CRUD helpers
# ---------------------------------------------------------------------------

def _get_vault() -> CredentialVault:
    """Lazily instantiate a CredentialVault (ensures ENCRYPTION_KEY is read at call time)."""
    return CredentialVault()


def store_credential(
    db: Session,
    integration_config_id: uuid.UUID,
    tenant_id: uuid.UUID,
    credential_key: str,
    plaintext_value: str,
    credential_type: str = "api_key",
) -> IntegrationCredential:
    """
    Encrypt and store a credential for an integration configuration.

    Args:
        db: SQLAlchemy database session.
        integration_config_id: ID of the IntegrationConfig this credential belongs to.
        tenant_id: Tenant ID for multi-tenant isolation.
        credential_key: Logical name (e.g. "api_key", "oauth_token").
        plaintext_value: The secret value to encrypt. **Never logged.**
        credential_type: One of api_key, oauth_token, webhook_url, basic_auth.

    Returns:
        The persisted IntegrationCredential row (encrypted_value is ciphertext).
    """
    vault = _get_vault()
    encrypted = vault.encrypt(plaintext_value)

    # Deactivate any existing credential with the same key (upsert behavior)
    existing = (
        db.query(IntegrationCredential)
        .filter(
            IntegrationCredential.integration_config_id == integration_config_id,
            IntegrationCredential.tenant_id == tenant_id,
            IntegrationCredential.credential_key == credential_key,
            IntegrationCredential.status == "active",
        )
        .all()
    )
    for old in existing:
        old.status = "revoked"

    credential = IntegrationCredential(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        integration_config_id=integration_config_id,
        credential_key=credential_key,
        encrypted_value=encrypted,
        credential_type=credential_type,
        status="active",
    )
    db.add(credential)
    db.commit()
    db.refresh(credential)

    logger.info(
        "Stored credential key='%s' type='%s' for integration_config=%s tenant=%s",
        credential_key,
        credential_type,
        integration_config_id,
        tenant_id,
    )
    return credential


def retrieve_credential(
    db: Session,
    credential_id: uuid.UUID,
    tenant_id: uuid.UUID,
) -> Optional[str]:
    """
    Decrypt and return a single credential's plaintext value.

    Args:
        db: SQLAlchemy database session.
        credential_id: Primary key of the IntegrationCredential.
        tenant_id: Tenant ID for multi-tenant isolation.

    Returns:
        Decrypted plaintext string, or None if not found / not active.
    """
    credential = (
        db.query(IntegrationCredential)
        .filter(
            IntegrationCredential.id == credential_id,
            IntegrationCredential.tenant_id == tenant_id,
            IntegrationCredential.status == "active",
        )
        .first()
    )

    if credential is None:
        logger.warning(
            "Credential %s not found or not active for tenant %s",
            credential_id,
            tenant_id,
        )
        return None

    vault = _get_vault()
    try:
        plaintext = vault.decrypt(credential.encrypted_value)
    except InvalidToken:
        logger.error(
            "Failed to decrypt credential %s — key mismatch or corrupted data",
            credential_id,
        )
        return None

    # Update last_used_at timestamp
    credential.last_used_at = datetime.utcnow()
    db.commit()

    return plaintext


def retrieve_credentials_for_skill(
    db: Session,
    integration_config_id: uuid.UUID,
    tenant_id: uuid.UUID,
) -> Dict[str, str]:
    """
    Return all active credentials for an integration config as {key: decrypted_value}.

    Args:
        db: SQLAlchemy database session.
        integration_config_id: The IntegrationConfig whose credentials to fetch.
        tenant_id: Tenant ID for multi-tenant isolation.

    Returns:
        Dictionary mapping credential_key to decrypted plaintext value.
        Credentials that fail to decrypt are silently skipped.
    """
    credentials = (
        db.query(IntegrationCredential)
        .filter(
            IntegrationCredential.integration_config_id == integration_config_id,
            IntegrationCredential.tenant_id == tenant_id,
            IntegrationCredential.status == "active",
        )
        .all()
    )

    vault = _get_vault()
    result: Dict[str, str] = {}
    now = datetime.utcnow()

    for cred in credentials:
        try:
            result[cred.credential_key] = vault.decrypt(cred.encrypted_value)
            cred.last_used_at = now
        except InvalidToken:
            logger.error(
                "Failed to decrypt credential %s (key='%s') — skipping",
                cred.id,
                cred.credential_key,
            )

    db.commit()

    logger.info(
        "Retrieved %d/%d credentials for integration_config=%s tenant=%s",
        len(result),
        len(credentials),
        integration_config_id,
        tenant_id,
    )
    return result


def revoke_credential(
    db: Session,
    credential_id: uuid.UUID,
    tenant_id: uuid.UUID,
) -> bool:
    """
    Revoke a credential by setting its status to 'revoked'.

    Args:
        db: SQLAlchemy database session.
        credential_id: Primary key of the IntegrationCredential.
        tenant_id: Tenant ID for multi-tenant isolation.

    Returns:
        True if the credential was found and revoked, False otherwise.
    """
    credential = (
        db.query(IntegrationCredential)
        .filter(
            IntegrationCredential.id == credential_id,
            IntegrationCredential.tenant_id == tenant_id,
        )
        .first()
    )

    if credential is None:
        logger.warning(
            "Cannot revoke credential %s — not found for tenant %s",
            credential_id,
            tenant_id,
        )
        return False

    credential.status = "revoked"
    credential.updated_at = datetime.utcnow()
    db.commit()

    logger.info("Revoked credential %s for tenant %s", credential_id, tenant_id)
    return True
