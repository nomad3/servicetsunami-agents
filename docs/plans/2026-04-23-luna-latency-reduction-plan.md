# Luna Latency Reduction — Benchmark v4 + Reduction Roadmap

**Date:** 2026-04-23
**Trigger:** aremko user reporting Luna feels slow on WhatsApp
**Owner:** unassigned
**Stack baseline:** docker-compose, Phase 2 dual-read (`USE_MEMORY_V2=true`), Rust embedding-service + memory-core, native Ollama (Gemma 4) for scoring

---

## Why now

Last benchmark snapshots:
- **2026-04-04 (Luna v3):** 30 prompts, p50 ~80s on OpenCode/light, ~75s on Claude Code, $1.25 total. WhatsApp-style turns dominated by CLI orchestration overhead.
- **2026-04-10 (post-K8s, Phase 2):** 4-prompt smoke, p50 ~5.5s, fast-path 5.2s, heavy recall 6.1s. **88% improvement** over pre-Phase-1 47s baseline.

Six weeks later, aremko reports it feels slow again. We don't know:
- Whether p50 has regressed since the April 10 measurement
- Whether the regression is in CLI orchestration, MCP tool calls, memory recall, or post-chat dispatch
- Whether "slow" is steady-state or only on tool-using turns
- Whether we've drifted away from the configured CLI tier (e.g. always landing on `full`)

Two issues already surfaced during recon (independent of latency):

1. **`orchestration-worker` crashes post-chat ingest** — `TypeError: 'KnowledgeEntity' object is not subscriptable` at `app/memory/ingest.py:64`. PostChatMemoryWorkflow events failing → no entities/commitments are being recorded from chat. Doesn't affect user latency directly, but affects the memory-first promise.
2. **Embedding-service intent-init race on cold boot** — API came up before `embedding-service` was ready, `Failed to initialize intent embeddings`. Intent classifier runs in degraded mode (no semantic, only keyword). When intent classification falls back, the router is more likely to select the `full` tier → more cost, more latency.

Both warrant a fix in this initiative or a follow-up.

---

## Goals

1. **Reproducible benchmark** that any engineer can run from a clean shell against any tenant.
2. **Stage-level breakdown** of where latency is spent (recall / routing / LLM / tool / post-chat) — not just total wall time.
3. **Per-CLI matrix**: Gemini CLI, Claude Code, Codex, OpenCode (local Gemma 4).
4. **With-tools vs without-tools** comparison so we can isolate MCP overhead from raw LLM latency.
5. **Concrete reduction plan** ranked by `expected ms saved / engineering hours`.

Non-goals:
- Streaming / token-level latency (the user complaint is end-to-end response time, not first-token).
- Cost optimization (covered separately by tier routing).
- WhatsApp transport latency (neonize / Cloudflare tunnel) — measured but not optimized in this pass.

---

## Phase A — Instrument

Before benchmarking, add (or surface) the timing breakdown the chat path **almost has** but doesn't expose end-to-end. Today only `duration_ms` (total) is persisted on `execution_traces`.

### A.1 — Stage timer in `cli_session_manager.generate_response`

Wrap each stage with `time.monotonic()` and emit a single structured log line + persist into `execution_traces.details.timings` JSONB:

```json
{
  "recall_ms": 87,
  "router_ms": 12,
  "credentials_ms": 4,
  "claude_md_build_ms": 31,
  "cli_spawn_ms": 1850,
  "cli_first_byte_ms": 4120,
  "cli_total_ms": 4980,
  "post_dispatch_ms": 9,
  "total_ms": 5022
}
```

Where:
- `recall_ms` = `build_memory_context_with_git`
- `router_ms` = `agent_router.route_and_execute` decision time (excluding CLI run)
- `credentials_ms` = `_get_cli_platform_credentials` + vault decrypt
- `claude_md_build_ms` = persona + memory context render time
- `cli_spawn_ms` = subprocess start until first stderr byte
- `cli_first_byte_ms` = subprocess start until first stdout byte
- `cli_total_ms` = subprocess wall time
- `post_dispatch_ms` = `dispatch_post_chat_memory` + auto-quality scorer trigger

Files to touch:
- `apps/api/app/services/cli_session_manager.py` — instrument `generate_response`
- `apps/api/app/services/chat.py:438-465` — extend ExecutionTrace `details.timings` write
- `apps/api/app/api/v1/execution_traces.py` (or equivalent) — surface timings in the trace endpoint

### A.2 — Surface timings in the API

Add `GET /api/v1/execution-traces/recent?tenant_id=...&limit=50&channel=whatsapp` returning the last 50 traces with timings. Frontend doesn't need a UI in this pass — the benchmark script will hit this directly.

### A.3 — Fix the bug & race that already surfaced

These block accurate measurement, so they go in Phase A:

