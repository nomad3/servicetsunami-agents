from datetime import timedelta, datetime
import hashlib
import hmac
import json
import secrets
import logging
import time
import uuid
from fastapi import APIRouter, BackgroundTasks, Cookie, Depends, HTTPException, Path, Request, Response, status
from fastapi.responses import JSONResponse
from fastapi.security import OAuth2PasswordRequestForm
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from app.schemas import token as token_schema
from app.schemas import user as user_schema
from app.schemas import tenant as tenant_schema
from app.schemas import auth as auth_schema
from app.api import deps
from app.core import security
from app.core.config import settings
from app.models.refresh_token import RefreshToken
from app.services import base as base_service
from app.services import refresh_tokens as refresh_token_service
from app.services.refresh_tokens import RevokeReason
from app.services import users as user_service
from app.core.rate_limit import limiter

router = APIRouter()
logger = logging.getLogger(__name__)

_PASSWORD_RESET_MESSAGE = "Password reset instructions sent if email is registered"

# Legacy JWT-refresh chain cap (the bearer-required `/auth/refresh`
# endpoint below). With the original-iat preserved across refreshes, a
# stolen token at hour 0 can extend at most until
# `MAX_TOKEN_CHAIN_AGE_SECONDS / 3600` hours later. After that the user
# re-authenticates. 7 days = weekly forced re-auth for the legacy path.
#
# NOTE: the **opaque-credential** `/auth/token/refresh` path added in
# the long-lived-sessions PR is bounded by `settings.REFRESH_TOKEN_EXPIRE_DAYS`
# instead (default 30 days); this constant does NOT apply to that path.
MAX_TOKEN_CHAIN_AGE_SECONDS = 7 * 24 * 60 * 60

# Cap traversal in `refresh_token_service.revoke_chain_from` to prevent
# write-amplification DoS if a malicious actor with a stolen token replays
# against a very long chain. Pairs with the rate limiter on
# `/auth/token/refresh` below.
MAX_REFRESH_CHAIN_TRAVERSAL = 1000


_REFRESH_HINT_DEVICE_LABEL = "X-AP-Device-Label"  # opt-in header for CLI/SDK clients


def _device_label_from(request: Request) -> str | None:
    """Pull a human-readable origin label off the request. The CLI sets
    `X-AP-Device-Label: alpha CLI on <hostname>`; everyone else falls
    back to the User-Agent or None. Kept loose on purpose — this is for
    `alpha sessions list` UX, not for auth decisions."""
    explicit = request.headers.get(_REFRESH_HINT_DEVICE_LABEL)
    if explicit:
        return explicit.strip()[:255] or None
    ua = request.headers.get("user-agent")
    if ua:
        return ua.strip()[:255] or None
    return None


def _client_ip(request: Request) -> str | None:
    """Resolve the caller's IP for the refresh-token audit row.

    Cloudflare tunnel + uvicorn behind nginx in production make
    `request.client.host` always the proxy, so we have to peek at
    forwarding headers. **But** any client can set those headers when
    the API is reachable directly (local dev, mesh-internal calls).
    Trusting them unconditionally lets an attacker spoof their
    audit-row IP at will. So we only honour forwarding headers when
    `settings.TRUSTED_FORWARD_HEADERS=True` (helm prod values set this;
    docker-compose local dev does not).
    """
    if settings.TRUSTED_FORWARD_HEADERS:
        for hdr in ("cf-connecting-ip", "x-forwarded-for", "x-real-ip"):
            val = request.headers.get(hdr)
            if val:
                # X-Forwarded-For is comma-separated; first hop is the client.
                return val.split(",", 1)[0].strip()
    if request.client is None:
        return None
    return request.client.host


@router.post("/login", response_model=token_schema.Token)
@limiter.limit("10/minute")
def login_for_access_token(
    request: Request,
    db: Session = Depends(deps.get_db), form_data: OAuth2PasswordRequestForm = Depends()
):
    user = base_service.authenticate_user(db, email=form_data.username, password=form_data.password)
    if not user:
        raise HTTPException(
            status_code=401,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    claims = {"user_id": str(user.id)}
    if user.tenant_id:
        claims["tenant_id"] = str(user.tenant_id)

    access_token = security.create_access_token(
        user.email,
        expires_delta=access_token_expires,
        additional_claims=claims,
    )
    # Issue a refresh token alongside the access token so CLIs (and
    # eventually the web UI's persistent-session pathway) can stay
    # signed in for REFRESH_TOKEN_EXPIRE_DAYS without re-prompting for
    # password. Older clients ignore the extra fields per the schema's
    # Optional shape; new clients persist `refresh_token` next to
    # `access_token` and call /auth/token/refresh on 401.
    refresh_secret, _row = refresh_token_service.issue_refresh_token(
        db,
        user=user,
        device_label=_device_label_from(request),
        user_agent=request.headers.get("user-agent"),
        ip=_client_ip(request),
    )
    db.commit()
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "refresh_token": refresh_secret,
        "expires_in": settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    }


def _bearer_token(request: Request) -> str | None:
    auth = request.headers.get("authorization") or request.headers.get("Authorization")
    if not auth:
        return None
    parts = auth.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    return parts[1].strip()


