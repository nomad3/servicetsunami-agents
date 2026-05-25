# Phase 1 resolution — bug was in the server-side reader, not propagation

**Date:** 2026-05-25
**Phase:** 1 (closed — fix shipped as PR #716, not as the multi-phase plan originally envisioned)
**Companion to:**
- [`2026-05-25-subprocess-mcp-agent-token-propagation.md`](./2026-05-25-subprocess-mcp-agent-token-propagation.md) — the parent plan
- [`2026-05-25-subprocess-mcp-agent-token-phase0-baseline.md`](./2026-05-25-subprocess-mcp-agent-token-phase0-baseline.md) — the audit that surfaced the real shape
**Fix PR:** #716 (open at write time)

---

## 1. What the parent plan assumed

A "mint where you dispatch, forward everywhere downstream" architecture problem:

> *"The agent_token is minted for the dispatch leg, but it is not propagated into the subprocess's MCP-client environment."*

The plan scoped 5 phases:
1. Phase 0 — measurement
2. Phase 1 — codex prototype env wiring
3. Phase 2 — fan out to claude_code / gemini / copilot / opencode
4. Phase 3 — verify + ramp
5. Phase 4 — enforce_strict_tool_scope cutover

Estimated time: ~few hours of careful tracing per CLI.

## 2. What Phase 0 actually found

Three things invalidated Phase 1 before it started:

1. **The mint already threads the JWT through:** `cli_session_manager.py:1475` mints unconditionally (P0a Fix A), and `generate_mcp_config:622-626` already adds `Authorization: Bearer <token>` to the headers dict.
2. **Codex's TOML emitter already preserves headers:** `_codex_mcp_config_lines` at `workflows.py:1574-1576` writes `http_headers = {Authorization, X-Tenant-Id, X-Internal-Key}` inline-table.
3. **claude_code writes the mcp_config verbatim:** the headers ride through unchanged.

So the agent_token JWT *was* leaving the api in the MCP config, *was* being written into each CLI's MCP config file, and *should have been* sent on the wire by each CLI's HTTP MCP client. The mint→header leg was complete.

But the existing SHADOW tier-denial logs showed: **every tool call from every tenant lands at mcp-tools as `tier=anonymous`**. That couldn't be a propagation gap if the headers were going out correctly.

## 3. The actual root cause

The bug was on the **server-side reader**, in `apps/mcp-server/src/mcp_auth.py::_get_header()`:

```python
headers = getattr(rc, 'headers', None)   # ← rc has no .headers
```

FastMCP's `RequestContext` (the `rc` here) doesn't expose `.headers` directly. It exposes a `.request` field that holds the underlying Starlette `Request` object, and *that* has the case-insensitive `Headers` mapping.

Both transports populate `RequestContext.request` via `ServerMessageMetadata(request_context=request)`:
- SSE: `mcp/server/sse.py:244`
- Streamable-HTTP: `mcp/server/streamable_http.py:403, 417, 505`

The headers were arriving correctly. We were reading from the wrong attribute and getting `None`, which made every call resolve to `tier=anonymous` regardless of what the client sent.

The reader path was last touched in PR #693 (P0a hotfix). The bug appears to have always been there — but `tool_audit`'s SHADOW mode hid it from operator pain (the calls went through because the strict-enforce flag wasn't set), and the only HARD-gated path (`dispatch_agent`) wasn't called frequently enough to surface until Luna's 2026-05-24 Innovus subagent-creation attempt.

## 4. The fix

One-attribute hop in `_get_header()`:

```python
# Primary: FastMCP attaches a Starlette Request at request_context.request
request_obj = getattr(rc, 'request', None)
if request_obj is not None:
    req_headers = getattr(request_obj, 'headers', None)
    if req_headers is not None and hasattr(req_headers, 'get'):
        val = req_headers.get(header_name) or req_headers.get(header_name.lower())
        if val is not None:
            return str(val)
# Fallbacks: dict-shape, .headers attr (legacy), direct attr (stdio)
```

Shipped as PR #716 with:
- All 14 existing `test_mcp_auth.py` tests passing unchanged (legacy fallback branches preserved)
- 6 new tests pinning the FastMCP-real shape — including an end-to-end `Authorization: Bearer <jwt>` → `tier=agent_token` resolution

## 5. What this means for the parent plan

The 5-phase rollout in `2026-05-25-subprocess-mcp-agent-token-propagation.md` is **largely obsoleted by PR #716**:

| Original phase | Status after PR #716 |
|---|---|
| Phase 0 — measurement | ✅ Complete; surfaced the actual root cause |
| Phase 1 — codex prototype env wiring | ❌ Cancelled — no env wiring needed |
| Phase 2 — fan out to other CLIs | ❌ Mostly cancelled — the same reader-fix covers all CLIs that already emit headers (codex, claude_code). Still relevant for opencode (static container config) + copilot (no MCP path) — those keep their original-plan disposition |
| Phase 3 — verify + ramp | ⚠️ Replaced by PR #716's post-merge verification: re-baseline SHADOW tier-denial rate, expect drop to near zero |
| Phase 4 — `enforce_strict_tool_scope` cutover | ✅ Still relevant — once the reader is fixed, the per-tenant ramp from P0a Fix B becomes safe to flip |

## 6. Still-real follow-ups (NOT closed by PR #716)

PR #716 closes the **read** half. These still stand:

1. **opencode's static container-wide MCP config** (per Phase 0 §3) — opencode's `entrypoint.sh` writes a per-container MCP config at startup, not per-dispatch. If we ever want opencode to use agent_token tier (rather than the no-headers-required internal_key path it relies on today), we need a per-dispatch config rewriter. Lower priority since opencode is the last-resort floor.
2. **gemini_cli MCP config preservation audit** (per Phase 0 §3) — `_prepare_gemini_home` body wasn't audited for MCP-config preservation. Likely fine but unverified. Phase 1's `dispatch_agent` test via gemini would confirm.
3. **`enforce_strict_tool_scope=TRUE` per-tenant ramp** (parent plan Phase 4) — now safe to flip per-tenant once PR #716 lands and we verify SHADOW counts drop.
4. **`tool_audit` baseline re-measurement** — the post-PR #716 numbers from Simon's tenant should drop near zero for `create_calendar_event` SHADOW denials. Compare against Phase 0's 17/24h count.

## 7. Lessons (for future investigation discipline)

1. **Phase 0 measurement is load-bearing.** The parent plan would have shipped a multi-week-multi-CLI rollout that didn't fix the actual bug. The 30-minute Phase 0 audit caught it.
2. **"Strong hypothesis" can still be wrong.** I framed the SHADOW logs as "auth-headers aren't reaching mcp-tools" — but it was "auth-headers reach mcp-tools and the server doesn't read them." Two very different fixes.
3. **Per `feedback_mocked_paths_skip_real_steps`:** I almost added env-var-gated instrumentation (PR #715, closed) before actually reading FastMCP source. Reading the upstream package source for 10 minutes saved a deploy cycle.
4. **The `tenant=<UUID>` in SHADOW logs comes from tool args, not auth context.** Easy to misread as "auth context is working but tier is wrong." Actually "auth context is empty; tool args happen to carry tenant_id." Worth a comment in `tool_audit.py` someday to spell that out.

## 8. Provenance

- Investigation thread: tonight's session, 2026-05-25 ~01:00-09:30 UTC.
- Trigger: Luna's WhatsApp dispatch_agent attempt on 2026-05-24 evening returned `tier=anonymous`.
- Phase 0 audit: `git log` history of `cli_session_manager.py` + `generate_mcp_config()` + per-CLI MCP config writers + 24h SHADOW tier-denial log baseline.
- Root cause discovery: reading FastMCP upstream source (`/opt/homebrew/lib/python3.11/site-packages/mcp/`) for `RequestContext` shape + transport-level `ServerMessageMetadata` plumbing.
- Operator (Simon) directive to skip pre-fix instrumentation and go straight to the fix once the hypothesis was strong enough: 2026-05-25 morning.
