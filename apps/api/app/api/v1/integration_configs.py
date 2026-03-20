from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app import schemas
from app.api import deps
from app.services import integration_configs as integration_config_service
from app.services.orchestration.credential_vault import store_credential, revoke_credential
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
        "display_name": "GitHub",
        "description": "Manage repositories, issues, pull requests",
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
    "linear": {
        "display_name": "Linear",
        "description": "Manage Linear issues and projects",
        "icon": "FaProjectDiagram",
        "credentials": [
            {"key": "api_key", "label": "API Key", "type": "password", "required": True},
        ],
    },
    "claude_code": {
        "display_name": "Claude Code",
        "description": "Connect your Claude Code Pro/Max subscription for coding agent and AI chat",
        "icon": "FaTerminal",
        "credentials": [
            {"key": "session_token", "label": "OAuth Token", "type": "password", "required": True,
             "help": "Run 'claude setup-token' in your terminal and paste the token (valid 1 year)"},
        ],
    },
    "codex": {
        "display_name": "Codex",
        "description": "Connect your ChatGPT / Codex subscription for AI agent chat",
        "icon": "FaTerminal",
        "auth_type": "device_auth",
        "credentials": [
            {"key": "auth_json", "label": "ChatGPT Auth JSON", "type": "password", "required": True,
             "help": "Run 'codex login' or 'codex login --device-auth'. For headless use, complete login in a browser on another machine, then paste the contents of ~/.codex/auth.json here."},
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
    "anthropic_llm": {
        "display_name": "Anthropic (Claude)",
        "description": "Use Claude models for agent chat",
        "icon": "FaRobot",
        "credentials": [
            {"key": "api_key", "label": "API Key", "type": "password", "required": True,
             "help": "Get your key at console.anthropic.com"},
            {"key": "model", "label": "Model ID", "type": "text", "required": True,
             "help": "e.g. claude-sonnet-4-5, claude-haiku-4-5"}
        ],
        "auth_type": "manual"
    },
    "gemini_llm": {
        "display_name": "Google Gemini",
        "description": "Use Gemini models for agent chat (default)",
        "icon": "FaGoogle",
        "credentials": [
            {"key": "api_key", "label": "API Key", "type": "password", "required": True,
             "help": "Get your key at aistudio.google.com"},
            {"key": "model", "label": "Model ID", "type": "text", "required": True,
             "help": "e.g. gemini-2.5-pro, gemini-2.5-flash"}
        ],
        "auth_type": "manual"
    },
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

@router.get("/", response_model=List[schemas.integration_config.IntegrationConfig])
def read_integration_configs(
    db: Session = Depends(deps.get_db),
    skip: int = 0,
    limit: int = 100,
    current_user: User = Depends(deps.get_current_active_user),
):
    """
    Retrieve integration configurations for the current tenant.
    """
    configs = integration_config_service.get_integration_configs_by_tenant(
        db, tenant_id=current_user.tenant_id, skip=skip, limit=limit
    )
    return configs


@router.post("/", response_model=schemas.integration_config.IntegrationConfig, status_code=status.HTTP_201_CREATED)
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
