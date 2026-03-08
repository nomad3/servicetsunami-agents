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
    "google_calendar": {
        "display_name": "Google Calendar",
        "description": "Manage calendar events and schedules",
        "icon": "FaCalendar",
        "credentials": [],
        "auth_type": "oauth",
        "oauth_provider": "google",
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
        "description": "Autonomous coding agent — implements features, fixes bugs, creates PRs",
        "icon": "FaTerminal",
        "credentials": [
            {"key": "session_token", "label": "Session Token", "type": "password", "required": True,
             "help": "Run 'claude setup-token' in your terminal, then paste the token here"},
        ],
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
    for skill_name, schema in INTEGRATION_CREDENTIAL_SCHEMAS.items():
        entries.append(
            schemas.integration_config.IntegrationRegistryEntry(
                skill_name=skill_name,
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
