# ServiceTsunami Enterprise AI Platform Design

**Date:** 2025-11-25
**Status:** Approved
**Author:** Design Session with Claude

## Executive Summary

Transform ServiceTsunami from a single-agent data platform into a full-featured enterprise AI orchestration platform with:

- **Agent Orchestration**: Hybrid hierarchy (supervisor-worker + peer collaboration)
- **Agent Memory**: Three-tier system (Redis → Vector Store → Knowledge Graph)
- **Multi-LLM**: 50+ models across 20+ providers with smart routing
- **Whitelabel**: Branding, feature flags, custom domains, industry templates

---

## 1. Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│  FRONTEND (React)                                           │
│  - Agent Teams UI, Memory Inspector, LLM Config, Branding   │
├─────────────────────────────────────────────────────────────┤
│  API LAYER (FastAPI)                                        │
│  - Orchestration Engine, Memory Service, LLM Router         │
├─────────────────────────────────────────────────────────────┤
│  MCP SERVER (FastMCP)                                       │
│  - Tools, Databricks, External Integrations                 │
├─────────────────────────────────────────────────────────────┤
│  DATA LAYER                                                 │
│  - PostgreSQL (entities, knowledge graph)                   │
│  - Redis (hot context, message queues)                      │
│  - Vector Store (semantic memory)                           │
└─────────────────────────────────────────────────────────────┘
```

---

## 2. Agent Orchestration

### 2.1 Data Models

#### AgentGroup (NEW)
```python
AgentGroup:
  - id: UUID
  - name: str
  - description: str
  - tenant_id: FK(tenants.id)
  - goal: str  # Team objective
  - strategy: JSON  # How team approaches problems
  - shared_context: JSON  # Knowledge all agents share
  - escalation_rules: JSON  # When to escalate to supervisor
  - created_at, updated_at
```

#### Agent (EXTEND existing)
```python
Agent (add fields):
  - role: str  # "SDR", "Researcher", "Analyst", "Manager"
  - capabilities: JSON  # What this agent can do
  - personality: JSON  # Communication style, tone, approach
  - llm_config_id: FK(llm_configs.id)  # Which LLM to use
  - memory_config: JSON  # Memory preferences
  - autonomy_level: enum('full', 'supervised', 'approval_required')
  - max_delegation_depth: int  # How deep can it delegate
```

#### AgentRelationship (NEW)
```python
AgentRelationship:
  - id: UUID
  - group_id: FK(agent_groups.id)
  - from_agent_id: FK(agents.id)
  - to_agent_id: FK(agents.id)
  - relationship_type: enum('supervises', 'delegates_to', 'collaborates_with', 'reports_to', 'consults')
  - trust_level: float  # 0-1, affects autonomy
  - communication_style: enum('sync', 'async', 'broadcast')
  - handoff_rules: JSON  # When/how to pass work
```

#### AgentTask (NEW)
```python
AgentTask:
  - id: UUID
  - group_id: FK(agent_groups.id)
  - assigned_agent_id: FK(agents.id)
  - created_by_agent_id: FK(agents.id)
  - human_requested: bool
  - status: enum('queued', 'thinking', 'executing', 'waiting_input', 'delegated', 'reviewing', 'completed', 'failed')
  - priority: enum('critical', 'high', 'normal', 'low', 'background')
  - task_type: str  # "research", "analyze", "generate", "decide", "execute"
  - objective: str
  - context: JSON
  - reasoning: JSON  # Chain of thought
  - output: JSON
  - confidence: float
  - parent_task_id: FK  # Subtask hierarchy
  - requires_approval: bool
  - tokens_used: int
  - cost: decimal
  - started_at, completed_at
```

#### AgentMessage (NEW)
```python
AgentMessage:
  - id: UUID
  - group_id: FK(agent_groups.id)
  - task_id: FK(agent_tasks.id)
  - from_agent_id: FK(agents.id)
  - to_agent_id: FK(agents.id)  # Nullable for broadcast
  - message_type: enum('request', 'response', 'handoff', 'escalation', 'update', 'question', 'approval_request')
  - content: JSON
  - reasoning: str
  - requires_response: bool
  - response_deadline: datetime
  - created_at
```

#### AgentSkill (NEW)
```python
AgentSkill:
  - id: UUID
  - agent_id: FK(agents.id)
  - skill_name: str  # "sql_query", "summarization", "negotiation"
  - proficiency: float  # 0-1, improves with use
  - times_used: int
  - success_rate: float
  - learned_from: enum('training', 'observation', 'practice', 'feedback')
  - examples: JSON  # Good examples for few-shot learning
  - last_used_at
