"""OAuth2 Authorization Code flow for Google, Microsoft, GitHub, and LinkedIn.

Supports multiple connected accounts per provider per tenant.

Endpoints:
  GET  /oauth/{provider}/authorize          — Returns auth URL (authenticated)
  GET  /oauth/{provider}/callback           — Provider redirect (unauthenticated)
  POST /oauth/{provider}/disconnect         — Revoke credentials (authenticated)
  GET  /oauth/{provider}/status             — Connection status (authenticated)
  GET  /oauth/internal/token/{integration_name}   — Decrypted token (internal only)
"""

import logging
import secrets
import uuid
from datetime import datetime, timedelta
from typing import Dict, Optional

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import HTMLResponse
from jose import jwt, JWTError
from sqlalchemy.orm import Session

from app.api import deps
from app.core.config import settings
from app.models.integration_config import IntegrationConfig
from app.models.integration_credential import IntegrationCredential
from app.models.user import User
from app.services.orchestration.credential_vault import (
    store_credential,
    revoke_credential,
    retrieve_credentials_for_skill,
)

logger = logging.getLogger(__name__)

router = APIRouter()

# ---------------------------------------------------------------------------
# Provider configuration
# ---------------------------------------------------------------------------

OAUTH_PROVIDERS = {
    "google": {
        "authorize_url": "https://accounts.google.com/o/oauth2/v2/auth",
        "token_url": "https://oauth2.googleapis.com/token",
        "userinfo_url": "https://www.googleapis.com/oauth2/v2/userinfo",
        "scopes": [
            "https://www.googleapis.com/auth/gmail.readonly",
            "https://www.googleapis.com/auth/gmail.send",
            "https://www.googleapis.com/auth/calendar.readonly",
            "https://www.googleapis.com/auth/calendar.events",
            "https://www.googleapis.com/auth/userinfo.email",
        ],
        "integration_names": ["gmail", "google_calendar"],
    },
    "microsoft": {
        "authorize_url": "https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
        "token_url": "https://login.microsoftonline.com/common/oauth2/v2.0/token",
        "userinfo_url": "https://graph.microsoft.com/v1.0/me?$select=mail,userPrincipalName",
        "scopes": [
            "offline_access",
            "openid",
            "profile",
            "email",
            "User.Read",
            "Mail.Read",
            "Mail.Send",
        ],
        "integration_names": ["outlook"],
    },
    "github": {
        "authorize_url": "https://github.com/login/oauth/authorize",
        "token_url": "https://github.com/login/oauth/access_token",
        "userinfo_url": "https://api.github.com/user",
        "scopes": ["repo", "read:user", "read:org"],
        "integration_names": ["github"],
    },
    "linkedin": {
        "authorize_url": "https://www.linkedin.com/oauth/v2/authorization",
        "token_url": "https://www.linkedin.com/oauth/v2/accessToken",
        "userinfo_url": "https://api.linkedin.com/v2/userinfo",
        "scopes": ["openid", "profile", "email", "w_member_social"],
        "integration_names": ["linkedin"],
    },
}


def _get_provider_credentials(provider: str):
    """Return (client_id, client_secret, redirect_uri) for a provider."""
    if provider == "google":
        return settings.GOOGLE_CLIENT_ID, settings.GOOGLE_CLIENT_SECRET, settings.GOOGLE_REDIRECT_URI
    elif provider == "microsoft":
        return (
            settings.MICROSOFT_CLIENT_ID,
            settings.MICROSOFT_CLIENT_SECRET,
            settings.MICROSOFT_REDIRECT_URI,
        )
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


def _get_provider_scope_string(provider: str) -> str:
    return " ".join(OAUTH_PROVIDERS[provider]["scopes"])


