import json
import time
import uuid
from sqlalchemy.orm import Session
from sqlalchemy.exc import OperationalError
from sqlalchemy import text

from app.db import base  # noqa: F401
from app.db.session import engine

# make sure all SQL Alchemy models are imported (app.db.base) before initializing DB
# otherwise, SQL Alchemy might fail to initialize relationships properly
# for more details: https://github.com/tiangolo/full-stack-fastapi-postgresql/issues/28
from app.models.user import User
from app.models.tenant import Tenant
from app.models.data_source import DataSource
from app.models.data_pipeline import DataPipeline
from app.models.notebook import Notebook
from app.models.agent import Agent
from app.models.agent_group import AgentGroup  # noqa: F401
from app.models.agent_relationship import AgentRelationship  # noqa: F401
from app.models.agent_task import AgentTask  # noqa: F401
from app.models.agent_skill import AgentSkill  # noqa: F401
from app.models.agent_memory import AgentMemory  # noqa: F401
from app.models.knowledge_entity import KnowledgeEntity  # noqa: F401
from app.models.knowledge_relation import KnowledgeRelation  # noqa: F401
from app.models.llm_provider import LLMProvider  # noqa: F401
from app.models.llm_model import LLMModel  # noqa: F401
from app.models.llm_config import LLMConfig  # noqa: F401
from app.models.tenant_branding import TenantBranding  # noqa: F401
from app.models.tenant_features import TenantFeatures  # noqa: F401
from app.models.tenant_analytics import TenantAnalytics  # noqa: F401
from app.models.tool import Tool
from app.models.deployment import Deployment  # noqa: F401
from app.models.vector_store import VectorStore  # noqa: F401
from app.models.agent_kit import AgentKit  # noqa: F401
from app.models.agent_integration_config import AgentIntegrationConfig  # noqa: F401
from app.models.chat import ChatSession, ChatMessage

from app.core.security import get_password_hash
from app.services import datasets as dataset_service

def init_db(db: Session) -> None:
    # Tables should be created with Alembic migrations
    # But for this initial setup, we'll create them directly

    # Add retry logic for database connection
    max_retries = 10
    retry_delay = 5  # seconds

    for i in range(max_retries):
        try:
            print(f"Attempting to connect to database (attempt {i+1}/{max_retries})...")
            base.Base.metadata.create_all(bind=engine)
            print("Database connection successful and tables created.")
            break
        except OperationalError as e:
            print(f"Database connection failed: {e}")
            if i < max_retries - 1:
                print(f"Retrying in {retry_delay} seconds...")
                time.sleep(retry_delay)
            else:
                print("Max retries reached. Could not connect to database.")
                raise

    seed_llm_providers(db)
    seed_llm_models(db)
    seed_demo_data(db)
    seed_system_skills(db)


