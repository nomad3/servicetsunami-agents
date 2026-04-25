# Luna Hallucination Reduction — Investigation + Plan

**Date:** 2026-04-25
**Triggers:**
- aremko owner Jorge: "muestras precios y nombres de cabañas que no existen. Porqué alucinas?" — direct user-reported hallucination on bookings.
- AgentProvision (Simon): Gemini CLI calling tool names that don't exist (`default_api:list_connected_email_accounts`, `list_dynamic_workflows` bare, `read_file` against missing paths). Caused subprocess wedges and one workflow heartbeat-timeout we already triaged.

Two categories of hallucination, two different root-cause families. Treating them separately.

---

## Category A — Tool-name hallucinations (structural, all tenants)

### Evidence

`docker compose logs code-worker` shows during real chat turns:

```
Error executing tool default_api:list_connected_email_accounts: Tool not found.
  Did you mean one of: "mcp_agentprovision_list_connected_email_accounts",
  "mcp_agentprovision_list_connected_devices", ...

Error executing tool list_dynamic_workflows: Tool not found.
  Did you mean one of: "list_directory", "activate_skill",
  "list_background_processes"?

Error executing tool read_file: File not found.
```

### Why it happens

- The MCP server is registered with key `agentprovision` (`cli_session_manager.py:387`).
- Gemini CLI namespaces those tools as `agentprovision__<tool>` (double underscore).
- Claude Code namespaces them as `mcp__agentprovision__<tool>`.
- **CLAUDE.md** (the persona prompt at `cli_session_manager.py:95-97`) tells Luna both prefix conventions and instructs "if a tool described as `foo_bar` fails with `not found`, retry with `agentprovision__foo_bar` then `mcp__agentprovision__foo_bar`".
- Reality: Gemini-2.5-pro disregards the retry instruction. When it doesn't find a tool by the bare name, it sometimes guesses `default_api:<tool>` (a Claude-internal namespace it picked up from training data, irrelevant here), or falls back to gemini-cli's built-in filesystem tools (`read_file`, `list_directory`).
- The persona text "may omit the server prefix for readability" is the original sin: it tells the model the prefix is optional, then asks the model to figure out the right form. Models are bad at this.
- `tool_groups` filtering passes `allowed_tools` to the CLI, but the names there are also bare. The CLI's tool registry itself is the source of truth, but Luna can't see that programmatically.

### Reduction options (ranked)

| # | Action | Expected reduction in tool-name errors | Effort | Risk |
|---|--------|----------------------------------------|--------|------|
| **A1** | **Render fully-qualified tool names directly into CLAUDE.md.** Stop teaching prefix rules and instead inject an explicit "Available tools:" block with the exact `agentprovision__foo` (or `mcp__agentprovision__foo`) names per platform, generated from the MCP registry at session-build time. Drop the prefix-explainer text. | **70-90%** | low | **2-4 h** |
| A2 | Add a rejection guard: when Gemini returns "Tool not found", post-process to detect the bare-name case and synthetically retry with the server-prefixed name once before returning to the model. Activity-side, not model-side. | 40-60% | medium | ~1d |
| A3 | Rename the MCP server key from `agentprovision` to `tools` (shorter, less likely to collide with model-internal names like `default_api`). Cosmetic but reduces some confusions. | 10-20% | low | <1h |
| A4 | Tighten `allowed_tools` filtering and inject the resolved list into the prompt — gives the model a closed-world tool inventory instead of an open-ended namespace. | 20-40%, complementary to A1 | medium | ~1d |

**Recommend A1 first** — it removes the entire class of "what's the prefix" guessing. The CLI already knows the right name; the model just needs to read it.

---

## Category B — Data hallucinations (aremko bookings)

### Evidence

Real chat turn from aremko production, 2026-04-17 21:27:27 UTC, session bound to aremko (`73583e84-…`):

