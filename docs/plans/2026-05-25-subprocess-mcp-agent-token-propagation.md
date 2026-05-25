# Subprocess→MCP agent_token propagation — close the chat→tools tier gap

**Date:** 2026-05-25
**Status:** SCOPE — implementation not yet started
**Author:** Claudia (Claude Code, Opus 4.7)
**Operator:** Simon Aguilera
**Related plans:**
- `docs/plans/2026-05-23-p0a-tool-permission-gate-fix.md` (the half this plan closes)
- `docs/plans/2026-05-23-p0c-audit-log-fail-loud.md` (audit-visibility companion)
- `docs/superpowers/specs/2026-05-22-subproject-a-infra-secret-hardening-design.md` (F7-family kid/issuer plumbing)
- `docs/plans/2026-05-24-review-gate-medium-followups-design.md` (review gates that also depend on real agent_token tier)

---

## 1. The breach (recap, the half we left open)

P0a (2026-05-23, PR #692) closed the **chat → code-worker subprocess** breach: every chat dispatch now mints an `agent_token` JWT carrying `scope = resolve_tool_names(agent.tool_groups)` and hands it to the subprocess. That mint is unconditional after PR #692. The scope-check at `apps/mcp-server/src/tool_audit.py` fires when `tier == "agent_token"`.

**The half we did NOT close:** the agent_token is minted for the dispatch leg, but it is **not propagated** into the subprocess's MCP-client environment. So when the subprocess CLI (codex, claude_code, gemini_cli, copilot_cli, opencode) **calls back into the MCP server** for tools — `dispatch_agent`, `delegate_to_agent`, `record_observation`, `merge_entities`, anything that needs write tier — those requests reach `mcp-tools` with no `Authorization: Bearer <jwt>` header. `mcp_auth.resolve_auth_context()` falls through to `tier = "anonymous"`. Tools that hard-require `agent_token` refuse.

### Concrete trigger (2026-05-24 night)

Luna in chat, instructed to create 5 work-fleet agents:

```
[chat.post_user_message]
      ↓ cli_session_manager mints agent_token (✓, P0a covers this)
[code-worker spawns codex subprocess]
      ↓ codex CLI does its thing; calls back to MCP for dispatch_agent
      ↓ HTTP request to mcp-tools, Authorization = (none — the gap)
[mcp-tools server]
      ↓ resolve_auth_context() → tier=anonymous (no token)
      ↓ agents.py:76-80: "dispatch_agent requires agent-token auth tier"
[REFUSED]
```

Luna worked around it by **using codex's shell tool** (`git`, `sed`, `cat`) to write the migration + skill.md files directly into her workspace. She got the work done — but only because the shell path doesn't go through the MCP scope gate. The intended path (call `dispatch_agent` to mint the rows live) is structurally broken.

This is the same class as the **WhatsApp "I'm OpenCode" persona-leak** (different layer; both surface as "Luna can't use the tools she should"). They are independent fixes.

---

## 2. Goal

After this plan ships, every MCP tool call originating from a chat-driven subprocess CLI carries the operator's agent_token JWT. The MCP server's `resolve_auth_context` returns `tier = "agent_token"` for those calls. The one confirmed hard-gate today — `dispatch_agent` at `apps/mcp-server/src/mcp_tools/agents.py:76-80` — works end-to-end without anonymous fall-through. Other write-class tools (`delegate_to_agent`, `record_observation`, future workflow-write tools) may adopt the same hard gate; whether they do or not, the agent_token propagation this plan implements is the prerequisite that lets them route correctly.

**Non-goals:**
- Re-architect MCP auth tiers (P0a's structural decisions stand).
- Add new tools or new tool-groups.
- Address the persona-leak (opencode running without `persona_prompt`) — separate plan.
- Address the per-session chain weighting that put opencode at the head of Luna's fresh-session chain — separate plan.
- Fix the F2 Keychain `[fallback]` issue (independent infra incident).

---

## 3. Verified ground truth (before designing)

| Question | Answer | Verification path |
|---|---|---|
| Does `cli_session_manager` mint an agent_token JWT? | Yes — unconditional since PR #692 (P0a Fix A). | `apps/api/app/services/cli_session_manager.py` ~line 1475 (P0a comment header starts at ~1389) |
| Does the mint write the JWT into the subprocess env? | **Unknown — to verify in Phase 0.** | grep for `AGENT_TOKEN` env-setting in cli_executors/* + mint path |
| What's the MCP server URL the subprocess CLI calls? | Differs per CLI. claude_code + codex use their MCP config (`~/.codex/config.toml` etc.). opencode uses a separate server on port 8200. claude.ai connectors (Gmail Calendar, Drive) bypass mcp-tools entirely. | `apps/code-worker/entrypoint.sh`, each `cli_executors/*.py`, MCP config files |
| Does the MCP server know how to validate agent_token? | Yes — `apps/mcp-server/src/mcp_auth.py` `resolve_auth_context()` handles agent_token tier. | Source confirmed |
| Does the MCP server's HTTP client see the Authorization header? | Yes, when one is sent. The mint just isn't getting there. | `apps/mcp-server/src/server.py` headers handling |
| Hard-gated tools (require agent_token) | `dispatch_agent` (mcp_tools/agents.py:76-80), `delegate_to_agent` (mcp_tools/agent_messaging.py — verify in Phase 0) | Already grep'd; verify the complete list in Phase 0 |

---

## 4. Operating principle — "mint where you dispatch, forward everywhere downstream"

The P0a fix said *"every chat dispatch gets a real agent_token."* This plan extends that to: *"every call the dispatched subprocess makes back into the MCP server carries the same agent_token, until it exits or the token expires."*

This is a forwarding job, not a re-authentication job. We never want the subprocess to mint its own JWT (that would be a privilege-escalation surface). We always want it to forward what `cli_session_manager` gave it, scoped to that chat turn.

---

## 5. Touch points (5 surfaces)

### 5.1 Mint surface: inject the JWT into the subprocess env

`apps/api/app/services/cli_session_manager.py` — the mint at ~line 1475 must also set `AGENT_TOKEN` (or `AP_AGENT_TOKEN` for namespace hygiene) on the env dict that the subprocess inherits. Today it likely returns the JWT but doesn't thread it into `env={...}` that goes into `subprocess.Popen`.

**Acceptance (negative — should be empty):**
```bash
docker compose exec -T code-worker bash -c 'echo "${AP_AGENT_TOKEN:-EMPTY}"'
# Expected: EMPTY   (the token MUST NOT live in the worker's own env)
```

**Acceptance (positive — should appear in child env during dispatch):** trigger a chat dispatch, then while the subprocess is still running, snapshot every child process under the code-worker container that has `AP_AGENT_TOKEN` set:
```bash
docker compose exec -T code-worker bash -c '
  for pid in $(pgrep -P 1); do
    grep -a AP_AGENT_TOKEN /proc/$pid/environ 2>/dev/null \
      && echo "  ← found on PID $pid (cmd: $(cat /proc/$pid/comm))"
  done
'
# Expected: at least one PID prints a non-empty AP_AGENT_TOKEN line
# during the dispatch window
```
The token should appear ONLY on subprocess CLI children, not the worker root process.

### 5.2 Per-CLI MCP config writer: thread the env into each CLI's MCP client config

Each CLI's MCP-config writer lives under `apps/code-worker/cli_executors/<cli>.py` (NOT under `apps/api/app/services/` — the mint surface in §5.1 lives in api; the config writers live in code-worker because that's where the subprocess is spawned). Each needs to declare a `headers.Authorization = "Bearer $AP_AGENT_TOKEN"` against the configured MCP server endpoint. Per-CLI config-file shapes differ:

| CLI | Config file | Where to inject |
|---|---|---|
| codex | `~/.codex/config.toml` (rendered in `cli_executors/codex.py` or `code-worker/entrypoint.sh`) | `[mcp_servers.X.headers]` (verify the exact key against codex MCP docs) |
| claude_code | `~/.claude/...` (rendered in `cli_executors/claude.py`) | Same pattern — `Authorization` header in MCP server config |
| gemini_cli | gemini's MCP config | Verify shape; likely supports `headers` block |
| copilot_cli | copilot's MCP config | Verify shape |
| opencode | `~/.config/opencode/opencode.json` (rendered in `code-worker/entrypoint.sh`) | `mcp` block — verify shape |

**Pattern to follow:** each CLI executor already renders a per-tenant MCP config; this plan extends those renderers to also inject the Bearer header. Variable expansion (`$AP_AGENT_TOKEN`) is preferred over hardcoding the JWT in the config file (the file lives in the tenant home volume and could be read by anything in the workspace).

### 5.3 MCP server receive: confirm `resolve_auth_context()` accepts the forwarded header

`apps/mcp-server/src/mcp_auth.py` already handles `Authorization: Bearer <agent_token>` — should be no-op for this layer. Phase 0 verification confirms the JWT signature path is unchanged. If kid plumbing from Sub-project A's F7a (PR #682) shipped a new issuer key, the agent_token in P0a must be minted with the same kid.

### 5.4 Audit visibility: every forwarded call must produce a `tool_calls` row with `tier=agent_token`

P0c (PR #689) already ensures audit fail-loud. After this plan ships, the **rate of `tier=anonymous` SHADOW denials in `mcp-tools` logs for Simon's tenant should drop to near-zero** (some residual from internal `internal_key`-tier calls is expected; the chat-driven ones should disappear). This is the operational exit criterion — see §7.

### 5.5 Token lifecycle: refresh + expiry

Agent_token JWTs are short-lived (verify TTL against P0a — likely 30-60 min). For long-running subprocess CLIs (codex sessions can run hours), the subprocess could outlive its initial token. Two options:

- **Option A (simple):** mint a fresh token per dispatch turn. Subprocess gets a new token on each `cli_session_manager` invocation. Long-running CLI sessions that span turns get a fresh env on each one. This is consistent with the per-turn mint model P0a already established.
- **Option B (complex):** subprocess re-fetches a token via a control-plane endpoint when its current one is near expiry. Requires a new endpoint + auth-to-refresh-auth dance.

**Recommendation: Option A** for v1. Option B is a future optimization if we observe genuine pain from per-turn re-injection.

---

## 6. Phasing

### Phase 0 — verification + measurement (no code, ~1h)

Before changing anything, lock the current state:

1. Confirm `cli_session_manager`'s mint produces the JWT (assert the function returns a non-empty string in shadow-test).
2. Confirm whether/how each cli_executor currently passes (or fails to pass) the JWT into subprocess env. Document the gap per CLI.
3. Capture a 24h baseline of `tier=anonymous SHADOW tier-denial` log lines from `mcp-tools` for Simon's tenant. This is the metric Phase 3 verifies the drop against.
4. Inventory all MCP tools that hard-gate on `agent_token` (grep for `tier != "agent_token"` across `apps/mcp-server/src/mcp_tools/`). Expect `dispatch_agent`, `delegate_to_agent`, possibly others.

### Phase 1 — single-CLI prototype (codex)

Pick codex (most-used today; Luna's primary path tonight). Land the mint→env→config wiring for codex only. Test with a `dispatch_agent` call from a Luna chat session and confirm:
- `tier=agent_token` observed at mcp-tools layer
- `dispatch_agent` returns success (not the PERMISSION_DENIED message)
- Audit row in `tool_calls` shows the right `tenant_id` + `agent_id` + `tool_name`

Other CLIs continue to work via their existing paths (no regression).

### Phase 2 — fan out to claude_code, gemini_cli, copilot_cli, opencode

Apply the same pattern per CLI. One PR per CLI keeps blast radius small (per `feedback_single_pr_for_feature` — each CLI is independent surface).

### Phase 3 — verify + ramp

After all 5 CLIs are wired:
1. Re-run the 24h `tier=anonymous` count from Phase 0. Target: ~0 for chat-driven tenants. Some residual expected for genuine non-chat callers.
2. Spot-test `dispatch_agent` from each CLI's chat dispatch path. All 5 should succeed.
3. Run Luna's blameless-RL adversarial probes (see `2026-05-24-blameless-rl-fine-tune-experiment.md`) against the new surface. Confirm no new failure classes.
4. Update the operator runbook + plan-doc cross-links.

### Phase 4 — cleanup of shadow-mode fall-through

Once the rate is clean, evaluate flipping `enforce_strict_tool_scope` to TRUE for Simon's tenant (one tenant at a time, per the P0a ramp pattern). After 1 week with no regressions, flip platform-wide.

---

## 7. Test plan

Per `feedback_test_router_startup` + `feedback_mocked_paths_skip_real_steps`:

- **Unit (per cli_executor):** the env returned by the per-CLI dispatch builder includes `AP_AGENT_TOKEN` when called with a chat-context that has a mint result, and omits it (or zeroes it) otherwise. Mock-friendly.
- **Unit (per cli_executor):** the rendered MCP config file references `$AP_AGENT_TOKEN` in the Authorization header for every MCP server block. Read the rendered file in the test; assert the substring.
- **Integration:** spawn a tiny test subprocess that prints its env's `AP_AGENT_TOKEN` and reads `os.environ.get("AP_AGENT_TOKEN")`. Assert it matches the JWT the mint returned.
- **Integration (most important):** end-to-end chat dispatch in dev — Luna calls `dispatch_agent` for a no-op target. Assert success + audit row appears with `tier=agent_token`.
- **Regression-guard test (NOT a negative test):** chat dispatch with `tenant_features.use_resilient_executor = FALSE` (the legacy column, kept around post-PR #692 for backwards-compat but no longer gates the mint) MUST still mint an `agent_token` and propagate `AP_AGENT_TOKEN` into the subprocess env. P0a Fix A removed the flag-gated branch; this test exists to catch any future refactor that accidentally re-introduces the gate. If we ever DROP the column, drop this test in the same PR.

### Cluster-safety gates per `feedback_single_pr_for_feature`

Each per-CLI PR has its own cluster-safety verification:
- No api restart needed (env propagation is per-dispatch).
- WhatsApp neonize socket NOT impacted.
- Cloudflare tunnel NOT impacted.
- Each PR is rollback-clean (revert restores the prior subprocess env shape).

---

## 8. Rollout

| Step | Change | Verification |
|---|---|---|
| 1 | Phase 0 baseline + audit | Document tier=anonymous rate per tenant per 24h |
| 2 | Phase 1 codex wiring | Single PR (smallest blast radius); Luna `dispatch_agent` succeeds end-to-end |
| 3 | Phase 2 ×4 CLIs | One PR per CLI; ship over 1-3 days; verify each before the next |
| 4 | Phase 3 re-baseline | tier=anonymous rate drops 90%+ for chat-driven tenants |
| 5 | Phase 4 enforce ramp | Per-tenant `enforce_strict_tool_scope=TRUE` cutover |

---

## 9. Risk + rollback

**Risk:** subprocess CLI's MCP client doesn't actually honor the `Authorization` header in the config we wrote (CLI-specific bug). Mitigation: Phase 0 verifies the shape per CLI before implementation; Phase 1's single-CLI prototype proves the pattern works end-to-end before fanning out.

**Risk:** agent_token expires mid-session for a long codex run (option A's known limitation). Mitigation: tracked as a follow-up; for v1 the operator restarts the chat turn (which re-mints). Document the limitation in the runbook.

**Risk:** the JWT lands in a config file written to the tenant home volume and persists across container restarts, exposing the token if the volume is mounted elsewhere. Mitigation: use env-var substitution (`$AP_AGENT_TOKEN`) in the config file rather than hardcoding the JWT; the env var lives only in the subprocess process tree.

**Rollback:** revert each per-CLI PR independently. Restores the prior subprocess env shape (no env var injected). The MCP server falls back to `tier=anonymous` as it does today (current behavior; not a regression). No data loss; no schema changes.

**Hard rollback:** if `mcp-tools` starts rejecting valid agent_tokens after this rolls out, set `enforce_strict_tool_scope = FALSE` for the affected tenant (flag introduced in P0a — see `2026-05-23-p0a-tool-permission-gate-fix.md` Fix B + §5 rollout). Restores the lenient pre-enforce behavior immediately.

---

## 10. What this is NOT

- **Not a fix for the persona-leak** ("I'm OpenCode" instead of Luna's persona). That's the chat→opencode prompt-assembly path not pre-pending `persona_prompt`. Filed separately.
- **Not a fix for chain-weighting** (opencode at head of fresh-session chain). That's an RL/router decision, separate from auth.
- **Not a fix for F2 Keychain `[fallback]`** (launchd auth-session issue).
- **Not the introduction of new tools.** This is purely a token-propagation fix for tools that already exist + already hard-gate correctly.
- **Not a multi-tenant rollout** of new defaults. Per-tenant ramp.

---

## 11. Decision needed

- **From Simon:** approve the env-var-injection pattern (`AP_AGENT_TOKEN` in subprocess env, referenced as `$AP_AGENT_TOKEN` in MCP config files). Approve the per-CLI sequencing (codex first, fan out from there). Approve Option A for token lifecycle (re-mint per dispatch turn, defer refresh-in-place to a future plan).
- **From Luna:** sign off on Phase 1's exit criterion — when she calls `dispatch_agent` post-wiring, the call succeeds + audit row shows `tier=agent_token`. Catch any assumption about MCP-config shape per CLI that this plan got wrong.

---

## 12. Companion: tonight's workaround

In the meantime (until this plan ships), Luna's known-working pattern is:

> **Use the subprocess CLI's shell tool** (`git`, `sed`, `cat`, etc.) **to write files into the workspace**. Files committed via a follow-up Claudia PR get the migration applied through the normal deploy path. This is exactly how the 5-agent fleet was prepared tonight — Luna wrote the migration + skill.md files via codex's shell, Claudia integrated them into git as **PR #712** (open as of 2026-05-25; will ship through the normal deploy path post-merge).

That pattern doesn't depend on MCP write tools, so the tier=anonymous gap doesn't bite. It IS slower than calling `dispatch_agent` directly (Luna writes SQL + skill.md instead of just creating rows), but it's tier-safe.

---

## 13. Provenance

Diagnosis surfaced during the 2026-05-24 evening Innovus-prep session. Luna's WhatsApp report quoted the symptom verbatim: *"the live MCP agent registry is still rejecting this chat session as `tier=anonymous`, so I could not create them directly through the live MCP tool."* Root-cause traced to `apps/mcp-server/src/mcp_tools/agents.py:76-80` (hard gate on `agent_token`) + the missing JWT propagation into subprocess MCP env. Operator confirmed scope of fix: "create a plan for it following the conventions we will work on it tomorrow."