@router.post("/refresh", response_model=token_schema.Token)
@limiter.limit("60/minute")
def refresh_access_token(
    request: Request,
    current_user=Depends(deps.get_current_active_user),
):
    """Re-issue a fresh access token for the currently authenticated caller.

    The caller must present a still-valid bearer token. We re-mint with the
    same identity claims, a fresh `exp`, and the *original* `iat` preserved
    so the refresh chain has a bounded lifetime. After
    `MAX_TOKEN_CHAIN_AGE_SECONDS` since original login, the user must
    re-authenticate.
    """
    raw_token = _bearer_token(request)
    original_iat: int | None = None
    if raw_token:
        try:
            decoded = jwt.decode(
                raw_token,
                settings.SECRET_KEY,
                algorithms=[security.ALGORITHM],
            )
            iat_claim = decoded.get("iat")
            if isinstance(iat_claim, (int, float)):
                original_iat = int(iat_claim)
        except JWTError:
            # Token failed to decode — get_current_active_user already
            # rejected this case, so we shouldn't get here. Fall through
            # to a fresh iat to avoid hard-failing the refresh on an
            # encoding edge case.
            original_iat = None

    if original_iat is not None:
        age = int(time.time()) - original_iat
        if age > MAX_TOKEN_CHAIN_AGE_SECONDS:
            raise HTTPException(
                status_code=401,
                detail="session too old; please re-authenticate",
                headers={"WWW-Authenticate": "Bearer"},
            )

    access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    claims = {"user_id": str(current_user.id)}
    if current_user.tenant_id:
        claims["tenant_id"] = str(current_user.tenant_id)
    access_token = security.create_access_token(
        current_user.email,
        expires_delta=access_token_expires,
        additional_claims=claims,
        iat=original_iat,
    )
    logger.info("Token refreshed for %s", current_user.email)
    return {"access_token": access_token, "token_type": "bearer"}


@router.post("/token/refresh", response_model=token_schema.Token)
@limiter.limit("30/minute")
def exchange_refresh_token(
    request: Request,
    payload: token_schema.RefreshTokenRequest,
    db: Session = Depends(deps.get_db),
):
    """Long-lived "log in once" exchange endpoint.

    Unlike `/auth/refresh` (which requires a still-valid access token
    and caps at MAX_TOKEN_CHAIN_AGE_SECONDS), this endpoint accepts an
    opaque DB-backed refresh token. Each successful call:

      1. Returns a fresh access_token + a fresh refresh_token.
      2. Marks the presented refresh token revoked (reason='rotated').
      3. Links the new token to the old via parent_id for reuse
         detection (replaying the old token → kill the whole chain).

    The CLI hits this transparently inside its HTTP client middleware
    on a 401, then retries the original request with the new
    access_token. Users see no interruption for 30 days (settings.
    REFRESH_TOKEN_EXPIRE_DAYS).
    """
    secret = (payload.refresh_token or "").strip()
    if not secret:
        raise HTTPException(status_code=400, detail="refresh_token is required")

    # Step 1: look up by hash. We deliberately do NOT use find_active here
    # — we need to see revoked rows to detect replay.
    hashed = hashlib.sha256(secret.encode("utf-8")).hexdigest()
    row = db.query(RefreshToken).filter(RefreshToken.token_hash == hashed).first()
    if row is None:
        # Unknown secret — could be a typo or a forged value. Don't leak
        # which (timing-side-channel doesn't matter here at refresh
        # cadence). Generic 401.
        logger.info("refresh_token: unknown hash")
        raise HTTPException(status_code=401, detail="invalid refresh_token")

    # Step 2: replay detection. If the row exists but is revoked with
    # reason='rotated', somebody is replaying an already-exchanged
    # token. We can't tell whether it's the legitimate user or an
    # attacker, so we kill the entire chain and force re-auth on the
    # next attempt.
    if row.revoked_at is not None:
        if row.revoked_reason == RevokeReason.ROTATED:
            # B-1 grace window: if the row was rotated very recently,
            # this is most likely a concurrent-CLI race (alpha chat +
            # alpha watch both hit 401 at the same second). Replay
            # the cached child instead of burning the chain.
            grace = settings.REFRESH_REUSE_GRACE_SECONDS
            within_grace = (
                grace > 0
                and (datetime.utcnow() - row.revoked_at).total_seconds() <= grace
            )
            if within_grace:
                child = refresh_token_service.find_rotated_child(db, parent=row)
                if child is not None:
                    # Grace pathway: mint a fresh access_token tied to
                    # the winning racer's child, but DO NOT issue a new
                    # refresh credential (the child's secret is only
                    # stored hashed). The caller's existing refresh in
                    # keychain becomes the dead `row`; they re-login
                    # if the next 401 lands outside the grace window.
                    # Tunable via `REFRESH_REUSE_GRACE_SECONDS`.
                    user = child.user
                    access_token_expires = timedelta(
                        minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES
                    )
                    claims = {"user_id": str(user.id)}
                    if user.tenant_id:
                        claims["tenant_id"] = str(user.tenant_id)
                    access_token = security.create_access_token(
                        user.email,
                        expires_delta=access_token_expires,
                        additional_claims=claims,
                    )
                    logger.info(
                        "refresh_token replay within grace window for user_id=%s — "
                        "returning fresh access_token with no new refresh credential",
                        row.user_id,
                    )
                    return {
                        "access_token": access_token,
                        "token_type": "bearer",
                        # No refresh_token: the caller already has the
                        # newly-rotated one from the winning racer.
                        "refresh_token": None,
                        "expires_in": settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
                    }
            # Outside the grace window OR no child found: this is
            # genuine reuse. Burn the chain.
            burned = refresh_token_service.revoke_chain_from(
                db,
                leaf=row,
                reason=RevokeReason.REUSE_DETECTED,
                max_rows=MAX_REFRESH_CHAIN_TRAVERSAL,
            )
            db.commit()
            logger.warning(
                "refresh_token reuse detected for user_id=%s — revoked %d chain rows",
                row.user_id,
                burned,
            )
        raise HTTPException(status_code=401, detail="refresh_token revoked")

    # Step 3: expiry
    if row.expires_at <= datetime.utcnow():
        raise HTTPException(status_code=401, detail="refresh_token expired")

    # Step 4: rotate. Issues a new RefreshToken with parent_id=row.id
    # and revokes row with reason='rotated'.
    new_secret, _new_row = refresh_token_service.rotate(
        db,
        presented=row,
        user_agent=request.headers.get("user-agent"),
        ip=_client_ip(request),
    )

    # Step 5: mint a fresh access token.
    user = row.user
    access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    claims = {"user_id": str(user.id)}
    if user.tenant_id:
        claims["tenant_id"] = str(user.tenant_id)
    access_token = security.create_access_token(
        user.email,
        expires_delta=access_token_expires,
        additional_claims=claims,
    )
    db.commit()
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "refresh_token": new_secret,
        "expires_in": settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    }