def _fetch_account_email(provider: str, access_token: str) -> Optional[str]:
    """Fetch the authenticated user's email from the provider's userinfo endpoint."""
    config = OAUTH_PROVIDERS[provider]
    userinfo_url = config.get("userinfo_url")
    if not userinfo_url:
        return None

    try:
        headers = {"Authorization": f"Bearer {access_token}"}
        if provider == "github":
            headers["Accept"] = "application/vnd.github+json"

        with httpx.Client(timeout=10.0) as client:
            resp = client.get(userinfo_url, headers=headers)
            resp.raise_for_status()
            data = resp.json()

        if provider == "google":
            return data.get("email")
        elif provider == "microsoft":
            return data.get("mail") or data.get("userPrincipalName")
        elif provider == "github":
            email = data.get("email")
            if not email:
                # GitHub may not return email in /user, try /user/emails
                with httpx.Client(timeout=10.0) as client:
                    resp = client.get(
                        "https://api.github.com/user/emails",
                        headers=headers,
                    )
                    if resp.status_code == 200:
                        emails = resp.json()
                        primary = next((e for e in emails if e.get("primary")), None)
                        email = primary["email"] if primary else (emails[0]["email"] if emails else None)
            return email or data.get("login")
        elif provider == "linkedin":
            return data.get("email")

    except Exception as e:
        logger.warning("Failed to fetch account email from %s: %s", provider, e)

    return None


def _refresh_access_token(provider: str, refresh_token: str) -> Optional[Dict[str, str]]:
    """Use a refresh token to get fresh OAuth tokens."""
    config = OAUTH_PROVIDERS[provider]
    client_id, client_secret, _ = _get_provider_credentials(provider)

    try:
        token_data = {
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        }
        if provider == "microsoft":
            token_data["scope"] = _get_provider_scope_string(provider)

        with httpx.Client(timeout=10.0) as client:
            resp = client.post(config["token_url"], data=token_data)
            resp.raise_for_status()
            tokens = resp.json()
            access_token = tokens.get("access_token")
            if not access_token:
                return None

            refreshed_tokens = {"access_token": access_token}
            if tokens.get("refresh_token"):
                refreshed_tokens["refresh_token"] = tokens["refresh_token"]
            return refreshed_tokens
    except Exception as e:
        logger.warning("Failed to refresh token for %s: %s", provider, e)
        return None


def _integration_to_provider(integration_name: str) -> Optional[str]:
    """Map an integration name back to its OAuth provider."""
    for provider, config in OAUTH_PROVIDERS.items():
        if integration_name in config["integration_names"]:
            return provider
    return None


def _update_stored_tokens(
    db: Session,
    integration_config_id: uuid.UUID,
    tenant_id: uuid.UUID,
    access_token: str,
    refresh_token: Optional[str] = None,
):
    """Replace stored OAuth credentials with fresh values."""
    try:
        old_creds = (
            db.query(IntegrationCredential)
            .filter(
                IntegrationCredential.integration_config_id == integration_config_id,
                IntegrationCredential.tenant_id == tenant_id,
                IntegrationCredential.credential_key.in_(["oauth_token", "refresh_token"]),
                IntegrationCredential.status == "active",
            )
            .all()
        )
        for old_cred in old_creds:
            revoke_credential(db, credential_id=old_cred.id, tenant_id=tenant_id)

        store_credential(
            db,
            integration_config_id=integration_config_id,
            tenant_id=tenant_id,
            credential_key="oauth_token",
            plaintext_value=access_token,
            credential_type="oauth_token",
        )
        if refresh_token:
            store_credential(
                db,
                integration_config_id=integration_config_id,
                tenant_id=tenant_id,
                credential_key="refresh_token",
                plaintext_value=refresh_token,
                credential_type="oauth_token",
            )
    except Exception:
        logger.exception("Failed to update stored tokens for config=%s", integration_config_id)


