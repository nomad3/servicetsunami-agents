# Phase 0 baseline — subprocess→MCP agent_token propagation

**Date:** 2026-05-25
**Phase:** 0 (verification + measurement — no code change)
**Companion to:** [`2026-05-25-subprocess-mcp-agent-token-propagation.md`](./2026-05-25-subprocess-mcp-agent-token-propagation.md)
**Status:** Complete

Measurement captured at the start of Monday 2026-05-25's work-day, before any Phase 1 code change. Phase 3 will re-baseline the same metrics post-fix to verify the drop.

---

## §1 — Mint surface (verified)

`apps/api/app/services/cli_session_manager.py:1475` calls `mint_agent_token(...)` unconditionally for chat dispatches that resolve to a real Agent row. Confirmed against current main. No conditional gates remain post-PR #692. The token is passed downstream into `generate_mcp_config(..., agent_token=...)` at line ~1522.

Conditional bypass: if `agent_slug` doesn't resolve to an Agent row, the dispatch falls through to `agent_token=None` and `internal_key` auth (see lines 1483-1495). Logged loudly per P0a §4.1.

## §2 — Generator embeds the Authorization header (verified)

`generate_mcp_config` at `cli_session_manager.py:586-639` already adds `Authorization: Bearer <agent_token>` to the per-server `headers` dict when a token is provided (lines 622-626). The dict ships into the `mcpServers.agentprovision.headers` key in the MCP JSON config.

This is a stronger starting state than the plan assumed. The mint→config-dict leg is already complete. The remaining work is **transport of those headers** through each CLI's MCP-config writer.

## §3 — Per-CLI propagation audit (this is the actual gap surface)

| CLI | Config file format | Where mcp_config is written | Headers preserved? | Notes |
|---|---|---|---|---|
| **codex** | TOML in `~/.codex/config.toml` | `_prepare_codex_home` + `_codex_mcp_config_lines` in `apps/code-worker/workflows.py:1452, 1511` | **Yes** — emits `http_headers = {...}` inline-table (line 1574-1576) | Codex CLI uses rmcp client (gated by `experimental_use_rmcp_client = true`). Unknown whether rmcp actually sends `http_headers` on the wire — must verify in Phase 1 |
| **claude_code** | JSON at `<session>/mcp.json` | `apps/code-worker/cli_executors/claude.py:76-78` writes the mcp_config string verbatim | **Yes** — verbatim JSON includes the headers as-shaped by the generator | Claude Code is documented to read `mcpServers.*.headers` directly. Should work |
| **gemini_cli** | JSON inside `.gemini/` settings | `_prepare_gemini_home` at `workflows.py:1678` — body NOT yet audited for MCP-config preservation | Unknown | Verify in Phase 1 |
| **copilot_cli** | n/a (zero hits for `mcp_config`) | `apps/code-worker/cli_executors/copilot.py` doesn't reference mcp_config | **No** — copilot doesn't get an MCP config at all today | Either copilot doesn't support MCP yet in this codebase, or its MCP comes from a different path |
| **opencode** | Static container-wide config | `apps/code-worker/entrypoint.sh:59-87` writes `~/.config/opencode/opencode.json` at **container startup**, NOT per-dispatch | **No** — uses container-wide config, can't carry a per-dispatch token | This is the structural gap for opencode. Needs a different fix shape (re-write the config per-dispatch, OR opencode-server-side env-var injection) |

**Implication:** the plan's §5.2 table needs updating — `copilot_cli` and `opencode` are NOT just "verify shape" cases; they're "needs new architecture" cases. The codex + claude_code paths may already work end-to-end (Phase 1 testing will confirm).

## §4 — Hard-gated tools inventory

Searched `apps/mcp-server/src/mcp_tools/` for `tier != "agent_token"`. Two hits, both in `agents.py`:

| Line | Function | Behavior |
|---|---|---|
| 76 | `dispatch_agent` | Hard-refuses if `auth.tier != "agent_token"` with `PERMISSION_DENIED` |
| 153 | (verify which function) | Same gate pattern |

Other tier-related code in mcp-server (`tool_audit.py`) operates in shadow mode — logs `SHADOW tier-denial` but does not refuse the call. The shadow gate flips to enforce when `enforce_strict_tool_scope=TRUE` on `tenant_features` (P0a Fix B; per-tenant rollout).

**Net:** the only **hard refusal** path today is `dispatch_agent` (and its sibling at agents.py:153). Everything else is shadow-only. That refines Phase 1's exit criterion: a `dispatch_agent` call from a codex chat dispatch must succeed.