@router.get("/sessions", response_model=list[token_schema.SessionInfo])
def list_active_sessions(
    db: Session = Depends(deps.get_db),
    current_user=Depends(deps.get_current_active_user),
):
    """List the caller's still-active refresh tokens — one row per
    live `alpha login` (or other long-lived client). Powers
    `alpha sessions list` and a future web UI "logged-in devices" panel.
    """
    rows = (
        db.query(RefreshToken)
        .filter(
            RefreshToken.user_id == current_user.id,
            RefreshToken.revoked_at.is_(None),
            RefreshToken.expires_at > datetime.utcnow(),
        )
        .order_by(RefreshToken.last_used_at.desc().nulls_last(),
                  RefreshToken.created_at.desc())
        .all()
    )
    return [
        token_schema.SessionInfo(
            id=str(r.id),
            device_label=r.device_label,
            user_agent=r.user_agent,
            created_at=r.created_at,
            last_used_at=r.last_used_at,
            expires_at=r.expires_at,
        )
        for r in rows
    ]


@router.delete("/sessions/{session_id}", status_code=204)
def revoke_session(
    session_id: uuid.UUID,
    db: Session = Depends(deps.get_db),
    current_user=Depends(deps.get_current_active_user),
):
    """Revoke a single refresh token — `alpha sessions revoke <id>`.

    Tenant + ownership check: the row must belong to the caller.
    Cross-user revoke is intentionally not supported here; admins
    revoking another user's sessions go through the admin surface
    (TODO; see `app/api/v1/admin/auth.py`).

    `session_id` is typed `uuid.UUID` so FastAPI returns 422 (not 500)
    on a malformed value before the query layer ever sees it — review
    finding B-2 on PR #442.
    """
    row = (
        db.query(RefreshToken)
        .filter(RefreshToken.id == session_id, RefreshToken.user_id == current_user.id)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="session not found")
    refresh_token_service.revoke_one(db, row=row, reason=RevokeReason.USER_REVOKED)
    db.commit()
    return None


@router.post("/token/revoke", status_code=204)
@limiter.limit("30/minute")
def revoke_refresh_token_by_secret(
    request: Request,
    payload: token_schema.RefreshTokenRequest,
    db: Session = Depends(deps.get_db),
):
    """Server-side revocation by **opaque refresh-token secret**.

    Counterpart to `/auth/token/refresh`: same shape, opposite verb.
    The CLI's `alpha logout` POSTs the locally-stored refresh_token
    here BEFORE wiping the keychain, so a stolen credential can't
    keep auto-refreshing for the full 30-day window. RFC 7009 shape.

    Idempotent: a token that's already revoked, expired, or unknown
    still 204s. Defends against logout-flow flakiness; aggregated
    revocation goes through `DELETE /auth/sessions/{id}` instead.

    Review finding B-3 on PR #442.
    """
    secret = (payload.refresh_token or "").strip()
    if not secret:
        # Unlike /auth/token/refresh where the empty body is a 400,
        # here we no-op — logout should never fail user-visibly on a
        # malformed local credential. The local keychain wipe in the
        # CLI is still the authoritative end state.
        return None

    hashed = hashlib.sha256(secret.encode("utf-8")).hexdigest()
    row = db.query(RefreshToken).filter(RefreshToken.token_hash == hashed).first()
    if row is None:
        return None
    refresh_token_service.revoke_one(db, row=row, reason=RevokeReason.LOGOUT)
    db.commit()
    return None


