"""Tests for Memory System - Phase 2"""
import pytest
from uuid import uuid4
from datetime import datetime, timedelta
import os

# Set TESTING environment variable BEFORE importing app modules
os.environ["TESTING"] = "True"

from sqlalchemy.orm import Session
from app.models.agent_memory import AgentMemory
from app.models.agent import Agent
from app.models.tenant import Tenant
from app.models.user import User  # noqa: F401 - Required for foreign key
from app.models.agent_task import AgentTask  # noqa: F401 - Required for foreign key
from app.models.agent_group import AgentGroup  # noqa: F401 - Required for foreign key
from app.schemas.agent_memory import AgentMemoryCreate, AgentMemoryUpdate
from app.db.base import Base
from app.db.session import SessionLocal, engine


@pytest.fixture(name="db_session")
def db_session_fixture():
    """Create a fresh database session for each test"""
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    yield db
    db.close()
    Base.metadata.drop_all(bind=engine)


@pytest.fixture(name="test_tenant")
def test_tenant_fixture(db_session: Session):
    """Create a test tenant"""
    tenant = Tenant(name="Test Tenant")
    db_session.add(tenant)
    db_session.commit()
    db_session.refresh(tenant)
    return tenant


@pytest.fixture(name="test_agent")
def test_agent_fixture(db_session: Session, test_tenant: Tenant):
    """Create a test agent"""
    agent = Agent(
        name="Test Agent",
        description="Test agent for memory tests",
        config={"model": "claude-3-5-sonnet-20241022"},
        tenant_id=test_tenant.id,
    )
    db_session.add(agent)
    db_session.commit()
    db_session.refresh(agent)
    return agent


@pytest.mark.integration
class TestAgentMemoryModel:
    """Test AgentMemory model creation and attributes.

    Requires Postgres + pgvector — uses `Vector` columns and JSONB metadata.
    Marked integration so the default unit run skips it.
    """

    def test_create_agent_memory(self, db_session: Session, test_tenant: Tenant, test_agent: Agent):
        """Test creating an agent memory record"""
        memory = AgentMemory(
            agent_id=test_agent.id,
            tenant_id=test_tenant.id,
            memory_type="fact",
            content="Customer prefers email communication",
            importance=0.8,
        )
        db_session.add(memory)
        db_session.commit()
        db_session.refresh(memory)

        assert memory.id is not None
        assert memory.agent_id == test_agent.id
        assert memory.tenant_id == test_tenant.id
        assert memory.memory_type == "fact"
        assert memory.content == "Customer prefers email communication"
        assert memory.importance == 0.8
        assert memory.access_count == 0
        assert memory.created_at is not None

    def test_memory_with_embedding(self, db_session: Session, test_tenant: Tenant, test_agent: Agent):
        """Test memory with vector embedding"""
        embedding = [0.1] * 1536  # OpenAI embedding dimension
        memory = AgentMemory(
            agent_id=test_agent.id,
            tenant_id=test_tenant.id,
            memory_type="experience",
            content="Successfully resolved customer complaint",
            embedding=embedding,
            importance=0.9,
        )
        db_session.add(memory)
        db_session.commit()
        db_session.refresh(memory)

        assert memory.embedding is not None
        assert len(memory.embedding) == 1536

    def test_memory_types(self, db_session: Session, test_tenant: Tenant, test_agent: Agent):
        """Test different memory types"""
        memory_types = ["fact", "experience", "skill", "preference", "relationship", "procedure"]

        for mtype in memory_types:
            memory = AgentMemory(
                agent_id=test_agent.id,
                tenant_id=test_tenant.id,
                memory_type=mtype,
                content=f"Test content for {mtype}",
            )
            db_session.add(memory)

        db_session.commit()

        memories = db_session.query(AgentMemory).filter(
            AgentMemory.agent_id == test_agent.id
        ).all()

        assert len(memories) == len(memory_types)

    def test_memory_expiration(self, db_session: Session, test_tenant: Tenant, test_agent: Agent):
        """Test memory with expiration date"""
        expires = datetime.utcnow() + timedelta(days=30)
        memory = AgentMemory(
            agent_id=test_agent.id,
            tenant_id=test_tenant.id,
            memory_type="fact",
            content="Temporary promotion details",
            expires_at=expires,
        )
        db_session.add(memory)
        db_session.commit()
        db_session.refresh(memory)

        assert memory.expires_at is not None
        assert memory.expires_at > datetime.utcnow()


