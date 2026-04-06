# Gemma 4 Integration Plan

**Date:** 2026-04-02
**Status:** Proposed
**Author:** Luna

## Goal

Integrate Gemma 4 into ServiceTsunami without inventing a fake new platform. The repo already has two valid seams for this work:

1. `gemini_llm` and the generic LLM model registry for hosted model selection
2. Local/self-hosted inference services for open-weight execution

Gemma 4 should enter through those seams as a model family, not as a new `gemma_cli` platform.

## What Changed Externally

As of **April 2, 2026**, Google publicly announced Gemma 4 availability and Android AI Core preview support for the edge variants `E4B` and `E2B`. Google also documents:

- Gemma can be invoked through the Gemini API as a managed path.
- Gemma function calling is prompt-structured rather than token-native, so tool execution must remain application-controlled.

That matters here because ServiceTsunami currently depends on structured tool usage, MCP execution, routing, and multi-provider abstractions.

## Current Repo Fit

The existing codebase already supports the pieces we need:

- `apps/api/app/api/v1/integration_configs.py`
  Already registers `gemini_llm`, `anthropic_llm`, `gemini_cli`, `codex`, and `claude_code`.
- `apps/api/app/models/tenant_features.py`
  Already includes `active_llm_provider` and `default_cli_platform`.
- `apps/api/app/api/v1/llm.py`
  Already exposes provider/model configuration APIs.
- `apps/api/app/db/init_db.py`
  Seeds Google models, but the list is stale and still centered on Gemini 1.5.
- `apps/api/app/services/llm/router.py`
  Already routes against `LLMModel` records.
- `apps/api/app/services/agent_router.py`
  Routes live chat through CLI platforms; this is not the right first insertion point for Gemma 4.
- `apps/api/app/services/cli_session_manager.py`
  Supports `gemini_cli`, `claude_code`, and `codex`.
- `apps/code-worker/workflows.py`
  Already has Gemini CLI execution, which is separate from Gemma model hosting.

## Recommendation

Implement Gemma 4 in two tracks:

### Track A — Hosted Gemma 4 via Google APIs

Use the existing `google` provider and `gemini_llm` credential path to add Gemma 4 as a selectable model family in the LLM registry and tenant configuration APIs.

This is the fastest path because it avoids new infra and fits the repo's existing model selection layer.

### Track B — Optional Self-Hosted Gemma 4 Runtime

Add an explicit self-hosted open-model runtime only if one of these is true:

- you need fixed-cost inference at scale
- you need tenant-isolated open weights
- you need on-device or edge execution experiments

This should be a separate runtime service, not bolted onto the current CLI platform abstraction.

## Non-Goals

- Do not add `gemma_cli`
- Do not replace `gemini_cli`
- Do not route normal chat traffic directly to Gemma 4 before evaluation
- Do not assume native function-calling parity with Claude/Codex-style agent runtimes

## Phase 1 — Hosted Gemma 4 Registry Support

### Files

- Modify `apps/api/app/db/init_db.py`
- Modify `apps/api/app/db/seed_llm_data.py`
- Modify `apps/api/app/schemas/llm_model.py` only if capability metadata needs expansion
- Modify `apps/web/src/pages/LLMSettingsPage.js` if the UI filters or labels specific model families

### Tasks

1. Add Gemma 4 model entries to the seeded Google model catalog.
2. Mark capabilities conservatively:
   - `function_calling: true` only if the app-side prompt/parse wrapper is used
   - `vision: true` only for Gemma 4 variants that actually support multimodal input in the chosen serving path
   - `edge_optimized: true` for `E4B` and `E2B`
3. Keep pricing nullable or flagged as estimated until the serving path is finalized.
4. Ensure `/api/v1/llm/models` returns Gemma 4 records for provider `google`.

### Verification

- API returns Gemma 4 rows from `/api/v1/llm/models?provider_name=google`
- Tenant default configs can select a Gemma 4 model without schema changes