@router.post("/register", response_model=user_schema.User)
def register_user(
    *,
    db: Session = Depends(deps.get_db),
    user_in: user_schema.UserCreate,
    tenant_in: tenant_schema.TenantCreate
):
    user = user_service.get_user_by_email(db, email=user_in.email)
    if user:
        raise HTTPException(
            status_code=400,
            detail="The user with this email already exists in the system.",
        )
    user = user_service.create_user_with_tenant(db, user_in=user_in, tenant_in=tenant_in)
    return user

@router.get("/users/me", response_model=user_schema.User)
def read_users_me(
    current_user: user_schema.User = Depends(deps.get_current_active_user)
):
    """
    Get current user.
    """
    return current_user

# RFC-5322-ish loose check for the path param. The full grammar is
# absurd; this catches everything Python's email.utils.parseaddr would
# reasonably consider an address while keeping the regex small. Length
# capped to 254 (the practical max per RFC 5321 + RFC 5322 errata).
_EMAIL_PATH_RE = r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$"

# B-5: requester-confirmer binding. The recovery endpoint sets this
# cookie; the reset endpoint refuses to redeem a token without it.
# Defeats the leaked-link → anyone-can-redeem class.
_RESET_CSRF_COOKIE = "ap_reset_csrf"
_RESET_CSRF_COOKIE_TTL = 60 * 60 * 24  # 24h; matches token expiry


def _cookie_should_be_secure() -> bool:
    """N4-5: `secure=True` is correct for prod (cookie only travels
    over HTTPS) but blocks the entire flow when local dev runs over
    plain HTTP — the browser silently drops the Set-Cookie and the
    reset confirm endlessly 400s with the same-browser hint.

    Auto-detect by looking at PUBLIC_BASE_URL: localhost/127.0.0.1/
    0.0.0.0/[::1] over http → allow non-secure cookies so dev works
    without config; everything else → secure required.

    N5-1 (round 5): hostname check is parser-based (not startswith)
    so a misconfigured `PUBLIC_BASE_URL=http://localhost.attacker.com`
    can't trick the helper into emitting a non-secure cookie in prod.
    Matches the URL-allowlist style in email_sender.py.
    N5-4 (round 5): also accept `0.0.0.0` and `::1` so dev binding
    to any of the loopback forms works without re-toggling config.
    """
    base = (settings.PUBLIC_BASE_URL or "").strip()
    if not base:
        # No PUBLIC_BASE_URL configured — assume prod posture
        # (better to ship the flow broken in dev than ship it
        # quietly insecure in any unconfigured env).
        return True
    try:
        from urllib.parse import urlparse
        parsed = urlparse(base)
    except Exception:
        return True
    if parsed.scheme != "http":
        return True  # https → secure required, period
    host = (parsed.hostname or "").lower()
    if host in ("localhost", "127.0.0.1", "0.0.0.0", "::1"):
        return False
    return True


def _hash_token(t: str) -> str:
    """SHA-256 hex digest — shared helper for both the reset token and
    the CSRF correlation cookie. Both are stored hashed in the DB."""
    return hashlib.sha256(t.encode()).hexdigest()


def _log_safe_email_id(email: str) -> str:
    """N-N3: HMAC-SHA256 of an email under a derived sub-key for log
    lines. Plain SHA-256 of an email is reversible against a wordlist
    (anyone with log read access + a customer-export dump can confirm
    which addresses hit a given log event).

    N3-2 (round 3): derive a dedicated sub-key by prefixing SECRET_KEY
    with a fixed purpose-label string. Same trust boundary (compromise
    of SECRET_KEY trivially yields this key too) but a future log-id
    leak can't be cross-applied to JWT analytics — different inputs
    to the same HMAC primitive produce uncorrelated outputs.
    """
    _PWRESET_LOG_KEY = (settings.SECRET_KEY + "|pwreset-log|").encode()
    return hmac.new(
        _PWRESET_LOG_KEY,
        email.lower().encode(),
        hashlib.sha256,
    ).hexdigest()[:16]


# N-N5: module-level Redis client so the per-email cap doesn't open a
# fresh connection on every recovery request. Lazy-initialised so a
# Redis-less local dev environment doesn't crash on import; the
# per-call helper catches connection failures and fails open per I-5.
_redis_client = None
# N5-2 (round 5): proper circuit-breaker. After a Redis failure we
# set a "don't retry until" timestamp so concurrent requests during
# a sustained outage don't all independently re-build a Redis client
# and re-hit the broken socket. Skip reconnect attempts for 60s.
_redis_disabled_until: float = 0.0
_REDIS_CIRCUIT_BREAKER_SECONDS = 60


def _get_redis_client():
    """Return a cached Redis client. None when the breaker is open
    (recent failure) or when the client can't be constructed at all."""
    global _redis_client
    if _redis_client is not None:
        return _redis_client
    # N5-2: don't even try to reconstruct during the breaker window.
    # Concurrent requests during a sustained Redis outage thus pay
    # at most one connect attempt per 60s rather than one per call.
    if _redis_disabled_until and time.monotonic() < _redis_disabled_until:
        return None
    try:
        import redis as _redis
        _redis_client = _redis.from_url(
            settings.REDIS_URL, decode_responses=True, socket_timeout=2
        )
    except Exception as exc:
        # N3-3: surface the failure once at module-init time so a
        # silently-disabled per-email cap shows up in monitoring.
        # The fail-open per-call behaviour stays — better to allow
        # a legit user through than to deny on a Redis blip.
        logger.warning("pwreset: redis client unavailable, per-email cap disabled: %s", exc)
        _redis_client = None
    return _redis_client


