import pytest
import os
import sys
import importlib
from unittest.mock import patch
from pydantic import ValidationError


def test_secret_key_has_no_insecure_default(monkeypatch, tmp_path):
    """Settings must raise on startup when critical env vars are missing.

    Uses ``monkeypatch.chdir`` into an empty directory so pydantic-settings
    cannot pick up the developer's local ``apps/api/.env`` file (which would
    silently provide the missing values).
    """
    env_without_secrets = {k: v for k, v in os.environ.items()
                           if k not in ("SECRET_KEY", "MCP_API_KEY", "API_INTERNAL_KEY")}
    monkeypatch.chdir(tmp_path)
    with patch.dict(os.environ, env_without_secrets, clear=True):
        # Remove any cached module so the reload triggers a fresh Settings() call
        sys.modules.pop("app.core.config", None)
        with pytest.raises(ValidationError) as exc_info:
            importlib.import_module("app.core.config")
        # Verify the error is about missing required fields, not some unrelated failure
        error_str = str(exc_info.value)
        assert any(field in error_str for field in [
            "SECRET_KEY", "MCP_API_KEY", "API_INTERNAL_KEY",
            "secret_key", "mcp_api_key", "api_internal_key",
        ]), f"Error should mention missing required fields, got: {error_str}"


os.environ.setdefault("SECRET_KEY", "test-secret-key-for-tests")
os.environ.setdefault("MCP_API_KEY", "test-mcp-key")
os.environ.setdefault("API_INTERNAL_KEY", "test-internal-key")
os.environ.setdefault("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/agentprovision")

from unittest.mock import MagicMock, patch
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _make_oauth_test_client():
    """Create a minimal FastAPI test app with just the OAuth router mounted,
    bypassing database initialisation which requires a live PostgreSQL server."""
    # Patch init_db before importing app.main so the module-level DB call is a no-op
    with patch("app.db.init_db.init_db", return_value=None), \
         patch("app.db.session.SessionLocal", return_value=MagicMock()):
        # Reload the oauth module in isolation to avoid DB dependency
        import importlib
        import app.api.v1.oauth as oauth_module
        importlib.reload(oauth_module)

    test_app = FastAPI()

    # Provide a stub DB dependency so the router can be mounted without a live DB
    from app.api import deps

    def _stub_db():
        yield MagicMock()

    test_app.dependency_overrides[deps.get_db] = _stub_db
    test_app.include_router(oauth_module.router, prefix="/api/v1/oauth")
    return TestClient(test_app)


def test_oauth_callback_error_escapes_xss():
    """XSS payload in ?error must be HTML-escaped in the response."""
    client = _make_oauth_test_client()
    xss = "<script>alert(1)</script>"
    resp = client.get(f"/api/v1/oauth/google/callback?error={xss}")
    assert resp.status_code == 200
    body = resp.text
    assert "<script>alert(1)</script>" not in body, "Raw XSS tag must NOT appear in response"
    assert "&lt;script&gt;" in body, "Escaped form must appear in response"


def test_oauth_callback_img_injection_escaped():
    """HTML injection via error param must be escaped in <p> tag."""
    client = _make_oauth_test_client()
    resp = client.get('/api/v1/oauth/google/callback?error="><img src=x onerror=alert(1)>')
    body = resp.text
    assert '<img' not in body, "Raw <img> must not appear"
    assert '&lt;img' in body, "Escaped <img> must appear"


def test_skill_github_import_requires_superuser():
    """Regular (non-superuser) users must receive 403 on skill import."""
    import uuid
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.api import deps
    from app.models.user import User
    import app.api.v1.skills_new as skills_module

    # Create a mock non-superuser
    mock_user = User(
        id=uuid.uuid4(),
        email="regular@test.com",
        tenant_id=uuid.uuid4(),
        is_active=True,
        is_superuser=False,
        hashed_password="x",
    )

    test_app = FastAPI()

    def _stub_db():
        yield MagicMock()

    test_app.dependency_overrides[deps.get_db] = _stub_db
    test_app.dependency_overrides[deps.get_current_active_user] = lambda: mock_user
    test_app.include_router(skills_module.router, prefix="/api/v1/skills")

    client = TestClient(test_app, raise_server_exceptions=False)
    resp = client.post(
        "/api/v1/skills/library/import-github",
        json={"repo_url": "https://github.com/example/skill"},
    )
    assert resp.status_code == 403, f"Expected 403, got {resp.status_code}: {resp.text}"