def seed_demo_data(db: Session) -> None:
    demo_email = "test@example.com"
    existing_user = db.query(User).filter(User.email == demo_email).first()
    if existing_user:
        return

    demo_tenant = Tenant(name="Demo Enterprise")
    db.add(demo_tenant)
    db.flush()

    demo_user = User(
        email=demo_email,
        full_name="Demo Operator",
        hashed_password=get_password_hash("password"),
        tenant_id=demo_tenant.id,
        is_active=True,
    )
    db.add(demo_user)

    data_sources = [
        DataSource(
            name="PostgreSQL Data Warehouse",
            type="warehouse",
            config={},
            tenant_id=demo_tenant.id,
        ),
        DataSource(
            name="Product Telemetry Stream",
            type="stream",
            config={},
            tenant_id=demo_tenant.id,
        ),
    ]
    db.add_all(data_sources)
    db.flush()

    data_pipelines = [
        DataPipeline(
            name="ARR Forecasting",
            config={},
            tenant_id=demo_tenant.id,
        ),
        DataPipeline(
            name="Usage Churn Risk",
            config={},
            tenant_id=demo_tenant.id,
        ),
    ]
    db.add_all(data_pipelines)
    db.flush()

    notebooks = [
        Notebook(
            name="Executive ARR Summary",
            tenant_id=demo_tenant.id,
        ),
        Notebook(
            name="Churn Risk Deep Dive",
            tenant_id=demo_tenant.id,
        ),
    ]
    db.add_all(notebooks)

    agents = [
        Agent(
            name="Revenue Copilot",
            tenant_id=demo_tenant.id,
        ),
        Agent(
            name="Telemetry Sentinel",
            tenant_id=demo_tenant.id,
        ),
    ]
    db.add_all(agents)

    tools = [
        Tool(
            name="Scenario Planner",
            tenant_id=demo_tenant.id,
        ),
        Tool(
            name="Retention Playbook",
            tenant_id=demo_tenant.id,
        ),
    ]
    db.add_all(tools)

    vector_stores = [
        VectorStore(
            name="Customer Feedback Embeddings",
            description="Vector store for customer feedback analysis",
            config={"provider": "pinecone", "index": "customer-feedback"},
            tenant_id=demo_tenant.id,
        ),
        VectorStore(
            name="Product Documentation Embeddings",
            description="Vector store for product documentation RAG",
            config={"provider": "weaviate", "index": "product-docs"},
            tenant_id=demo_tenant.id,
        ),
    ]
    db.add_all(vector_stores)

    agent_kits = [
        AgentKit(
            name="Customer Support Agent Kit",
            description="Kit for deploying customer support agents",
            version="1.0.0",
            config={
                "primary_objective": "Provide excellent customer support by answering questions and resolving issues",
                "base_model": "gpt-4",
                "tools": ["faq_retrieval", "order_status"]
            },
            tenant_id=demo_tenant.id,
        ),
        AgentKit(
            name="Data Analysis Agent Kit",
            description="Kit for deploying data analysis agents",
            version="1.1.0",
            config={
                "primary_objective": "Analyze data and provide actionable insights to drive business decisions",
                "base_model": "claude-3-5-sonnet-20240620",
                "tools": ["sql_query", "chart_generation"]
            },
            tenant_id=demo_tenant.id,
        ),
    ]
    db.add_all(agent_kits)

    deployments = [
        Deployment(
            name="Revenue Copilot - Prod",
            agent_id=agents[0].id,
            tenant_id=demo_tenant.id,
        ),
        Deployment(
            name="Telemetry Sentinel - Staging",
            agent_id=agents[1].id,
            tenant_id=demo_tenant.id,
        ),
    ]
    db.add_all(deployments)

    revenue_dataset_rows = [
        {
            "order_id": "1001",
            "customer_name": "Acme Corp",
            "segment": "Enterprise",
            "region": "North America",
            "revenue": 125000,
            "cost": 83000,
            "profit": 42000,
            "order_date": "2024-01-15",
        },
        {
            "order_id": "1002",
            "customer_name": "Globex Inc",
            "segment": "Mid-Market",
            "region": "Europe",
            "revenue": 78000,
            "cost": 52000,
            "profit": 26000,
            "order_date": "2024-02-10",
        },
        {
            "order_id": "1003",
            "customer_name": "Initech",
            "segment": "SMB",
            "region": "North America",
            "revenue": 45000,
            "cost": 29000,
            "profit": 16000,
            "order_date": "2024-02-28",
        },
        {
            "order_id": "1004",
            "customer_name": "Stark Industries",
            "segment": "Enterprise",
            "region": "Asia-Pacific",
            "revenue": 152000,
            "cost": 101000,
            "profit": 51000,
            "order_date": "2024-03-07",
        },
        {
            "order_id": "1005",
            "customer_name": "Wayne Enterprises",
            "segment": "Enterprise",
            "region": "Latin America",
            "revenue": 98500,
            "cost": 64000,
            "profit": 34500,
            "order_date": "2024-03-21",
        },
    ]

    # Use the proper ingestion service to create dataset with parquet file
    seeded_dataset = dataset_service.ingest_records(
        db,
        tenant_id=demo_tenant.id,
        records=revenue_dataset_rows,
        name="Revenue Performance",
        description="Sample revenue transactions for demo analysis",
        source_type="seed",
    )

    demo_chat_session = ChatSession(
        title="Q1 Revenue Review",
        dataset_id=seeded_dataset.id,
        agent_kit_id=agent_kits[1].id,
        tenant_id=demo_tenant.id,
    )
    db.add(demo_chat_session)
    db.flush()

    chat_messages = [
        ChatMessage(
            session_id=demo_chat_session.id,
            role="user",
            content="What were our top customer segments last quarter?",
        ),
        ChatMessage(
            session_id=demo_chat_session.id,
            role="assistant",
            content="Enterprise accounts generated the highest share of revenue, led by Acme Corp and Wayne Enterprises.",
            context={
                "summary": {
                    "numeric_columns": [
                        {"column": "revenue", "avg": 99800.0, "min": 45000, "max": 152000},
                        {"column": "profit", "avg": 33900.0, "min": 16000, "max": 51000},
                    ]
                }
            },
        ),
    ]
    db.add_all(chat_messages)

    db.commit()


