from typing import List

from sqlalchemy.orm import Session

from app.core.security import get_password_hash
from app.models.user import User
from app.schemas.user import UserCreate, UserUpdate
from app.services import tenants as tenant_service
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

def create_user_with_tenant(db: Session, *, user_in: UserCreate, tenant_in: TenantCreate) -> User:
    tenant = tenant_service.create_tenant(db, tenant_in=tenant_in)

    # Create tenant features with CLI orchestrator enabled by default
    features = TenantFeatures(
        tenant_id=tenant.id,
        cli_orchestrator_enabled=True,
        default_cli_platform="claude_code",
    )
    db.add(features)
    default_kit = AgentKit(
        name="Luna Supervisor",
        description="Luna is your AI co-pilot. She coordinates specialized teams for data analysis, sales, marketing, development, and more.",
        version="1.0.0",
        kit_type="hierarchy",
        industry=None,
        config={
            "primary_objective": "Provide intelligent AI co-pilot assistance by routing requests to specialized teams and delivering actionable responses.",
            "model": "claude-3-5-sonnet-20240620",
            "personality": "friendly",
            "temperature": 0.7,
            "max_tokens": 2000,
            "tools": ["entity_extraction", "knowledge_search", "lead_scoring", "calculator", "data_summary"],
            "system_prompt": "You are Luna, an intelligent AI co-pilot. Route requests to the best specialized team and provide helpful, actionable responses.",
        },
        default_hierarchy={
            "supervisor": "servicetsunami_supervisor",
            "workers": ["personal_assistant", "dev_team", "data_team", "sales_team", "marketing_team", "vet_supervisor", "deal_team"],
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

    # Auto-provision Claude Code integration with platform-wide token
    # so new tenants get CLI orchestrator out of the box
    platform_claude_token = os.environ.get("PLATFORM_CLAUDE_CODE_TOKEN", "")
    if platform_claude_token:
        claude_config = IntegrationConfig(
            tenant_id=tenant.id,
            integration_name="claude_code",
            enabled=True,
        )
        db.add(claude_config)
        db.flush()

        from app.services.orchestration.credential_vault import store_credential
        store_credential(
            db,
            integration_config_id=claude_config.id,
            tenant_id=tenant.id,
            credential_key="session_token",
            plaintext_value=platform_claude_token,
            credential_type="oauth_token",
        )

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