def test_skill_execution_does_not_expose_secret_key(tmp_path):
    """Skill scripts must not be able to read SECRET_KEY from the environment."""
    import textwrap
    os.environ["SECRET_KEY"] = "super-secret-sentinel-value"

    script = tmp_path / "script.py"
    script.write_text(textwrap.dedent("""
        import os
        def execute(inputs):
            return {"secret": os.environ.get("SECRET_KEY", "NOT_FOUND")}
    """))

    from app.services.skill_manager import SkillManager
    mgr = SkillManager()
    result = mgr._execute_python("test-skill", str(script), {})

    assert result.get("result", {}).get("secret") != "super-secret-sentinel-value", \
        "SECRET_KEY must not be visible to skill scripts"


def _make_auth_test_client():
    """Create a minimal FastAPI test app with just the auth router mounted,
    bypassing database initialisation which requires a live PostgreSQL server."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.api import deps
    import app.api.v1.auth as auth_module

    test_app = FastAPI()

    def _stub_db():
        yield MagicMock()

    test_app.dependency_overrides[deps.get_db] = _stub_db
    test_app.include_router(auth_module.router, prefix="/api/v1/auth")
    return TestClient(test_app, raise_server_exceptions=False)


def test_password_recovery_no_debug_token():
    """debug_token must never appear in the password recovery response."""
    from app.services import users as user_service

    with patch.object(user_service, "get_user_by_email", return_value=None):
        client = _make_auth_test_client()
        resp = client.post("/api/v1/auth/password-recovery/nonexistent@example.com")
    assert resp.status_code == 200
    data = resp.json()
    assert "debug_token" not in data, "debug_token must not be exposed in API response"
    assert "token" not in data, "Raw token must not be exposed in API response"


def test_password_recovery_same_message_for_existing_and_missing_email():
    """Response message must be identical for existing and non-existing emails (prevent enumeration)."""
    import uuid
    from app.models.user import User
    from app.services import users as user_service

    mock_user = MagicMock(spec=User)
    mock_user.password_reset_token = None
    mock_user.password_reset_expires = None
    mock_user.id = uuid.uuid4()
    mock_user.email = "existing@example.com"

    with patch.object(user_service, "get_user_by_email", return_value=None):
        client = _make_auth_test_client()
        resp_missing = client.post(
            "/api/v1/auth/password-recovery/definitely_not_registered_12345@example.com"
        )

    with patch.object(user_service, "get_user_by_email", return_value=mock_user):
        client2 = _make_auth_test_client()
        # db.add and db.commit are no-ops on the MagicMock stub db
        resp_existing = client2.post(
            "/api/v1/auth/password-recovery/existing@example.com"
        )

    assert resp_missing.status_code == resp_existing.status_code, \
        "HTTP status must be identical for existing and missing emails"
    assert resp_missing.json().get("message") == resp_existing.json().get("message"), \
        "Response messages must be identical to prevent user enumeration"


# ── Password recovery security hardening tests (2026-05-12 review) ───
# Each test pins one of the BLOCKER/IMPORTANT fixes so a future
# refactor that regresses the behaviour fails CI loudly.


def _make_reset_test_client(mock_user_or_none):
    """Build a test client whose db.query(User).filter(...).with_for_update().first()
    returns `mock_user_or_none`. Used by the cookie-binding regression
    tests to exercise both the user-exists and user-missing paths.

    N3-5 (security review round 3): the stub's `_StubDb.commit` is
    intentionally a no-op. Tests using this client cannot verify
    post-commit STATE on the user row (e.g. that the attempt-counter
    actually persisted). The current tests only assert response shape,
    so this is fine — but if a future test needs to verify post-commit
    persistence (e.g. attempt-counter > 0 after 1 wrong attempt),
    it'll need a real Session or a smarter stub that mutates
    `mock_user_or_none` in place. Filed for visibility, not blocking.
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.api import deps
    import app.api.v1.auth as auth_module

    class _StubQuery:
        def filter(self, *_a, **_k): return self
        def with_for_update(self): return self
        def first(self): return mock_user_or_none

    class _StubDb:
        def query(self, *_a, **_k): return _StubQuery()
        def add(self, *_a, **_k): pass
        def commit(self): pass
        def refresh(self, *_a, **_k): pass

    def _stub_db_dep():
        yield _StubDb()

    test_app = FastAPI()
    test_app.dependency_overrides[deps.get_db] = _stub_db_dep
    test_app.include_router(auth_module.router, prefix="/api/v1/auth")
    return TestClient(test_app, raise_server_exceptions=False)


