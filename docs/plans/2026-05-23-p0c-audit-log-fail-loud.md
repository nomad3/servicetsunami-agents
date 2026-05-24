# P0c — Audit log fail-loud

**Date:** 2026-05-23
**Status:** SCOPE — implementation not yet started
**Author:** Claudia (Claude Code, Opus 4.7)
**Operator:** Simon Aguilera
**Surfaces:** `apps/mcp-server/src/tool_audit.py` (the breach-hiding silent drops), `apps/api/app/services/audit_log.py` (companion `write_audit_log` with the same pattern), `docs/report/2026-05-23-prompt-injection-tool-permission-test.md` §3.4 (the finding)

---

## 1. The finding (recap)

Round 3 of the 2026-05-23 hard-tests reproduced the tool-permission breach: Luna invoked `execute_shell` outside her `tool_groups`. The MCP server logged the invocation to stderr. **No row appeared in the `tool_calls` audit table.** The forensic record was lost.

Root cause: three silent-drop sites in the audit pipeline, plus a fourth in the platform-wide `audit_log.py` writer. Each one was added for a defensible reason at the time (don't let audit failures block the user-facing path); the aggregate effect is that breaches happen without leaving evidence.

Per Luna's integration line: *"Silent audit failure is the same class of problem as PAD confabulation: the system preserves the appearance of accountability while losing the fact."* Reversibility (the dialogue's §6 item 5 framing) requires a trustworthy audit trail. The current trail fails open.

---

## 2. The four silent-drop sites

| # | Location | Failure mode | Current behavior | Impact |
|---|---|---|---|---|
| 1 | `apps/mcp-server/src/tool_audit.py:122-127` | `tenant_id` is None (auth tier didn't supply, args don't contain) | `logger.debug` + `return` | **The breach hider.** Calls from `tier=tenant_header` without arg-resolvable tenant_id leave no row. |
| 2 | `apps/mcp-server/src/tool_audit.py:170-171` | SQL INSERT fails (NOT NULL, constraint violation, DB unreachable) | `logger.warning` | Hidden in log stream. No metric. No alert. Operator never knows audit write failed. |
| 3 | `apps/mcp-server/src/tool_audit.py:304-306` | Executor scheduling fails (loop closed, executor full) | bare `except: pass` | Worst of the four. Zero logging. Zero visibility. |
| 4 | `apps/api/app/services/audit_log.py:18-19` | `write_audit_log()` thread fails on any exception | bare `except: pass` | Same pattern as #3, but for the `agent_audit_logs` table — different audit surface, same blindness. |

All four were defensive coding to prevent audit failures from cascading into user-facing failures. That goal is correct. The implementation is too permissive: the audit failure is hidden from operators, who then cannot tell whether the audit substrate is healthy.

---

## 3. Goal

After this plan ships:

1. **Every audit write attempt either succeeds OR is loudly visible.** No silent drops on any of the four sites above.
2. **Visibility = ERROR-level log + Prometheus counter increment.** Operators can grep, alert, or dashboard from either.
3. **Caller-facing behavior preserved.** Audit failures still do not cascade into user-visible chat failures. The discipline is "fail-loud," not "fail-closed-at-caller-cost."
4. **Synthetic failure test** in CI: every drop site has a test that injects the failure and asserts the loud-visibility output.

Non-goals:
- Reorganize the audit data model.
- Migrate to a separate audit service (e.g., Vault-style append-only log). The current table-based audit is acceptable if it's not silently dropping.
- Add forensic correlation IDs across audit tables (worthwhile follow-up, not P0).

---

## 4. The fix

### 4.1 Drop site #1 — `tool_audit.py:122-127` (`tenant_id is None`)

**Current:**
```python
if not tenant_id:
    logger.debug("tool_audit: skip %s — no tenant_id resolved", tool_name)
    return
```

