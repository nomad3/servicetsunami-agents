"""SSH key support for the GitHub integration (2026-05-31).

Plan: docs/plans/2026-05-31-github-ssh-key-support.md (Codex + Luna reviewed).

Stores a Fernet-encrypted SSH PRIVATE key (``credential_key="ssh_private_key"``)
on the tenant's ``github`` integration so the code-worker can clone OAuth-blocked
org repos (NFL / ustwo) via ``git@github.com``, complementing the OAuth/HTTPS path.

Security posture (from the review):
- **Passphrase-protected keys are REJECTED at save** — the worker runs
  non-interactive; a passphrase would prompt and hang (no ``ssh-agent``).
- **Key material is NEVER logged or returned to a user** — only a SHA256
  fingerprint is surfaced for status display.
- A private key at rest is materially more sensitive than an OAuth token, so the
  decrypted key is exposed ONLY via the internal (service-to-service) fetch.
"""
from __future__ import annotations

import base64
import hashlib
import logging

from sqlalchemy.orm import Session

from app.models.integration_config import IntegrationConfig
from app.models.integration_credential import IntegrationCredential
from app.services.orchestration.credential_vault import (
    retrieve_credentials_for_skill,
    revoke_credential,
    store_credential,
)

logger = logging.getLogger(__name__)

GITHUB = "github"
SSH_KEY_CRED = "ssh_private_key"
SSH_FP_CRED = "ssh_key_fingerprint"  # non-secret; lets status display a fingerprint without decrypting the key


def validate_and_fingerprint_ssh_key(private_key: str) -> tuple[bool, str | None, str | None]:
    """Return ``(ok, fingerprint, error)``.

    Rejects passphrase-protected and invalid OpenSSH private keys. The fingerprint
    is ``SHA256:<base64>`` (``ssh-keygen -lf`` style), computed from the PUBLIC
    half — the private key is never exposed. Never logs key material.
    """
    from cryptography.hazmat.primitives import serialization

    if not private_key or not private_key.strip():
        return False, None, "empty SSH key"
    try:
        key = serialization.load_ssh_private_key(private_key.encode(), password=None)
    except (TypeError, ValueError) as exc:
        # An encrypted key with password=None raises a TypeError ("Password was not
        # given but private key is encrypted") OR a ValueError ("Key is password-
        # protected") depending on key format/version — detect either by message.
        msg = str(exc).lower()
        if any(t in msg for t in ("encrypt", "password", "protected", "passphrase")):
            return False, None, (
                "passphrase-protected SSH keys are not supported — the worker is "
                "non-interactive; provide a passphrase-less deploy/fine-grained key"
            )
        return False, None, "not a valid OpenSSH private key (expected an 'OPENSSH PRIVATE KEY' block)"
    except Exception:  # noqa: BLE001 - never leak parser internals; never raise
        return False, None, "could not parse SSH private key"

    try:
        pub = key.public_key().public_bytes(
            serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH
        )
        blob = base64.b64decode(pub.split()[1])
        fp = "SHA256:" + base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip("=")
    except Exception:  # noqa: BLE001
        fp = "SHA256:unavailable"
    return True, fp, None


def _config_holding_ssh_key(db: Session, tenant_id) -> IntegrationConfig | None:
    """The tenant's github config that currently holds an active ssh_private_key."""
    cred = (
        db.query(IntegrationCredential)
        .join(IntegrationConfig, IntegrationConfig.id == IntegrationCredential.integration_config_id)
        .filter(
            IntegrationConfig.tenant_id == tenant_id,
            IntegrationConfig.integration_name == GITHUB,
            IntegrationCredential.credential_key == SSH_KEY_CRED,
            IntegrationCredential.status == "active",
        )
        .first()
    )
    if not cred:
        return None
    return (
        db.query(IntegrationConfig)
        .filter(IntegrationConfig.id == cred.integration_config_id)
        .first()
    )


def get_or_create_github_config(db: Session, tenant_id) -> IntegrationConfig:
    """The tenant's github IntegrationConfig (preferring an enabled one), creating
    a minimal one if none exists (SSH-only, no prior OAuth connect required)."""
    cfg = (
        db.query(IntegrationConfig)
        .filter(IntegrationConfig.tenant_id == tenant_id, IntegrationConfig.integration_name == GITHUB)
        .order_by(IntegrationConfig.enabled.desc())
        .first()
    )
    if cfg:
        return cfg
    cfg = IntegrationConfig(tenant_id=tenant_id, integration_name=GITHUB, account_email=None, enabled=True)
    db.add(cfg)
    db.flush()
    return cfg


def save_ssh_key(db: Session, tenant_id, private_key: str) -> str:
    """Validate + store the SSH key (revoking any existing). Returns the SHA256
    fingerprint. Raises ``ValueError(message)`` on an invalid/passphrase key."""
    ok, fp, err = validate_and_fingerprint_ssh_key(private_key)
    if not ok:
        raise ValueError(err)
    cfg = get_or_create_github_config(db, tenant_id)
    for cred in (
        db.query(IntegrationCredential)
        .filter(
            IntegrationCredential.tenant_id == tenant_id,
            IntegrationCredential.integration_config_id == cfg.id,
            IntegrationCredential.credential_key.in_([SSH_KEY_CRED, SSH_FP_CRED]),
            IntegrationCredential.status == "active",
        )
        .all()
    ):
        revoke_credential(db, credential_id=cred.id, tenant_id=tenant_id)
    store_credential(
        db, integration_config_id=cfg.id, tenant_id=tenant_id,
        credential_key=SSH_KEY_CRED, plaintext_value=private_key, credential_type="ssh_key",
    )
    store_credential(
        db, integration_config_id=cfg.id, tenant_id=tenant_id,
        credential_key=SSH_FP_CRED, plaintext_value=fp, credential_type="metadata",
    )
    db.commit()
    # fp is the PUBLIC fingerprint (non-secret); the private key is never logged.
    logger.info("github ssh key saved tenant=%s fingerprint=%s", str(tenant_id)[:8], fp)
    return fp


def ssh_key_status(db: Session, tenant_id) -> dict:
    """Presence + fingerprint only — never returns the key."""
    cfg = _config_holding_ssh_key(db, tenant_id)
    if not cfg:
        return {"present": False, "fingerprint": None}
    creds = retrieve_credentials_for_skill(db, cfg.id, tenant_id)
    return {"present": SSH_KEY_CRED in creds, "fingerprint": creds.get(SSH_FP_CRED)}


def delete_ssh_key(db: Session, tenant_id) -> bool:
    """Revoke the stored SSH key + its fingerprint. Returns whether anything was removed."""
    removed = False
    for cred in (
        db.query(IntegrationCredential)
        .join(IntegrationConfig, IntegrationConfig.id == IntegrationCredential.integration_config_id)
        .filter(
            IntegrationConfig.tenant_id == tenant_id,
            IntegrationConfig.integration_name == GITHUB,
            IntegrationCredential.credential_key.in_([SSH_KEY_CRED, SSH_FP_CRED]),
            IntegrationCredential.status == "active",
        )
        .all()
    ):
        revoke_credential(db, credential_id=cred.id, tenant_id=tenant_id)
        removed = True
    db.commit()
    return removed


def read_ssh_key_for_worker(db: Session, tenant_id) -> str | None:
    """Internal (service-to-service): the decrypted SSH private key, or None."""
    cfg = _config_holding_ssh_key(db, tenant_id)
    if not cfg:
        return None
    creds = retrieve_credentials_for_skill(db, cfg.id, tenant_id)
    return creds.get(SSH_KEY_CRED)
