from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app import schemas
from app.api import deps
from app.services import integration_configs as integration_config_service
from app.services.orchestration.credential_vault import (
    store_credential,
    revoke_credential,
    retrieve_credentials_for_skill,
)
from app.services import integration_test_service
from app.models.user import User
from app.models.integration_credential import IntegrationCredential
import uuid

router = APIRouter()

# ---------------------------------------------------------------------------
# Integration Credential Registry — defines what credentials each integration requires
# ---------------------------------------------------------------------------

INTEGRATION_CREDENTIAL_SCHEMAS = {
    "gmail": {
        "display_name": "Gmail",
        "description": "Send and read emails, manage labels",
        "icon": "FaEnvelope",
        "credentials": [],
        "auth_type": "oauth",
        "oauth_provider": "google",
    },
    "google_drive": {
        "display_name": "Google Drive",
        "description": "Search, read, and manage files in Google Drive",
        "icon": "FaGoogleDrive",
        "credentials": [],
        "auth_type": "oauth",
        "oauth_provider": "google",
    },
    "google_calendar": {
        "display_name": "Google Calendar",
        "description": "Manage calendar events and schedules",
        "icon": "FaCalendar",
        "credentials": [],
        "auth_type": "oauth",
        "oauth_provider": "google",
    },
    "outlook": {
        "display_name": "Outlook",
        "description": "Send and read Microsoft 365 and Outlook emails",
        "icon": "FaMicrosoft",
        "credentials": [],
        "auth_type": "oauth",
        "oauth_provider": "microsoft",
    },
    "github": {
        # Single GitHub OAuth covers two use cases for the platform:
        #   1. Repo / issue / PR management (code-worker, code agents).
        #   2. GitHub Copilot CLI runtime (chat agents — see
        #      `cli_platform_resolver.py`'s copilot_cli → github mapping).
        # Customers think of the card as "Copilot CLI" once connected, so
        # that's the lead label; description explicitly mentions both.
        "display_name": "GitHub Copilot CLI",
        "description": "Run chat agents on the GitHub Copilot subscription, plus manage repos, issues, and pull requests",
        "icon": "FaGithub",
        "credentials": [],
        "auth_type": "oauth",
        "oauth_provider": "github",
    },
    "linkedin": {
        "display_name": "LinkedIn",
        "description": "Post updates, manage profile, and send messages",
        "icon": "FaLinkedin",
        "credentials": [],
        "auth_type": "oauth",
        "oauth_provider": "linkedin",
    },
    "slack": {
        "display_name": "Slack",
        "description": "Send messages, manage channels, automate workflows",
        "icon": "FaSlack",
        "credentials": [
            {"key": "bot_token", "label": "Bot Token", "type": "password", "required": True},
            {"key": "webhook_url", "label": "Webhook URL", "type": "text", "required": False},
        ],
    },
    "whatsapp": {
        "display_name": "WhatsApp",
        "description": "Send and receive WhatsApp messages via QR-linked phone",
        "icon": "FaWhatsapp",
        "credentials": [],
    },
    "twilio_sms": {
        # SMS via Twilio. Apple iMessage / "Messages for Business" is gated
        # behind Apple's enterprise approval (4-12 weeks via
        # https://register.apple.com/business/messages) — when that lands the
        # card here will gain an `imessage_business_id` field. Until then
        # this card is SMS only.
        "display_name": "SMS (Twilio)",
        "description": "Send and receive SMS via a Twilio phone number. Configure the Twilio Console webhook to POST to /api/v1/integrations/twilio/inbound.",
        "icon": "FaSms",
        "auth_type": "manual",
        "credentials": [
            {"key": "account_sid", "label": "Account SID", "type": "text", "required": True,
             "help": "Twilio Console > Account Info > Account SID (starts with AC)"},
            {"key": "auth_token", "label": "Auth Token", "type": "password", "required": True,
             "help": "Twilio Console > Account Info > Auth Token. Used to verify inbound webhook signatures and authorize outbound REST calls."},
            {"key": "phone_number", "label": "Clinic Phone Number", "type": "text", "required": True,
             "help": "E.164 format (e.g. +17145551234). The Twilio number that receives inbound SMS for this tenant."},
        ],
    },
    "notion": {
        "display_name": "Notion",
        "description": "Read and write Notion pages and databases",
        "icon": "FaBook",
        "credentials": [
            {"key": "integration_token", "label": "Integration Token", "type": "password", "required": True},
        ],
    },
    "jira": {
        "display_name": "Jira",
        "description": "Manage Jira issues and projects",
        "icon": "FaTasks",
        "credentials": [
            {"key": "api_token", "label": "API Token", "type": "password", "required": True},
            {"key": "email", "label": "Email", "type": "text", "required": True},
            {"key": "domain", "label": "Jira Domain", "type": "text", "required": True},
        ],
    },
    "claude_code": {
        "display_name": "Claude Code",
        "description": "Connect your Claude Pro/Max subscription for coding agent and AI chat",
        "icon": "FaTerminal",
        "auth_type": "browser_auth",
        "credentials": [
            {"key": "session_token", "label": "OAuth Token", "type": "password", "required": False,
             "help": "Click 'Connect' to sign in with your Anthropic account, or paste a token manually."},
        ],
    },
    "codex": {
        "display_name": "Codex CLI",
        "description": "Connect your ChatGPT / Codex subscription for AI agent chat",
        "icon": "FaTerminal",
        "auth_type": "device_auth",
        "credentials": [
            {"key": "auth_json", "label": "ChatGPT Auth JSON", "type": "password", "required": True,
             "help": "Run 'codex login' or 'codex login --device-auth'. For headless use, complete login in a browser on another machine, then paste the contents of ~/.codex/auth.json here."},
        ],
    },
    "gemini_cli": {
        "display_name": "Gemini CLI",
        "description": "Connect your Google account and Gemini Pro subscription for agent chat",
        "icon": "FaGoogle",
        "credentials": [],
        "auth_type": "device_auth",
        "device_auth_endpoint": "/gemini-cli-auth",
    },
    "higgsfield": {
        # Wave 1a of the CLI integration catalog (#270). Per-tenant
        # OAuth — every tenant brings their own Higgsfield account
        # (multi-tenant ToS not confirmed yet, so no shared-founder
        # path). Calls bill against tenant credits.
        "display_name": "Higgsfield",
        "description": "Creative-content MCP source: image (Soul, Cinema Studio, Flux, Seedream, Nano Banana), video (Seedance, Kling, Veo, Minimax Hailuo), plus Ad Engine + virality prediction. Powered by your Higgsfield account credits.",
        "icon": "FaPalette",
        "credentials": [],
        "auth_type": "device_auth",
        "device_auth_endpoint": "/higgsfield-auth",
    },
    "qwen_code": {
        # Wave 1b — Qwen Code (Tongyi Lab) joins the catalog via BYOK API
        # key paste. OAuth dance is intentionally not wired yet; the
        # platform-key lane-B fallback lands in a follow-up PR once
        # adoption signals justify the secrets-manager work.
        "display_name": "Qwen Code",
        "description": "Connect your Qwen API key for Tongyi Qwen-Coder agent chat. Calls are billed against your DashScope account.",
        "icon": "FaTerminal",
        "auth_type": "api_key",
        "credentials": [
            {"key": "api_key", "label": "Qwen API Key", "type": "password", "required": True,
             "help": "From DashScope console (dashscope.console.aliyun.com) > API-KEY. Used for both DashScope and OpenAI-compatible Qwen endpoints."},
        ],
    },
    "meta_ads": {
        "display_name": "Meta Ads",
        "description": "Manage Facebook & Instagram ad campaigns, view insights, monitor competitor ads",
        "icon": "FaFacebook",
        "credentials": [
            {"key": "access_token", "label": "Access Token", "type": "password", "required": True,
             "help": "Long-lived access token from Meta Business Suite > Settings > API"},
            {"key": "ad_account_id", "label": "Ad Account ID", "type": "text", "required": True,
             "help": "Format: act_123456789. Find in Meta Business Suite > Settings > Ad Accounts"},
        ],
    },
    "google_ads": {
        "display_name": "Google Ads",
        "description": "Manage Google search and display campaigns, view keyword performance",
        "icon": "FaGoogle",
        "credentials": [
            {"key": "developer_token", "label": "Developer Token", "type": "password", "required": True,
             "help": "From Google Ads API Center. Apply at ads.google.com/aw/apicenter"},
            {"key": "customer_id", "label": "Customer ID", "type": "text", "required": True,
             "help": "10-digit Google Ads customer ID (no dashes). Found at top-right of Google Ads UI"},
            {"key": "refresh_token", "label": "OAuth Refresh Token", "type": "password", "required": True,
             "help": "OAuth2 refresh token. Generate using Google OAuth Playground for Ads API scope"},
        ],
    },
    "tiktok_ads": {
        "display_name": "TikTok Ads",
        "description": "Manage TikTok ad campaigns and view performance insights",
        "icon": "FaTiktok",
        "credentials": [
            {"key": "access_token", "label": "Access Token", "type": "password", "required": True,
             "help": "From TikTok Business Center > Developer Portal > My Apps"},
            {"key": "advertiser_id", "label": "Advertiser ID", "type": "text", "required": True,
             "help": "Found in TikTok Ads Manager > Account Info"},
        ],
    },
    "brightlocal": {
        "display_name": "BrightLocal",
        "description": "Local SEO rank tracking. Powers the daily SEO Sentinel workflow.",
        "icon": "FaSearch",
        "auth_type": "api_key",
        "credentials": [
            {"key": "api_key", "label": "API Key", "type": "password", "required": True,
             "help": "From BrightLocal account: Profile > API > Manage API Keys"},
            {"key": "api_secret", "label": "API Secret", "type": "password", "required": False,
             "help": "Required for production keys. Trial keys may share a single value with API Key."},
            {"key": "account_id", "label": "Account ID", "type": "text", "required": False,
             "help": "Optional. Found in BrightLocal Account Settings; useful when one agent manages multiple BrightLocal accounts."},
        ],
    },
    "scribblevet": {
        # ScribbleVet is the AI scribe DVMs use in the exam room — see
        # docs/research/2026-05-09-scribblevet-api-research.md for the
        # API-access posture. ScribbleVet itself does not publish a
        # public API today; this card scaffolds against the OAuth2
        # client_credentials shape expected from Instinct Science's
        # Partner API (Instinct acquired ScribbleVet 2026-01-16). The
        # adapter activates the moment partner intake delivers
        # credentials. Until then this card is the "request access"
        # surface — operators can save the values they receive from
        # partner intake without any further engineering.
        "display_name": "ScribbleVet",
        "description": "Veterinary AI scribe partner integration. Ingests finalized SOAP notes into the knowledge graph so Pet Health Concierge can recall prior visits and Clinical Triage can pre-load history into intake summaries. Requires ScribbleVet/Instinct partner credentials.",
        "icon": "FaNotesMedical",
        "auth_type": "oauth_partner",
        "credentials": [
            {"key": "client_id", "label": "Client ID", "type": "text", "required": True,
             "help": "Issued by ScribbleVet / Instinct partner intake. OAuth2 client_credentials grant."},
            {"key": "client_secret", "label": "Client Secret", "type": "password", "required": True,
             "help": "Issued alongside the Client ID. Treat as a password; rotate via the partner portal."},
            {"key": "practice_id", "label": "Practice ID", "type": "text", "required": True,
             "help": "ScribbleVet practice identifier. The Animal Doctor SOC runs all 3 sites on a single ScribbleVet account with one practice_id."},
            {"key": "environment", "label": "Environment", "type": "text", "required": False,
             "help": "Either 'sandbox' or 'prod' (default 'prod'). Sandbox URL is issued at partner intake."},
        ],
    },
    "covetrus_pulse": {
        # Covetrus Connect Technology Integration Partner Program — issued
        # at partner intake (~6-8wk approval cycle as of 2026-05-09). Both
        # the OAuth client_credentials flow and the HMAC fallback are
        # scaffolded; the active flow is selected by PULSE_AUTH_FLOW env
        # var (default "oauth"). The credential shape below is the union
        # of both — the operator fills in client_id + client_secret +
        # practice_id; environment defaults to "prod"; location_ids is an
        # optional comma-separated allowlist that filters appointment +
        # invoice queries to a subset of practice locations.
        "display_name": "Covetrus Pulse",
        "description": "Veterinary PMS partner integration. Powers Pet Health Concierge (record-aware client replies) and Multi-Site Revenue Sync. Requires Covetrus Connect partner credentials.",
        "icon": "FaHeartbeat",
        "auth_type": "oauth_partner",
        "credentials": [
            {"key": "client_id", "label": "Client ID", "type": "text", "required": True,
             "help": "Issued by Covetrus Connect at partner enrollment. Format follows OAuth2 client_credentials grant."},
            {"key": "client_secret", "label": "Client Secret", "type": "password", "required": True,
             "help": "Issued by Covetrus Connect alongside the Client ID. Treat as a password; rotate via the Covetrus partner portal."},
            {"key": "practice_id", "label": "Practice ID", "type": "text", "required": True,
             "help": "Covetrus Pulse practice identifier. The Animal Doctor SOC runs all 3 sites on a single Pulse instance with one practice_id."},
            {"key": "environment", "label": "Environment", "type": "text", "required": False,
             "help": "Either 'sandbox' or 'prod' (default 'prod'). Sandbox URL is issued at partner intake."},
            {"key": "location_ids", "label": "Location ID Allowlist", "type": "text", "required": False,
             "help": "Optional comma-separated list of Pulse location IDs (e.g. 'anaheim,buena_park,mission_viejo'). Filters appointment + invoice queries; leave blank to allow all locations on the practice."},
        ],
    },
    # NOTE: Direct-API LLM cards (`anthropic_llm`, `gemini_llm`) were
    # removed. The platform routes chat agents through CLI OAuth
    # subscriptions only — Claude Code, Gemini CLI, GitHub Copilot CLI,
    # Codex CLI — not raw API keys. The AI Models tab in the UI handles
    # any remaining provider-key flows.
}