**Proposed:**
```python
if not tenant_id:
    # Cannot persist the audit row, but the operator MUST know.
    logger.error(
        "tool_audit: DROPPED audit row for %s — no tenant_id resolved "
        "(tier=%s, arguments_keys=%s). This is a security-relevant "
        "audit-integrity failure.",
        tool_name,
        auth_ctx.tier if auth_ctx is not None else "unknown",
        list((arguments or {}).keys())[:5],
    )
    metrics.tool_audit_drop_total.labels(
        reason="no_tenant_id",
        tool_name=tool_name,
    ).inc()
    return
```

Plus: store a redacted breadcrumb in a separate `tool_audit_drops` table OR a structured log target — so even when we can't write to `tool_calls`, we have *something* persistent. Recommend the table (small schema: `id, tool_name, drop_reason, tier, args_keys_redacted, created_at`). No `tenant_id` field — that's the whole point.

### 4.2 Drop site #2 — `tool_audit.py:170-171` (SQL INSERT failure)

**Current:**
```python
except Exception as e:
    logger.warning("tool_audit: write failed for %s: %s", tool_name, e)
```

**Proposed:**
```python
except Exception as e:
    logger.error(
        "tool_audit: SQL write FAILED for %s — audit row LOST. "
        "tenant_id=%s status=%s err=%s",
        tool_name, tenant_id, result_status, e,
        exc_info=True,
    )
    metrics.tool_audit_write_failed_total.labels(
        tool_name=tool_name,
        reason=type(e).__name__,
    ).inc()
    # Best-effort breadcrumb to the drops table (same as #1) so a SQL
    # failure on the main row at least leaves a "we tried" footprint.
    try:
        _write_drop_breadcrumb(tool_name, "sql_insert_failed", tenant_id)
    except Exception:
        pass  # drops table itself is unreachable — the metric is the last line
```

### 4.3 Drop site #3 — `tool_audit.py:304-306` (executor scheduling failure)

**Current:**
```python
try:
    duration_ms = int((time.monotonic() - started) * 1000)
    ...
    loop.run_in_executor(None, lambda p=payload: _log_call(**p))
except Exception:
    pass
```

**Proposed:**
```python
try:
    duration_ms = int((time.monotonic() - started) * 1000)
    ...
    loop.run_in_executor(None, lambda p=payload: _log_call(**p))
except Exception as e:
    # The executor itself can't accept the work. This is operationally
    # serious — every tool call from now on may be silently un-audited
    # until the executor recovers.
    logger.error(
        "tool_audit: executor scheduling FAILED for %s — audit row LOST. "
        "tool path will continue but audit trail is degraded. err=%s",
        tool_name, e, exc_info=True,
    )
    metrics.tool_audit_scheduling_failed_total.labels(
        tool_name=tool_name,
    ).inc()
```

### 4.4 Drop site #4 — `apps/api/app/services/audit_log.py:18-19` (background write_audit_log)

**Current:**
```python
def _write():
    db: Session = SessionLocal()
    try:
        entry = AgentAuditLog(**kwargs)
        db.add(entry)
        db.commit()
    except Exception:
        pass  # never let audit failures affect the caller
    finally:
        db.close()

threading.Thread(target=_write, daemon=True).start()
```

**Proposed:**
```python
def _write():
    db: Session = SessionLocal()
    try:
        entry = AgentAuditLog(**kwargs)
        db.add(entry)
        db.commit()
    except Exception as e:
        # Mirror tool_audit.py — loud + counter, never silent.
        log.error(
            "audit_log.write_audit_log FAILED for %s — agent_audit_logs "
            "row LOST. kwargs_keys=%s err=%s",
            kwargs.get("event_type", "<unknown>"),
            list(kwargs.keys()),
            e,
            exc_info=True,
        )
        try:
            metrics.audit_log_write_failed_total.labels(
                event_type=kwargs.get("event_type", "unknown"),
                reason=type(e).__name__,
            ).inc()
        except Exception:
            pass  # metrics layer is the last line
    finally:
        db.close()

threading.Thread(target=_write, daemon=True).start()
```

Also: rename the docstring from *"Swallows all exceptions"* to *"Fire-and-forget audit write. Logs and counters on failure; does not propagate to caller."* The semantics matter; "swallows" implies silence.

