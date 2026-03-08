# Import all models so SQLAlchemy mapper registry is fully configured.
# Workers that import a subset of models trigger mapper initialization,
# which fails if relationship() targets aren't in the registry yet.
from .tenant import Tenant
from .user import User
from .agent import Agent
from .agent_group import AgentGroup
from .agent_kit import AgentKit
from .agent_memory import AgentMemory
from .agent_message import AgentMessage
from .agent_relationship import AgentRelationship
from .agent_skill import AgentSkill
from .agent_task import AgentTask
from .channel_account import ChannelAccount
from .channel_event import ChannelEvent
from .chat import ChatSession, ChatMessage
from .connector import Connector
from .data_pipeline import DataPipeline
from .data_source import DataSource
from .dataset import Dataset
from .dataset_group import DatasetGroup
from .deployment import Deployment
from .execution_trace import ExecutionTrace
from .knowledge_entity import KnowledgeEntity
from .knowledge_relation import KnowledgeRelation
from .llm_config import LLMConfig
from .llm_model import LLMModel
from .llm_provider import LLMProvider
from .memory_activity import MemoryActivity
from .notebook import Notebook
from .notification import Notification
from .pipeline_run import PipelineRun
from .integration_config import IntegrationConfig
from .integration_credential import IntegrationCredential
from .skill import Skill
from .skill_config import SkillConfig
from .skill_credential import SkillCredential
from .skill_execution import SkillExecution
from .tenant_analytics import TenantAnalytics
from .tenant_branding import TenantBranding
from .tenant_features import TenantFeatures
from .tool import Tool
from .vector_store import VectorStore

__all__ = [
    "Tenant", "User",
    "Agent", "AgentGroup", "AgentKit", "AgentMemory", "AgentMessage",
    "AgentRelationship", "AgentSkill", "AgentTask",
    "ChannelAccount", "ChannelEvent",
    "ChatSession", "ChatMessage",
    "Connector",
    "DataPipeline", "DataSource", "Dataset", "DatasetGroup",
    "Deployment", "ExecutionTrace",
    "KnowledgeEntity", "KnowledgeRelation",
    "LLMConfig", "LLMModel", "LLMProvider",
    "MemoryActivity", "Notebook", "Notification",
    "PipelineRun",
    "IntegrationConfig", "IntegrationCredential",
    "Skill", "SkillConfig", "SkillCredential", "SkillExecution",
    "TenantAnalytics", "TenantBranding", "TenantFeatures",
    "Tool", "VectorStore",
]