def test_reset_password_without_csrf_cookie_returns_same_browser_email_agnostic():
    """B3-1 (round-3 review): missing-cookie check fires BEFORE the
    user-lookup, so the same-browser detail is email-agnostic. An
    attacker without a cookie cannot use the response detail to
    enumerate which emails have active in-flight resets — every
    cookie-less request returns the same friendly hint regardless
    of whether the email exists in the DB at all.

    This test asserts both halves of the property:
      a) Cookie-less + valid user → same-browser detail
      b) Cookie-less + non-existent user → ALSO same-browser detail
    """
    import uuid
    import hashlib
    from datetime import datetime, timedelta
    from app.models.user import User

    # (a) User exists with active reset state.
    mock_user = MagicMock(spec=User)
    mock_user.id = uuid.uuid4()
    mock_user.email = "victim@example.com"
    mock_user.password_reset_token = hashlib.sha256(b"correcttoken").hexdigest()
    mock_user.password_reset_csrf_hash = hashlib.sha256(b"correctcsrf").hexdigest()
    mock_user.password_reset_expires = datetime.utcnow() + timedelta(hours=1)
    mock_user.password_reset_attempts = 0

    client_a = _make_reset_test_client(mock_user)
    resp_a = client_a.post(
        "/api/v1/auth/reset-password",
        json={
            "email": "victim@example.com",
            "token": "correcttoken",
            "new_password": "ValidPass1234",
        },
    )
    assert resp_a.status_code == 400
    detail_a = (resp_a.json() or {}).get("detail", "")
    assert "same browser" in detail_a.lower()

    # (b) No user found — response detail MUST be identical.
    client_b = _make_reset_test_client(None)
    resp_b = client_b.post(
        "/api/v1/auth/reset-password",
        json={
            "email": "ghost@example.com",
            "token": "correcttoken",
            "new_password": "ValidPass1234",
        },
    )
    assert resp_b.status_code == 400
    detail_b = (resp_b.json() or {}).get("detail", "")
    assert detail_a == detail_b, (
        f"B3-1: missing-cookie detail must be email-agnostic to close "
        f"the enumeration oracle; got user-exists={detail_a!r} vs "
        f"user-missing={detail_b!r}"
    )


def test_reset_password_with_wrong_cookie_returns_generic_error():
    """B-5: cookie-present-but-wrong is the attacker-bypass signal —
    must return the GENERIC error so the attacker can't distinguish
    'I forged a cookie' from 'I have a stale token'."""
    import uuid
    import hashlib
    from datetime import datetime, timedelta
    from app.models.user import User

    mock_user = MagicMock(spec=User)
    mock_user.id = uuid.uuid4()
    mock_user.email = "victim@example.com"
    mock_user.password_reset_token = hashlib.sha256(b"correcttoken").hexdigest()
    mock_user.password_reset_csrf_hash = hashlib.sha256(b"correctcsrf").hexdigest()
    mock_user.password_reset_expires = datetime.utcnow() + timedelta(hours=1)
    mock_user.password_reset_attempts = 0

    client = _make_reset_test_client(mock_user)
    client.cookies.set("ap_reset_csrf", "wrong-cookie-value")
    resp = client.post(
        "/api/v1/auth/reset-password",
        json={
            "email": "victim@example.com",
            "token": "correcttoken",
            "new_password": "ValidPass1234",
        },
    )
    assert resp.status_code == 400
    detail = (resp.json() or {}).get("detail", "")
    assert "same browser" not in detail.lower(), (
        "Wrong cookie must NOT return the same-browser hint — only "
        "missing-cookie does. Wrong cookie = potential attack."
    )
    assert "invalid" in detail.lower() or "expired" in detail.lower()


def test_password_complexity_rejected_at_schema_layer():
    """N-1: 12+ chars + ≥3 of {upper, lower, digit, symbol}. Weak
    passwords are rejected at the Pydantic validator BEFORE reaching
    any handler logic — protects both the reset path and (via N-N6)
    the user-create path."""
    from app.schemas.auth import PasswordResetConfirm

    # Too short
    with pytest.raises(ValidationError):
        PasswordResetConfirm(
            email="x@y.com", token="abcd1234", new_password="short"
        )
    # Long enough but only 1 class (all lower).
    with pytest.raises(ValidationError):
        PasswordResetConfirm(
            email="x@y.com", token="abcd1234", new_password="onlylowercase"
        )
    # 2 classes — still rejected
    with pytest.raises(ValidationError):
        PasswordResetConfirm(
            email="x@y.com", token="abcd1234", new_password="lowercase1234"
        )
    # 3 classes — accepted
    ok = PasswordResetConfirm(
        email="x@y.com", token="abcd1234", new_password="ValidPass1234"
    )
    assert ok.new_password == "ValidPass1234"