def _lazy_backfill_email(
    db: Session, provider: str, config: "IntegrationConfig", tenant_id: uuid.UUID,
) -> Optional[str]:
    """Backfill account_email for legacy configs missing it.

    Uses refresh_token → fresh access_token → userinfo to discover the email.
    """
    if config.account_email:
        return config.account_email

    try:
        creds = retrieve_credentials_for_skill(db, config.id, tenant_id)
        refresh_tok = creds.get("refresh_token")
        if not refresh_tok:
            return None

        refreshed_tokens = _refresh_access_token(provider, refresh_tok)
        if not refreshed_tokens:
            return None

        email = _fetch_account_email(provider, refreshed_tokens["access_token"])
        if email:
            config.account_email = email
            db.commit()
            logger.info("Backfilled account_email=%s for config %s", email, config.id)
            return email
    except Exception as e:
        logger.warning("Lazy backfill failed for config %s: %s", config.id, e)

    return None


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
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": _get_provider_scope_string(provider),
        "state": state_token,
    }

    # Google-specific: request refresh token + always show account picker
    if provider == "google":
        params["access_type"] = "offline"
        params["prompt"] = "consent select_account"
    elif provider == "microsoft":
        params["prompt"] = "select_account"
        params["response_mode"] = "query"

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
  window.opener && window.opener.postMessage({{ type: '{msg_type}', provider: '{provider}', email: '{email}' }}, '*');
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
            msg_type="oauth-error", provider=provider, email="",
            message="Unknown provider",
        ))

    if error:
        logger.warning("OAuth error from %s: %s", provider, error)
        return HTMLResponse(CALLBACK_HTML.format(
            msg_type="oauth-error", provider=provider, email="",
            message=f"Authorization denied: {error}",
        ))

    if not code or not state:
        return HTMLResponse(CALLBACK_HTML.format(
            msg_type="oauth-error", provider=provider, email="",
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
            msg_type="oauth-error", provider=provider, email="",
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
    elif provider == "microsoft":
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
            msg_type="oauth-error", provider=provider, email="",
            message="Failed to exchange authorization code",
        ))

    access_token = tokens.get("access_token")
    refresh_token = tokens.get("refresh_token", "")

    if not access_token:
        logger.error("No access_token in response from %s: %s", provider, tokens)
        return HTMLResponse(CALLBACK_HTML.format(
            msg_type="oauth-error", provider=provider, email="",
            message="Provider did not return an access token",
        ))

    # Fetch the authenticated user's email to identify the account
    account_email = _fetch_account_email(provider, access_token)
    logger.info("OAuth %s account email: %s", provider, account_email)

    # Store tokens for each integration associated with this provider
    for integration_name in config["integration_names"]:
        # Find existing IntegrationConfig for THIS specific account (by email)
        # or fall back to any config without an email (legacy)
        ic = None
        if account_email:
            ic = (
                db.query(IntegrationConfig)
                .filter(
                    IntegrationConfig.tenant_id == tenant_id,
                    IntegrationConfig.integration_name == integration_name,
                    IntegrationConfig.account_email == account_email,
                )
                .first()
            )

        if not ic:
            # Check for a legacy config (no account_email) to upgrade
            legacy_config = (
                db.query(IntegrationConfig)
                .filter(
                    IntegrationConfig.tenant_id == tenant_id,
                    IntegrationConfig.integration_name == integration_name,
                    IntegrationConfig.account_email.is_(None),
                )
                .first()
            )

            if legacy_config:
                # Upgrade legacy config with account email
                legacy_config.account_email = account_email
                legacy_config.enabled = True
                db.commit()
                db.refresh(legacy_config)
                ic = legacy_config
            else:
                # Create new config for this account
                ic = IntegrationConfig(
                    id=uuid.uuid4(),
                    tenant_id=tenant_id,
                    integration_name=integration_name,
                    account_email=account_email,
                    enabled=True,
                )
                db.add(ic)
                db.commit()
                db.refresh(ic)

        elif not ic.enabled:
            ic.enabled = True
            db.commit()

        # Revoke old credentials for THIS specific config only
        old_creds = (
            db.query(IntegrationCredential)
            .filter(
                IntegrationCredential.integration_config_id == ic.id,
                IntegrationCredential.tenant_id == tenant_id,
                IntegrationCredential.status == "active",
            )
            .all()
        )
        for old in old_creds:
            revoke_credential(db, credential_id=old.id, tenant_id=tenant_id)

        # Store new tokens
        store_credential(
            db,
            integration_config_id=ic.id,
            tenant_id=tenant_id,
            credential_key="oauth_token",
            plaintext_value=access_token,
            credential_type="oauth_token",
        )
        if refresh_token:
            store_credential(
                db,
                integration_config_id=ic.id,
                tenant_id=tenant_id,
                credential_key="refresh_token",
                plaintext_value=refresh_token,
                credential_type="oauth_token",
            )

    logger.info(
        "OAuth %s connected for tenant=%s user=%s email=%s",
        provider, tenant_id, user_id, account_email,
    )

    # Auto-start inbox monitor when Google connects
    if provider == "google":
        try:
            import asyncio
            from temporalio.client import Client as TemporalClient
            from app.workflows.inbox_monitor import InboxMonitorWorkflow

            async def _start_monitor():
                tc = await TemporalClient.connect(settings.TEMPORAL_ADDRESS)
                wf_id = f"inbox-monitor-{tenant_id}"
                await tc.start_workflow(
                    InboxMonitorWorkflow.run,
                    args=[str(tenant_id), 900],  # 15 min interval
                    id=wf_id,
                    task_queue="servicetsunami-orchestration",
                )
                logger.info("Auto-started inbox monitor for tenant=%s", tenant_id)

            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    asyncio.ensure_future(_start_monitor())
                else:
                    loop.run_until_complete(_start_monitor())
            except RuntimeError:
                asyncio.run(_start_monitor())
        except Exception as e:
            # Don't fail OAuth if monitor start fails
            logger.warning("Auto-start inbox monitor failed (non-fatal): %s", e)

    safe_email = (account_email or "").replace("'", "\\'")

    return HTMLResponse(CALLBACK_HTML.format(
        msg_type="oauth-success", provider=provider, email=safe_email,
        message=f"Connected {account_email or provider.title()}! This window will close.",
    ))


