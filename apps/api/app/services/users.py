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
from app.models.agent import Agent
from app.models.chat import ChatSession
from app.models.integration_config import IntegrationConfig
from app.models.knowledge_entity import KnowledgeEntity
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
    # Persona prompt — leave high-level guidance only. The actual tool
    # surface (find_entities, search_knowledge, calendar, gmail, etc.)
    # comes from the MCP server registration; the CLI runtime exposes
    # them automatically and the universal anti-hallucination preamble
    # in cli_session_manager already tells the model when to call them.
    luna_persona_prompt = (
        "You are Luna, an intelligent AI co-pilot. Route requests to the right specialized agent or tool, "
        "maintain context across conversations, and deliver helpful, actionable responses. "
        "Be concise and conversational."
    )
    # New tenants start with a single skill: the bundled `luna` agent persona.
    # Tenants compose additional skills (receptionist, booking, vet-cardio,
    # etc.) per agent via the AgentDetailPage Config tab. The deprecated
    # tool-class wrappers (calculator, data_summary, entity_extraction,
    # knowledge_search, sql_query, report_generation) were removed 2026-04-26
    # — CLIs already cover those operations natively.
    luna_skills = ["luna"]

    luna_agent = Agent(
        name="Luna",
        description="Your AI co-pilot. Routes requests to specialized agents and maintains conversation context.",
        tenant_id=tenant.id,
        status="production",
        persona_prompt=luna_persona_prompt,
        capabilities=luna_skills,
        tool_groups=["knowledge", "email"],
        default_model_tier="light",
        memory_domains=["conversation", "user"],
        role="supervisor",
        autonomy_level="supervised",
        config={
            "temperature": 0.7,
            "max_tokens": 2000,
            "system_prompt": luna_persona_prompt,
            "skills": luna_skills,
            "personality_preset": "friendly",
            "template_used": "luna_default",
            "avatar": "🌙",
        },
    )
    db.add(luna_agent)
    db.flush()
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
        agent_id=luna_agent.id,
    )
    db.add(welcome_session)

    # Auto-provision shared CLI credentials so new tenants inherit the
    # platform owner's Claude Code and Codex subscriptions by default.
    seed_shared_cli_credentials_for_tenant(db, tenant.id)

    # Seed a starter knowledge entity so Memory > Entities is never blank.
    # Embedding is backfilled asynchronously below — sentence-transformers
    # cold-start is 10-30s and must not block the registration HTTP response.
    company_name = tenant_in.name if tenant_in.name else "My Organization"
    seed_description = f"{company_name} — primary organization for this workspace."

    seed_entity = KnowledgeEntity(
        name=company_name,
        entity_type="organization",
        category="company",
        description=seed_description,
        tenant_id=tenant.id,
        confidence=1.0,
        # embedding left NULL — backfilled by the background thread below.
    )
    db.add(seed_entity)
    db.flush()  # populate seed_entity.id so the background thread can find it
    seed_entity_id = seed_entity.id
    seed_embed_text = f"{company_name}. {seed_description}"

    db.commit()
    db.refresh(db_user)

    # Kick off embedding backfill AFTER commit so the row is visible to the
    # thread's own DB session. The thread owns its lifecycle and must not
    # propagate exceptions into the request path.
    import threading

    def _backfill_seed_embedding(entity_id: uuid.UUID, text: str, tenant_id_str: str):
        from app.db.session import SessionLocal
        from app.services.embedding_service import embed_text
        import logging
        log = logging.getLogger(__name__)
        bg_db = SessionLocal()
        try:
            vec = embed_text(text)
            if vec is None:
                log.warning(
                    "Seed entity embedding returned None for tenant %s", tenant_id_str
                )
                return
            ent = (
                bg_db.query(KnowledgeEntity)
                .filter(KnowledgeEntity.id == entity_id)
                .first()
            )
            if ent is None:
                return
            ent.embedding = vec
            bg_db.commit()
        except Exception as exc:  # background thread — never let this crash the pod
            bg_db.rollback()
            log.warning(
                "Seed entity embedding backfill failed for tenant %s: %s",
                tenant_id_str, exc,
            )
        finally:
            bg_db.close()

    threading.Thread(
        target=_backfill_seed_embedding,
        args=(seed_entity_id, seed_embed_text, str(tenant.id)),
        daemon=True,
    ).start()

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
