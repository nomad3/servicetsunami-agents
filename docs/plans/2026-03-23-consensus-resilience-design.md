# Consensus Resilience & Multi-Agent Hardening — Design Document

**Date**: 2026-03-23 (revised after code review)
**Status**: Design
**Context**: Simon shared ChatGPT recommendations on multi-agent consensus systems. This document critically evaluates each recommendation and addresses the real failure modes observed in production testing.

## Real Failure Modes (Observed 2026-03-22/23)

The actual production failures are NOT about consensus voting math:

1. **Foreground/background GPU contention** — Background scoring (rubric + 3 consensus reviewers) competes with foreground local_tool_agent for the single Ollama GPU. The tool agent times out waiting for the GPU semaphore while background scoring holds it.

2. **Local tool agent timeouts** — gemma4 with 9 tool schemas takes 18-60s per call. With 3 rounds + background scoring queued, total time exceeds HTTP request timeouts.

3. **Dynamic workflow runs never finalize** — DB row stays "running" forever. Schema nullable fields cause 500s. (Fixed in `0b0c271` but separate reliability track.)

4. **High-risk actions execute before review** — `send_email`, `create_jira_issue` etc. run immediately. The consensus reviewer only scores the response AFTER the action is done. Can't prevent bad actions.

## Architecture: Three Separate Reliability Tracks

### Track 1: Inference Bulkhead (Foreground vs Background)

**Problem**: Single GPU semaphore serializes everything. Background scoring blocks foreground chat.

**Solution**: Priority-based inference queue.

```
Foreground (user-blocking):     HIGH priority — never waits for background
  - local_tool_agent calls
  - generate_agent_response_sync
  - schema-aware extraction

Background (async, can wait):   LOW priority — yields to foreground
  - auto_quality_scorer rubric
  - consensus reviewer (3 calls)
  - conversation summarization
```

**Implementation**: Replace single semaphore with a priority queue. Foreground callers acquire immediately (or fail fast). Background callers wait in queue with a hard timeout and circuit breaker.

```python
# In local_inference.py
_foreground_lock = asyncio.Lock()       # Foreground gets exclusive GPU
_background_semaphore = asyncio.Semaphore(1)  # Background waits

async def generate(prompt, ..., priority="background"):
    if priority == "foreground":
        async with _foreground_lock:
            return await _do_generate(...)
    else:
        # Background: skip if foreground is running
        if _foreground_lock.locked():
            logger.debug("GPU busy with foreground — skipping background inference")
            return None
        async with _background_semaphore:
            return await _do_generate(...)
```

**Degradation order** (degrade scorer first, never foreground):
1. Skip consensus reviewers (3 calls saved)
2. Skip rubric scoring (1 call saved)
3. Fall back to plain text response (no tools)
4. Return error message (last resort)

### Track 2: Pre-Execution Safety Gate (High-Risk Actions)

**Problem**: Consensus reviewer runs AFTER the response is returned. It can't prevent `send_email` from firing.

**Solution**: Move high-risk gating into the MCP tool execution path, not the scorer.

**Implementation**: Add a `_check_risk` gate in `local_tool_agent.py` before executing each tool call:

```python
HIGH_RISK_TOOLS = {"send_email", "deploy_changes", "execute_shell", "create_jira_issue"}
CONFIRM_TOOLS = {"create_entity", "record_observation"}  # Medium risk — execute but flag

def _check_risk(tool_name: str, arguments: dict) -> str:
    """Returns 'allow', 'confirm', or 'block'."""
    if tool_name in HIGH_RISK_TOOLS:
        return "block"  # Local model should NOT send emails or deploy
    if tool_name in CONFIRM_TOOLS:
        return "allow"  # Allow but log as medium-risk
    return "allow"
```

For the local tool agent specifically: **block all side-effect tools**. A 1.7B model should not be trusted to send emails or create Jira issues autonomously. Only read/search operations should be allowed in the local fallback.

This is NOT about consensus voting — it's about limiting the blast radius of a small model's decisions.

### Track 3: Dynamic Workflow Reliability (Separate Track)

**Problem**: Workflow runs don't finalize, schema fields cause 500s.

**Status**: Fixed in `0b0c271`:
- `finalize_workflow_run` activity persists final state to DB
- `WorkflowStepLogInDB` schema defaults for nullable fields

**Remaining**: This track has nothing to do with consensus. Don't conflate it.

## Consensus Optimization (Secondary — Reduces Load)

These are valid optimizations but secondary to the bulkhead:

### Low-Risk Consensus Skip
Skip consensus for trivial messages (greetings, thanks, simple Q&A without tools).
Saves 3 Ollama calls per trivial message (~50-70% of all messages).

```python
def _should_skip_consensus(tools_called: list, agent_response: str) -> bool:
    if not tools_called and len(agent_response) < 200:
        return True  # Trivial response, no tools — skip
    return False
```

### Leave-One-Out Fragility
Zero-cost check on existing results. If `approved_count == required`, consensus is fragile.

## Implementation Plan

### Phase 1: Inference Bulkhead (Highest Priority)
- Split semaphore into foreground/background with priority
- Background scorer skips when foreground is active
- Hard 60s circuit breaker on background scoring
- **Impact**: Local tool agent stops timing out

### Phase 2: Pre-Execution Safety Gate
- Block HIGH_RISK_TOOLS in local_tool_agent (local model can't send emails)
- Log medium-risk tool calls for audit
- **Impact**: Prevents small model from executing dangerous side effects

### Phase 3: Low-Risk Consensus Skip
- Skip consensus for trivial messages
- Add fragility flag to ConsensusResult
- **Impact**: 50-70% reduction in background Ollama calls

## What NOT to Implement

- KL divergence / position reversals — no iterative rounds
- Weighted adjudication — all reviewers use same model
- Cross-examination — too expensive locally
- Full chaos harness — `return_exceptions` already handles failures
- Risk-based quorum in the scorer — wrong layer, gate belongs in execution path