def test_jwt_iat_floor_rejects_token_issued_before_password_change():
    """B-4: a JWT whose `iat` predates `user.password_changed_at` is
    rejected by `_jwt_iat_before_password_change`. Locks out an
    attacker already inside the account after a successful reset."""
    from datetime import datetime, timezone
    from app.api.deps import _jwt_iat_before_password_change

    user = MagicMock()
    # Password changed RIGHT NOW.
    user.password_changed_at = datetime.utcnow()

    # JWT issued 10 minutes ago — predates the password change.
    old_iat = (datetime.now(timezone.utc).timestamp()) - 600
    old_payload = {"iat": old_iat, "sub": "x@y.com"}
    assert _jwt_iat_before_password_change(old_payload, user) is True

    # JWT issued 10 seconds AFTER the password change — valid.
    new_iat = (datetime.now(timezone.utc).timestamp()) + 10
    new_payload = {"iat": new_iat, "sub": "x@y.com"}
    assert _jwt_iat_before_password_change(new_payload, user) is False

    # Missing iat or missing password_changed_at → don't reject
    # (fail-open so we don't break legacy tokens that lack iat or
    # users whose row predates migration 130).
    assert _jwt_iat_before_password_change({"sub": "x@y.com"}, user) is False
    user.password_changed_at = None
    assert _jwt_iat_before_password_change(old_payload, user) is False


def test_email_sender_rejects_attacker_smtp_host():
    """B-7: EMAIL_SMTP_HOST allowlist refuses to ship credentials to
    an arbitrary host even if the env-var is set maliciously."""
    from unittest.mock import patch as _patch
    from app.services import email_sender

    with _patch.object(email_sender.settings, "EMAIL_SMTP_HOST", "attacker.example.com"), \
         _patch.object(email_sender.settings, "EMAIL_SMTP_USERNAME", "u"), \
         _patch.object(email_sender.settings, "EMAIL_SMTP_PASSWORD", "p"):
        ok = email_sender.send_email(
            to="x@y.com",
            subject="test",
            text_body="hi",
        )
    assert ok is False, "Sending to a non-allowlisted SMTP host must refuse"


def test_email_sender_rejects_attacker_link_hostname():
    """B-3: the password-reset email refuses to send if
    PUBLIC_BASE_URL points anywhere outside the link-hostname
    allowlist."""
    from app.services import email_sender

    ok = email_sender.send_password_reset_email(
        to="x@y.com",
        reset_token="xyz",
        public_base_url="https://attacker.example.com",
    )
    assert ok is False, "Sending with attacker base URL must refuse"

    # Valid base URL produces True (dry-run path: EMAIL_SMTP_HOST
    # is unset in tests, so send_email returns True without doing
    # network IO).
    ok2 = email_sender.send_password_reset_email(
        to="x@y.com",
        reset_token="xyz",
        public_base_url="https://agentprovision.com",
    )
    assert ok2 is True


def test_email_sender_strips_crlf_from_headers():
    """B-2: CRLF / NUL injection into header values must be stripped
    before reaching the SMTP layer. Combined with the path-param
    regex on /password-recovery/{email}, this is belt-and-suspenders."""
    from app.services.email_sender import _sanitize_header

    assert "\n" not in _sanitize_header("hi\nBcc: attacker@evil.com")
    assert "\r" not in _sanitize_header("hi\r\nFrom: spoof@evil.com")
    assert "\0" not in _sanitize_header("hi\0bye")
    # Length cap (998 default)
    assert len(_sanitize_header("a" * 5000)) == 998


