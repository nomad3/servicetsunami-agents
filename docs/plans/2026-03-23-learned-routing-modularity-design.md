# Learned Routing via Modularity Hypothesis — Design Document

**Date**: 2026-03-23
**Status**: Design
**Goal**: Wire accumulated RL experience data back into the agent router so the coordination module learns which specialized module (agent + platform) to activate based on past performance — not just keyword matching.

## The Modularity Hypothesis Applied

The system already implements modular AGI architecture:

```
                    ┌─────────────────────┐
                    │   Root Supervisor    │  ← Coordination (learns from RL)
                    │   (Agent Router)     │
                    └──────────┬──────────┘
                               │
        ┌──────────┬───────────┼───────────┬──────────┐
        │          │           │           │          │
   ┌────▼────┐ ┌───▼───┐ ┌────▼────┐ ┌────▼────┐ ┌───▼────┐
   │  Luna   │ │ Code  │ │  Data   │ │  Sales  │ │Marketing│  ← Specialized modules
   └─────────┘ └───────┘ └─────────┘ └─────────┘ └────────┘
        │          │           │           │          │
   ┌────▼──────────▼───────────▼───────────▼──────────▼────┐
   │              MCP Tool Layer (81 tools)                  │  ← Shared perception/action
   └────────────────────────┬──────────────────────────────┘
                            │
   ┌────────────────────────▼──────────────────────────────┐
   │    Knowledge Graph + Vector Search (shared memory)     │
   └────────────────────────┬──────────────────────────────┘
                            │
   ┌────────────────────────▼──────────────────────────────┐
   │  RL + Provider Council + Consensus (meta-cognition)    │
   └───────────────────────────────────────────────────────┘
```

**Missing link**: The coordination module (Agent Router) is deterministic — keyword matching. The RL data exists to make it learned. The provider council data exists to make platform selection learned. Neither is wired back yet.

## What We Have (Data)

### RL Experiences Table
Every response generates an RL experience with:
- `decision_point`: chat_response, code_task, agent_routing
- `state`: task_type, channel, agent_slug, entities recalled
- `action`: platform used, agent selected, tools called
- `reward`: 0-1 scale from quality score
- `reward_components`: 6-dimension breakdown + consensus + provider council
- `state_text`: embedded for semantic similarity search

### Provider Council Data
For qualifying responses, `reward_components.provider_council` contains:
- Per-provider scores (claude_code, codex, local_gemma)
- Agreement score (0.0-1.0)
- Disagreements list
- Recommended platform

### Current Volume
- ~2,625 RL experiences (and growing)
- ~11 provider council reviews (accumulating)
- Platform breakdown across claude_code, codex, local_gemma

## What We Need (Learned Routing)

### 1. Platform Selection Learning

**Current**: `tenant_features.default_cli_platform` — static per-tenant setting.

**Learned**: For each task_type, query historical RL data to find which platform scores highest.

```python
def get_best_platform(task_type: str, tenant_id: uuid.UUID) -> str:
    """Query RL experiences to find the best-performing platform for this task type."""
    # SELECT platform, AVG(reward) as avg_reward
    # FROM rl_experiences
    # WHERE tenant_id = ? AND state->>'task_type' = ?
    # AND reward IS NOT NULL
    # GROUP BY platform
    # ORDER BY avg_reward DESC LIMIT 1
```

**Integration point**: `cli_session_manager.py` before selecting the platform.

### 2. Agent Selection Learning

**Current**: `agent_router.py` uses keyword matching to map messages to agent slugs.

**Learned**: For similar past messages (semantic search on `state_text`), which agent produced the highest rewards?

```python
def get_best_agent(message: str, tenant_id: uuid.UUID) -> Optional[str]:
    """Find similar past messages and return the agent that scored best."""
    # 1. Embed the message
    # 2. Search rl_experiences by state_embedding similarity
    # 3. Among top-k similar experiences, find which agent_slug had highest avg reward
    # 4. If confident (enough data + clear winner), return that agent
    # 5. Otherwise, return None (fall back to keyword matching)
```

**Integration point**: `agent_router.py` as a pre-check before keyword matching.

### 3. Uncertainty Estimation

**Current**: No explicit uncertainty. Router always picks one agent confidently.

**Learned**: When the RL data shows no clear winner (low variance, mixed signals), flag uncertainty.

