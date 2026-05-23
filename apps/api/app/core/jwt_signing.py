"""JWT signing helper — domain-isolated secrets with kid plumbing.

Spec: docs/superpowers/specs/2026-05-22-subproject-a-infra-secret-hardening-design.md
PR2 (F7a kid plumbing).

Three JWT signing domains, each with its own secret:

  - "user"        : access tokens (created via create_access_token)
  - "agent"       : agent-scoped tokens (services/agent_token.py)
  - "oauth_state" : OAuth callback state envelope (api/v1/oauth.py)

In PR2, all three secrets default to SECRET_KEY so the cluster sees
zero behavior change. PR4 (F7b) introduces real distinct values via
Keychain hydration. PR5 (F7c) ends the legacy no-kid fallback.

mint_token adds a kid="<domain>-v1" header so the verifier can route
to the right secret. verify_token accepts BOTH the new (kid present)
and legacy (kid absent, signed with SECRET_KEY) shapes for the whole
PR2 → PR4 dual-acceptance window.
"""
from __future__ import annotations

from typing import Any, Dict, Literal

from jose import jwt
from jose.exceptions import JWTError

from app.core.config import settings


# The three supported signing domains. Adding a new domain requires
# (1) a new field in Settings (e.g. JWT_FOO_SECRET) and (2) a new
# entry in _DOMAIN_KID + _secret_for_domain below.
JWT_DOMAINS = ("user", "agent", "oauth_state")

JwtDomain = Literal["user", "agent", "oauth_state"]

ALGORITHM = "HS256"

# Current generation for each domain. Bumped in PR5 (F7c) when the
# Ed25519 cutover happens and legacy support is dropped.
_DOMAIN_KID = {
    "user": "user-v1",
    "agent": "agent-v1",
    "oauth_state": "oauth-state-v1",
}


def _secret_for_domain(domain: JwtDomain) -> str:
    """Look up the domain-specific secret. All three default to
    SECRET_KEY in PR2 (see Settings.model_post_init)."""
    if domain == "user":
        return settings.JWT_USER_SECRET
    if domain == "agent":
        return settings.JWT_AGENT_TOKEN_SECRET
    if domain == "oauth_state":
        return settings.JWT_OAUTH_STATE_SECRET
    raise ValueError(f"Unknown JWT domain: {domain!r}")


def mint_token(
    claims: Dict[str, Any],
    *,
    domain: JwtDomain,
    algorithm: str = ALGORITHM,
) -> str:
    """Mint a JWT for the given signing domain.

    Adds ``kid="<domain>-v1"`` to the token header so the verifier can
    route to the right secret. The payload itself is whatever the
    caller provides (sub, exp, iat, etc.) — this helper doesn't
    enforce a claims schema.
    """
    if domain not in JWT_DOMAINS:
        raise ValueError(f"Unknown JWT domain: {domain!r}")
    secret = _secret_for_domain(domain)
    kid = _DOMAIN_KID[domain]
    return jwt.encode(
        claims, secret, algorithm=algorithm, headers={"kid": kid},
    )


def verify_token(
    token: str,
    *,
    expected_domain: JwtDomain,
    algorithms: list[str] | None = None,
) -> Dict[str, Any]:
    """Verify a JWT, dispatching on the kid header claim.

    Safety rationale for reading the unverified header: ``kid`` is
    used ONLY to select which secret to verify against. Any tampering
    with the kid value still requires producing a valid signature
    under the chosen secret, which an attacker without the secret
    cannot do. The kid is never trusted for an authorization
    decision on its own — only for secret-routing — so reading it
    pre-verification is safe.

    Three accepted shapes:

      1. ``kid`` present AND matches ``_DOMAIN_KID[expected_domain]``
         → verify against the domain-specific secret. The PR2+ path.

      2. ``kid`` absent (legacy token minted before PR2) → verify
         against ``SECRET_KEY``. Preserved until PR5 (F7c) cutover.

      3. ``kid`` present BUT does not match the expected domain's kid
         → REJECTED. Prevents cross-domain replay (e.g. an agent
         token being accepted as a user token).

    Raises ``jose.JWTError`` on any failure (signature mismatch, kid
    mismatch, expired, etc.) — same exception family as the legacy
    one-line ``jwt.decode`` calls so callers don't have to change
    their except clauses.
    """
    if expected_domain not in JWT_DOMAINS:
        raise ValueError(f"Unknown JWT domain: {expected_domain!r}")
    if algorithms is None:
        algorithms = [ALGORITHM]

    # Inspect the header WITHOUT verifying the signature — used only
    # to choose which secret to use for the actual signature check.
    try:
        header = jwt.get_unverified_header(token)
    except JWTError:
        # Malformed token (not even base64-decodable) — fall through
        # to legacy verify which will also fail, but with a more
        # informative error.
        header = {}

    kid = header.get("kid")
    expected_kid = _DOMAIN_KID[expected_domain]

    if kid is None:
        # Path 2 — legacy token, no kid claim. Verify with SECRET_KEY.
        return jwt.decode(
            token, settings.SECRET_KEY, algorithms=algorithms,
        )

    if kid != expected_kid:
        # Path 3 — kid present but doesn't match expected domain. REJECT.
        raise JWTError(
            f"Token kid={kid!r} does not match expected domain "
            f"{expected_domain!r} (expected kid={expected_kid!r})"
        )

    # Path 1 — kid matches. Verify with the domain-specific secret.
    secret = _secret_for_domain(expected_domain)
    return jwt.decode(token, secret, algorithms=algorithms)


__all__ = [
    "JWT_DOMAINS",
    "mint_token",
    "verify_token",
    "ALGORITHM",
]
