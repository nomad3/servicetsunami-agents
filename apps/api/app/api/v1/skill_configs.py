from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app import schemas
from app.api import deps
from app.services import skill_configs as skill_config_service
from app.services.orchestration.credential_vault import store_credential, revoke_credential
from app.models.user import User
from app.models.skill_credential import SkillCredential
import uuid

router = APIRouter()

# ---------------------------------------------------------------------------
# Skill Credential Registry — defines what credentials each skill requires
# ---------------------------------------------------------------------------

SKILL_CREDENTIAL_SCHEMAS = {
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
        "channel_type": "baileys",
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
}


# ---------------------------------------------------------------------------
# Registry endpoint
# ---------------------------------------------------------------------------

@router.get("/registry", response_model=List[schemas.skill_config.SkillRegistryEntry])
def get_skill_registry(
    current_user: User = Depends(deps.get_current_active_user),
):
    """
    Return the list of available skills and the credentials each one requires.
    The frontend uses this to render dynamic credential forms.
    """
    entries = []
    for skill_name, schema in SKILL_CREDENTIAL_SCHEMAS.items():
        entries.append(
            schemas.skill_config.SkillRegistryEntry(
                skill_name=skill_name,
                display_name=schema["display_name"],
                description=schema["description"],
                icon=schema["icon"],
                credentials=schema["credentials"],
                channel_type=schema.get("channel_type"),
                auth_type=schema.get("auth_type", "manual"),
                oauth_provider=schema.get("oauth_provider"),
            )
        )
    return entries


# ---------------------------------------------------------------------------
# Skill config CRUD
# ---------------------------------------------------------------------------

@router.get("/", response_model=List[schemas.skill_config.SkillConfig])
def read_skill_configs(
    db: Session = Depends(deps.get_db),
    skip: int = 0,
    limit: int = 100,
    current_user: User = Depends(deps.get_current_active_user),
):
    """
    Retrieve skill configurations for the current tenant.
    """
    configs = skill_config_service.get_skill_configs_by_tenant(
        db, tenant_id=current_user.tenant_id, skip=skip, limit=limit
    )
    return configs


@router.post("/", response_model=schemas.skill_config.SkillConfig, status_code=status.HTTP_201_CREATED)
def create_skill_config(
    *,
    db: Session = Depends(deps.get_db),
    item_in: schemas.skill_config.SkillConfigCreate,
    current_user: User = Depends(deps.get_current_active_user),
):
    """
    Enable a skill for the current tenant by creating a skill config.
    """
    item = skill_config_service.create_tenant_skill_config(
        db=db, item_in=item_in, tenant_id=current_user.tenant_id
    )
    return item


@router.put("/{skill_config_id}", response_model=schemas.skill_config.SkillConfig)
def update_skill_config(
    *,
    db: Session = Depends(deps.get_db),
    skill_config_id: uuid.UUID,
    item_in: schemas.skill_config.SkillConfigUpdate,
    current_user: User = Depends(deps.get_current_active_user),
):
    """
    Update an existing skill config (approval settings, rate limit, LLM, etc.).
    """
    config = skill_config_service.get_skill_config(db, skill_config_id=skill_config_id)
    if not config or str(config.tenant_id) != str(current_user.tenant_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Skill config not found")
    item = skill_config_service.update_skill_config(db=db, db_obj=config, obj_in=item_in)
    return item


@router.delete("/{skill_config_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_skill_config(
    *,
    db: Session = Depends(deps.get_db),
    skill_config_id: uuid.UUID,
    current_user: User = Depends(deps.get_current_active_user),
):
    """
    Disable/delete a skill config for the current tenant.
    """
    config = skill_config_service.get_skill_config(db, skill_config_id=skill_config_id)
    if not config or str(config.tenant_id) != str(current_user.tenant_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Skill config not found")
    skill_config_service.delete_skill_config(db=db, skill_config_id=skill_config_id)
    return {"message": "Skill config deleted successfully"}


# ---------------------------------------------------------------------------
# Credential endpoints
# ---------------------------------------------------------------------------

@router.post(
    "/{skill_config_id}/credentials",
    response_model=schemas.skill_config.CredentialOut,
    status_code=status.HTTP_201_CREATED,
)
def add_credential(
    *,
    db: Session = Depends(deps.get_db),
    skill_config_id: uuid.UUID,
    cred_in: schemas.skill_config.CredentialCreate,
    current_user: User = Depends(deps.get_current_active_user),
):
    """
    Add or update a credential for a skill config.
    The plaintext value is encrypted at rest by the CredentialVault.
    """
    config = skill_config_service.get_skill_config(db, skill_config_id=skill_config_id)
    if not config or str(config.tenant_id) != str(current_user.tenant_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Skill config not found")

    credential = store_credential(
        db,
        skill_config_id=skill_config_id,
        tenant_id=current_user.tenant_id,
        credential_key=cred_in.credential_key,
        plaintext_value=cred_in.value,
        credential_type=cred_in.credential_type,
    )
    return credential


@router.delete(
    "/{skill_config_id}/credentials/{credential_key}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_credential(
    *,
    db: Session = Depends(deps.get_db),
    skill_config_id: uuid.UUID,
    credential_key: str,
    current_user: User = Depends(deps.get_current_active_user),
):
    """
    Revoke a credential by skill_config_id and credential key.
    """
    config = skill_config_service.get_skill_config(db, skill_config_id=skill_config_id)
    if not config or str(config.tenant_id) != str(current_user.tenant_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Skill config not found")

    # Find the active credential by key
    credential = (
        db.query(SkillCredential)
        .filter(
            SkillCredential.skill_config_id == skill_config_id,
            SkillCredential.credential_key == credential_key,
            SkillCredential.tenant_id == current_user.tenant_id,
            SkillCredential.status == "active",
        )
        .first()
    )
    if not credential:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Credential not found")

    revoked = revoke_credential(db, credential_id=credential.id, tenant_id=current_user.tenant_id)
    if not revoked:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to revoke credential")

    return {"message": "Credential revoked successfully"}