# ---------------------------------------------------------------------------
# Registry endpoint
# ---------------------------------------------------------------------------

@router.get("/registry", response_model=List[schemas.integration_config.IntegrationRegistryEntry])
def get_integration_registry(
    current_user: User = Depends(deps.get_current_active_user),
):
    """
    Return the list of available integrations and the credentials each one requires.
    The frontend uses this to render dynamic credential forms.
    """
    entries = []
    for integration_name, schema in INTEGRATION_CREDENTIAL_SCHEMAS.items():
        entries.append(
            schemas.integration_config.IntegrationRegistryEntry(
                integration_name=integration_name,
                display_name=schema["display_name"],
                description=schema["description"],
                icon=schema["icon"],
                credentials=schema["credentials"],
                auth_type=schema.get("auth_type", "manual"),
                oauth_provider=schema.get("oauth_provider"),
            )
        )
    return entries


# ---------------------------------------------------------------------------
# Integration config CRUD
# ---------------------------------------------------------------------------

@router.get("", response_model=List[schemas.integration_config.IntegrationConfig])
def read_integration_configs(
    db: Session = Depends(deps.get_db),
    skip: int = 0,
    limit: int = 100,
    current_user: User = Depends(deps.get_current_active_user),
):
    """
    Retrieve integration configs for the current tenant.
    """
    configs = integration_config_service.get_integration_configs_by_tenant(
        db, tenant_id=current_user.tenant_id, skip=skip, limit=limit
    )
    return configs


