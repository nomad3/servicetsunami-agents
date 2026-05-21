-- Migration 144: value-layer kill-switch + value-set version uniqueness.
--
-- Per docs/plans/2026-05-21-luna-value-layer-design.md §4.3.
--
-- Two changes in one migration:
--
-- 1) tenant_features.value_layer_enabled — per-tenant gate for the
--    5 consultation points. Default OFF so adoption is opt-in.
--    Same shape as migration 142 (nightly_reflection_enabled).
--
-- 2) Unique partial index on (tenant_id, agent_id, version) for
--    memory_type='value_set' rows. The value-set substrate is
--    append-only with monotonic version (see design §4.1). A
--    concurrent writer collision is rare in Phase 1 (operator-only
--    writes) but the index makes it cleanly detectable so the
--    writer can retry with version+1 instead of silently winning.
--
-- Idempotent — safe to re-run.

ALTER TABLE tenant_features
    ADD COLUMN IF NOT EXISTS value_layer_enabled BOOLEAN NOT NULL DEFAULT FALSE;

COMMENT ON COLUMN tenant_features.value_layer_enabled IS
    'Gate for the value layer (consult engine, 5 consultation points). '
    'Default OFF; operators flip per tenant after seeding their value '
    'set. When FALSE, every consult() call returns allow/kill_switch_off. '
    'Locked design decision: design doc §6 + §10 PR 3.';

-- The value-set body is JSON-serialized text in agent_memories.content.
-- A monotonic version field lives inside that JSON; we pull it out
-- via the cast and uniqueness-constrain (tenant, agent, version) for
-- value_set rows only.
--
-- Concurrent writers that pick the same target version race here and
-- one of them gets a duplicate-key error — the writer retries with
-- version+1. Operator-only writes in Phase 1 means this is mostly
-- defensive; Phase 2 reflection-derived proposals make it real.
--
-- (Review B4, round-7 correction.) The partial-index expression
-- AND its WHERE predicate both must avoid any jsonb cast on
-- malformed text. Round-6 fixed the WHERE side, but the index
-- EXPRESSION `(content::jsonb ->> 'version')::int` still casts
-- content::jsonb on rows that passed the regex but happen to be
-- almost-but-not-quite-valid JSON (e.g. `{ "version": 1, bad }`
-- passes the regex but fails the jsonb cast).
--
-- Fix: extract the version text-side via substring regex capture
-- and cast that small numeric string to int. The cast now operates
-- on a single integer-shaped substring extracted via regex; no
-- jsonb cast runs in either the predicate or the expression on
-- malformed-text rows.
--
-- Rows that fail the WHERE regex skip the index entirely; they're
-- still in the table (writer's INSERT succeeds) but uniqueness
-- isn't enforced for them. Caller-side write_value_set guarantees
-- the shape; this WHERE is the defense-in-depth against bad writes
-- from other code paths (operator manual repair, import scripts).
CREATE UNIQUE INDEX IF NOT EXISTS uq_value_set_version
    ON agent_memories (
        tenant_id,
        agent_id,
        ((substring(content FROM '"version"[[:space:]]*:[[:space:]]*([0-9]+)'))::int)
    )
    WHERE memory_type = 'value_set'
      AND content IS NOT NULL
      AND content ~ '"version"[[:space:]]*:[[:space:]]*[0-9]+';
