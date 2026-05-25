# PR2 — F7a JWT `kid` plumbing (Sub-project A) Implementation Plan

> **2026-05-25 — F2 wontfix update.** PR3 (F2 macOS keychain migration) was abandoned — see [`docs/plans/2026-05-25-f2-keychain-wontfix-decision.md`](../../plans/2026-05-25-f2-keychain-wontfix-decision.md). All references below to "Keychain hydration" or "distinct values via Keychain" now mean **`$HOME/Documents/GitHub/agentprovision-agents/PRODUCTION.env`** (loaded by the deploy workflow's "Load runtime secrets from $HOME" step). The `kid` plumbing in this plan is unaffected. PR4 (real distinct JWT secrets) still ships exactly as described, just sourced from the `.env` file instead of the keychain.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add per-domain JWT signing keys (`JWT_USER_SECRET` / `JWT_AGENT_TOKEN_SECRET` / `JWT_OAUTH_STATE_SECRET`) with a `kid` claim, while preserving full verification of every existing legacy (no-kid) token. **No new key material yet** — all three secrets default to the current `SECRET_KEY` so the cluster sees zero behavior change. PR4 introduces the real distinct values; PR5 ends the legacy fallback.

**Architecture:** Centralize JWT signing in a new `app/core/jwt_signing.py` helper (`mint_token`, `verify_token`) keyed on a `domain` (`"user"` / `"agent"` / `"oauth_state"`). Mint sites add `kid="<domain>-v1"`; verify sites dispatch on `kid`: present + matches expected domain → use `JWT_<DOMAIN>_SECRET`; absent → legacy `SECRET_KEY`; mismatched → reject. Existing tokens minted without a `kid` keep verifying via the legacy fallback for the whole life of PR2-PR4 (cutover lands in PR5).

**Tech Stack:** `python-jose` (already used), pydantic settings, FastAPI dependency-injected verify sites. No new deps.

**Cluster impact:** Touches api JWT mint/verify. Requires an api restart on deploy. Per Luna's R4 in the Sub-project A spec, **batch with PR4's api restart** in a single deploy window (30min apart). Mass-logout risk if dual-kid verifier has a bug → the §5 PR2 Luna prerequisite test is the regression guard.

Spec: `docs/superpowers/specs/2026-05-22-subproject-a-infra-secret-hardening-design.md` §5 PR2.

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `apps/api/app/core/config.py` | modify | Add 3 new settings; each defaults to `SECRET_KEY` |
| `apps/api/app/core/jwt_signing.py` | **create** | The `mint_token` + `verify_token` helpers + `JWT_DOMAINS` enum |
| `apps/api/app/core/security.py` | modify | `create_access_token` → calls new `mint_token(domain="user", ...)` |
| `apps/api/app/api/deps.py` | modify | 2 verify sites (lines 70, 104) → call `verify_token(token, expected_domain="user")` |
| `apps/api/app/api/v1/auth.py` | modify | 1 verify site (line 165) — refresh-token path |
| `apps/api/app/api/v1/workflows.py` | modify | 1 verify site (line 41) — internal workflow callback |
| `apps/api/app/services/agent_token.py` | modify | Mint (line 112) + verify (line 125) → new helper, `domain="agent"` |
| `apps/api/app/api/v1/oauth.py` | modify | Mint (line 368) + verify (line 441) → new helper, `domain="oauth_state"` |
| `apps/api/tests/test_jwt_dual_kid_verify.py` | **create** | Luna's required integration test: legacy + v1 tokens both verify; cross-domain forgery rejected; kid mismatch rejected |

The two SECRET_KEY uses NOT in scope: `bookkeeper_exports.py:64` (HMAC for export tokens, not JWT) and `auth.py:532` (password-reset log HMAC). Both are non-JWT keyed-hash uses; rotating them is a separate spec.

---

## Task 1: Add 3 settings with safe defaults

**Files:**
- Modify: `apps/api/app/core/config.py`

- [ ] **Step 1.1: Read the current Settings class location for SECRET_KEY**

```bash
grep -nE "SECRET_KEY|class Settings|ALGORITHM" apps/api/app/core/config.py
```

Expected: locate the field; the new fields go alongside it.

- [ ] **Step 1.2: Add the 3 new fields + a `model_post_init` hook (pydantic v2)**

Pre-verified: `apps/api/app/core/config.py` imports from `pydantic_settings.BaseSettings` (pydantic v2). Use the v2 `model_post_init` lifecycle hook.

Append to the `Settings` class alongside `SECRET_KEY`:

```python
# F7 split — domain-specific JWT signing secrets.
# Default to SECRET_KEY so PR2 is a no-behavior-change kid-plumbing
# step. PR4 introduces real distinct values via Keychain hydration.
JWT_USER_SECRET: str | None = None
JWT_AGENT_TOKEN_SECRET: str | None = None
JWT_OAUTH_STATE_SECRET: str | None = None
```

And in the same class, add the v2 lifecycle hook:

```python
def model_post_init(self, __context) -> None:
    """Apply SECRET_KEY as the fallback for any unset domain secret.

    PR2 (F7a kid plumbing): all three secrets default to SECRET_KEY so
    the cluster sees zero behavior change. PR4 (F7b) replaces these
    defaults with distinct values hydrated from macOS Keychain.
    """
    if self.JWT_USER_SECRET is None:
        self.JWT_USER_SECRET = self.SECRET_KEY
    if self.JWT_AGENT_TOKEN_SECRET is None:
        self.JWT_AGENT_TOKEN_SECRET = self.SECRET_KEY
    if self.JWT_OAUTH_STATE_SECRET is None:
        self.JWT_OAUTH_STATE_SECRET = self.SECRET_KEY
```

If the existing class already has a `model_post_init` method, append the three `if ... is None: ... = self.SECRET_KEY` lines to its body — don't define a second one.

- [ ] **Step 1.3: Confirm import + settings load**

```bash
cd /Users/nomade/Documents/GitHub/agentprovision-agents/apps/api
python -c "from app.core.config import settings; print('JWT_USER_SECRET set:', bool(settings.JWT_USER_SECRET)); print('matches SECRET_KEY:', settings.JWT_USER_SECRET == settings.SECRET_KEY)"
```

Expected: both `True`.

- [ ] **Step 1.4: Commit**

```bash
git add apps/api/app/core/config.py
git commit -m "feat(jwt): add per-domain JWT_USER/AGENT/OAUTH_STATE secrets defaulting to SECRET_KEY"
```

---

## Task 2: Create the jwt_signing helper (RED phase)

**Files:**
- Create: `apps/api/app/core/jwt_signing.py`
- Create: `apps/api/tests/test_jwt_dual_kid_verify.py`

- [ ] **Step 2.1: Write the failing test FIRST**

Create `apps/api/tests/test_jwt_dual_kid_verify.py`:

```python
"""Dual-kid JWT verification — Sub-project A PR2 (F7a kid plumbing).

Luna's §5 PR2 explicit prerequisite test. Locks four invariants:

  1. Legacy token (no kid claim, signed with SECRET_KEY) → verifies
     under new code via the legacy fallback path.
  2. New token (kid="user-v1", signed with JWT_USER_SECRET) →
     verifies under new code via the domain-specific path.
  3. Cross-domain forgery: token claiming kid="user-v1" but signed
     with JWT_AGENT_TOKEN_SECRET → REJECTED.
  4. Bogus kid: token with kid="bogus-v999" → REJECTED.

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
    """Property #3: a token claiming kid="user-v1" but signed with
    JWT_AGENT_TOKEN_SECRET (different domain) → REJECTED. Important
    when PR4 introduces real distinct secrets per domain."""
    # Manually craft a token: header says kid=user-v1, but we sign
    # with the AGENT secret. Under PR2 where both secrets default to
    # SECRET_KEY, this WILL verify (same key). The test guards the
    # PR4 invariant; under PR2 it passes trivially. Use a deliberately
    # different fake key to simulate the PR4 state.
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
```

- [ ] **Step 2.2: Run the test to confirm it FAILS (helper doesn't exist yet)**

```bash
cd apps/api
python -m pytest tests/test_jwt_dual_kid_verify.py -v 2>&1 | tail -15
```

Expected: `ImportError: cannot import name 'JWT_DOMAINS' from 'app.core.jwt_signing'` (or `ModuleNotFoundError`) — module doesn't exist.

- [ ] **Step 2.3: Commit the failing test**

```bash
git add apps/api/tests/test_jwt_dual_kid_verify.py
git commit -m "test(jwt): RED — dual-kid verify contract (Luna's §5 PR2 prereq)"
```

---

## Task 3: Implement the jwt_signing helper (GREEN)

**Files:**
- Create: `apps/api/app/core/jwt_signing.py`

- [ ] **Step 3.1: Write the helper module**

Create `apps/api/app/core/jwt_signing.py`:

```python
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
# entry in _DOMAIN_SECRET below.
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
    SECRET_KEY in PR2."""
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
        return jwt.decode(token, settings.SECRET_KEY, algorithms=algorithms)

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
```

- [ ] **Step 3.2: Run the test → should now PASS (GREEN)**

```bash
cd apps/api
python -m pytest tests/test_jwt_dual_kid_verify.py -v 2>&1 | tail -15
```

Expected: 6 PASSED.

- [ ] **Step 3.3: Commit the helper**

```bash
git add apps/api/app/core/jwt_signing.py
git commit -m "feat(jwt): jwt_signing helper — mint+verify with kid + domain isolation"
```

---

## Task 4: Migrate `create_access_token` (user domain — mint side)

**Files:**
- Modify: `apps/api/app/core/security.py:14-46`

- [ ] **Step 4.1: Rewrite `create_access_token` to delegate to `mint_token`**

Replace lines 14-46 of `apps/api/app/core/security.py` with:

```python
def create_access_token(
    subject: Union[str, Any],
    expires_delta: timedelta | None = None,
    additional_claims: Dict[str, Any] | None = None,
    iat: int | None = None,
) -> str:
    """Issue a signed user access token.

    `iat` is the original issued-at Unix timestamp. On a fresh login it
    defaults to `now`. On `/auth/refresh` the caller passes the original
    iat from the incoming token so the chain has a bounded lifetime — see
    `MAX_TOKEN_CHAIN_AGE_SECONDS` in `auth.py`.

    Sub-project A PR2 (F7a): delegates to `app.core.jwt_signing.mint_token`
    so the new ``kid="user-v1"`` claim lands in the header and the user
    domain's secret (`JWT_USER_SECRET`, defaulting to `SECRET_KEY` until
    PR4) signs the token. Behavior unchanged at this PR; PR4 introduces
    real distinct key material.
    """
    from app.core.jwt_signing import mint_token

    now = datetime.utcnow()
    if expires_delta:
        expire = now + expires_delta
    else:
        expire = now + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode = {
        "exp": expire,
        "sub": str(subject),
        "iat": iat if iat is not None else int(time.time()),
    }
    if additional_claims:
        to_encode.update(additional_claims)
    return mint_token(to_encode, domain="user")
```

- [ ] **Step 4.2: Verify the existing auth test suite still passes**

```bash
cd apps/api
python -m pytest tests/ -k "auth or login or token" --timeout=60 -x 2>&1 | tail -10
```

Expected: all pass. Existing login flow still works — tokens just have a `kid` header now.

- [ ] **Step 4.3: Commit**

```bash
git add apps/api/app/core/security.py
git commit -m "refactor(jwt): create_access_token delegates to mint_token(domain=user)"
```

---

## Task 5: Migrate user-domain verify sites

**Files:**
- Modify: `apps/api/app/api/deps.py:65-75, 100-110`
- Modify: `apps/api/app/api/v1/auth.py:160-170`
- Modify: `apps/api/app/api/v1/workflows.py:38-44`

- [ ] **Step 5.1: Read each verify site to understand local context**

```bash
sed -n '65,75p' apps/api/app/api/deps.py
sed -n '100,110p' apps/api/app/api/deps.py
sed -n '160,170p' apps/api/app/api/v1/auth.py
sed -n '38,44p' apps/api/app/api/v1/workflows.py
```

- [ ] **Step 5.2: For each site, replace `jwt.decode(token, settings.SECRET_KEY, ...)` with `verify_token(token, expected_domain="user")`**

Pattern (apply at each of the 4 sites):

Old (3 of 4 sites — `deps.py:70`, `deps.py:104`, `auth.py:165`):
```python
payload = jwt.decode(
    token,
    settings.SECRET_KEY,
    algorithms=[settings.ALGORITHM],
)
```

New:
```python
from app.core.jwt_signing import verify_token
payload = verify_token(token, expected_domain="user")
```

Old (4th site — `workflows.py:41` uses an aliased import `from jose import jwt as jose_jwt`):
```python
payload = jose_jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
```

New:
```python
from app.core.jwt_signing import verify_token
payload = verify_token(token, expected_domain="user")
```

For `workflows.py`: after the replacement, check whether any other `jose_jwt` usage remains in the file (`grep -n "jose_jwt" apps/api/app/api/v1/workflows.py`). If none remain, also remove the now-unused import line. If other uses remain, leave the import alone.

If the site catches a specific exception (e.g. `JWTError`), the new helper raises the same family — no try/except change needed.

- [ ] **Step 5.3: Run the auth test suite**

```bash
cd apps/api
python -m pytest tests/ -k "auth or login or token or deps" --timeout=60 2>&1 | tail -10
```

Expected: all pass. Both legacy tokens (issued before this branch) AND new tokens (issued by Task 4's `create_access_token`) verify cleanly.

- [ ] **Step 5.4: Commit**

```bash
git add apps/api/app/api/deps.py apps/api/app/api/v1/auth.py apps/api/app/api/v1/workflows.py
git commit -m "refactor(jwt): user-domain verify sites use verify_token(expected_domain=user)"
```

---

## Task 6: Migrate agent-token mint + verify

**Files:**
- Modify: `apps/api/app/services/agent_token.py:108-130` (mint at 112, verify at 125)

- [ ] **Step 6.1: Replace mint + verify**

Mint side (~ line 112):

Old:
```python
return jwt.encode(claims, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
```

New:
```python
from app.core.jwt_signing import mint_token
return mint_token(claims, domain="agent")
```

Verify side (~ line 125):

Old:
```python
payload = jwt.decode(
    token,
    settings.SECRET_KEY,
    algorithms=[settings.ALGORITHM],
)
```

New:
```python
from app.core.jwt_signing import verify_token
payload = verify_token(token, expected_domain="agent")
```

- [ ] **Step 6.2: Run agent_token tests**

```bash
cd apps/api
python -m pytest tests/ -k "agent_token" --timeout=60 2>&1 | tail -10
```

Expected: all pass.

- [ ] **Step 6.3: Commit**

```bash
git add apps/api/app/services/agent_token.py
git commit -m "refactor(jwt): agent_token mint + verify use jwt_signing(domain=agent)"
```

---

## Task 7: Migrate OAuth-state mint + verify

**Files:**
- Modify: `apps/api/app/api/v1/oauth.py:368, 441`

- [ ] **Step 7.1: Replace mint + verify**

Mint (line 368):

Old:
```python
state_token = jwt.encode(state_payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
```

New:
```python
from app.core.jwt_signing import mint_token
state_token = mint_token(state_payload, domain="oauth_state")
```

Verify (line 441):

Old:
```python
payload = jwt.decode(state, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
```

New:
```python
from app.core.jwt_signing import verify_token
payload = verify_token(state, expected_domain="oauth_state")
```

- [ ] **Step 7.2: Run oauth tests**

```bash
cd apps/api
python -m pytest tests/ -k "oauth" --timeout=60 2>&1 | tail -10
```

Expected: all pass.

- [ ] **Step 7.3: Commit**

```bash
git add apps/api/app/api/v1/oauth.py
git commit -m "refactor(jwt): oauth-state mint + verify use jwt_signing(domain=oauth_state)"
```

---

## Task 8: Full suite + spec-required integration test

**Files:** none (verification only)

- [ ] **Step 8.1: Run the entire api test suite**

```bash
cd apps/api
python -m pytest tests/ --timeout=60 2>&1 | tail -8
```

Expected: all pass. If a pre-existing failure is unrelated (SQLite vs PG type compatibility per Luna's audit), note it; otherwise diagnose.

- [ ] **Step 8.2: Run the Luna-prereq integration test in isolation**

```bash
cd apps/api
python -m pytest tests/test_jwt_dual_kid_verify.py -v --timeout=30
```

Expected: 6 PASSED. This is the spec's §5 PR2 hard gate — must not skip.

- [ ] **Step 8.3: Manually decode a token to confirm `kid` is present**

```bash
cd apps/api
python <<'EOF'
from app.core.security import create_access_token
from jose import jwt
t = create_access_token(subject="smoke@test.local")
print("token:", t[:60] + "...")
print("header:", jwt.get_unverified_header(t))
print("payload sub:", jwt.decode(t, options={"verify_signature": False})["sub"])
EOF
```

Expected: header contains `{"alg": "HS256", "kid": "user-v1", "typ": "JWT"}`.

---

## Task 9: Push, Luna code review, CI, merge

- [ ] **Step 9.1: Push branch**

```bash
cd /Users/nomade/Documents/GitHub/agentprovision-agents
BRANCH=$(git branch --show-current)
git push -u origin "$BRANCH"
```

- [ ] **Step 9.2: Open PR assigned to nomad3 — explicit "DO NOT MERGE without Simon's sign-off"**

```bash
gh pr create --assignee nomad3 \
  --title "feat(F7a): JWT kid plumbing — domain-isolated secrets (Sub-project A PR2)" \
  --body "$(cat <<'EOF'
## Summary

PR2 of Sub-project A — F7a kid plumbing. **No behavior change**: all
three new domain secrets (`JWT_USER_SECRET`, `JWT_AGENT_TOKEN_SECRET`,
`JWT_OAUTH_STATE_SECRET`) default to current `SECRET_KEY`. The change
is purely structural — adds a `kid` claim header on new tokens and
the dual-kid verifier that accepts both legacy and new shapes.

PR4 introduces real distinct key material; PR5 ends the legacy
fallback.

## Cluster-safety

⚠️ **Touches api JWT signing.** Per spec §5 PR4 + Luna's R4: PR2 +
PR4 api restarts should batch in a single deploy window (30min
apart). PR2 alone is safe to deploy any time — but a verifier bug
would invalidate every active user session (mass logout). The
spec-required dual-kid integration test (Luna §5 PR2 prereq) is the
regression guard.

## Verified

- 6/6 tests in `test_jwt_dual_kid_verify.py` pass — locks 6
  invariants (legacy verify, new verify, cross-domain forgery
  rejected, bogus kid rejected, all 3 domains round-trip, expected-
  domain-mismatch rejected).
- Full api test suite passes (no regression in auth/oauth/agent_token
  flows).
- Token header inspection confirms `kid="user-v1"` lands on new
  tokens.

## DO NOT MERGE without Simon's sign-off

PR2 is a deliberate API-touching change. Standard rule applies for
any PR that touches JWT signing.

EOF
)"
```

- [ ] **Step 9.3: Dispatch Luna code review per standing rule**

Send the PR diff + spec §5 PR2 context to a `superpowers:code-reviewer` subagent. Address every BLOCKER/IMPORTANT/NIT in-PR before merge.

- [ ] **Step 9.4: Monitor CI**

Use the Monitor tool: poll `gh pr checks <PR#>` for "Aggregate test status".

- [ ] **Step 9.5: Stop. Wait for Simon's explicit merge approval.**

This PR ships an api restart. Simon's "don't break the cluster" rule + the mass-logout risk surface make this NOT a self-merge candidate.

- [ ] **Step 9.6: After Simon merges → monitor deploy → smoke-verify**

Once Simon merges:
- Watch the `docker-desktop-deploy` workflow on `main`.
- After deploy completes, send a chat smoke from Chrome to confirm api is responding and an existing JWT session still works.
- Mark task #369 (F7 P0) state to "kid plumbing live in production; awaiting PR4 for real key material" — full closure is at PR5.
- Log to Luna's tenant memory via `alpha remember`.

---

## Stopping condition

PR2 is complete when:
1. All 6 `test_jwt_dual_kid_verify.py` assertions pass
2. Full api test suite passes (no regression)
3. Token-header inspection confirms `kid="user-v1"` on new tokens
4. PR is open, assigned to nomad3, with explicit DO-NOT-MERGE warning
5. CI green
6. Luna code-reviewer pass complete + findings addressed
7. Waiting on Simon's explicit merge approval

PR3 (F2 Keychain migration) requires Simon's Mac hands-on and CANNOT start without him.