## Phase 2 — Gemma Tool-Use Wrapper

Gemma's documented function-calling pattern is text-schema driven, so we need an adapter before using it for tool-enabled agent flows.

### Files

- Create `apps/api/app/services/llm/gemma_adapter.py`
- Modify `apps/api/app/services/llm/router.py`
- Modify whichever request execution layer actually calls the selected `LLMModel`
- Add tests under `apps/api/tests/`

### Tasks

1. Create a Gemma-specific prompt builder that injects:
   - allowed tool schema
   - exact output contract
   - no-extra-text requirement when emitting tool calls
2. Create a parser that:
   - accepts JSON or explicit function-call text patterns
   - rejects malformed outputs
   - never executes a tool call without validation
3. Put the adapter behind model capability detection rather than hard-coding provider name checks everywhere.

### Verification

- Unit tests cover valid tool call, malformed tool call, and plain-text answer fallback
- Tool execution remains application-owned, never model-owned

## Phase 3 — Local or Dedicated Runtime

Only start this after hosted Gemma 4 passes evaluation.

### Files

- Create `apps/gemma-runtime/` or `apps/open-model-runtime/`
- Add service config in `docker-compose.yml`
- Add internal API route under `apps/api/app/api/v1/internal/` for runtime health and inference
- Wire any new client in the API service layer, not in CLI worker code

### Tasks

1. Stand up a dedicated serving runtime for Gemma 4.
2. Add internal auth with `X-Internal-Key`.
3. Define explicit supported modes:
   - text generation
   - structured tool-call generation
   - optional multimodal inference
4. Add model health probes, timeout policy, and concurrency limits.
5. Gate rollout behind tenant feature flags.

### Verification

- Internal health endpoint returns model readiness
- API can route a controlled subset of tasks to the runtime
- Fallback to existing providers remains intact

## Phase 4 — Routing and Evaluation

Do not route broad user traffic to Gemma 4 until it is scored against current baselines.

### Files

- Modify `apps/api/app/services/agent_router.py`
- Modify `apps/api/app/services/rl_routing.py`
- Modify `apps/api/app/services/auto_quality_scorer.py`
- Add or extend tests in `apps/api/tests/`

### Tasks

1. Add Gemma 4 to the candidate model set for non-CLI LLM routing only.
2. Start with narrow task classes:
   - structured extraction
   - summarization
   - cost-sensitive analysis
3. Track quality, latency, and tool-call validity separately from existing CLI platform rewards.
4. Keep code-agent and PR-writing paths on CLI platforms until Gemma 4 proves strong enough in evals.

### Verification

- RL logs distinguish model-level performance from CLI platform performance
- Routing can disable Gemma 4 instantly without schema rollback

## Suggested Sequence

1. Refresh the Google model registry with Gemma 4 entries.
2. Add the Gemma adapter for structured tool-use.
3. Build evaluation tests and shadow traffic measurement.
4. Only then decide whether a dedicated self-hosted runtime is worth operating.

## Risks

- The current seeded Google model catalog is stale, so model metadata can drift quickly.
- Gemma function calling is not natively tool-tokenized, which raises parsing and safety risk.
- The repo's primary chat path is still CLI-routed; forcing Gemma 4 into that layer would create unnecessary platform confusion.
- Local runtime support may require different infra choices than the current Ollama-based fallback stack.

## Success Criteria

- Gemma 4 appears as a first-class selectable model in the LLM registry.
- Tool-enabled Gemma responses are validated before execution.
- Gemma 4 is evaluated as a model family, not conflated with CLI platforms.
- Rollout can be enabled per tenant and reversed without breaking existing Claude, Codex, or Gemini CLI flows.

## References

- Google AI for Developers: Gemma on Gemini API
- Google AI for Developers: Function calling with Gemma
- Android Developers Blog, 2026-04-02: Gemma 4 in AI Core Developer Preview
