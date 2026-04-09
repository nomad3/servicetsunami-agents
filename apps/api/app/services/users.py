from typing import List

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import get_password_hash
from app.models.integration_credential import IntegrationCredential
from app.models.user import User
from app.schemas.user import UserCreate, UserUpdate
from app.services import tenants as tenant_service
from app.services.orchestration.credential_vault import store_credential, retrieve_credentials_for_skill
from app.schemas.tenant import TenantCreate
from app.models.agent_kit import AgentKit
from app.models.chat import ChatSession
from app.models.integration_config import IntegrationConfig
from app.models.tenant_features import TenantFeatures
import os
import uuid

def get_user(db: Session, user_id: uuid.UUID) -> User | None:
    return db.query(User).filter(User.id == user_id).first()

def get_user_by_email(db: Session, *, email: str) -> User | None:
    return db.query(User).filter(User.email == email).first()

def get_users(db: Session, skip: int = 0, limit: int = 100) -> List[User]:
    return db.query(User).offset(skip).limit(limit).all()

def create_user(db: Session, *, user_in: UserCreate, tenant_id: uuid.UUID) -> User:
    db_user = User(
        email=user_in.email,
        hashed_password=get_password_hash(user_in.password),
        full_name=user_in.full_name,
        tenant_id=tenant_id,
    )
    db.add(db_user)
    db.commit()
    db.refresh(db_user)
    return db_user


def _get_or_create_integration_config(
    db: Session,
    tenant_id: uuid.UUID,
    integration_name: str,
) -> IntegrationConfig:
    config = (
        db.query(IntegrationConfig)
        .filter(
            IntegrationConfig.tenant_id == tenant_id,
            IntegrationConfig.integration_name == integration_name,
        )
        .first()
    )
    if config:
        if not config.enabled:
            config.enabled = True
            db.add(config)
            db.flush()
        return config

    config = IntegrationConfig(
        tenant_id=tenant_id,
        integration_name=integration_name,
        enabled=True,
    )
    db.add(config)
    db.flush()
    return config


def seed_shared_cli_credentials_for_tenant(
    db: Session,
    tenant_id: uuid.UUID,
    source_tenant_id: uuid.UUID | None = None,
) -> list[str]:
    """Seed shared Claude Code / Codex credentials into a tenant when missing."""
    copied: list[str] = []
    source_tenant_value = source_tenant_id or (
        uuid.UUID(settings.PLATFORM_SHARED_CREDENTIALS_TENANT_ID)
        if settings.PLATFORM_SHARED_CREDENTIALS_TENANT_ID
        else None
    )

    seed_specs = [
        ("gemini_cli", {"oauth_token": (settings.PLATFORM_GEMINI_CLI_TOKEN or "").strip()}),
    ]

    for integration_name, env_fallback in seed_specs:
        active_count = (
            db.query(IntegrationCredential)
            .join(
                IntegrationConfig,
                IntegrationCredential.integration_config_id == IntegrationConfig.id,
            )
            .filter(
                IntegrationCredential.tenant_id == tenant_id,
                IntegrationCredential.status == "active",
                IntegrationConfig.integration_name == integration_name,
            )
            .count()
        )
        if active_count:
            continue

        credentials_to_copy: dict[str, str] = {}

        if source_tenant_value and source_tenant_value != tenant_id:
            source_config = (
                db.query(IntegrationConfig)
                .filter(
                    IntegrationConfig.tenant_id == source_tenant_value,
                    IntegrationConfig.integration_name == integration_name,
                    IntegrationConfig.enabled.is_(True),
                )
                .first()
            )
            if source_config:
                credentials_to_copy = retrieve_credentials_for_skill(
                    db,
                    integration_config_id=source_config.id,
                    tenant_id=source_tenant_value,
                )

        if not credentials_to_copy:
            credentials_to_copy = {k: v for k, v in env_fallback.items() if v}

        if not credentials_to_copy:
            continue

        target_config = _get_or_create_integration_config(db, tenant_id, integration_name)
        # Use oauth_token type for Claude Code and Gemini CLI
        credential_type = "oauth_token" if integration_name in ["claude_code", "gemini_cli"] else "api_key"
        for credential_key, plaintext_value in credentials_to_copy.items():
            store_credential(
                db,
                integration_config_id=target_config.id,
                tenant_id=tenant_id,
                credential_key=credential_key,
                plaintext_value=plaintext_value,
                credential_type=credential_type,
            )
        copied.append(integration_name)

    return copied

def create_user_with_tenant(db: Session, *, user_in: UserCreate, tenant_in: TenantCreate) -> User:
    tenant = tenant_service.create_tenant(db, tenant_in=tenant_in)

    # Create tenant features with CLI orchestrator enabled by default
    features = TenantFeatures(
        tenant_id=tenant.id,
        cli_orchestrator_enabled=True,
        default_cli_platform="gemini_cli",
        rl_enabled=True,
    )
    db.add(features)
    db.flush()
    default_kit = AgentKit(
        name="Luna Supervisor",
        description="Luna is your AI co-pilot. She coordinates specialized teams for data analysis, sales, marketing, development, and more.",
        version="1.0.0",
        kit_type="hierarchy",
        industry=None,
        config={
            "primary_objective": "Provide intelligent AI co-pilot assistance by routing requests to specialized teams and delivering actionable responses.",
            "skill_slug": "luna",
            "personality": "friendly",
            "temperature": 0.7,
            "max_tokens": 2000,
            "tools": ["entity_extraction", "knowledge_search", "lead_scoring", "calculator", "data_summary"],
            "system_prompt": "You are Luna, an intelligent AI co-pilot. Route requests to the best specialized team and provide helpful, actionable responses.",
        },
        default_hierarchy={
            "platform": "claude_code",
        },
        tenant_id=tenant.id,
    )
    db.add(default_kit)
    db_user = User(
        email=user_in.email,
        hashed_password=get_password_hash(user_in.password),
        full_name=user_in.full_name,
        tenant_id=tenant.id,
    )
    db.add(db_user)
    db.flush()  # Get IDs assigned before creating session

    # Auto-create a welcome chat session so new users can talk to Luna immediately
    welcome_session = ChatSession(
        title="Chat with Luna",
        tenant_id=tenant.id,
        agent_kit_id=default_kit.id,
    )
    db.add(welcome_session)

    # Auto-provision shared CLI credentials so new tenants inherit the
    # platform owner's Claude Code and Codex subscriptions by default.
    seed_shared_cli_credentials_for_tenant(db, tenant.id)

    db.commit()
    db.refresh(db_user)
    return db_user



def update_user(db: Session, *, db_user: User, user_in: UserUpdate) -> User:
    if user_in.full_name is not None:
        db_user.full_name = user_in.full_name
    if user_in.email is not None:
        db_user.email = user_in.email
    if user_in.password is not None:
        db_user.hashed_password = get_password_hash(user_in.password)
    if user_in.is_active is not None:
        db_user.is_active = user_in.is_active
    if user_in.is_superuser is not None:
        db_user.is_superuser = user_in.is_superuser
    db.add(db_user)
    db.commit()
    db.refresh(db_user)
    return db_user

def delete_user(db: Session, *, user_id: uuid.UUID) -> User | None:
    user = db.query(User).filter(User.id == user_id).first()
    if user:
        db.delete(user)
        db.commit()
    return user