- **Fix `app/memory/ingest.py:64`** — `prop["name"]` should be `prop.name` (or vice versa). Whichever side is wrong, post-chat ingest is silently throwing — fix the type contract.
- **Fix embedding-service cold-start race** — `docker-compose.yml` should add `condition: service_healthy` on `api.depends_on.embedding-service` (and `memory-core.depends_on.embedding-service`). Same fix called out in the prior memory note.

---

## Phase B — Benchmark script

`scripts/benchmark_luna.py` — single Python script, no external deps beyond `requests` + the project venv.

### B.1 — Parameters

```
python scripts/benchmark_luna.py \
  --tenant <uuid>              # required, defaults to AREMKO_TENANT_ID env var
  --token <jwt>                # required, login once to get
  --base-url http://localhost:8000
  --runs 3                     # repeat each (cli, prompt) cell N times
  --warmup 1                   # discard first N runs per cell
  --clis claude_code,gemini_cli,codex,opencode
  --output benchmarks/2026-04-23-luna-v4.json
  --baseline docs/plans/2026-04-04-luna-benchmark-results.md   # for delta column
```

For each run the script:
1. Creates a fresh chat session bound to the tenant's primary agent.
2. Sends each prompt via `POST /api/v1/chat/sessions/{id}/messages`.
3. Waits for the assistant message, then fetches the matching `execution_trace` to read the timings JSON.
4. Tags the row with `(cli, prompt_class, with_tools, run_idx, cold_or_warm)`.

### B.2 — Prompt matrix

5 prompt classes × 4 CLIs × {cold, warm} × 3 runs = **120 cells**.

| Class | Sample prompt (ES, since aremko is Spanish) | Tools expected |
|-------|---------------------------------------------|----------------|
| `greeting` | "hola luna" | none |
| `light_recall` | "qué citas tengo hoy?" | calendar (read) |
| `entity_recall` | "qué sabes de Carolina Vega?" | knowledge graph (read) |
| `tool_call_read` | "muestrame mis últimos 5 emails" | gmail (read) |
| `tool_call_write` | "agenda una llamada con Pedro mañana 10am" | calendar (write) |

Two of the five exercise tools and two don't — that's the with/without-tools split the user asked for.

### B.3 — Output

Three artifacts per run:

1. **`benchmarks/<date>-luna-v4.json`** — raw rows (CLI, prompt, run_idx, all timings, tokens, cost, error).
2. **`benchmarks/<date>-luna-v4.md`** — summary table modeled on the v3 doc, with **stage breakdown columns** (recall / cli_spawn / cli_first_byte / cli_total / post_dispatch / total).
3. **`benchmarks/<date>-luna-v4-deltas.md`** — per-cell delta vs. v3 baseline (where the prompt matches), to spot regressions.

### B.4 — Run discipline

- Run between 22:00–02:00 local to avoid contention with live aremko WhatsApp traffic.
- Disable auto-quality scoring during measurement (`QUALITY_MODEL=` empty, set via env) to avoid Ollama scheduler contention biasing CLI subprocess wall time.
- Re-enable scoring for one final 10-prompt run to measure its drag.

---

## Phase C — Likely findings (hypothesis register)

We're hypothesis-testing, not blindly optimizing. Each hypothesis below is what we expect to *learn* from the benchmark; the optimizations in Phase D are conditional on these landing.

| # | Hypothesis | If true, expected to dominate | Disproven by |
|---|-----------|------------------------------|--------------|
| H1 | CLI subprocess spawn is 1.5–2s flat overhead per turn | `cli_spawn_ms > 1500` consistent across CLIs | All CLIs show <500ms spawn |
| H2 | Gemini CLI is fastest end-to-end on tool-using turns | gemini_cli `total_ms` < claude_code by >25% on `tool_call_*` | Claude Code matches or beats |
| H3 | OpenCode + local Gemma 4 is fastest on greeting/light recall | opencode `total_ms` < cloud CLIs by >40% on `greeting` | Local is slower (GPU contention?) |
| H4 | MCP tool roundtrips add 800–1500ms each | `tool_call_*` − `light_recall` ≈ 1s+ on same CLI | Difference <300ms |
| H5 | CLAUDE.md render is non-trivial (>200ms) | `claude_md_build_ms > 200` | <50ms |
| H6 | Memory recall is already well-optimized | `recall_ms < 250` p95 | Recall blows past 500ms |
| H7 | Cold runs are >2× slower than warm (CLI MCP server reconnect) | first-run `total_ms` ≥ 2 × warm-run | <30% gap |

The shape of the answer determines which Phase D items to fund.

---

## Phase D — Reduction actions, ranked by expected impact

Each item is `[expected ms saved] · [eng hours] · [risk]`. Numbers are estimates pre-benchmark; revise after Phase B data lands.

### Tier 1 — Highest leverage, low risk

