"""OAuth2 Authorization Code flow for Google, GitHub, and LinkedIn.

Endpoints:
  GET  /oauth/{provider}/authorize   — Returns auth URL (authenticated)
  GET  /oauth/{provider}/callback    — Provider redirect (unauthenticated)
  POST /oauth/{provider}/disconnect  — Revoke credentials (authenticated)
  GET  /oauth/{provider}/status      — Connection status (authenticated)
"""

import logging
import secrets
import uuid
from datetime import datetime, timedelta

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from jose import jwt, JWTError
from sqlalchemy.orm import Session

from app.api import deps
from app.core.config import settings
from app.models.skill_config import SkillConfig
from app.models.skill_credential import SkillCredential
from app.models.user import User
from app.services.orchestration.credential_vault import store_credential, revoke_credential

logger = logging.getLogger(__name__)

router = APIRouter()

# ---------------------------------------------------------------------------
# Provider configuration
# ---------------------------------------------------------------------------

OAUTH_PROVIDERS = {
    "google": {
        "authorize_url": "https://accounts.google.com/o/oauth2/v2/auth",
        "token_url": "https://oauth2.googleapis.com/token",
        "scopes": [
            "https://www.googleapis.com/auth/gmail.readonly",
            "https://www.googleapis.com/auth/gmail.send",
            "https://www.googleapis.com/auth/calendar.readonly",
            "https://www.googleapis.com/auth/calendar.events",
        ],
        "skill_names": ["gmail", "google_calendar"],
    },
    "github": {
        "authorize_url": "https://github.com/login/oauth/authorize",
        "token_url": "https://github.com/login/oauth/access_token",
        "scopes": ["repo", "read:user", "read:org"],
        "skill_names": ["github"],
    },
    "linkedin": {
        "authorize_url": "https://www.linkedin.com/oauth/v2/authorization",
        "token_url": "https://www.linkedin.com/oauth/v2/accessToken",
        "scopes": ["openid", "profile", "email", "w_member_social"],
        "skill_names": ["linkedin"],
    },
}


def _get_provider_credentials(provider: str):
    """Return (client_id, client_secret, redirect_uri) for a provider."""
    if provider == "google":
        return settings.GOOGLE_CLIENT_ID, settings.GOOGLE_CLIENT_SECRET, settings.GOOGLE_REDIRECT_URI
    elif provider == "github":
        return settings.GITHUB_CLIENT_ID, settings.GITHUB_CLIENT_SECRET, settings.GITHUB_REDIRECT_URI
    elif provider == "linkedin":
        return settings.LINKEDIN_CLIENT_ID, settings.LINKEDIN_CLIENT_SECRET, settings.LINKEDIN_REDIRECT_URI
    return None, None, None


def _validate_provider(provider: str):
    if provider not in OAUTH_PROVIDERS:
        raise HTTPException(status_code=400, detail=f"Unknown OAuth provider: {provider}")
    client_id, client_secret, _ = _get_provider_credentials(provider)
    if not client_id or not client_secret:
        raise HTTPException(status_code=501, detail=f"OAuth not configured for {provider}")


# ---------------------------------------------------------------------------
# GET /oauth/{provider}/authorize
# ---------------------------------------------------------------------------

@router.get("/{provider}/authorize")
def oauth_authorize(
    provider: str,
    current_user: User = Depends(deps.get_current_active_user),
):
    """Generate OAuth authorization URL with signed state JWT."""
    _validate_provider(provider)

    config = OAUTH_PROVIDERS[provider]
    client_id, _, redirect_uri = _get_provider_credentials(provider)

    # Build state JWT for CSRF protection
    state_payload = {
        "tenant_id": str(current_user.tenant_id),
        "user_id": str(current_user.id),
        "provider": provider,
        "nonce": secrets.token_urlsafe(16),
        "exp": datetime.utcnow() + timedelta(minutes=10),
    }
    state_token = jwt.encode(state_payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)

    # Build authorization URL
    scopes = " ".join(config["scopes"])
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": scopes,
        "state": state_token,
    }

    # Google-specific: request refresh token
    if provider == "google":
        params["access_type"] = "offline"
        params["prompt"] = "consent"

    query = "&".join(f"{k}={httpx.QueryParams({k: v})[k]}" for k, v in params.items())
    auth_url = f"{config['authorize_url']}?{query}"

    return {"auth_url": auth_url}