```python
@dataclass
class RoutingDecision:
    agent_slug: str
    platform: str
    confidence: float      # 0.0 (guessing) to 1.0 (strong RL signal)
    source: str            # "keyword", "rl_learned", "semantic_match"
    alternatives: list     # other candidates with scores
```

When confidence is low: use the default agent but flag for provider council review.

### 4. Episodic Memory for Routing

**Current**: Each routing decision is independent — no memory of what worked before for this user/topic.

**Learned**: Before routing, recall the last 3-5 similar interactions and their outcomes.

```python
def recall_routing_context(message: str, tenant_id: uuid.UUID) -> dict:
    """Recall similar past routing decisions and their outcomes."""
    # Returns: {
    #   "similar_experiences": [...],
    #   "best_agent_for_similar": "luna",
    #   "best_platform_for_similar": "claude_code",
    #   "avg_reward_for_similar": 0.72,
    # }
```

## Implementation Plan

### Phase 1: Platform Selection from RL Data
- File: `apps/api/app/services/rl_routing.py` (new)
- Query `rl_experiences` for avg reward per platform per task_type
- Minimum 10 experiences per platform before recommending
- Fallback to tenant default if insufficient data
- Wire into `cli_session_manager.py` platform selection

### Phase 2: Agent Selection from Semantic RL Search
- Add to `rl_routing.py`
- Embed incoming message, search similar `state_text` embeddings
- Among top-10 similar experiences, find agent with highest avg reward
- Confidence threshold: only override keyword routing if avg_reward > 0.6 AND >= 5 matching experiences
- Wire into `agent_router.py` as pre-check

### Phase 3: Routing Decision Logging
- Log every routing decision as an RL experience with `decision_point="agent_routing"`
- State includes: message, task_type, channel, available agents, available platforms
- Action includes: selected agent, selected platform, confidence, source
- Reward assigned later from the response quality score
- Creates the feedback loop: route → respond → score → learn → route better

### Phase 4: Uncertainty-Driven Provider Council
- When routing confidence < 0.5, always trigger provider council
- When RL data shows platform A and platform B within 5% of each other, run both and compare
- Log platform comparison as RL data for future disambiguation

## Architecture Decision: Where Learning Happens

**NOT in the hot path.** The routing lookup should be a fast DB query (< 10ms), not a model call.

```
Message arrives
  → agent_router.py: keyword match (instant)
  → rl_routing.py: check RL data (fast DB query, <10ms)
      → If strong RL signal: override keyword match
      → If weak/no signal: keep keyword match
  → cli_session_manager.py: select platform
      → rl_routing.py: check platform performance (fast DB query)
      → If strong signal: use best platform
      → If no signal: use tenant default
```

The learning itself happens offline:
- RL experiences accumulate from every response
- Provider council adds multi-model quality signals
- Periodic policy update workflow aggregates signals
- Routing queries read aggregated data, not raw experiences

## Data Requirements

| Signal | Min Experiences | Confidence |
|--------|----------------|------------|
| Platform selection | 10 per platform per task_type | Moderate |
| Agent selection | 5 similar messages with clear winner | Low-moderate |
| Provider recommendation | 20 provider council runs | Moderate |
| Full learned routing | 100+ experiences per task_type | High |

Current data: ~2,834 experiences, ~11 provider council runs. Enough for platform selection. Provider council data still accumulating — agent selection needs more task-type diversity and provider reviews.

## Missing Modules (from Modularity Hypothesis)

| Module | Implementation | Priority |
|--------|---------------|----------|
| **Learned routing** | `rl_routing.py` — this design | Phase 1-2 |
| **Episodic memory** | Semantic search on RL experiences | Phase 2 |
| **Uncertainty estimation** | Confidence score on routing decisions | Phase 3 |
| **Conflict resolution** | Provider council (already built) | Done |
| **Planning module** | PR #25's Architect agent | Done (code tasks only) |

## What This Enables

Once learned routing is active, the system exhibits emergent AGI-like properties:
- **Self-improving**: Better responses → higher RL scores → better routing → better responses
- **Platform-adaptive**: If Claude starts scoring lower, system automatically shifts to Codex
- **Task-specialized**: System learns "code tasks go to Claude, data queries go to Data Team" from actual performance, not hardcoded rules
- **Uncertainty-aware**: When unsure, system requests multi-provider validation instead of guessing

The modularity hypothesis predicts this coordination-through-learning approach will outperform any static routing scheme, because the specialized modules' relative strengths change over time (model updates, cost changes, capability shifts).