1. **CLI process pool / warm CLI workers** — `[~1500ms · 16h · medium]`
   Today every chat turn spawns a fresh `claude` / `gemini` subprocess. Pool 3–5 warm CLI workers per tenant per platform; route turns to an idle worker; restart on persona/memory-domain change. Already partially designed under "Phase 3a warm chat-runtime pods" (referenced in the April 10 baseline doc as the path to <2s p50). H1 quantifies the prize.

2. **Local fast-path bypass for greetings** — `[~3500ms · 6h · low]`
   `agent_router` already classifies intent. Add a `greeting` intent that short-circuits to a deterministic Spanish/English template per tenant persona — never spawn a CLI. Catches "hola", "gracias", "ok", "👍" turns. Roughly 25% of aremko WhatsApp volume by inspection of recent message logs.

3. **Pre-warmed memory context per session** — `[~200ms · 8h · low]`
   Today recall runs synchronously inside the request. Move recall to fire on `POST /messages` ingress before persona branching, in parallel with ExecutionTrace insert. Saves the recall hop from the critical path.

### Tier 2 — Meaningful, medium effort

4. **Reduce CLAUDE.md size for cached personas** — `[~400ms · 4h · low]`
   The persona prompt + memory context can run 8–12K tokens. Gemini CLI re-parses it every spawn. Cache the rendered persona section (it changes only on agent edit) and concat the per-turn memory delta. H5 confirms.

5. **Fix orchestration-worker ingest bug** — `[~0ms · 2h · low]`
   No latency win — but PostChatMemoryWorkflow currently dies after every chat. That means entities aren't being recorded → next-turn recall has less to work with → recall accuracy degrades over time. Indirect latency cost: more retries, larger CLAUDE.md scratch, lower hit rate.

6. **Embedding-service cold-start race fix** — `[indirect · 1h · low]`
   `condition: service_healthy` on the API depends_on. Stops the intent classifier degrading silently after every restart.

### Tier 3 — Speculative until benchmark lands

7. **Skip MCP tool init on no-tool turns** — `[~500ms · 6h · medium]`
   If the router decided no tools are needed, pass `--no-mcp` (or equivalent) to the CLI subprocess. Saves the MCP SSE handshake. H4 quantifies.

8. **Switch default tier to `light` for short-message turns** — `[~1000ms · 2h · low]`
   `tier_selection` RL is already in place. Tighten the threshold for messages <12 words. Risk: light tier may degrade response quality — measure with the existing rubric.

9. **gRPC-only memory recall for hot path** — `[~50–100ms · 12h · medium]`
   Phase 2 cutover is still dual-read (Python primary, Rust shadow). Cut over to Rust-only for `recall()`. Already designed in the Phase 2 cutover doc. Latency win is small; the real value is reducing dual-call CPU.

10. **WhatsApp typing-indicator pre-emptive send** — `[perceived ~2000ms · 3h · low]`
    Already exists, but verify it fires within 200ms of message receipt (not after recall completes). Doesn't reduce real latency — reduces perceived latency, which is what aremko's user is actually complaining about.

---

## Execution order

| Step | Phase | Owner | Depends on |
|------|-------|-------|-----------|
| Fix ingest bug + cold-start race | A.3 | unassigned | — |
| Add stage timer + persist timings | A.1 + A.2 | unassigned | — |
| Build benchmark script | B | unassigned | A.1 + A.2 |
| Run baseline benchmark (120 cells) | B.4 | unassigned | B |
| Publish results doc | B.3 | unassigned | benchmark run |
| Pick Tier-1 actions to fund | D | user | results doc |
| Implement chosen Tier-1 actions | D | unassigned | results |
| Re-benchmark, compute deltas | B (re-run) | unassigned | implementations |

Total estimated calendar time, end-to-end: **3–5 working days**, assuming one engineer, no scope creep into Tier 2/3 in v1.

---

## Out of scope (call out so we don't drift)

- Refactoring `cli_session_manager.py` (890 LOC, candidate for split) — out of scope, latency-only.
- Replacing the CLI orchestrator with direct API calls — major architectural shift, separate decision.
- Streaming responses to WhatsApp — neonize doesn't surface streaming cleanly; non-trivial.
- Switching tenants off CLI orchestration entirely — only if Tier-1 fails to land us under 8s p50.

---

## Success criteria (re-benchmark target)

After Tier-1 lands and re-benchmark runs:

| Metric | April-10 baseline | April-23 hypothesized current | Target |
|--------|-------------------|------------------------------|--------|
| Greeting p50 | 5.2s | unknown | **<1s** (template path) |
| Light recall p50 | 6.1s | unknown | **<4s** |
| Tool-call read p50 | unmeasured | unknown | **<6s** |
| Tool-call write p50 | unmeasured | unknown | **<8s** |
| Failure rate | 0/4 | unknown | **<2%** |

If we hit these, we close. If we don't, Phase D Tier 2 opens.