> User: "Si veamos alojamiento"
> Luna: "Para el domingo 3 de mayo, tengo estas cabañas disponibles para dos personas:
>   * **Cabaña Arrayán** ($80.000)
>   * **Cabaña Avellano** ($80.000)
>   * **Cabaña Coigüe** ($80.000)
>   * **Cabaña Mañío** ($80.000) …"

The actual aremko cabin catalog (per `apps/mcp-server/src/mcp_tools/aremko.py:11`):

> Arrayan(9), Laurel(8), Tepa(7), Torre(3), Acantilado(6) — five cabins.

**Hallucinated entries**: Avellano, Coigüe, Mañío — none exist. Prices were also fabricated. Jorge replied: *"Esos valores de donde los sacaste? Y los nombres?"*

The very next turn at 21:28:12, Luna apologized: *"Te pido una disculpa, me equivoqué con los nombres y valores en el mensaje anterior."* — but the damage was already shown to the customer.

### Why it happens

1. **Tool was not called.** `chat_messages.context.tools_called` is null/empty for that turn — but we cannot prove that, because **`tools_called` is never captured anywhere**. `cli_session_manager.py` and `code-worker/workflows.py` don't record CLI tool invocations into the chat row's context dict. We have a complete observability hole. Whether the model called `check_aremko_availability` or just made things up is currently unknowable from chat data alone.
2. **The aremko knowledge graph is empty for products.** Querying `knowledge_entities WHERE tenant_id=aremko`:
   - 5 persons, 1 customer, 1 organization, 1 reservation, 1 event.
   - **Zero `service`, `product`, `cabaña`, `tinaja`, `masaje`, `desayuno` entities.**
   So the memory-first recall path that pre-loads "Relevant Entities" into CLAUDE.md gives Luna nothing to anchor a booking question on. She must reach for the MCP tool — and if she doesn't, she invents.
3. **The persona-prompt safety rail is weak.** CLAUDE.md does say *"NEVER present a guess as a fact"* (`cli_session_manager.py:129`) but the language is generic. It does not specifically forbid "listing services / prices / availability without a tool call". It also doesn't tell Luna which tools must be called for which question types — there's no pattern like "for any question about cabin/tinaja/masaje availability you MUST call `check_aremko_availability` before answering."
4. **Self-attestation problem.** Luna's response at 21:25:56 starts *"Siguiendo nuestra regla de oro, revisé la disponibilidad…"* — i.e. she claims she checked. But we have no log that she did, and the catalog she returned (Llaima, Villarrica with hidromasaje at 14:00/16:30) is at least plausibly real. So she's mixing real-tool-output and fabricated-data within the same response.
5. **No post-response guard.** When Luna's response contains specific entity names not in the tenant's catalog, nothing flags or re-prompts before the message hits the user.

### Reduction options (ranked)

| # | Action | Expected reduction | Effort | Risk |
|---|--------|--------------------|--------|------|
| **B1** | **Capture `tools_called` per turn**, log the structured tool calls into `chat_messages.context.tools_called` and into a new `tool_calls` table for the audit trail. Without this we can't measure anything. | enables measurement | low | **3-4 h** |
| **B2** | **Seed aremko's knowledge graph from the catalog defined in `aremko.py`.** Create entity rows for each cabin (Arrayan, Laurel, Tepa, Torre, Acantilado), each tinaja (Hornopiren, Tronador, Osorno, Calbuco, Hidromasaje Puntiagudo, Llaima, Villarrica, Puyehue), the masaje (53), the desayuno (26). Memory recall now pre-loads them into CLAUDE.md so Luna can answer simple questions ("which cabins do you have?") without a tool call **and** has them as anchors that block invented names. | **40-60%** of name-fabrication cases | medium (data + idempotent migration) | **4-6 h** |
| **B3** | **Tenant-specific tool-required policy in CLAUDE.md.** For aremko, append a hard rule: "For any question about cabin / tinaja / masaje / desayuno availability, names, prices, or booking — you MUST call `check_aremko_availability` or `get_aremko_full_availability` first. NEVER list services or prices without a successful tool call. If the tool fails, say so explicitly; do not make up alternatives." Inject from a per-tenant `tool_use_policy` field. | **40-70%** of fabrication on Luna's side | low | **2-3 h** |
| **B4** | **Post-response entity guard.** After CLI returns, scan the response text for entity-shaped names (cabaña, tinaja, masaje proper nouns). Cross-reference against the tenant's registered catalog. If any name in the response doesn't appear in the catalog, retry the turn once with an explicit *"the following names you mentioned do not exist in our catalog: X, Y. Use only these: ... Try again."* | **70-90%** of remaining fabrications | high (tenant-specific, fragile pattern-matching) | **~1d** |
| **B5** | **Switch model tier for booking turns.** Route any aremko intent matching `bookings` keywords to `full` tier (Gemini 2.5 Pro instead of any lighter model). Already roughly the case via the intent classifier, but verify the routing doesn't degrade for short messages. | small but cheap to verify | <1h | low |
| **B6** | **Rubric-based RL flag for invented entities.** When auto-quality scorer detects "response mentions specific names/prices and tools_called is empty", down-weight reward sharply. Already partly in `auto_quality_scorer.py` but the dimension isn't explicitly wired to "ungrounded specifics". | long-term (RL learns over weeks) | medium | ~1d |