def seed_llm_providers(db: Session) -> None:
    """Seed LLM providers."""
    providers = [
        {
            "name": "openai",
            "display_name": "OpenAI",
            "base_url": "https://api.openai.com/v1",
            "auth_type": "api_key",
            "supported_features": {"streaming": True, "function_calling": True, "vision": True},
            "is_active": True,
        },
        {
            "name": "anthropic",
            "display_name": "Anthropic",
            "base_url": "https://api.anthropic.com/v1",
            "auth_type": "api_key",
            "supported_features": {"streaming": True, "function_calling": True, "vision": True},
            "is_active": True,
        },
        {
            "name": "deepseek",
            "display_name": "DeepSeek",
            "base_url": "https://api.deepseek.com/v1",
            "auth_type": "api_key",
            "supported_features": {"streaming": True, "function_calling": True, "vision": False},
            "is_active": True,
        },
        {
            "name": "google",
            "display_name": "Google AI",
            "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
            "auth_type": "api_key",
            "supported_features": {"streaming": True, "function_calling": True, "vision": True},
            "is_active": True,
        },
        {
            "name": "mistral",
            "display_name": "Mistral AI",
            "base_url": "https://api.mistral.ai/v1",
            "auth_type": "api_key",
            "supported_features": {"streaming": True, "function_calling": True, "vision": False},
            "is_active": True,
        },
    ]

    for provider_data in providers:
        existing = db.query(LLMProvider).filter(LLMProvider.name == provider_data["name"]).first()
        if not existing:
            db.add(LLMProvider(**provider_data))

    db.commit()