def _check_per_email_rate_limit(email: str) -> bool:
    """I-5: outbound-spam dampener. slowapi's 3/hour limit is keyed on
    client IP; an attacker rotating IPs (CGNAT/IPv6) can still hit
    `POST /password-recovery/{email}` for any registered email and
    AgentProvision becomes a soft-spam cannon out of `noreply@…`.

    This second-tier limit is keyed on the email itself, backed by
    Redis. Max 10 password-recovery emails per email-address per
    24h regardless of source IP. Returns True when allowed, False
    when this email has hit its cap.

    Best-effort: if Redis is unreachable we ALLOW (fail-open) rather
    than deny a real user trying to recover their account. The slowapi
    per-IP limit + the no-enumeration response shape are still in
    force.
    """
    global _redis_client
    client = _get_redis_client()
    if client is None:
        return True  # fail-open per the docstring
    try:
        key = f"pwreset:email:{email.lower()}"
        # 24h sliding window (set + expire).
        n = client.incr(key)
        if n == 1:
            client.expire(key, 60 * 60 * 24)
        return n <= 10
    except Exception as exc:
        # I4-1 (round-4 review): `redis-py.from_url` is lazy — it
        # builds a connection-pool client without touching the wire,
        # so the import-time WARNING in `_get_redis_client` never
        # fires on the realistic outage scenario (Redis unreachable
        # over TCP). Log here so a silently-disabled per-email cap
        # surfaces in monitoring.
        #
        # N5-2 (round-5): proper circuit-breaker — null the client AND
        # set a 60s "don't try again" timestamp so concurrent requests
        # during a sustained outage don't all independently reconnect.
        global _redis_disabled_until
        logger.warning(
            "pwreset: redis incr failed, per-email cap bypassed for %ds: %s",
            _REDIS_CIRCUIT_BREAKER_SECONDS,
            exc,
        )
        _redis_client = None
        _redis_disabled_until = time.monotonic() + _REDIS_CIRCUIT_BREAKER_SECONDS
        return True


@router.post(
    "/password-recovery/{email}",
    response_model=auth_schema.PasswordResetMessage,
)
@limiter.limit("3/hour")
def recover_password(
    request: Request,
    response: Response,
    background_tasks: BackgroundTasks,
    email: str = Path(..., min_length=3, max_length=254, pattern=_EMAIL_PATH_RE),
    db: Session = Depends(deps.get_db),
):
    """Password recovery.

    Always returns the same generic message (no enumeration). Sets a
    `SameSite=Strict` HTTP-only cookie containing a random correlation
    ID; the reset endpoint will only accept a token whose stored
    `password_reset_csrf_hash` matches the cookie's hash (B-5).
    """
    # I-5: per-email cap (Redis-backed) on top of slowapi's per-IP.
    # Silently behaves like the miss path when over the cap so an
    # observer can't enumerate which addresses are being targeted.
    if not _check_per_email_rate_limit(email):
        # N-N3: HMAC under SECRET_KEY so the digest isn't dictionary-
        # reversible by anyone with just log read access.
        logger.info("pwreset.over_email_cap email_id=%s", _log_safe_email_id(email))
        return {"message": _PASSWORD_RESET_MESSAGE}

    user = user_service.get_user_by_email(db, email=email)

    if not user:
        # Identical message + same cookie set so a network observer
        # can't distinguish hit/miss by the presence of the cookie.
        # The cookie is meaningless without a matching DB row, so
        # setting it on a miss is functionally a no-op for the user
        # but prevents enumeration via response-header diff.
        decoy = secrets.token_urlsafe(32)
        response.set_cookie(
            _RESET_CSRF_COOKIE,
            decoy,
            max_age=_RESET_CSRF_COOKIE_TTL,
            httponly=True,
            secure=_cookie_should_be_secure(),
            samesite="strict",
            # IMP-1: tighten cookie path so the CSRF token only travels
            # to the confirm endpoint, not the entire auth router.
            path="/api/v1/auth/reset-password",
        )
        return {"message": _PASSWORD_RESET_MESSAGE}

    token = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(32)
    user.password_reset_token = _hash_token(token)
    user.password_reset_csrf_hash = _hash_token(csrf)
    user.password_reset_expires = datetime.utcnow() + timedelta(hours=24)
    user.password_reset_attempts = 0  # reset attempt counter on new request
    db.add(user)
    db.commit()

    response.set_cookie(
        _RESET_CSRF_COOKIE,
        csrf,
        max_age=_RESET_CSRF_COOKIE_TTL,
        httponly=True,
        secure=_cookie_should_be_secure(),
        samesite="strict",
        # IMP-1: tighten cookie path so the CSRF token only travels
        # to the confirm endpoint, not the entire auth router.
        path="/api/v1/auth/reset-password",
    )

    # Best-effort; send_password_reset_email never raises. N-7: do
    # NOT log the email plaintext on the hit path — anyone with log
    # access could otherwise enumerate registered accounts.
    #
    # I3-2 (round-3 review): SMTP send happens in a BackgroundTasks
    # so the request returns in roughly constant time on both hit
    # and miss paths. The previous synchronous-send version leaked
    # account existence via timing (miss = few ms; hit = 100ms-2s
    # SMTP RTT). BackgroundTasks runs AFTER the response body is
    # flushed to the client, so an enumerator watching response time
    # can't distinguish hit/miss any more.
    from app.services.email_sender import send_password_reset_email

    background_tasks.add_task(
        send_password_reset_email,
        to=user.email,
        reset_token=token,
        public_base_url=settings.PUBLIC_BASE_URL,
    )
    logger.info("pwreset.dispatched user_id=%s", user.id)

    return {"message": _PASSWORD_RESET_MESSAGE}


