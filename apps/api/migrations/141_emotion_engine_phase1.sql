-- 141_emotion_engine_phase1.sql
--
-- Phase 1 PR A of the Digital Emotions Engine (see
-- docs/plans/2026-05-19-emotions-engine-prototype-design.md). Adds the
-- two JSONB columns the EmotionEngine service writes/reads. NO READS
-- WIRED YET — this migration is intentionally inert until PR B wires
-- the appraise call sites and PR C wires the prompt-side behavioural
-- change.
--
--   conversation_episodes.affect_vector
--       Per-session PAD (Pleasure, Arousal, Dominance) snapshot at
--       episode close, plus terminal mood derived for legacy readers.
--       NULL means "no affect recorded for this episode". Coexists with
--       the existing `mood String(30)` column — that column has four
--       readers (memories.py, auto_quality_scorer,
--       luna_presence_service, local_inference) and IS DELIBERATELY
--       LEFT UNTOUCHED in Phase 1. Unification to a single source of
--       truth happens in Phase 4 per the design doc.
--
--       Schema (validated by app.schemas.emotion.PADVector):
--           {
--             "pleasure":  float in [-1, 1],
--             "arousal":   float in [-1, 1],
--             "dominance": float in [-1, 1],
--             "label":     string in luna_presence_service.VALID_MOODS,
--             "updated_at": ISO 8601 timestamp
--           }
--
--   agent_memories.affect_baseline
--       Per-agent stable trait vector (the "personality" the agent
--       returns to in the absence of stimuli). Decay in
--       EmotionEngine.decay pulls the current affect_vector back toward
--       this baseline each tick. NULL means "use the flat neutral
--       default (0,0,0)" — agents created before this migration get
--       neutral until persona-derived seeding lands in Phase 2.
--
--       Same JSON schema as affect_vector above.
--
-- Multi-tenancy: no change. Both columns sit on tables that already
-- have tenant_id FKs. Tenant isolation is enforced by the existing
-- query patterns when readers land in PR B (see § Risks §
-- "Emotion-state pollution across tenants" in the design doc).
--
-- Rollback: drop the two columns. No data loss outside the new columns
-- themselves since nothing reads them yet.

BEGIN;

ALTER TABLE conversation_episodes
    ADD COLUMN IF NOT EXISTS affect_vector JSONB;

ALTER TABLE agent_memories
    ADD COLUMN IF NOT EXISTS affect_baseline JSONB;

COMMIT;