def seed_llm_models(db: Session) -> None:
    """Seed LLM models for each provider."""
    from decimal import Decimal

    models = [
        # OpenAI
        {
            "provider": "openai",
            "model_id": "gpt-4o",
            "display_name": "GPT-4o",
            "input_cost": "2.50",
            "output_cost": "10.00",
            "context_window": 128000,
            "speed_tier": "fast",
            "quality_tier": "best",
            "size_category": "large",
        },
        {
            "provider": "openai",
            "model_id": "gpt-4o-mini",
            "display_name": "GPT-4o Mini",
            "input_cost": "0.15",
            "output_cost": "0.60",
            "context_window": 128000,
            "speed_tier": "fast",
            "quality_tier": "good",
            "size_category": "small",
        },
        # Anthropic
        {
            "provider": "anthropic",
            "model_id": "claude-3-5-sonnet-20240620",
            "display_name": "Claude 3.5 Sonnet (v1)",
            "input_cost": "3.00",
            "output_cost": "15.00",
            "context_window": 200000,
            "speed_tier": "standard",
            "quality_tier": "best",
            "size_category": "large",
        },
        {
            "provider": "anthropic",
            "model_id": "claude-3-5-haiku-20241022",
            "display_name": "Claude 3.5 Haiku",
            "input_cost": "0.80",
            "output_cost": "4.00",
            "context_window": 200000,
            "speed_tier": "fast",
            "quality_tier": "good",
            "size_category": "small",
        },
        # DeepSeek
        {
            "provider": "deepseek",
            "model_id": "deepseek-chat",
            "display_name": "DeepSeek Chat",
            "input_cost": "0.14",
            "output_cost": "0.28",
            "context_window": 64000,
            "speed_tier": "fast",
            "quality_tier": "good",
            "size_category": "medium",
        },
        {
            "provider": "deepseek",
            "model_id": "deepseek-coder",
            "display_name": "DeepSeek Coder",
            "input_cost": "0.14",
            "output_cost": "0.28",
            "context_window": 64000,
            "speed_tier": "fast",
            "quality_tier": "good",
            "size_category": "medium",
        },
        # Google
        {
            "provider": "google",
            "model_id": "gemini-1.5-pro",
            "display_name": "Gemini 1.5 Pro",
            "input_cost": "1.25",
            "output_cost": "5.00",
            "context_window": 1000000,
            "speed_tier": "standard",
            "quality_tier": "best",
            "size_category": "xl",
        },
        {
            "provider": "google",
            "model_id": "gemini-1.5-flash",
            "display_name": "Gemini 1.5 Flash",
            "input_cost": "0.075",
            "output_cost": "0.30",
            "context_window": 1000000,
            "speed_tier": "fast",
            "quality_tier": "good",
            "size_category": "small",
        },
        # Mistral
        {
            "provider": "mistral",
            "model_id": "mistral-large-latest",
            "display_name": "Mistral Large",
            "input_cost": "2.00",
            "output_cost": "6.00",
            "context_window": 128000,
            "speed_tier": "standard",
            "quality_tier": "best",
            "size_category": "large",
        },
        {
            "provider": "mistral",
            "model_id": "codestral-latest",
            "display_name": "Codestral",
            "input_cost": "0.30",
            "output_cost": "0.90",
            "context_window": 32000,
            "speed_tier": "fast",
            "quality_tier": "good",
            "size_category": "medium",
        },
    ]

    for model_data in models:
        provider = db.query(LLMProvider).filter(LLMProvider.name == model_data["provider"]).first()
        if provider:
            existing = db.query(LLMModel).filter(LLMModel.model_id == model_data["model_id"]).first()
            if not existing:
                db.add(
                    LLMModel(
                        provider_id=provider.id,
                        model_id=model_data["model_id"],
                        display_name=model_data["display_name"],
                        input_cost_per_1k=Decimal(model_data["input_cost"]),
                        output_cost_per_1k=Decimal(model_data["output_cost"]),
                        context_window=model_data["context_window"],
                        speed_tier=model_data["speed_tier"],
                        quality_tier=model_data["quality_tier"],
                        size_category=model_data["size_category"],
                        is_active=True,
                    )
                )

    db.commit()


def seed_system_skills(db: Session) -> None:
    """Seed system scoring rubrics as skills for the demo tenant."""
    from app.services.scoring_rubrics import RUBRICS

    # Find demo tenant
    demo_tenant = db.query(Tenant).filter(Tenant.name == "Demo Enterprise").first()
    if not demo_tenant:
        return

    # Check if skills table exists (migration 040 may not have run yet)
    try:
        result = db.execute(text("SELECT 1 FROM skills LIMIT 1"))
        result.close()
    except Exception:
        db.rollback()
        print("Skills table not found, skipping system skills seeding.")
        return

    for rubric_id, rubric in RUBRICS.items():
        # Check if this rubric already exists as a skill for the demo tenant
        existing = db.execute(
            text("SELECT id FROM skills WHERE tenant_id = :tid AND name = :name AND is_system = true LIMIT 1"),
            {"tid": str(demo_tenant.id), "name": rubric["name"]},
        ).first()

        if existing:
            continue

        skill_id = str(uuid.uuid4())
        db.execute(
            text(
                "INSERT INTO skills (id, tenant_id, name, description, skill_type, config, is_system, enabled, created_at, updated_at) "
                "VALUES (:id, :tid, :name, :desc, :stype, :config, true, true, now(), now())"
            ),
            {
                "id": skill_id,
                "tid": str(demo_tenant.id),
                "name": rubric["name"],
                "desc": rubric["description"],
                "stype": "scoring",
                "config": json.dumps(rubric),
            },
        )

    db.commit()
    print(f"System skills seeded: {len(RUBRICS)} rubrics checked.")