## §5 — Tier-denial baseline (Simon's tenant, last 24h)

From `docker compose logs mcp-tools --since 24h | grep "tenant=752626d9"`:

| Tool | SHADOW tier-denial count |
|---|---|
| `create_calendar_event` | 16 |
| `list_calendar_events` | 1 |
| **Total** | **17** |

**Important framing:** these are SHADOW denials only — calendar tools have **no hard gate**, so Luna's calendar writes DID actually go through (the May 24 evening calendar-fleet block succeeded). The denials would only become real refusals if `enforce_strict_tool_scope=TRUE` flipped for the tenant.

The **actual** hard-refusal pain Luna reported (the `dispatch_agent` "live MCP agent registry is still rejecting this chat session as tier=anonymous" line in her 2026-05-24 evening status) does NOT appear in this baseline because `dispatch_agent` rejects with `PERMISSION_DENIED` in-band rather than emitting the SHADOW tier-denial log line. We will need a separate counter (instrument in Phase 1) for that.

## §6 — Platform-wide baseline (all tenants, last 24h, top 20 tools)

For sanity:

| Tool | Count |
|---|---|
| `list_calendar_events` | 337 |
| `search_emails` | 304 |
| `list_competitors` | 285 |
| `call_mcp_tool` | 284 |
| `start_autonomous_learning` | 134 |
| `get_competitor_report` | 69 |
| `web_search` | 66 |
| `record_observation` | 53 |
| `find_entities` | 26 |
| `check_autonomous_learning_status` | 26 |
| `recall_memory` | 24 |
| `compare_campaigns` | 19 |
| `list_skills` | 17 |
| `create_calendar_event` | 16 |
| `search_knowledge` | 15 |
| `query_sql` | 15 |
| `get_skill_gaps` | 12 |
| `fetch_url` | 12 |
| `list_connected_email_accounts` | 10 |
| `get_proactive_actions` | 9 |

This is the universe of tools that will benefit when agent_token propagation is in place + `enforce_strict_tool_scope` flips per-tenant. The current platform-wide rate of ~2000 SHADOW denials in 24h is the headroom for Phase 4's enforce-ramp.

## §7 — Refined picture for Phase 1

The plan's original Phase 1 plan (codex prototype) is still right but the substance is different than initially assumed:

1. **codex CLI:** verify the `http_headers` TOML field actually reaches mcp-tools as an `Authorization` HTTP header. If yes (likely), then `dispatch_agent` calls from codex SHOULD already work and the bug is elsewhere (token format? mcp_auth parser?). If no, the fix is upstream in codex or our TOML emission.
2. **claude_code:** likely already works end-to-end. Confirm with a `dispatch_agent` test.
3. **opencode:** needs structural fix — its MCP config is static per-container, not per-dispatch.
4. **copilot_cli:** no MCP path today; out of scope until/unless copilot gets MCP support.
5. **gemini_cli:** audit `_prepare_gemini_home` for MCP-config preservation before scoping.

## §8 — Recommended next action

Phase 1 should split into two prototypes:

- **Phase 1a — codex live-wire test:** dispatch a `dispatch_agent` call through codex and pcap or log-instrument the inbound HTTP request at `mcp-tools` to see if `Authorization` arrives. If yes, the issue is in `mcp_auth.resolve_auth_context`'s parser. If no, the gap is in codex/rmcp's `http_headers` emission.
- **Phase 1b — claude_code live-wire test:** same probe against claude_code's MCP client. Likely fastest path to a successful end-to-end because the JSON config is written verbatim.

opencode and gemini move into Phase 2 with their own approach (opencode = per-dispatch config rewrite; gemini = audit + match codex/claude pattern). copilot stays out of scope.

## §9 — Open questions for Phase 1

1. Does codex's rmcp client actually transmit `http_headers` from TOML on the wire? (Likely yes per docs but not verified.)
2. Does `mcp_auth.resolve_auth_context` actually accept the Bearer token format we mint? (Spec says yes; code says yes; not verified end-to-end with a chat-driven token.)
3. Token lifetime: what's the TTL on the agent_token JWT? Does it survive a multi-minute codex run?
4. If the token IS arriving but tier evaluates to `anonymous`, what tier does mcp_auth assign and why? (Need to instrument the resolver.)

---

This baseline closes Phase 0 of the parent plan. Phase 1 work starts here.