```

---

## 3. Memory System

### 3.1 Three-Tier Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    Memory Service                           │
├─────────────────────────────────────────────────────────────┤
│  remember(agent_id, content, type, importance)              │
│  recall(agent_id, query, limit) → semantic search           │
│  forget(agent_id, memory_id) → explicit deletion            │
│  consolidate(agent_id) → merge similar, prune old           │
│  share(from_agent, to_agent, memory_ids) → transfer         │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  TIER 1: Redis (Hot Context) ─────────────────────────────  │
│  - Active conversation (last 10 messages)                   │
│  - Current task context                                     │
│  - TTL: 1 hour, <1ms access                                 │
│                                                             │
│  TIER 2: Vector Store (Semantic Memory) ──────────────────  │
│  - AgentMemory embeddings                                   │
│  - Similarity search for relevant past experiences          │
│  - TTL: configurable per agent, ~10ms access                │
│                                                             │
│  TIER 3: PostgreSQL (Knowledge Graph) ────────────────────  │
│  - KnowledgeEntity + KnowledgeRelation                      │
│  - Structured facts, relationships                          │
│  - Permanent, queryable, ~50ms access                       │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### 3.2 Data Models

#### AgentMemory (NEW)
```python
AgentMemory:
  - id: UUID
  - agent_id: FK(agents.id)
  - tenant_id: FK(tenants.id)
  - memory_type: enum('fact', 'experience', 'skill', 'preference', 'relationship', 'procedure')
  - content: str
  - embedding: vector(1536)
  - importance: float  # 0-1
  - access_count: int
  - source: enum('conversation', 'training', 'observation', 'inference', 'user_feedback')
  - source_task_id: FK(agent_tasks.id)
  - expires_at: datetime
  - created_at, last_accessed_at
```

#### KnowledgeEntity (NEW)
```python
KnowledgeEntity:
  - id: UUID
  - tenant_id: FK(tenants.id)
  - entity_type: str  # "customer", "product", "concept", "person"
  - name: str
  - attributes: JSON
  - confidence: float
  - source_agent_id: FK(agents.id)
  - created_at, updated_at
```

#### KnowledgeRelation (NEW)
```python
KnowledgeRelation:
  - id: UUID
  - tenant_id: FK(tenants.id)
  - from_entity_id: FK(knowledge_entities.id)
  - to_entity_id: FK(knowledge_entities.id)
  - relation_type: str  # "works_at", "purchased", "prefers", "related_to"
  - strength: float
  - evidence: JSON
  - discovered_by_agent_id: FK(agents.id)
  - created_at
```

---

## 4. Multi-LLM System

### 4.1 Data Models

#### LLMProvider (NEW)
```python
LLMProvider:
  - id: UUID
  - name: str  # "anthropic", "openai", "deepseek", etc.
  - display_name: str
  - base_url: str
  - auth_type: enum('api_key', 'oauth', 'custom')
  - supported_features: JSON
  - is_active: bool
```

#### LLMModel (NEW)
```python
LLMModel:
  - id: UUID
  - provider_id: FK(llm_providers.id)
  - model_id: str  # "claude-sonnet-4-5"
  - display_name: str
  - context_window: int
  - max_output_tokens: int
  - input_cost_per_1k: decimal
  - output_cost_per_1k: decimal
  - capabilities: JSON
  - speed_tier: enum('fast', 'standard', 'slow')
  - quality_tier: enum('best', 'good', 'basic')
  - size_category: enum('tiny', 'small', 'medium', 'large', 'xl')
  - edge_optimized: bool
  - is_active: bool
```

#### LLMConfig (NEW)
```python
LLMConfig:
  - id: UUID
  - tenant_id: FK(tenants.id)
  - name: str
  - is_tenant_default: bool
  - primary_model_id: FK(llm_models.id)
  - fallback_model_id: FK(llm_models.id)
  - api_key_encrypted: str  # BYOK
  - use_platform_key: bool
  - temperature: float
  - max_tokens: int
  - routing_rules: JSON
  - budget_limit_daily: decimal
  - budget_limit_monthly: decimal