# ---------------------------------------------------------------------------
# POST /oauth/{provider}/disconnect
# ---------------------------------------------------------------------------

@router.post("/{provider}/disconnect")
def oauth_disconnect(
    provider: str,
    account_email: Optional[str] = Query(None, description="Disconnect a specific account by email"),
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    """Revoke OAuth credentials and disable integration configs for a provider.

    If account_email is provided, only disconnects that specific account.
    Otherwise disconnects all accounts for the provider.
    """
    if provider not in OAUTH_PROVIDERS:
        raise HTTPException(status_code=400, detail=f"Unknown provider: {provider}")

    config = OAUTH_PROVIDERS[provider]
    revoked_count = 0

    for integration_name in config["integration_names"]:
        query = (
            db.query(IntegrationConfig)
            .filter(
                IntegrationConfig.tenant_id == current_user.tenant_id,
                IntegrationConfig.integration_name == integration_name,
            )
        )
        if account_email:
            query = query.filter(IntegrationConfig.account_email == account_email)

        configs = query.all()

        for cfg in configs:
            # Revoke all active credentials
            creds = (
                db.query(IntegrationCredential)
                .filter(
                    IntegrationCredential.integration_config_id == cfg.id,
                    IntegrationCredential.tenant_id == current_user.tenant_id,
                    IntegrationCredential.status == "active",
                )
                .all()
            )
            for cred in creds:
                revoke_credential(db, credential_id=cred.id, tenant_id=current_user.tenant_id)
                revoked_count += 1

            # Disable the integration config
            cfg.enabled = False
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
    """Check OAuth connection status for a provider.

    Returns overall connected status plus list of individual connected accounts.
    """
    if provider not in OAUTH_PROVIDERS:
        raise HTTPException(status_code=400, detail=f"Unknown provider: {provider}")

    config = OAUTH_PROVIDERS[provider]
    accounts = []

    # Use the first skill to check (e.g., "gmail" for google)
    primary_integration = config["integration_names"][0]

    configs = (
        db.query(IntegrationConfig)
        .filter(
            IntegrationConfig.tenant_id == current_user.tenant_id,
            IntegrationConfig.integration_name == primary_integration,
            IntegrationConfig.enabled.is_(True),
        )
        .all()
    )

    for sc in configs:
        # Lazy backfill: discover email for legacy accounts missing it
        if not sc.account_email:
            _lazy_backfill_email(db, provider, sc, current_user.tenant_id)

        has_token = (
            db.query(IntegrationCredential)
            .filter(
                IntegrationCredential.integration_config_id == sc.id,
                IntegrationCredential.tenant_id == current_user.tenant_id,
                IntegrationCredential.credential_key == "oauth_token",
                IntegrationCredential.status == "active",
            )
            .first()
        ) is not None

        if has_token:
            accounts.append({
                "email": sc.account_email,
                "integration_config_id": str(sc.id),
                "connected_at": sc.created_at.isoformat() if sc.created_at else None,
            })

    return {
        "connected": len(accounts) > 0,
        "provider": provider,
        "accounts": accounts,
    }


# ---------------------------------------------------------------------------
# GET /oauth/internal/token/{integration_name}  (service-to-service only)
# ---------------------------------------------------------------------------

def _verify_internal_key(
    x_internal_key: Optional[str] = Header(None, alias="X-Internal-Key"),
):
    if x_internal_key not in (settings.API_INTERNAL_KEY, settings.MCP_API_KEY):
        raise HTTPException(status_code=401, detail="Invalid internal key")


@router.get("/internal/connected-accounts/{integration_name}")
def get_connected_accounts(
    integration_name: str,
    tenant_id: str = Query(...),
    db: Session = Depends(deps.get_db),
    _auth: None = Depends(_verify_internal_key),
):
    """List all connected accounts for an integration. Internal use only."""
    try:
        tid = uuid.UUID(tenant_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid tenant_id")

    configs = (
        db.query(IntegrationConfig)
        .filter(
            IntegrationConfig.tenant_id == tid,
            IntegrationConfig.integration_name == integration_name,
            IntegrationConfig.enabled.is_(True),
        )
        .all()
    )

    accounts = []
    for c in configs:
        accounts.append({
            "account_email": c.account_email,
            "integration_name": c.integration_name,
            "enabled": c.enabled,
        })

    return {"accounts": accounts, "count": len(accounts)}


@router.get("/internal/token/{integration_name}")
def get_integration_token(
    integration_name: str,
    tenant_id: str = Query(...),
    account_email: Optional[str] = Query(None, description="Specific account email"),
    db: Session = Depends(deps.get_db),
    _auth: None = Depends(_verify_internal_key),
):
    """Return decrypted OAuth credentials for an integration. Internal use only.

    If account_email is provided, returns credentials for that specific account.
    Otherwise returns credentials for the first active account.

    For Google and Microsoft OAuth: automatically refreshes the access token
    using the stored refresh_token when available.
    """
    try:
        tid = uuid.UUID(tenant_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid tenant_id")

    query = (
        db.query(IntegrationConfig)
        .filter(
            IntegrationConfig.tenant_id == tid,
            IntegrationConfig.integration_name == integration_name,
            IntegrationConfig.enabled.is_(True),
        )
    )
    if account_email:
        query = query.filter(IntegrationConfig.account_email == account_email)

    config = query.first()
    if not config:
        raise HTTPException(status_code=404, detail=f"No active config for '{integration_name}'")

    creds = retrieve_credentials_for_skill(db, config.id, tid)

    # For OAuth integrations, require oauth_token; for manual, require any credential
    provider = _integration_to_provider(integration_name)
    if provider and not creds.get("oauth_token"):
        raise HTTPException(status_code=404, detail="No active OAuth token found")
    elif not provider and not creds:
        raise HTTPException(status_code=404, detail=f"No active credentials for '{integration_name}'")

    # Auto-refresh provider tokens that support refresh_token rotation.
    refresh_token = creds.get("refresh_token")
    if provider in {"google", "microsoft"} and refresh_token:
        refreshed_tokens = _refresh_access_token(provider, refresh_token)
        if refreshed_tokens:
            # Update stored credential with fresh token(s)
            _update_stored_tokens(
                db,
                config.id,
                tid,
                refreshed_tokens["access_token"],
                refreshed_tokens.get("refresh_token"),
            )
            creds["oauth_token"] = refreshed_tokens["access_token"]
            if refreshed_tokens.get("refresh_token"):
                creds["refresh_token"] = refreshed_tokens["refresh_token"]
            logger.debug("Refreshed %s token for integration=%s tenant=%s", provider, integration_name, tid)
        else:
            logger.warning("Token refresh failed for integration=%s tenant=%s, returning stored token", integration_name, tid)

    return creds