# I-1: max failed reset attempts before token is invalidated. Per-user
# (not per-IP like slowapi) so two IPs can't share 10 attempts.
_RESET_MAX_ATTEMPTS = 3


@router.post(
    "/reset-password",
    response_model=auth_schema.PasswordResetMessage,
)
@limiter.limit("5/hour")
def reset_password(
    request: Request,
    response: Response,
    body: auth_schema.PasswordResetConfirm,
    db: Session = Depends(deps.get_db),
    ap_reset_csrf: str | None = Cookie(default=None),
):
    """Reset password using the token + new password.

    Enforces:
      - B-5: requester-confirmer binding via `ap_reset_csrf` cookie
        (must hash-match `password_reset_csrf_hash` stored on the user)
      - I-1: per-user attempt counter — after 3 wrong tokens we null
        the stored hash and force a fresh /password-recovery
      - I-7: row-level lock on the user during compare+update so two
        racing reset attempts can't both succeed with different
        passwords
      - I-9: user is looked up by `body.email` then the token+csrf
        are verified against THAT user; mismatched email rejects
        with the same generic error
      - B-4: bumps `password_changed_at` so any existing JWT issued
        before the reset is rejected by `deps.get_current_active_user`
      - I-8: writes an audit log row (actor, ip, ua) for forensics
    """
    generic_error = HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Invalid or expired token",
    )
    # I-N1: cross-device email-link redemption fails with
    # SameSite=Strict — the cookie set on device A doesn't travel to
    # device B. We surface that with a SPECIFIC detail the SPA can
    # map to a clearer message.
    same_browser_required_error = HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Open this link in the same browser where you requested the reset",
    )

    # B3-1 (round-3 security review): missing-cookie check FIRST,
    # BEFORE any DB lookup. Otherwise the friendlier message becomes
    # an enumeration oracle: an attacker without a cookie can POST
    # against a target email and learn whether that account has an
    # active in-flight reset (active → same-browser hint, no-active
    # → generic error). Email-agnostic refusal here closes the
    # oracle — cookie-less callers always see the same-browser hint
    # regardless of whether the email exists or has a pending reset.
    if not ap_reset_csrf:
        raise same_browser_required_error

    # I-7: `with_for_update` takes a row lock so two simultaneous
    # confirm requests can't both pass the compare. Released on commit.
    from app.models.user import User
    user = (
        db.query(User)
        .filter(User.email == body.email)
        .with_for_update()
        .first()
    )

    if not user or not user.password_reset_token or not user.password_reset_expires:
        raise generic_error
    if user.password_reset_expires < datetime.utcnow():
        raise generic_error

    # B-5: cookie binding — verify the cookie matches what we stored
    # when the recovery email was minted. Cookie-present-but-wrong is
    # an attack signal, NOT a cross-device user; surface as the
    # generic error so attackers can't tell their forged cookie
    # apart from a stale-token redemption.
    if not user.password_reset_csrf_hash:
        raise generic_error
    if not hmac.compare_digest(
        user.password_reset_csrf_hash, _hash_token(ap_reset_csrf)
    ):
        raise generic_error

    # Token hash compare with attempt-counter on failure (I-1).
    submitted_hash = _hash_token(body.token)
    if not hmac.compare_digest(user.password_reset_token, submitted_hash):
        attempts = (user.password_reset_attempts or 0) + 1
        user.password_reset_attempts = attempts
        if attempts >= _RESET_MAX_ATTEMPTS:
            # Burn the token — force a fresh /password-recovery.
            user.password_reset_token = None
            user.password_reset_csrf_hash = None
            user.password_reset_expires = None
            user.password_reset_attempts = 0
        db.add(user)
        db.commit()
        raise generic_error

    # All checks passed. Commit the new password + clear all reset
    # state + stamp password_changed_at so existing JWTs are invalid.
    user.hashed_password = security.get_password_hash(body.new_password)
    user.password_reset_token = None
    user.password_reset_csrf_hash = None
    user.password_reset_expires = None
    user.password_reset_attempts = 0
    user.password_changed_at = datetime.utcnow()  # B-4
    db.add(user)
    db.commit()

    # I-8: audit-log the reset. Best-effort; a logging failure must
    # not block the actual password update from sticking.
    try:
        client_ip = request.client.host if request.client else "unknown"
        ua = (request.headers.get("user-agent") or "")[:200]
        logger.info(
            "pwreset.success user_id=%s ip=%s ua=%r",
            user.id,
            client_ip,
            ua,
        )
    except Exception:
        pass

    # I-N2: use the injected `Response` to clear the cookie so the
    # FastAPI response_model validation still applies (returning a
    # raw JSONResponse here bypassed the typed contract — N-4 was
    # supposed to lock it down). Token is also nulled above so
    # cookie replay can't redeem anyway; this is defense-in-depth.
    # IMP-1: path must match the tightened cookie scope set on issue
    # (the recovery endpoint) so the browser can find and clear it.
    response.delete_cookie(_RESET_CSRF_COOKIE, path="/api/v1/auth/reset-password")
    return {"message": "Password updated successfully"}


