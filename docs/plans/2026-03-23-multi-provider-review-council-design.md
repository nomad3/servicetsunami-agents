# Multi-Provider Review Council — Design Document

**Date**: 2026-03-23
**Status**: Design
**Goal**: Add a provider-diverse review council where Claude, Codex, Gemini, and Qwen each independently evaluate agent responses. Runs as async Temporal workflow, not inline in chat.

## Why

The current consensus council uses 3 Qwen reviewers — same model family, different prompts. This catches formatting and factual issues but has zero model diversity. If Qwen has a systematic blind spot, all 3 reviewers share it.

A multi-provider council gives real disagreement signals: if Claude approves but Codex rejects, that's meaningful. If all 4 providers agree, confidence is high. These signals feed into RL for platform selection optimization.

## Architecture

```
Agent Response (already returned to user)
  → auto_quality_scorer.py (existing)
      → Local Qwen Council (always-on, fast, free)
      → Triggers provider council? (decision gate)
          ↓
  → Temporal: ProviderReviewWorkflow (async, never blocks user)
      → Claude CLI reviewer  (tenant's subscription)
      → Codex CLI reviewer   (tenant's subscription)
      → Qwen local reviewer  (free)
      → [future] Gemini CLI reviewer
      → Meta-adjudicator: combines votes, stores disagreement patterns
      → RL experience update with provider-level breakdown
```

## When to Run Provider Council

NOT on every message. Decision gate in `auto_quality_scorer.py`:

| Trigger | Condition |
|---------|-----------|
| **Code tasks** | Always — PR quality matters |
| **Side-effect tools used** | send_email, create_jira_issue, deploy_changes |
| **Fragile consensus** | Local council passed but `fragile=True` (2/3 exactly) |
| **Sampled evaluation** | Random 5-10% of normal chat for RL data diversity |
| **Low local score** | Rubric score < 40 — worth a second opinion |
| **Manual trigger** | API endpoint to request provider review on any message |

## Provider Reviewer Design

Each provider reviewer receives the same payload:

```python
@dataclass
class ProviderReviewInput:
    user_message: str
    agent_response: str
    agent_slug: str
    platform_used: str        # which CLI generated the original response
    tools_called: list
    entities_recalled: list
    channel: str
    tenant_id: str
```

Each reviewer returns:

```python
@dataclass
class ProviderReview:
    provider: str             # "claude_code", "codex", "local_qwen", "gemini_cli"
    approved: bool
    verdict: str              # APPROVED, REJECTED, CONDITIONAL
    score: int                # 0-100
    issues: list[str]
    suggestions: list[str]
    summary: str
    tokens_used: int
    cost_usd: float
    duration_ms: int
```

### Provider Implementations

**Claude CLI Reviewer** — Uses tenant's Claude Code subscription:
```
claude -p "{review_prompt}" --output-format json --model sonnet
```
- Read-only review prompt, no tools
- Uses the cheaper model (sonnet, not opus) to save credits
- Falls back gracefully if subscription exhausted

**Codex CLI Reviewer** — Uses tenant's Codex subscription:
```
codex exec "{review_prompt}" --json
```
- Same review prompt format
- Falls back if no Codex credential

**Qwen Local Reviewer** — Already exists (consensus_reviewer.py):
- Reuses existing local reviewer infrastructure
- Always available, zero cost

**Gemini CLI Reviewer** (future):
```
gemini "{review_prompt}" --output-format json
```
- When Gemini CLI integration is connected

## Meta-Adjudicator

Combines provider votes into a final assessment:

```python
@dataclass
class ProviderCouncilResult:
    consensus: bool               # majority approved
    provider_agreement: float     # 0.0 (all disagree) to 1.0 (all agree)
    reviews: list[ProviderReview]
    disagreements: list[str]      # specific issues where providers disagree
    recommended_platform: str     # which provider scored highest
    total_cost: float
    total_tokens: int
```

**Disagreement detection**: When providers disagree, store the specific dimensions:
- Provider A says accurate, Provider B says hallucinated → flag for human review
- Provider A scores 90, Provider B scores 30 → high variance, investigate

**Platform recommendation**: Track which provider consistently scores highest for each task type. Feed back into `agent_router.py` for routing decisions.

## Temporal Workflow

```python
@workflow.defn
class ProviderReviewWorkflow:
    @workflow.run
    async def run(self, input: ProviderReviewInput) -> ProviderCouncilResult:
        # Run available providers in parallel
        reviews = await asyncio.gather(
            workflow.execute_activity(review_with_claude, ...),
            workflow.execute_activity(review_with_codex, ...),
            workflow.execute_activity(review_with_local_qwen, ...),
            return_exceptions=True,
        )
        # Filter out failed providers
        # Compute meta-adjudication
        # Update RL experience with provider breakdown
        return result
```

**Queue**: `servicetsunami-code` (reuses code-worker which has Claude + Codex CLIs installed)

**Timeout**: 5 min per provider, 10 min total workflow

## RL Integration

Each provider council run produces an RL experience update:

```json
{
  "reward_source": "provider_council",
  "reward_components": {
    "provider_reviews": {
      "claude_code": {"score": 85, "approved": true},
      "codex": {"score": 72, "approved": true},
      "local_qwen": {"score": 65, "approved": false}
    },
    "provider_agreement": 0.67,
    "disagreements": ["qwen flagged hallucination, claude/codex did not"],
    "recommended_platform": "claude_code",
    "total_cost": 0.04
  }
}
```

Over time, this data enables:
- **Platform selection**: Route tasks to the provider that scores best for that task type
- **Regression detection**: If a provider's scores suddenly drop, flag it
- **Cost optimization**: Compare quality/cost ratios across providers

## Implementation Plan

### Phase 1: Review Activities (code-worker)
- `review_with_claude`: CLI call with review prompt, parse JSON verdict
- `review_with_codex`: Same pattern with Codex CLI
- `review_with_local_qwen`: Calls existing consensus_reviewer
- All activities in `apps/code-worker/workflows.py`

### Phase 2: ProviderReviewWorkflow
- Temporal workflow that runs reviewers in parallel
- Meta-adjudicator computes agreement, disagreements, recommendation
- Queue: `servicetsunami-code`

### Phase 3: Decision Gate + RL Update
- Trigger logic in `auto_quality_scorer.py`
- RL experience update with provider-level breakdown
- API endpoint for manual trigger: `POST /api/v1/chat/messages/{id}/provider-review`

### Phase 4: Platform Routing Feedback
- `agent_router.py` reads provider council RL data
- Adjusts platform selection based on historical quality scores per task type

## Cost Estimate

| Scenario | Providers Used | Cost/Review | Frequency |
|----------|---------------|-------------|-----------|
| Code PR | Claude + Codex + Qwen | ~$0.05 | Every code task |
| Fragile consensus | Claude + Qwen | ~$0.02 | ~10% of messages |
| Sampled eval | All available | ~$0.05 | ~5% of messages |
| Low score | Claude + Qwen | ~$0.02 | ~5% of messages |

Estimated: ~$0.50-2.00/day at current volume. Well within subscription budgets.

## What This is NOT

- NOT a replacement for the local Qwen council (that stays as fast QA)
- NOT inline in the chat path (always async via Temporal)
- NOT required for every message (decision gate controls when it runs)
- NOT generating alternative responses (review only, not re-generation)
