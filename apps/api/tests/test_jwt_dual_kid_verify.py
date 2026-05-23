"""Dual-kid JWT verification — Sub-project A PR2 (F7a kid plumbing).

Luna's §5 PR2 explicit prerequisite test. Locks six invariants:

  1. Legacy token (no kid claim, signed with SECRET_KEY) → verifies
     under new code via the legacy fallback path.
  2. New token (kid="user-v1", signed with JWT_USER_SECRET) →
     verifies under new code via the domain-specific path.
  3. Cross-domain forgery: token claiming kid="user-v1" but signed
     with a deliberately-different secret → REJECTED. Guards the
     PR4 invariant where JWT_USER_SECRET and JWT_AGENT_TOKEN_SECRET
     are genuinely different.
  4. Bogus kid: token with kid="bogus-v999" → REJECTED. Prevents
     "just put any kid value" bypasses.
  5. All three domains round-trip cleanly via the helper.
  6. expected_domain mismatch (mint user, verify agent) → REJECTED.
     Locks the per-domain isolation guarantee.

Without this test, a regression in the dual-kid verifier would
invalidate every active user session on deploy (mass logout) — the
chief reason Luna gated PR2 on it.

Spec:
  docs/superpowers/specs/2026-05-22-subproject-a-infra-secret-hardening-design.md
  §5 PR2.
"""
from __future__ import annotations

import time

import pytest
from jose import jwt

from app.core.config import settings
from app.core.jwt_signing import (
    JWT_DOMAINS,
    mint_token,
    verify_token,
)


@pytest.fixture
def base_claims():
    return {
        "sub": "test-user@example.com",
        "iat": int(time.time()),
        "exp": int(time.time()) + 600,
    }


def test_legacy_no_kid_token_verifies_via_fallback(base_claims):
    """Property #1: a token minted by the OLD code path (no kid,
    signed with SECRET_KEY) must still verify under the new code."""
    legacy_token = jwt.encode(
        base_claims, settings.SECRET_KEY, algorithm="HS256",
    )
    payload = verify_token(legacy_token, expected_domain="user")
    assert payload["sub"] == "test-user@example.com"


def test_new_kid_token_verifies_via_domain_path(base_claims):
    """Property #2: a token minted by mint_token() with kid="user-v1"
    must verify under verify_token() with expected_domain="user"."""
    token = mint_token(base_claims, domain="user")
    payload = verify_token(token, expected_domain="user")
    assert payload["sub"] == "test-user@example.com"
    # The kid claim is present in the token header (not payload)
    header = jwt.get_unverified_header(token)
    assert header.get("kid") == "user-v1"


def test_cross_domain_forgery_is_rejected(base_claims):
    """Property #3: a token claiming kid="user-v1" but signed with a
    deliberately-different secret → REJECTED. Under PR2 where the
    JWT_USER_SECRET defaults to SECRET_KEY, using a different fake
    key simulates the PR4 state where the secrets diverge."""
    fake_agent_secret = settings.SECRET_KEY + "-DIFFERENT-FOR-TEST"
    forged = jwt.encode(
        base_claims,
        fake_agent_secret,
        algorithm="HS256",
        headers={"kid": "user-v1"},
    )
    with pytest.raises(Exception):  # jose raises JWTError
        verify_token(forged, expected_domain="user")


def test_bogus_kid_is_rejected(base_claims):
    """Property #4: a token with a kid value the helper doesn't know
    about → REJECTED. Prevents 'just put any kid value' bypasses."""
    bogus = jwt.encode(
        base_claims,
        settings.SECRET_KEY,
        algorithm="HS256",
        headers={"kid": "bogus-v999"},
    )
    with pytest.raises(Exception):
        verify_token(bogus, expected_domain="user")


def test_all_three_domains_mint_and_verify(base_claims):
    """Coverage — all 3 supported domains work end-to-end."""
    for domain in JWT_DOMAINS:
        token = mint_token(base_claims, domain=domain)
        payload = verify_token(token, expected_domain=domain)
        assert payload["sub"] == "test-user@example.com"


def test_expected_domain_mismatch_is_rejected(base_claims):
    """Mint as user, verify as agent → REJECTED. The expected_domain
    arg is what enforces the per-domain isolation guarantee."""
    user_token = mint_token(base_claims, domain="user")
    with pytest.raises(Exception):
        verify_token(user_token, expected_domain="agent")