# ---------------------------------------------------------------------------
# Device-flow login (gh-style) for the `agentprovision` CLI
# ---------------------------------------------------------------------------
#
# Flow:
#   1. Client POST /api/v1/auth/device-code  -> returns { device_code, user_code, verification_uri, expires_in, interval }
#   2. User opens verification_uri in a browser, authenticates with the
#      existing /login page, and POSTs /api/v1/auth/device-approve { user_code }
#      while logged in to bind their access_token to the device_code.
#   3. Client polls POST /api/v1/auth/device-token { device_code }
#      -> 200 { access_token, token_type } once approved
#      -> 400 { error: "authorization_pending" | "slow_down" | "expired_token" | "access_denied" }
#
# Pending state is stored in Redis with a short TTL. If Redis is unavailable
# we fail closed (CLI falls back to email/password prompts).

from pydantic import BaseModel, Field

_DEVICE_CODE_TTL_SECONDS = 600  # 10 minutes
_DEVICE_CODE_INTERVAL_SECONDS = 5
_DEVICE_USER_CODE_LEN = 8  # Pretty-printed as XXXX-XXXX


class DeviceCodeResponse(BaseModel):
    device_code: str
    user_code: str
    verification_uri: str
    verification_uri_complete: str
    expires_in: int
    interval: int


class DeviceApproveRequest(BaseModel):
    user_code: str = Field(
        ...,
        description="The XXXX-XXXX code the user typed in the browser",
        min_length=8,
        max_length=10,
    )


class DeviceApproveResponse(BaseModel):
    approved: bool


class DeviceTokenRequest(BaseModel):
    device_code: str = Field(..., description="Opaque token issued by /device-code")


class DeviceTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"

# Treat 'I', 'O', '0', '1' as ambiguous; pick a friendly alphabet.
_USER_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def _device_redis():
    try:
        import redis as redis_lib
        return redis_lib.from_url(settings.REDIS_URL)
    except Exception as exc:
        logger.warning("auth.device-code: redis unavailable: %s", exc)
        return None


def _generate_user_code() -> str:
    raw = "".join(secrets.choice(_USER_CODE_ALPHABET) for _ in range(_DEVICE_USER_CODE_LEN))
    return f"{raw[:4]}-{raw[4:]}"


def _device_state_key(device_code: str) -> str:
    return f"auth:device:{device_code}"


def _user_code_index_key(user_code: str) -> str:
    return f"auth:device:user:{user_code}"


@router.post("/device-code", response_model=DeviceCodeResponse)
@limiter.limit("20/minute")
def request_device_code(request: Request) -> DeviceCodeResponse:
    """Mint a new device_code + user_code pair (gh-style device-flow). No auth required.

    The CLI calls this first, opens ``verification_uri_complete`` in a browser,
    and then polls ``POST /device-token`` with the returned ``device_code``
    until the user approves in the web UI.
    """
    redis = _device_redis()
    if redis is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="device-flow login unavailable (cache backend down)",
        )
    device_code = secrets.token_urlsafe(32)
    user_code = _generate_user_code()
    base_url = (settings.PUBLIC_BASE_URL or "").rstrip("/")
    verification_uri = f"{base_url}/login/device" if base_url else "/login/device"
    verification_uri_complete = f"{verification_uri}?user_code={user_code}"
    state = json.dumps({
        "user_code": user_code,
        "status": "pending",
        "access_token": None,
    })
    try:
        redis.set(_device_state_key(device_code), state, ex=_DEVICE_CODE_TTL_SECONDS)
        redis.set(_user_code_index_key(user_code), device_code, ex=_DEVICE_CODE_TTL_SECONDS)
    except Exception as exc:
        logger.warning("auth.device-code: redis write failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="device-flow login unavailable",
        )
    return DeviceCodeResponse(
        device_code=device_code,
        user_code=user_code,
        verification_uri=verification_uri,
        verification_uri_complete=verification_uri_complete,
        expires_in=_DEVICE_CODE_TTL_SECONDS,
        interval=_DEVICE_CODE_INTERVAL_SECONDS,
    )


