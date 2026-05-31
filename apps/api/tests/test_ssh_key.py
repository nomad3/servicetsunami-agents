"""SSH key validation for the GitHub integration (PR1, plan 2026-05-31).

Security-critical: rejects passphrase-protected + invalid keys; produces a
fingerprint without exposing the private key.
"""
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

from app.services.ssh_key import validate_and_fingerprint_ssh_key


def _gen_openssh_key(passphrase: str | None = None) -> str:
    key = ed25519.Ed25519PrivateKey.generate()
    enc = (
        serialization.BestAvailableEncryption(passphrase.encode())
        if passphrase
        else serialization.NoEncryption()
    )
    return key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.OpenSSH, enc
    ).decode()


def test_valid_passphraseless_key_accepted_with_fingerprint():
    ok, fp, err = validate_and_fingerprint_ssh_key(_gen_openssh_key())
    assert ok is True
    assert err is None
    assert fp and fp.startswith("SHA256:")
    # The fingerprint must NOT contain the private key material.
    assert "PRIVATE KEY" not in fp


def test_passphrase_protected_key_is_rejected():
    ok, fp, err = validate_and_fingerprint_ssh_key(_gen_openssh_key(passphrase="hunter2"))
    assert ok is False
    assert fp is None
    assert "passphrase" in (err or "").lower()


def test_invalid_text_is_rejected():
    ok, fp, err = validate_and_fingerprint_ssh_key("definitely not a private key")
    assert ok is False
    assert fp is None
    assert err


def test_empty_is_rejected():
    for bad in ("", "   ", "\n"):
        ok, fp, err = validate_and_fingerprint_ssh_key(bad)
        assert ok is False
        assert err


def test_fingerprint_is_stable_for_same_key():
    k = _gen_openssh_key()
    _, fp1, _ = validate_and_fingerprint_ssh_key(k)
    _, fp2, _ = validate_and_fingerprint_ssh_key(k)
    assert fp1 == fp2


# ── storage round-trip (real models + Fernet vault) ──────────────────────────
import uuid  # noqa: E402

import pytest  # noqa: E402

from app.db.base import Base  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402
from app.models.tenant import Tenant  # noqa: E402
from app.services import ssh_key as sshmod  # noqa: E402


# The storage round-trip needs the real Postgres models (UUID / pgvector); skip on
# sqlite (the default `api (pytest)` job) — runs in `api (integration, postgres)`.
_PG_ONLY = pytest.mark.skipif(
    engine.dialect.name != "postgresql", reason="needs postgres models (UUID/pgvector)"
)


@pytest.fixture(name="db_session")
def _db_session():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    yield db
    db.close()
    Base.metadata.drop_all(bind=engine)


@pytest.fixture(name="tenant_id")
def _tenant_id(db_session):
    t = Tenant(name=f"ssh-test-{uuid.uuid4().hex[:8]}")
    db_session.add(t)
    db_session.commit()
    return t.id


@_PG_ONLY
def test_storage_round_trip(db_session, tenant_id):
    key = _gen_openssh_key()
    fp = sshmod.save_ssh_key(db_session, tenant_id, key)
    assert fp.startswith("SHA256:")
    status = sshmod.ssh_key_status(db_session, tenant_id)
    assert status["present"] is True
    assert status["fingerprint"] == fp
    # the worker fetch returns the real decrypted key
    assert sshmod.read_ssh_key_for_worker(db_session, tenant_id).strip() == key.strip()
    # delete revokes both the key and the fingerprint
    assert sshmod.delete_ssh_key(db_session, tenant_id) is True
    assert sshmod.ssh_key_status(db_session, tenant_id)["present"] is False
    assert sshmod.read_ssh_key_for_worker(db_session, tenant_id) is None


@_PG_ONLY
def test_save_rejects_passphrase_key_before_storing(db_session, tenant_id):
    with pytest.raises(ValueError):
        sshmod.save_ssh_key(db_session, tenant_id, _gen_openssh_key(passphrase="x"))
    # nothing was stored
    assert sshmod.read_ssh_key_for_worker(db_session, tenant_id) is None


@_PG_ONLY
def test_save_overwrites_previous_key(db_session, tenant_id):
    fp1 = sshmod.save_ssh_key(db_session, tenant_id, _gen_openssh_key())
    fp2 = sshmod.save_ssh_key(db_session, tenant_id, _gen_openssh_key())
    assert fp1 != fp2
    # only the latest is active
    assert sshmod.ssh_key_status(db_session, tenant_id)["fingerprint"] == fp2


@_PG_ONLY
def test_no_key_clean_status_and_fetch(db_session, tenant_id):
    assert sshmod.ssh_key_status(db_session, tenant_id) == {"present": False, "fingerprint": None}
    assert sshmod.read_ssh_key_for_worker(db_session, tenant_id) is None