@router.post("", response_model=schemas.integration_config.IntegrationConfig, status_code=status.HTTP_201_CREATED)
def create_integration_config(
    *,
    db: Session = Depends(deps.get_db),
    item_in: schemas.integration_config.IntegrationConfigCreate,
    current_user: User = Depends(deps.get_current_active_user),
):
    """
    Enable an integration for the current tenant by creating an integration config.
    """
    item = integration_config_service.create_tenant_integration_config(
        db=db, item_in=item_in, tenant_id=current_user.tenant_id
    )
    return item


@router.put("/{integration_config_id}", response_model=schemas.integration_config.IntegrationConfig)
def update_integration_config(
    *,
    db: Session = Depends(deps.get_db),
    integration_config_id: uuid.UUID,
    item_in: schemas.integration_config.IntegrationConfigUpdate,
    current_user: User = Depends(deps.get_current_active_user),
):
    """
    Update an existing integration config (approval settings, rate limit, LLM, etc.).
    """
    config = integration_config_service.get_integration_config(db, integration_config_id=integration_config_id)
    if not config or str(config.tenant_id) != str(current_user.tenant_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Integration config not found")
    item = integration_config_service.update_integration_config(db=db, db_obj=config, obj_in=item_in)
    return item


@router.delete("/{integration_config_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_integration_config(
    *,
    db: Session = Depends(deps.get_db),
    integration_config_id: uuid.UUID,
    current_user: User = Depends(deps.get_current_active_user),
):
    """
    Disable/delete an integration config for the current tenant.
    """
    config = integration_config_service.get_integration_config(db, integration_config_id=integration_config_id)
    if not config or str(config.tenant_id) != str(current_user.tenant_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Integration config not found")
    integration_config_service.delete_integration_config(db=db, integration_config_id=integration_config_id)
    return {"message": "Integration config deleted successfully"}


# ---------------------------------------------------------------------------
# Credential endpoints
# ---------------------------------------------------------------------------

@router.post(
    "/{integration_config_id}/credentials",
    response_model=schemas.integration_config.CredentialOut,
    status_code=status.HTTP_201_CREATED,
)
def add_credential(
    *,
    db: Session = Depends(deps.get_db),
    integration_config_id: uuid.UUID,
    cred_in: schemas.integration_config.CredentialCreate,
    current_user: User = Depends(deps.get_current_active_user),
):
    """
    Add or update a credential for an integration config.
    The plaintext value is encrypted at rest by the CredentialVault.
    """
    config = integration_config_service.get_integration_config(db, integration_config_id=integration_config_id)
    if not config or str(config.tenant_id) != str(current_user.tenant_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Integration config not found")

    credential = store_credential(
        db,
        integration_config_id=integration_config_id,
        tenant_id=current_user.tenant_id,
        credential_key=cred_in.credential_key,
        plaintext_value=cred_in.value,
        credential_type=cred_in.credential_type,
    )
    return credential


@router.delete(
    "/{integration_config_id}/credentials/{credential_key}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_credential(
    *,
    db: Session = Depends(deps.get_db),
    integration_config_id: uuid.UUID,
    credential_key: str,
    current_user: User = Depends(deps.get_current_active_user),
):
    """
    Revoke a credential by integration_config_id and credential key.
    """
    config = integration_config_service.get_integration_config(db, integration_config_id=integration_config_id)
    if not config or str(config.tenant_id) != str(current_user.tenant_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Integration config not found")

    # Find the active credential by key
    credential = (
        db.query(IntegrationCredential)
        .filter(
            IntegrationCredential.integration_config_id == integration_config_id,
            IntegrationCredential.credential_key == credential_key,
            IntegrationCredential.tenant_id == current_user.tenant_id,
            IntegrationCredential.status == "active",
        )
        .first()
    )
    if not credential:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Credential not found")

    revoked = revoke_credential(db, credential_id=credential.id, tenant_id=current_user.tenant_id)
    if not revoked:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to revoke credential")

    return {"message": "Credential revoked successfully"}


@router.get(
    "/{integration_config_id}/credentials/status",
)
def get_credential_status(
    *,
    db: Session = Depends(deps.get_db),
    integration_config_id: uuid.UUID,
    current_user: User = Depends(deps.get_current_active_user),
):
    """
    Return which credential keys have active values stored (without revealing the values).
    Used by the frontend to show credential status indicators.
    """
    config = integration_config_service.get_integration_config(db, integration_config_id=integration_config_id)
    if not config or str(config.tenant_id) != str(current_user.tenant_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Integration config not found")

    credentials = (
        db.query(IntegrationCredential.credential_key)
        .filter(
            IntegrationCredential.integration_config_id == integration_config_id,
            IntegrationCredential.tenant_id == current_user.tenant_id,
            IntegrationCredential.status == "active",
        )
        .distinct()
        .all()
    )

    return {
        "stored_keys": [c[0] for c in credentials],
    }


# ---------------------------------------------------------------------------
# Live credential test
# ---------------------------------------------------------------------------

@router.post("/{integration_config_id}/test")
async def test_integration_credentials(
    *,
    db: Session = Depends(deps.get_db),
    integration_config_id: uuid.UUID,
    current_user: User = Depends(deps.get_current_active_user),
):
    """
    Hit the upstream provider with the stored credentials to confirm they
    work. Returns ``{"ok": true, ...}`` on success or ``{"ok": false, "error": ...}``.

    Lightweight endpoint per integration — chosen so a "Test" button doesn't
    burn paid quota. Currently supported: ``brightlocal``. Other integrations
    return ``{"ok": false, "error": "Test not supported for ..."}``.
    """
    config = integration_config_service.get_integration_config(db, integration_config_id=integration_config_id)
    if not config or str(config.tenant_id) != str(current_user.tenant_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Integration config not found")

    creds = retrieve_credentials_for_skill(db, config.id, current_user.tenant_id)
    if not creds:
        return {"ok": False, "error": "No credentials stored for this integration."}

    return await integration_test_service.test_integration(config.integration_name, creds)