```

### 4.2 Supported Providers & Models (November 2025)

**Frontier Models:**
- Anthropic: Claude Sonnet 4.5, Opus 4, Sonnet 4, Haiku 4
- OpenAI: GPT-5, o1, o1-mini, GPT-4o, GPT-4o-mini
- Google: Gemini 2.5 Pro, Gemini 2.5 Flash
- xAI: Grok-3, Grok-3-mini
- Meta: Llama 4 Behemoth, Maverick, Scout (10M context)

**Chinese Models:**
- Alibaba Qwen3: qwen3-max, qwen3-next-80b-thinking
- DeepSeek: V3.2-exp, V3.1, R1
- Zhipu GLM: GLM-4-Plus
- Baidu ERNIE: ERNIE-4.0-Turbo
- Moonshot Kimi: K2
- ByteDance Doubao: Pro-256k

**Coding Specialists:**
- Mistral Codestral 25.08, Devstral
- DeepSeek Coder V2.5
- Gemma 4 27B

**Small/Edge Models:**
- Microsoft Phi-4: reasoning, multimodal, mini
- HuggingFace SmolLM2: 1.7B, 360M, 135M
- Meta Llama 3.2: 3B, 1B
- Gemma 4: 4B to 27B variants

**Embedding Models:**
- text-embedding-3-large/small (OpenAI)
- bge-m3 (multilingual)
- nomic-embed-text
- codestral-embed

---

## 5. Whitelabel System

### 5.1 Data Models

#### TenantBranding (NEW)
```python
TenantBranding:
  - id: UUID
  - tenant_id: FK(tenants.id)

  # Brand Identity
  - company_name: str
  - logo_url: str
  - logo_dark_url: str
  - favicon_url: str
  - support_email: str

  # Colors
  - primary_color: str
  - secondary_color: str
  - accent_color: str
  - background_color: str
  - sidebar_bg: str

  # AI Customization
  - ai_assistant_name: str
  - ai_assistant_persona: JSON

  # Domain
  - custom_domain: str
  - domain_verified: bool
  - ssl_certificate_id: str

  # Industry
  - industry: str
  - compliance_mode: JSON
```

#### TenantFeatures (NEW)
```python
TenantFeatures:
  - id: UUID
  - tenant_id: FK(tenants.id)

  # Core Features
  - agents_enabled: bool = True
  - agent_groups_enabled: bool = True
  - datasets_enabled: bool = True
  - chat_enabled: bool = True
  - multi_llm_enabled: bool = True
  - agent_memory_enabled: bool = True

  # AI Intelligence
  - ai_insights_enabled: bool = True
  - ai_recommendations_enabled: bool = True
  - ai_anomaly_detection: bool = True

  # Limits
  - max_agents: int = 10
  - max_agent_groups: int = 5
  - monthly_token_limit: int = 1000000
  - storage_limit_gb: float = 10.0

  # UI
  - hide_servicetsunami_branding: bool = False
```

#### TenantAnalytics (NEW)
```python
TenantAnalytics:
  - id: UUID
  - tenant_id: FK(tenants.id)
  - period: enum('hourly', 'daily', 'weekly', 'monthly')
  - period_start: datetime

  # Usage Metrics
  - total_messages: int
  - total_tasks: int
  - total_tokens_used: int
  - total_cost: decimal

  # AI-Generated
  - ai_insights: JSON
  - ai_recommendations: JSON
  - ai_forecast: JSON