class TestAgentMemorySchema:
    """Test AgentMemory Pydantic schemas"""

    def test_memory_create_schema(self):
        """Test AgentMemoryCreate schema validation"""
        data = AgentMemoryCreate(
            agent_id=uuid4(),
            memory_type="fact",
            content="Test memory content",
            importance=0.7,
        )
        assert data.memory_type == "fact"
        assert data.importance == 0.7

    def test_memory_update_schema(self):
        """Test AgentMemoryUpdate schema with partial updates"""
        data = AgentMemoryUpdate(importance=0.9)
        assert data.importance == 0.9
        assert data.content is None


def test_knowledge_entity_model():
    """Test KnowledgeEntity model has required fields."""
    from app.models.knowledge_entity import KnowledgeEntity

    assert hasattr(KnowledgeEntity, 'id')
    assert hasattr(KnowledgeEntity, 'tenant_id')
    assert hasattr(KnowledgeEntity, 'entity_type')
    assert hasattr(KnowledgeEntity, 'name')
    assert hasattr(KnowledgeEntity, 'attributes')
    assert hasattr(KnowledgeEntity, 'confidence')
    assert hasattr(KnowledgeEntity, 'source_agent_id')
    assert hasattr(KnowledgeEntity, 'created_at')
    assert hasattr(KnowledgeEntity, 'updated_at')


def test_knowledge_relation_model():
    """Test KnowledgeRelation model has required fields."""
    from app.models.knowledge_relation import KnowledgeRelation

    assert hasattr(KnowledgeRelation, 'id')
    assert hasattr(KnowledgeRelation, 'tenant_id')
    assert hasattr(KnowledgeRelation, 'from_entity_id')
    assert hasattr(KnowledgeRelation, 'to_entity_id')
    assert hasattr(KnowledgeRelation, 'relation_type')
    assert hasattr(KnowledgeRelation, 'strength')
    assert hasattr(KnowledgeRelation, 'evidence')
    assert hasattr(KnowledgeRelation, 'discovered_by_agent_id')
    assert hasattr(KnowledgeRelation, 'created_at')


def test_memory_schema():
    """Test AgentMemory schemas work correctly."""
    from app.schemas.agent_memory import AgentMemoryCreate
    import uuid

    create_data = AgentMemoryCreate(
        agent_id=uuid.uuid4(),
        memory_type="fact",
        content="Customer prefers email communication",
        importance=0.8,
        source="conversation"
    )
    assert create_data.memory_type == "fact"
    assert create_data.importance == 0.8


def test_knowledge_entity_schema():
    """Test KnowledgeEntity schemas work correctly."""
    from app.schemas.knowledge_entity import KnowledgeEntityCreate

    create_data = KnowledgeEntityCreate(
        entity_type="customer",
        name="Acme Corp",
        attributes={"industry": "tech", "size": "enterprise"},
        confidence=0.95
    )
    assert create_data.entity_type == "customer"
    assert create_data.name == "Acme Corp"


def test_knowledge_relation_schema():
    """Test KnowledgeRelation schemas work correctly."""
    from app.schemas.knowledge_relation import KnowledgeRelationCreate
    import uuid

    create_data = KnowledgeRelationCreate(
        from_entity_id=uuid.uuid4(),
        to_entity_id=uuid.uuid4(),
        relation_type="purchased",
        strength=0.9,
        evidence={"order_id": "123"}
    )
    assert create_data.relation_type == "purchased"


def test_memory_service_class():
    """Test MemoryService class exists with required methods."""
    from app.services.memory.memory_service import MemoryService

    assert hasattr(MemoryService, 'store')
    assert hasattr(MemoryService, 'recall')
    assert hasattr(MemoryService, 'forget')
    assert hasattr(MemoryService, 'share')
    assert hasattr(MemoryService, 'get_relevant_memories')
    assert callable(getattr(MemoryService, 'store'))