def test_cookie_should_be_secure_parser_semantics():
    """N5-1 (round 5): the localhost helper must use exact hostname
    match (not startswith) so a misconfigured
    PUBLIC_BASE_URL=http://localhost.attacker.com cannot trick the
    helper into emitting a non-secure cookie in prod."""
    from unittest.mock import patch as _patch
    from app.api.v1.auth import _cookie_should_be_secure
    from app.core.config import settings

    # localhost / 127.0.0.1 / 0.0.0.0 / [::1] over http → non-secure OK
    for base in (
        "http://localhost",
        "http://localhost:8000",
        "http://127.0.0.1",
        "http://127.0.0.1:8000",
        "http://0.0.0.0:8000",
        "http://[::1]:8000",
    ):
        with _patch.object(settings, "PUBLIC_BASE_URL", base):
            assert _cookie_should_be_secure() is False, (
                f"loopback over http must allow non-secure cookies: {base}"
            )

    # https anything → secure required
    with _patch.object(settings, "PUBLIC_BASE_URL", "https://agentprovision.com"):
        assert _cookie_should_be_secure() is True
    with _patch.object(settings, "PUBLIC_BASE_URL", "https://localhost"):
        assert _cookie_should_be_secure() is True  # https beats localhost

    # http but NOT loopback → secure required (defends against the
    # startswith() bypass: localhost.attacker.com, localhostfoo.com)
    for base in (
        "http://localhost.attacker.com",
        "http://localhostfoo.com",
        "http://127.0.0.1.attacker.com",
        "http://attacker.com",
    ):
        with _patch.object(settings, "PUBLIC_BASE_URL", base):
            assert _cookie_should_be_secure() is True, (
                f"non-loopback http MUST require secure cookies: {base}"
            )

    # Empty / unset → fail-closed (secure required)
    with _patch.object(settings, "PUBLIC_BASE_URL", ""):
        assert _cookie_should_be_secure() is True


def test_redis_circuit_breaker_skips_reconnect_during_window():
    """N5-2 (round 5): after a Redis failure the circuit-breaker
    timestamp must short-circuit reconnect attempts for 60s, so
    concurrent requests during a sustained outage don't all
    independently re-build a Redis client."""
    import time as _time
    from unittest.mock import patch as _patch
    import app.api.v1.auth as auth_module

    # Manually open the breaker (simulate just-failed state).
    auth_module._redis_client = None
    auth_module._redis_disabled_until = _time.monotonic() + 60

    # _get_redis_client should refuse to reconnect during the window.
    # We don't want the test to actually hit Redis even if it's up,
    # so patch the redis import to confirm it's never reached.
    with _patch("redis.from_url") as mock_from_url:
        client = auth_module._get_redis_client()
        assert client is None, (
            "Circuit-breaker must keep client None during the disabled window"
        )
        assert not mock_from_url.called, (
            "Circuit-breaker must not attempt to reconnect during window"
        )

    # Reset state so we don't leak into other tests.
    auth_module._redis_disabled_until = 0.0
    auth_module._redis_client = None


def test_seed_demo_data_skipped_in_production():
    """N5-3 (round 5): seed_demo_data must NOT run when
    ENVIRONMENT=production. Default is "production" so the gate is
    fail-closed against unconfigured environments."""
    from unittest.mock import patch as _patch, MagicMock
    from app.core.config import settings
    import app.db.init_db as init_db_mod

    # Patch the actual schema-creation call (base.Base.metadata.create_all)
    # so the test doesn't try to spin up a real DB.
    with _patch.object(settings, "ENVIRONMENT", "production"), \
         _patch.object(init_db_mod, "seed_demo_data") as mock_seed_demo, \
         _patch.object(init_db_mod, "seed_llm_providers"), \
         _patch.object(init_db_mod, "seed_llm_models"), \
         _patch.object(init_db_mod, "seed_system_skills"), \
         _patch.object(init_db_mod.base.Base.metadata, "create_all"):
        init_db_mod.init_db(MagicMock())
        assert not mock_seed_demo.called, (
            "seed_demo_data must NOT run when ENVIRONMENT=production"
        )

    # And ENVIRONMENT=local DOES seed.
    with _patch.object(settings, "ENVIRONMENT", "local"), \
         _patch.object(init_db_mod, "seed_demo_data") as mock_seed_demo, \
         _patch.object(init_db_mod, "seed_llm_providers"), \
         _patch.object(init_db_mod, "seed_llm_models"), \
         _patch.object(init_db_mod, "seed_system_skills"), \
         _patch.object(init_db_mod.base.Base.metadata, "create_all"):
        init_db_mod.init_db(MagicMock())
        assert mock_seed_demo.called, (
            "seed_demo_data must run when ENVIRONMENT=local"
        )