**Recommend B1 + B2 + B3 together.** B1 unblocks measurement, B2 closes the catalog gap, B3 closes the prompt-discipline gap. Collectively expected to remove ~80% of cabin/tinaja name fabrications. Defer B4/B6 until we measure post-B3 residuals.

---

## Cross-cutting observations

- **No tool-call observability.** This is a P0 instrumentation gap. We've been operating Luna in production for months with no per-turn record of which MCP tool was actually called. Hallucinations like aremko's are invisible to dashboards, RL, and post-mortems. B1 above also unblocks Phase A.1 of the latency plan (`docs/plans/2026-04-23-luna-latency-reduction-plan.md`) — same instrumentation point.
- **Aremko has good MCP tools but no knowledge-graph reflection of its own product catalog.** The data is in `aremko.py` as comments and in the live `aremko.cl` API but it never made it into the knowledge graph the agent recalls from. Memory-first design assumes the catalog is in the graph — that assumption is broken for this tenant.
- **CLAUDE.md is doing too much teaching, not enough constraining.** It explains prefix conventions, identity, conversation priority, uncertainty signaling — and the model selectively obeys. The parts that matter most for hallucination prevention (tool-required policies, fail-loud-on-missing-data) are weakest. Reverse the ratio.

---

## Recommended execution order

1. **A1 + B1 in one PR** — both are low-risk prompt/instrumentation changes; both touch `cli_session_manager.py`. Together they fix tool-name guessing AND give us tool-call observability. (~6 h)
2. **B2** — aremko catalog backfill. Idempotent migration that creates the missing entities. Tenant-scoped, low blast radius. (~4 h)
3. **B3** — per-tenant `tool_use_policy` field; aremko gets a bookings policy first. Other tenants opt in later. (~3 h)
4. Re-measure: scan one week of aremko chat turns; count hallucinated names against the catalog. Compare pre/post.
5. If fabrication rate is still >5%, fund B4 (post-response guard). Else stop.

Estimated total to first delivery: **~2 working days**.

## Out of scope here (mentioned for completeness)

- Switching to a stricter model (Claude Sonnet 4.6 instead of Gemini 2.5 Pro) — separate discussion; carries cost and migration tail.
- Fine-tuning. Not warranted; the data is the cure, not weights.
- Eliminating the CLI orchestrator. Architectural shift; separate plan.

## Telemetry to ship alongside

- New table `tool_calls` (tenant_id, session_id, message_id, tool_name, arguments_json, result_status, duration_ms, started_at). Populated by activity wrapper around the CLI subprocess output parser.
- New view `vw_ungrounded_responses` joining assistant `chat_messages` with the count of `tool_calls` for that turn, surfacing turns where response > N chars and tool_calls = 0. This is the dashboard for "how often is Luna talking without checking."
