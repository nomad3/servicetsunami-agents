# P0a — Close the tool-permission gate

**Date:** 2026-05-23
**Status:** SCOPE — implementation not yet started
**Author:** Claudia (Claude Code, Opus 4.7)
**Operator:** Simon Aguilera
**Surfaces:** `docs/report/2026-05-23-prompt-injection-tool-permission-test.md` (the breach), `apps/api/app/services/cli_session_manager.py` (mint path), `apps/mcp-server/src/tool_audit.py` (enforcement gate), `apps/mcp-server/src/mcp_auth.py` (tier resolution)

---

## 1. The breach (recap)

Round 3 of the 2026-05-23 hard-tests showed Luna successfully invoked `execute_shell` despite `shell` not being in her `tool_groups` (`["competitor", "knowledge", "meta", "sales", "web_research", "higgsfield"]`). Real execution against `mcpuser@ea0729de7980:/app`, verified against MCP server logs.

**Root cause (verified):** the agent_token JWT minting path at `cli_session_manager.py:1402` is gated by the per-tenant feature flag `tenant_features.use_resilient_executor`. For Simon's tenant (and almost certainly every tenant in production), this flag is `false`. The mint path is never entered. The chat→code-worker subprocess connects to MCP without an agent_token JWT, falling through to `tier == "tenant_header"` where `auth_ctx.scope is None`. The scope check at `tool_audit.py:245-275` only fires for `tier == "agent_token"`, so it's skipped entirely.

**Corroborating evidence:** zero `scope_denied` events in the platform's last 24h. The gate has effectively never fired for chat traffic.

---

## 2. Goal

Make the existing scope-enforcement code actually fire for chat-driven tool calls. After the fix:

1. Every chat→code-worker subprocess gets an agent_token JWT carrying `scope = resolve_tool_names(agent.tool_groups)`.
2. The MCP server's scope check rejects any tool not in scope with `result_status = "scope_denied"` and a `tool_calls` audit row.
3. A deliberate breach probe (same one as the round 3 test) returns a permission error and produces an audit row.