# ---------------------------------------------------------------------------
# GET /oauth/{provider}/callback
# ---------------------------------------------------------------------------

CALLBACK_HTML = """<!DOCTYPE html>
<html>
<head><title>OAuth Complete</title></head>
<body>
<script>
  window.opener && window.opener.postMessage({{ type: '{msg_type}', provider: '{provider}' }}, '*');
  setTimeout(function() {{ window.close(); }}, 1500);
</script>
<p>{message}</p>
</body>
</html>"""


@router.get("/{provider}/callback", response_class=HTMLResponse)
def oauth_callback(
    provider: str,
    code: str = "",
    state: str = "",
    error: str = "",
    db: Session = Depends(deps.get_db),
):
    """Handle OAuth callback from provider. Exchanges code for tokens."""
    if provider not in OAUTH_PROVIDERS:
        return HTMLResponse(CALLBACK_HTML.format(
            msg_type="oauth-error", provider=provider,
            message="Unknown provider",
        ))

    if error:
        logger.warning("OAuth error from %s: %s", provider, error)
        return HTMLResponse(CALLBACK_HTML.format(
            msg_type="oauth-error", provider=provider,
            message=f"Authorization denied: {error}",
        ))

    if not code or not state:
        return HTMLResponse(CALLBACK_HTML.format(
            msg_type="oauth-error", provider=provider,
            message="Missing code or state parameter",
        ))

    # Verify state JWT
    try:
        payload = jwt.decode(state, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        if payload.get("provider") != provider:
            raise JWTError("Provider mismatch")
        tenant_id = uuid.UUID(payload["tenant_id"])
        user_id = uuid.UUID(payload["user_id"])
    except (JWTError, KeyError, ValueError) as e:
        logger.warning("Invalid OAuth state: %s", e)
        return HTMLResponse(CALLBACK_HTML.format(
            msg_type="oauth-error", provider=provider,
            message="Invalid or expired authorization state",
        ))

    # Exchange code for tokens
    config = OAUTH_PROVIDERS[provider]
    client_id, client_secret, redirect_uri = _get_provider_credentials(provider)

    token_data = {
        "client_id": client_id,
        "client_secret": client_secret,
        "code": code,
        "redirect_uri": redirect_uri,
    }

    if provider == "google":
        token_data["grant_type"] = "authorization_code"
    elif provider == "github":
        pass  # GitHub doesn't require grant_type
    elif provider == "linkedin":
        token_data["grant_type"] = "authorization_code"

    headers = {"Accept": "application/json"}

    try:
        with httpx.Client(timeout=15.0) as client:
            resp = client.post(config["token_url"], data=token_data, headers=headers)
            resp.raise_for_status()
            tokens = resp.json()
    except Exception as e:
        logger.exception("Token exchange failed for %s: %s", provider, e)
        return HTMLResponse(CALLBACK_HTML.format(
            msg_type="oauth-error", provider=provider,
            message="Failed to exchange authorization code",
        ))

    access_token = tokens.get("access_token")
    refresh_token = tokens.get("refresh_token", "")

    if not access_token:
        logger.error("No access_token in response from %s: %s", provider, tokens)
        return HTMLResponse(CALLBACK_HTML.format(
            msg_type="oauth-error", provider=provider,
            message="Provider did not return an access token",
        ))

    # Store tokens for each skill associated with this provider
    for skill_name in config["skill_names"]:
        # Ensure a SkillConfig exists and is enabled
        skill_config = (
            db.query(SkillConfig)
            .filter(SkillConfig.tenant_id == tenant_id, SkillConfig.skill_name == skill_name)
            .first()
        )
        if not skill_config:
            skill_config = SkillConfig(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                skill_name=skill_name,
                enabled=True,
            )
            db.add(skill_config)
            db.commit()
            db.refresh(skill_config)
        elif not skill_config.enabled:
            skill_config.enabled = True
            db.commit()

        # Revoke old credentials for this skill
        old_creds = (
            db.query(SkillCredential)
            .filter(
                SkillCredential.skill_config_id == skill_config.id,
                SkillCredential.tenant_id == tenant_id,
                SkillCredential.status == "active",
            )
            .all()
        )
        for old in old_creds:
            revoke_credential(db, credential_id=old.id, tenant_id=tenant_id)

        # Store new tokens
        store_credential(
            db,
            skill_config_id=skill_config.id,
            tenant_id=tenant_id,
            credential_key="oauth_token",
            plaintext_value=access_token,
            credential_type="oauth_token",
        )
        if refresh_token:
            store_credential(
                db,
                skill_config_id=skill_config.id,
                tenant_id=tenant_id,
                credential_key="refresh_token",
                plaintext_value=refresh_token,
                credential_type="oauth_token",
            )

    logger.info("OAuth %s connected for tenant=%s user=%s", provider, tenant_id, user_id)

    return HTMLResponse(CALLBACK_HTML.format(
        msg_type="oauth-success", provider=provider,
        message=f"Connected to {provider.title()}! This window will close.",
    ))


# ---------------------------------------------------------------------------
# POST /oauth/{provider}/disconnect
# ---------------------------------------------------------------------------

@router.post("/{provider}/disconnect")
def oauth_disconnect(
    provider: str,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    """Revoke OAuth credentials and disable skill configs for a provider."""
    if provider not in OAUTH_PROVIDERS:
        raise HTTPException(status_code=400, detail=f"Unknown provider: {provider}")

    config = OAUTH_PROVIDERS[provider]
    revoked_count = 0

    for skill_name in config["skill_names"]:
        skill_config = (
            db.query(SkillConfig)
            .filter(
                SkillConfig.tenant_id == current_user.tenant_id,
                SkillConfig.skill_name == skill_name,
            )
            .first()
        )
        if not skill_config:
            continue

        # Revoke all active credentials
        creds = (
            db.query(SkillCredential)
            .filter(
                SkillCredential.skill_config_id == skill_config.id,
                SkillCredential.tenant_id == current_user.tenant_id,
                SkillCredential.status == "active",
            )
            .all()
        )
        for cred in creds:
            revoke_credential(db, credential_id=cred.id, tenant_id=current_user.tenant_id)
            revoked_count += 1

        # Disable the skill config
        skill_config.enabled = False
        db.commit()

    return {"disconnected": True, "provider": provider, "credentials_revoked": revoked_count}


# ---------------------------------------------------------------------------
# GET /oauth/{provider}/status
# ---------------------------------------------------------------------------

@router.get("/{provider}/status")
def oauth_status(
    provider: str,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    """Check if OAuth is connected for a provider."""
    if provider not in OAUTH_PROVIDERS:
        raise HTTPException(status_code=400, detail=f"Unknown provider: {provider}")

    config = OAUTH_PROVIDERS[provider]
    connected = False

    for skill_name in config["skill_names"]:
        skill_config = (
            db.query(SkillConfig)
            .filter(
                SkillConfig.tenant_id == current_user.tenant_id,
                SkillConfig.skill_name == skill_name,
                SkillConfig.enabled == True,
            )
            .first()
        )
        if not skill_config:
            continue

        has_token = (
            db.query(SkillCredential)
            .filter(
                SkillCredential.skill_config_id == skill_config.id,
                SkillCredential.tenant_id == current_user.tenant_id,
                SkillCredential.credential_key == "oauth_token",
                SkillCredential.status == "active",
            )
            .first()
        ) is not None

        if has_token:
            connected = True
            break

    return {"connected": connected, "provider": provider}