@router.post("/device-approve", response_model=DeviceApproveResponse)
@limiter.limit("10/minute")
def approve_device_code(
    request: Request,
    body: DeviceApproveRequest,
    current_user=Depends(deps.get_current_active_user),
) -> DeviceApproveResponse:
    """Web UI calls this once the logged-in user enters the user_code they got
    from the CLI. Binds a fresh access token to the device_code so the CLI's
    next ``/device-token`` poll succeeds.

    Strips dashes + whitespace from the user_code before lookup so paste-from-
    screenshot users (extra spaces) and dashless typers ("ABCDEFGH" instead of
    "ABCD-EFGH") both work. Stored canonically as XXXX-XXXX uppercase.

    Refuses to re-bind an already-approved device_code (409) — closes the
    TOCTOU window where a second logged-in user could swap the bound token
    on a polling CLI at the last millisecond. Phase 4 review C-2.
    """
    # Normalise: strip whitespace, drop dashes, uppercase, then re-insert the
    # canonical dash. Accepts "ABCD-EFGH", "abcd-efgh", "ABCDEFGH", "abcdefgh",
    # "AB CD-EF GH", etc.
    raw_uc = "".join(body.user_code.split()).replace("-", "").upper()
    if len(raw_uc) != _DEVICE_USER_CODE_LEN:
        raise HTTPException(
            status_code=400,
            detail=f"user_code must be {_DEVICE_USER_CODE_LEN} characters (XXXX-XXXX)",
        )
    user_code = f"{raw_uc[:4]}-{raw_uc[4:]}"
    redis = _device_redis()
    if redis is None:
        raise HTTPException(status_code=503, detail="device-flow login unavailable")
    raw_dc = redis.get(_user_code_index_key(user_code))
    if not raw_dc:
        raise HTTPException(status_code=404, detail="user_code not found or expired")
    device_code = raw_dc.decode() if isinstance(raw_dc, (bytes, bytearray)) else raw_dc
    raw = redis.get(_device_state_key(device_code))
    if not raw:
        raise HTTPException(status_code=404, detail="device_code expired")
    state = json.loads(raw)
    if state.get("status") == "approved":
        # Already bound — refuse to overwrite with a different user's token.
        raise HTTPException(status_code=409, detail="device_code already approved")
    # Mint a token for the approving user.
    access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    claims = {"user_id": str(current_user.id)}
    if current_user.tenant_id:
        claims["tenant_id"] = str(current_user.tenant_id)
    access_token = security.create_access_token(
        current_user.email,
        expires_delta=access_token_expires,
        additional_claims=claims,
    )
    state["status"] = "approved"
    state["access_token"] = access_token
    redis.set(_device_state_key(device_code), json.dumps(state), ex=_DEVICE_CODE_TTL_SECONDS)
    return DeviceApproveResponse(approved=True)


def _device_error(error_code: str, http_status: int = 400) -> JSONResponse:
    """RFC-8628 error response with the error code at the TOP LEVEL of the
    body — NOT under FastAPI's default `detail` envelope. The Rust CLI client
    in apps/agentprovision-core/src/auth.rs deserializes `body.error` flat;
    any `{"detail": {"error": "..."}}` shape would deserialize to None and
    break every poll. Phase 4 review C-1.
    """
    return JSONResponse(status_code=http_status, content={"error": error_code})


@router.post(
    "/device-token",
    responses={
        200: {"model": DeviceTokenResponse},
        400: {"description": "RFC-8628 error: authorization_pending | slow_down | expired_token | access_denied | invalid_request"},
        503: {"description": "Cache backend unavailable"},
    },
)
@limiter.limit("60/minute")
def poll_device_token(request: Request, body: DeviceTokenRequest):
    """CLI polls this with the device_code. Mirrors GitHub's RFC-8628 wire model:
    400 + {"error": "authorization_pending" | "slow_down" | "expired_token" |
    "access_denied" | "invalid_request"} at the TOP LEVEL of the body so
    gh-style polling clients (incl. apps/agentprovision-core/src/auth.rs)
    deserialize the error code without unwrapping nested envelopes.
    """
    device_code = body.device_code.strip()
    if not device_code:
        return _device_error("invalid_request")
    redis = _device_redis()
    if redis is None:
        raise HTTPException(status_code=503, detail="device-flow login unavailable")
    raw = redis.get(_device_state_key(device_code))
    if not raw:
        # No record -> expired or never minted.
        return _device_error("expired_token")
    state = json.loads(raw)
    status_field = state.get("status")
    if status_field == "pending":
        return _device_error("authorization_pending")
    if status_field == "denied":
        return _device_error("access_denied")
    if status_field == "approved":
        token = state.get("access_token")
        if not token:
            # Race / corrupted state — treat as expired so the CLI re-bootstraps.
            return _device_error("expired_token")
        # One-shot: consume the device_code on first successful poll so a leaked
        # token in transit can't be replayed AND two parallel polls can't
        # double-issue. Use Redis GETDEL (atomic) so the read+delete is one
        # round-trip — a parallel poll either wins the GETDEL and gets the
        # token, or sees the key already gone and returns expired_token.
        # Phase 4 review I-1.
        try:
            atomic = redis.getdel(_device_state_key(device_code))
            if atomic is None:
                # Lost the race to a parallel poll — let the winner have the
                # token, return expired here.
                return _device_error("expired_token")
            user_code = state.get("user_code")
            if user_code:
                redis.delete(_user_code_index_key(user_code))
        except AttributeError:
            # Older redis-py without getdel — fall back to delete (still
            # one-shot under non-concurrent load, which is the realistic
            # case for a CLI polling at 5s intervals).
            try:
                redis.delete(_device_state_key(device_code))
                user_code = state.get("user_code")
                if user_code:
                    redis.delete(_user_code_index_key(user_code))
            except Exception:  # noqa: BLE001
                pass
        except Exception:  # noqa: BLE001
            # Best-effort cleanup; the TTL on the keys is the backstop.
            pass
        return DeviceTokenResponse(access_token=token, token_type="bearer")
    return _device_error("expired_token")