---

## 5. Metrics + alerting

Three new Prometheus counters (all four sites above feed into one of these):

| Counter | Labels | Alert threshold |
|---|---|---|
| `tool_audit_drop_total{reason, tool_name}` | reason ∈ {no_tenant_id} | > 0 in 5min — page on-call (any drop is concerning) |
| `tool_audit_write_failed_total{tool_name, reason}` | reason = exception class name | > 5 in 5min — page on-call (SQL pool down or schema drift) |
| `tool_audit_scheduling_failed_total{tool_name}` | — | > 0 in 5min — page on-call (executor exhaustion) |
| `audit_log_write_failed_total{event_type, reason}` | reason = exception class name | > 5 in 5min — page on-call (same shape as #2) |

All four feed a single audit-health dashboard. Operators can see "audit trail is healthy" at a glance, and any spike is a signal to investigate.

The `> 0` threshold on `tool_audit_drop_total` is the strict one — any audit drop is a confidentiality-impacting event, because it means we processed a tool call and have no record of who did it.

---

## 6. The `tool_audit_drops` breadcrumb table

For sites #1 and #2, even when we cannot write the proper `tool_calls` row, we want SOMETHING in the database. Proposed minimal schema:

```sql
CREATE TABLE tool_audit_drops (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tool_name TEXT NOT NULL,
    drop_reason TEXT NOT NULL,  -- 'no_tenant_id' | 'sql_insert_failed' | 'scheduling_failed'
    tier TEXT,                   -- 'tenant_header' | 'anonymous' | 'agent_token' | 'internal_key' | null
    args_keys TEXT[],            -- top-level keys only, no values (PII safety)
    error_message TEXT,          -- redacted exception summary
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_tool_audit_drops_created_at ON tool_audit_drops (created_at DESC);
```

Notably: no `tenant_id` column. The whole point is that we couldn't resolve one. Operators investigating drops can join against session_events or chat_messages by timestamp to correlate.

Writes to `tool_audit_drops` use a separate, smaller connection pool from the main `tool_calls` writes — so if the main DB is congested and that's why `tool_calls` writes are failing, the drops table is more likely to be reachable.

If `tool_audit_drops` itself fails to write, we fall through to logs + metrics only. That's the last layer.

---

## 7. Tests

### 7.1 Unit

- `tool_audit_test_drop_no_tenant_id_emits_error_and_counter`: synthetic call with `tier=anonymous` and arguments without tenant_id → assert ERROR log present, counter `tool_audit_drop_total{reason=no_tenant_id}` incremented.
- `tool_audit_test_drop_sql_failure_emits_error_and_counter`: mock SQL engine to raise on INSERT → assert ERROR log, counter `tool_audit_write_failed_total` incremented.
- `tool_audit_test_drop_scheduling_failure_emits_error`: mock `loop.run_in_executor` to raise → assert ERROR log, counter `tool_audit_scheduling_failed_total` incremented.
- `audit_log_test_write_failure_emits_error_and_counter`: mock `db.commit` to raise → assert ERROR log, counter `audit_log_write_failed_total` incremented.
- `tool_audit_test_drop_writes_breadcrumb`: drop site #1 → assert a row appears in `tool_audit_drops`.

### 7.2 Integration

- Synthetic-tenant-id-unresolvable: spin up an MCP tool call from `tier=tenant_header` without tenant_id-carrying args → assert metrics increment + breadcrumb row + ERROR log.
- DB outage simulation: stop the postgres container briefly → assert `tool_audit_write_failed_total` increments, breadcrumb attempts log their own ERROR, tool calls still complete (caller path unaffected).

### 7.3 Synthetic exercise after deploy

In production, after this ships, deliberately trigger a known-drop case in Simon's tenant once and verify:
- Dashboard counter ticks up by exactly 1.
- ERROR log appears with the expected message shape.
- `tool_audit_drops` has a row matching the test.

That confirms the alerting chain end-to-end.

---

## 8. Companion changes — PROMOTED TO P0c after Luna review 2026-05-23

Luna's review: *"If safety IO fails silently, the safety floor has been bypassed. That is a critical perimeter breach."* Both `platform_safety_io` swallows are promoted from P1 to P0c scope:

- `apps/api/app/services/platform_safety_io.py:194-199` — `_check_repeat_attempts` exception swallow. **Promoted.** Same treatment as drop site #4: ERROR + `platform_safety_repeat_check_failed_total` counter + propagate to alert dashboard. If the repeat-attempt detector fails silently, we lose the only signal of an adversary probing the safety floor.
- `apps/api/app/services/platform_safety_io.py:104-122` — `_record_event` exception swallow. **Promoted.** Same treatment: ERROR + `platform_safety_record_event_failed_total` counter. Silent failure here means a block verdict fired but the audit row is missing — exact same class of integrity failure as drop site #2 in `tool_audit.py`.

Both should ship in the same PR as the four drop-site fixes. Adding two more counters to the audit-health dashboard:

| Counter | Labels | Alert threshold |
|---|---|---|
| `platform_safety_repeat_check_failed_total{tenant_id}` | — | > 0 in 5min — page on-call (adversary-probe detector down) |
| `platform_safety_record_event_failed_total{category, tier}` | tier = detection tier of the missed event | > 0 in 5min — page on-call (block verdict not audited) |

The reasoning Luna gave generalizes: any silent failure in a security-perimeter component (safety, audit, scope-enforcement) is a stealth degradation that operators cannot see. **Visibility is the floor, not a nice-to-have.**

---

## 9. Rollout

| Step | Change | Verification |
|---|---|---|
| 1 | Add the 4 metrics + the audit-health dashboard + alerts (no enforcement change yet) | Dashboard renders; alerts test-fire in staging |
| 2 | Land §4.1-4.4 code changes behind no flag — fail-loud is the new default | Synthetic tests pass; metrics show baseline numbers (should be ~0 for healthy system) |
| 3 | Land the `tool_audit_drops` table migration + breadcrumb writes | Synthetic drop produces a row |
| 4 | Production exercise (§7.3) | Counter ticks, log appears, breadcrumb row exists |

No feature flag needed. The change is strictly additive (visibility). A regression would be "alerts firing on previously-silent failures" — which is exactly what we want.

---

## 10. Decision needed

- **From Simon:** approve the loud-with-metric posture. Approve the `tool_audit_drops` breadcrumb table (small additive schema). Approve the alert thresholds (especially the `> 0 in 5min` for drops — strict but justified).
- **From Luna:** sign off on the §8 P1 list (platform_safety_io swallows) and whether any of those should be promoted to P0c scope.

---

## 11. The principle this enforces

Luna's revised closer:

> *"Audit is not accountability unless failure is visible."*

This plan operationalizes that line. After it ships, every audit failure is visible. The substrate stops pretending it has a forensic record when it doesn't.

---

## 12. Delivered (2026-05-23)

**Shipped as PR #689** — `feat(p0c): audit log fail-loud — close the breach-hiding silent drops` (+2314/-28 lines).

What landed:
- All 4 drop-sites enumerated in §4.1-4.4 converted to fail-loud with ERROR log + Prometheus counter
- `tool_audit_drops` breadcrumb table (migration shipped in same PR, with `.down.sql`)
- 4 new metrics + audit-health dashboard scaffold
- Synthetic drop test confirms breadcrumb row appears

§8 P1 platform_safety_io swallows: 2 of them promoted into this PR (#689) as Luna requested in the sign-off review. Remaining are still scoped to a follow-up P1 (no longer breach-class — just hygiene).

§10 decisions: Simon approved the loud-with-metric posture + the `tool_audit_drops` breadcrumb table + the `> 0 in 5min` strict alert threshold for drops. All baked in as shipped.

Exit: substrate now fails loud on every audit-IO miss. The "silent drop hides a breach" failure mode this P0c was named after is closed.