```

### 5.2 Industry Templates

```python
INDUSTRY_TEMPLATES = {
    "healthcare": {
        "compliance": ["hipaa", "hitech"],
        "default_agents": [
            {"name": "Patient Data Analyst", "skills": ["pii_handling", "medical_coding"]},
            {"name": "Clinical Report Generator", "skills": ["summarization", "compliance_check"]},
        ],
        "auto_redact": True,
    },
    "finance": {
        "compliance": ["sox", "pci", "gdpr"],
        "default_agents": [
            {"name": "Financial Analyst", "skills": ["forecasting", "risk_assessment"]},
            {"name": "Compliance Monitor", "skills": ["anomaly_detection", "audit_trail"]},
        ],
    },
    "legal": {
        "default_agents": [
            {"name": "Document Reviewer", "skills": ["contract_analysis", "clause_extraction"]},
            {"name": "Research Assistant", "skills": ["case_law_search", "citation"]},
        ],
    },
    "retail": {
        "default_agents": [
            {"name": "Sales Analyst", "skills": ["trend_analysis", "inventory_forecasting"]},
            {"name": "Customer Insights", "skills": ["sentiment_analysis", "segmentation"]},
        ],
    },
}
```

---

## 6. Implementation Phases

### Phase 1: Agent Orchestration
- AgentGroup, AgentRelationship, AgentTask, AgentMessage models
- OrchestrationService, TaskDispatcher, MessageBroker
- Teams UI, Hierarchy View, Task Board
- MCP tools: delegate_to_agent, query_agent, escalate_to_supervisor

### Phase 2: Memory System
- AgentMemory, KnowledgeEntity, KnowledgeRelation models
- MemoryService (remember/recall/forget/consolidate)
- Redis integration for hot context
- Vector store for semantic search
- Memory Inspector UI, Knowledge Graph visualization
- MCP tools: remember, recall, learn_entity, query_knowledge

### Phase 3: Multi-LLM
- LLMProvider, LLMModel, LLMConfig, LLMUsage models
- LLMRouter with smart routing
- Provider adapters for 20+ providers
- BYOK (Bring Your Own Key) support
- Model Selector UI, Usage Dashboard
- Cost tracking and budget controls

### Phase 4: Whitelabel
- TenantBranding, TenantFeatures, TenantAnalytics models
- BrandingService, FeatureFlagService
- Custom domain with SSL provisioning
- Industry templates
- Branding Editor UI, Feature Toggles
- AI-powered tenant analytics

---

## 7. Integration with Existing System

### Extended Models
- `Agent`: Add role, capabilities, personality, llm_config_id, memory_config
- `AgentKit`: Add kit_type, default_agents, default_hierarchy, industry
- `ChatSession`: Add agent_group_id, root_task_id, memory_context
- `ChatMessage`: Add agent_id, task_id, reasoning, confidence, tokens_used
- `Tenant`: Add branding, features relationships, default_llm_config_id
- `Dataset`: Add auto_profile, pii_detected, quality_score, embedding_status

### Enhanced Services
- `ChatService`: Integrate orchestration, memory, multi-LLM routing
- `LLMService` → `LLMRouter`: Multi-provider support
- `ContextManager`: Integrate with memory service

### New API Routes
- `/api/v1/agent-groups` - Team management
- `/api/v1/tasks` - Task tracking
- `/api/v1/memory` - Memory operations
- `/api/v1/knowledge` - Knowledge graph
- `/api/v1/llm` - LLM configuration
- `/api/v1/branding` - Whitelabel settings
- `/api/v1/features` - Feature flags
- `/api/v1/tenant-analytics` - Usage analytics

### New Frontend Pages
- `/teams` - Agent team management
- `/memory` - Memory & knowledge explorer
- `/settings/llm` - LLM configuration
- `/settings/branding` - Whitelabel customization
- `/analytics` - Tenant analytics dashboard

---

## 8. File Structure

```
apps/api/app/
├── models/
│   ├── agent_group.py        # Phase 1
│   ├── agent_relationship.py # Phase 1
│   ├── agent_task.py         # Phase 1
│   ├── agent_message.py      # Phase 1
│   ├── agent_skill.py        # Phase 1
│   ├── agent_memory.py       # Phase 2
│   ├── knowledge_entity.py   # Phase 2
│   ├── knowledge_relation.py # Phase 2
│   ├── llm_provider.py       # Phase 3
│   ├── llm_model.py          # Phase 3
│   ├── llm_config.py         # Phase 3
│   ├── llm_usage.py          # Phase 3
│   ├── tenant_branding.py    # Phase 4
│   ├── tenant_features.py    # Phase 4
│   └── tenant_analytics.py   # Phase 4
│
├── services/
│   ├── orchestration/
│   │   ├── group_manager.py
│   │   ├── task_dispatcher.py
│   │   ├── message_broker.py
│   │   └── workflow_engine.py
│   ├── memory/
│   │   ├── memory_service.py
│   │   ├── knowledge_graph.py
│   │   ├── embedding_service.py
│   │   └── consolidation.py
│   ├── llm/
│   │   ├── router.py
│   │   ├── providers/
│   │   │   ├── anthropic.py
│   │   │   ├── openai.py
│   │   │   ├── deepseek.py
│   │   │   ├── gemma.py
│   │   │   └── ...
│   │   └── usage_tracker.py
│   └── tenant/
│       ├── branding_service.py
│       ├── feature_flags.py
│       └── analytics_service.py

apps/web/src/
├── components/
│   ├── teams/
│   │   ├── TeamBuilder.js
│   │   ├── HierarchyView.js
│   │   └── TaskBoard.js
│   ├── memory/
│   │   ├── MemoryInspector.js
│   │   └── KnowledgeGraph.js
│   ├── llm/
│   │   ├── ModelSelector.js
│   │   └── UsageDashboard.js
│   └── branding/
│       ├── BrandingEditor.js
│       └── FeatureToggles.js
├── pages/
│   ├── TeamsPage.js
│   ├── MemoryPage.js
│   ├── LLMConfigPage.js
│   └── BrandingPage.js

apps/mcp-server/src/tools/
├── orchestration_tools.py
└── memory_tools.py
```

---

## 9. Success Metrics

- **Orchestration**: 90% task completion rate, <30s average task time
- **Memory**: <100ms recall latency, 80% relevance score
- **Multi-LLM**: 30% cost reduction via smart routing
- **Whitelabel**: <5 minute custom domain setup

---

## References

- [Top LLMs November 2025](https://www.shakudo.io/blog/top-9-large-language-models)
- [Qwen3 Models](https://github.com/QwenLM/Qwen3)
- [DeepSeek V3.1](https://huggingface.co/deepseek-ai/DeepSeek-V3.1)
- [Codestral 25.08](https://mistral.ai/news/codestral-25-08)
- [Phi-4 Family](https://azure.microsoft.com/en-us/blog/empowering-innovation-the-next-generation-of-the-phi-family/)
- [SmolLM2](https://collabnix.com/smollm2-the-complete-developers-guide-to-hugging-faces-revolutionary-small-language-model-for-on-device-ai/)