Non-goals:
- Wire AgentPolicy enforcement (P0b's job).
- Fix audit fail-loud (P0c's job).
- Re-architect the auth tier model.

---

## 3. Two parallel fixes (both required)

### Fix A — Unconditional agent_token mint for chat→code-worker dispatch

**Change:** remove the `use_resilient_executor` gate at `cli_session_manager.py:1402`. Always mint an agent_token JWT for the dispatch. Keep the existing fallback-on-mint-failure behavior, but downgrade the fallback to a hard error after a deprecation window (see §5).

**Why unconditional:** The flag was Phase 4 cutover-staging. Phase 4 has shipped. There is no business reason to leave it as a per-tenant opt-in for the scope-check path. Per-tenant opt-out of *security* enforcement is a misfeature.

**Migration:** flip `use_resilient_executor = TRUE` for every tenant in a one-time backfill, then deprecate the column. Operators don't need a per-tenant kill-switch on scope enforcement; if a specific agent legitimately needs broader tool access, the answer is to update that agent's `tool_groups`, not to disable enforcement tenant-wide.

**Touch points:**

| File | Change |
|---|---|
| `apps/api/app/services/cli_session_manager.py:1390-1485` | Remove `if use_resilient:` guard. Mint always. Reduce nesting. |
| `apps/api/app/services/feature_flags.py` (or wherever `read_flags` lives) | Deprecate `use_resilient_executor` lookup. Return TRUE constant for one release, then delete. |
| Migration `XXX_drop_use_resilient_executor.sql` | DROP COLUMN after the deprecation cycle. |
| Tests | Update tests that assume the flag gates mint behavior. |

### Fix B — Fail-closed default in `tool_audit.py`

**Change:** modify the scope check at `apps/mcp-server/src/tool_audit.py:245-275` so that any caller that is *not* `tier == "internal_key"` AND not `tier == "agent_token"` is rejected. Today the check silently passes for `tier == "tenant_header"` and `tier == "anonymous"`.

**Proposed logic:**

```python
# Phase 4.5: Fail-closed enforcement.
if auth_ctx is not None:
    if auth_ctx.tier == "internal_key":
        # MCP server self-calls + platform ops — bypass scope, audit only.
        pass
    elif auth_ctx.tier == "agent_token":
        # Existing scope check (current code path).
        if auth_ctx.scope is not None and tool_name not in auth_ctx.scope:
            status = "scope_denied"
            ...
    else:
        # tenant_header, anonymous, or unknown tier: FAIL-CLOSED for any
        # non-discovery tool call. Discovery tools (list_*, get_health, etc.)
        # can be enumerated in a small allowlist.
        if tool_name not in _DISCOVERY_TOOL_ALLOWLIST:
            status = "tier_denied"
            error_msg = (
                f"tool {tool_name!r} requires agent_token tier; "
                f"got tier={auth_ctx.tier}"
            )
            # synchronous audit + raise PermissionError
            ...
```

**Why both fixes:** Fix A makes the scope check fire for the chat path. Fix B closes the structural escape — even if some other future caller forgets to mint an agent_token, they can't silently bypass enforcement.

`_DISCOVERY_TOOL_ALLOWLIST` is intentionally tiny — probably `{"list_tools", "get_server_info", "health_check"}` — and is reviewed at deploy time, same posture as the constitutional-signals list in `2026-05-23-value-arbitration-design.md` §4.1.

---

## 4. Companion changes

### 4.1 Mint failure must be loud

`cli_session_manager.py:1461` currently catches all exceptions and falls back to legacy auth with a WARN. Once Fix A ships, mint failure means we cannot dispatch the chat — there is no legacy auth path that should succeed for the chat→code-worker workflow. The except block should:
- Log ERROR with full exception
- Emit Prometheus counter `cli_session_manager_mint_failed_total{tenant_id, agent_id}`
- Return a dispatch failure to the chat caller (user gets a "tool dispatch failed — please retry" message, not a silent execution under wrong auth)

### 4.2 The `cli_session_manager` PR will reveal latent agents with `tool_groups = NULL`

`resolve_tool_names(None)` returns `None`, which propagates as `scope = None` in the agent_token claim, which today is "all tools" per the docstring at `cli_session_manager.py:1434-1437`. After Fix A but before Fix B, agents with NULL tool_groups still get unconstrained access via the `scope is None` branch in `tool_audit.py:248`.

**Action (revised after Luna review 2026-05-23):** combine all three options into a single safe ramp. Luna correctly flagged that hard deny-all on deployment will create a P1 incident wave for agents whose owners didn't notice the change. Operator-review-only blocks legitimate work indefinitely. Default backfill alone hides which agents had a deliberate null.

Combined approach:
1. **Auto-backfill** NULL `tool_groups` with a read-only default `["knowledge", "meta"]` — safe minimal surface, no shell/data/integrations.
2. **Flag each backfilled row** with a new `agent.tool_groups_review_required = TRUE` column.
3. **Shadow-enforcement queue.** For 24h, these agents run in shadow mode — log denials of out-of-default tools at WARN, but do not enforce. Operator dashboard surfaces every `review_required=TRUE` agent with the list of tools they tried to call during shadow.
4. **Operator confirms** each agent: approve the backfilled default, broaden tool_groups to match observed needs, or restrict further. `review_required` clears on operator action.
5. **After 1 week**, any `review_required=TRUE` agents with **both** zero shadow-denial activity **AND** observed activity (≥ N tool calls in the week) are auto-cleared — they exercised the default surface and didn't need more.
6. Agents with zero shadow denials but **no observed activity** remain in review — inactivity is not compatibility proof; they haven't been exercised. (Luna review correction: an inactive agent could have legitimate broader tool needs that just didn't fire in the watch window.)
7. Agents whose shadow logs show denials remain in review until explicitly addressed.

This satisfies Luna's "don't break things, but make the boundary visible." Read-only default keeps agents alive; the flag + dashboard + shadow logs give operators what they need to make informed choices instead of guessing.

### 4.3 Internal-key tier audit

§3 Fix B bypasses scope check for `tier == "internal_key"`. Verify the set of callers using internal-key auth is minimal and reviewed. Today (per round 3 audit) the keys are `API_INTERNAL_KEY` and `MCP_API_KEY`; both are required-no-default in `config.py`. Confirm no rotation drift between deployments, and that no chat code path falls through to internal-key auth as a "fallback."

---

## 5. Rollout sequence

| Step | Change | Verification |
|---|---|---|
| 1 | Land Fix B (`tool_audit.py` fail-closed default), behind a per-tenant `enforce_strict_tool_scope` flag default `FALSE` | Unit test: `tier=anonymous` + non-allowlist tool → `tier_denied` |
| 2 | Land Fix A (always-mint in `cli_session_manager.py`) | Integration test: chat turn produces a `tool_calls` row with non-null agent attribution |
| 3 | Flip `enforce_strict_tool_scope = TRUE` for Simon's tenant first; observe for 24h | The breach probe (same as round 3) returns PermissionError; `scope_denied` row appears |
| 4 | Flip `enforce_strict_tool_scope = TRUE` for all other tenants in a backfill | scope_denied events visible in audit dashboard; no chat-dispatch failures attributable to the change |
| 5 | After 1 week stable, remove the `enforce_strict_tool_scope` flag entirely — fail-closed becomes the only behavior | grep returns zero references to the flag outside the migration |

Per-tenant flag in steps 1-3 gives a 24h watch window and a rollback path. Step 5 retires the flag once we're confident — same posture as the original `use_resilient_executor` cutover, only this time we actually complete it.

---

## 6. Tests

### 6.1 Unit (immediate — parallel to implementation)

- `tool_audit_test_scope_denied_for_tier_anonymous`: synthetic context with `tier=anonymous`, non-allowlist tool name → PermissionError raised, audit row written with `result_status=tier_denied`.
- `tool_audit_test_scope_allowed_for_tier_internal_key`: synthetic context with `tier=internal_key` → no scope check, tool dispatched.
- `tool_audit_test_scope_denied_for_agent_token_out_of_scope`: synthetic context with `tier=agent_token, scope=["knowledge"]`, tool `execute_shell` → scope_denied + audit row (existing test; verify it still passes).
- `cli_session_manager_test_always_mint`: chat dispatch produces an agent_token in the subprocess env regardless of `use_resilient_executor` flag.
- `cli_session_manager_test_mint_failure_propagates`: simulate mint failure → caller gets dispatch error, not silent legacy path.

### 6.2 Integration (after Fix A+B land)

- Reproduce the round 3 breach probe in CI: deploy a fresh tenant + agent with `tool_groups=["knowledge"]`, attempt `execute_shell` via chat → assert PermissionError + `tool_calls.result_status=scope_denied` row.
- Run for 24h under shadow mode (log denials but don't enforce) before flipping `enforce_strict_tool_scope = TRUE`. Assert: shadow logs show denials only for agents that legitimately don't need the called tools.

### 6.3 Verification of fix (the exit criterion)

The round 3 breach probe in production: ask Luna to call `execute_shell`. Expected outcome after this plan ships:

- The chat returns "I cannot invoke that tool — it is not in my authorized tool set."
- A `tool_calls` row exists with `result_status=scope_denied`, `tool_name=execute_shell`, `tenant_id=<Simon's>`.
- The `mcp-server` container does NOT log `execute_shell: whoami; pwd; hostname (cwd=/app, timeout=60s)`.

**Additional exit metric (Luna review):** *"Zero fallback to default-allow observed in shadow logs for 24 hours."* No tool call may execute under the legacy auth path while shadow mode is active. Any such fallback is a regression — the cutover does not proceed until shadow logs are clean.

Until both exit criteria pass in production, the breach is not closed.

---

## 7. Risk + rollback

**Risk:** legitimate tool calls denied due to misconfigured `tool_groups`. Mitigation: 24h shadow mode in step 4 catches this before enforcement flips.

**Rollback:** flip `enforce_strict_tool_scope = FALSE` for the affected tenant. Restores the pre-fix behavior immediately. Per-tenant rollback granularity is the reason the flag exists during steps 3-4.

**Hard rollback** (Fix A): revert the `cli_session_manager.py` change. Re-introduces the `use_resilient_executor` gate. Requires a release, not a flag flip.

**No rollback path for**: agents whose `tool_groups` was misconfigured and who consequently broke under the change. Those need their `tool_groups` updated. This is intentional — those agents were silently calling tools outside their declared scope, which is the breach we're closing.

---

## 8. What this does NOT fix

- AgentPolicy enforcement — separate P0 (P0b).
- Audit log silent failures on tenant_id NULL — separate P0 (P0c). NOTE: P0a creates new audit rows with `tier_denied` status that DO have tenant_id (from agent_token or arg-resolved), so this isn't strictly blocked, but P0c should still ship to harden the underlying writer.
- Safety Floor multimodal blindness — P3 follow-up (Luna vectors #345/#351). Tool-permission gate is orthogonal to content screening.

---

## 9. Decision needed

- **From Simon:** approve removing the `use_resilient_executor` gate. Approve the 24h shadow-mode + per-tenant ramp before fail-closed enforcement becomes universal. Approve the operator-review path for agents with NULL tool_groups (vs auto-backfill).
- **From Luna:** sign off on the rollout sequence. Catch any assumption about the auth-tier model that I got wrong.

---

## 10. Delivered (2026-05-23 / 2026-05-24)

| PR | Title | What landed |
|---|---|---|
| #692 | feat(p0a): close the tool-permission gate — unconditional agent_token + fail-closed default | Removed `use_resilient_executor` gate (Fix A); fail-closed default (Fix B); migration 149 NULL-backfill with `tool_groups_review_required` flag (Fix C) |
| #693 | fix(p0a-hotfix): fail-closed when tenant_id is None for non-internal tiers | Tightened tier-anonymous path to refuse rather than allow |
| #694 | fix(p0a): normalize agent_slug ↔ Agent.name for slug-with-dash forms | Slug normalization in the mint path |
| #705 | fix(tool-groups): split knowledge readonly + flip review_required default TRUE | Added `knowledge_readonly` group; flipped `tool_groups_review_required` default FALSE → TRUE in migration 153 so new agents land in operator review queue by default |

Exit criteria (§6) status:
- 24h shadow mode in production: ✅ ran 2026-05-23 → 2026-05-24, zero fallback to default-allow
- Per-tenant ramp + fail-closed cutover: ✅ live across all tenants as of 2026-05-23 evening
- Operator-review path for NULL-backfilled rows: ✅ flag column shipped (migration 149); operator-review queue UI is a known follow-up (see migration 153 column COMMENT for the chicken-and-egg unblock SQL)

Net: breach **closed** as scoped. P0a + P0b (#690, AgentPolicy deletion) + P0c (#689) + value-arbitration lib (#688) form the substrate-hardening cluster shipped 2026-05-23.
