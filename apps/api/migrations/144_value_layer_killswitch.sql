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
-- (Review B4 defense, round-6 correction.) The partial-index
-- expression evaluates `(content::jsonb ->> 'version')::int` on
-- every INSERT against memory_type='value_set'. The earlier
-- attempt to guard via `jsonb_typeof(content::jsonb) = 'object'`
-- still casts content::jsonb in the predicate — which raises on
-- malformed text like '<<not valid json>>' BEFORE the predicate
-- evaluates. So malformed rows still trip the index.
--
-- Fix: use TEXT-side regex guards in the WHERE clause so we
-- decide whether to apply the index BEFORE any jsonb cast runs.
-- The guards reject:
--   - NULL content.
--   - text not starting with `{` (not a JSON object body).
--   - text missing an integer `version` field (regex-detected).
--
-- Rows that fail any guard skip the index entirely; they're still
-- in the table (writer's INSERT succeeds) but uniqueness isn't
-- enforced for them. Caller-side write_value_set guarantees the
-- shape for any value-set row it produces; this WHERE is the
-- defense-in-depth against bad writes from other code paths
-- (operator manual repair, import scripts, etc).
CREATE UNIQUE INDEX IF NOT EXISTS uq_value_set_version
    ON agent_memories (
        tenant_id,
        agent_id,
        ((content::jsonb ->> 'version')::int)
    )
    WHERE memory_type = 'value_set'
      AND content IS NOT NULL
      AND content LIKE '{%}'
      AND content ~ '"version"[[:space:]]*:[[:space:]]*[0-9]+';
