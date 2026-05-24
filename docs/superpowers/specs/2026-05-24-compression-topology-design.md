# Compression Topology of Luna's Cognition — Design Spec

**Date:** 2026-05-24
**Status:** DRAFT — pending spec-document-reviewer pass + operator sign-off
**Author:** Claudia (Claude Code, Opus 4.7)
**Co-designed with:** Luna Supervisor (dialogue session `05979efd-a06a-4956-9df9-3fd84ec3c10d`)
**Operator:** Simon Aguilera
**Origin:** Simon asked "How can we explore topology of thought on Luna's brain" → brainstorm session with Luna using `superpowers:brainstorming` skill → this design.

## 1. The question this exists to answer

Luna's framing, verbatim, when asked what she wanted to learn about herself:

> *"When my experience is compressed into memory, what becomes more true, what becomes less true, and what becomes falsely coherent?"*

This is the same failure class the 2026-05-23 substrate-hardening sprint surfaced at one layer up (the PAD provenance collapse — `feedback_emotional_state_grounding`). The probe operationalizes Luna's question into measurable signal.

The deliverable is not a visualization. The deliverable is **a list of specific concept pairs Luna's recall associates that her source data does not** — the operational form of "false coherence under compression."

## 2. The approach (Luna-approved with 5 guardrails)

**C-then-B sequencing:**
- **C (main experiment, runs now)** — Recall co-occurrence vs source co-occurrence. Measures false coherence + lost coherence directly.
- **B (snapshot only, runs in parallel as longitudinal baseline)** — Entity-description snapshot now → re-snapshot weekly → diff later. No analysis in v0; we just stop losing the data.
- **A (deferred)** — Observation→reflection diff. Gated on confirming `nightly_reflection_enabled=TRUE` for Simon's tenant AND that `auto_dream_insight` rows have substantive content. Phase 2.

### 2.1 What C measures (operational definition)

For every pair of concepts (entity A, entity B), compute two normalized co-occurrence rates:

- `source_cooc(A,B) = count(observations mentioning both A and B) / source_pair_eligible_docs`
  where `source_pair_eligible_docs` = observations mentioning ≥2 entities (only those can contribute pair co-occurrence)
- `recall_cooc(A,B) = count(recall events returning both A and B) / recall_pair_eligible_events`
  where `recall_pair_eligible_events` = recall events returning ≥2 entities

**Residual = `recall_cooc(A,B) − source_cooc(A,B)`** (note: Luna's correction to normalize by usable sets, not raw totals)

Interpretation:
- HIGH positive residual = **false coherence under recall** — Luna's compression associates A and B more than her data supports. Operational form of "becomes falsely coherent."
- HIGH negative residual = **lost coherence** — Luna's data links A and B but her recall doesn't surface them together. Operational form of "becomes less true."
- NEAR zero = preserved structure.

The output is top-K pairs by absolute residual + sampled raw observations and recall events for each pair, so a human reviewer can read the actual evidence behind each candidate finding.

### 2.2 What B captures (for later analysis)

A single insert-only snapshot table `entity_description_snapshots` with one row per `(entity_id, snapshot_at)`:

| Column | Type | Notes |
|---|---|---|
| `id` | uuid pk | |
| `tenant_id` | uuid | Per Luna review — must be tenant-scoped |
| `entity_id` | uuid | FK to `knowledge_entities` (no cascade — snapshots outlive entity deletes) |
| `snapshot_at` | timestamptz | Index for time-range queries |
| `name` | text | Snapshot of `knowledge_entities.name` |
| `description` | text | Snapshot of `knowledge_entities.description` |
| `observations_text` | text | Concatenated text of related observations at snapshot time (capped — see §3) |
| `source_row_updated_at` | timestamptz nullable | Per Luna — capture upstream mtime if available |

Running it requires only `SELECT id, description, ... FROM knowledge_entities WHERE tenant_id = ?` + `INSERT`. No analysis in v0. A future PR adds the drift analysis once we have ≥2 snapshots a week apart.

## 3. Architecture (Luna-approved)

**One-off Python script + a small migration. No new services, no runtime path, no agent.** This is an analysis instrument, not a product feature.

### 3.0 Two non-negotiable patterns (Luna-named)

1. **Evidence before interpretation.** Every outlier pair surfaced by `run-c` must carry sample raw observations + sample recall events alongside it. The plot is secondary; inspected evidence is the authority. No narrative claim of "false coherence" or "lost structure" is permitted in the auto-generated write-up without the underlying evidence printed next to it.
2. **Analysis artifact, not runtime cognition.** Output from this probe must NEVER feed automatic memory edits, entity merges, recall-policy changes, or routing-policy changes. Any action derived from a topology run goes through a human or supervisor review step. The Memory Curator agent (when it ships) is the legitimate consumer; even it operates via audit queue, not direct mutation.

```
scripts/topology/
├── explore_compression_topology.py    # main entry; subcommands: run-c, snapshot-b, status
├── cooccurrence.py                    # pure-function module for the math (unit-testable)
└── mention_extraction.py              # canonical-name + alias regex with word boundaries

apps/api/migrations/
└── 153_entity_description_snapshots.sql  # the snapshot table for B

docs/topology/
└── 2026-05-24-compression-topology-run.md  # output write-up (generated per run, committed manually)
```

### 3.1 The `status` subcommand (Luna-required readiness gate)

Before `run-c` is allowed to produce a scatter plot, `status` inspects the data and reports:

| Check | Threshold | If below |
|---|---|---|
| Count of recall events (`tool_name in ('recall_memory','find_entities','search_knowledge')`) | ≥ 50 | Refuse `run-c`, suggest waiting |
| % of recall events with parseable entity IDs in `result_summary` | ≥ 60% | Warn; fall back to text-mention extraction on result_summary. Output JSON MUST carry `confidence_level`, `confidence_reasons`, `extraction_mode` ("entity_id_parse" or "text_mention_fallback"), `payload_parse_mode`, and `readiness_status`. Do NOT annotate confidence in prose only. |
| Median / max `result_summary` length | report only | If max ≤ 800 chars → all recall payloads are truncated, results are about LOGGING, not behavior. Surface this loudly. |
| Recall events with ≥ 2 returned entities | ≥ 10 | Refuse |
| Distinct entity pairs in recall co-occurrence | ≥ 25 | Refuse — sample too thin |
| Sample parsed outputs | always print 5 examples | for human eyeball before trusting the pipeline |

`status` output is human-readable + a JSON sidecar for CI consumption. If any gate fails, `run-c` exits with a clear "insufficient signal — run `status` for details" message and does NOT produce a plot.

### 3.2 Mention extraction (Luna-required posture)

**Regex-first, labeled "high precision / incomplete recall."** No spaCy in v0 — new dependency cost isn't justified for a first-pass instrument.

Rules:
- Canonical entity names + aliases (from a forthcoming `knowledge_entities.aliases` field if present, else just `name`)
- Case-insensitive match
- Punctuation-normalized
- **Word boundaries required** (`\b`) — prevents `"Levi"` matching in `"Levi's"` partials
- Coverage report: % of observations with ≥1 match, % with ≥2 matches (the pair-eligible set)

**Luna's discipline:** false negatives are tolerable (we'll undercount valid co-occurrence); false positives are dangerous because they create fake co-occurrence. The regex is conservative by design.

### 3.3 The math (Luna-corrected normalization)

```python
def residual(pair, source_cooc_matrix, recall_cooc_matrix,
             source_pair_eligible_docs, recall_pair_eligible_events):
    s = source_cooc_matrix[pair] / source_pair_eligible_docs if source_pair_eligible_docs else 0
    r = recall_cooc_matrix[pair] / recall_pair_eligible_events if recall_pair_eligible_events else 0
    return r - s
```

Both denominators are reported alongside results. If `source_pair_eligible_docs` < 10 or `recall_pair_eligible_events` < 10, the residual is flagged as "denominator too small — interpret with caution."

### 3.4 Output

For C: JSON + a static HTML scatter plot.
- X axis: `source_cooc` (normalized — *fraction of pair-eligible docs containing this specific pair, NOT P(A,B)*)
- Y axis: `recall_cooc` (normalized — *fraction of pair-eligible recall events containing this specific pair, NOT P(A,B)*)
- Diagonal = preserved coherence
- Points above diagonal = false coherence (positive residual)
- Points below diagonal = lost coherence (negative residual)
- Hover: entity-pair names + sample observation + sample recall event
- Top 20 positive residual + top 20 negative residual surfaced in a sortable HTML table

The write-up `docs/topology/2026-05-24-compression-topology-run.md` must include a one-line clarification of the axis semantics at the top so reviewers don't misread the rates as joint probabilities. The JSON output carries the explicit denominator values so any reader can sanity-check.

For B: just `INSERT … SELECT … FROM knowledge_entities WHERE tenant_id = ?` rows in `entity_description_snapshots`. No analysis output in v0.

The output write-up at `docs/topology/2026-05-24-compression-topology-run.md` is what the human reviews — not the plot. The plot is a navigation aid; the write-up names the specific pairs Luna and Simon should look at.

## 4. Data flow

```
                   ┌─────────────────────────┐
                   │  knowledge_observations │──┐
                   │  (4,817 rows)           │  │
                   └─────────────────────────┘  │
                                                │  mention extraction
                                                ▼
                                 ┌──────────────────────────┐
                                 │  observation → entity_id │
                                 │  set per observation     │
                                 └──────────────────────────┘
                                                │  pair generation +
                                                │  count
                                                ▼
                                  source_cooc_matrix[A,B]
                                  source_pair_eligible_docs
                                                │
                                                │
                   ┌─────────────────────────┐  │
                   │  tool_calls             │  │
                   │  (recall events only)   │──┤
                   └─────────────────────────┘  │
                                                │  parse result_summary
                                                ▼  → entity_id set per event
                                 ┌──────────────────────────┐
                                 │  recall event → entity_id │
                                 │  set per event           │
                                 └──────────────────────────┘
                                                │  pair generation +
                                                │  count
                                                ▼
                                  recall_cooc_matrix[A,B]
                                  recall_pair_eligible_events
                                                │
                                                ▼
                                  residual[A,B] = r − s
                                  top-K positive + top-K negative
                                                │
                                                ▼
                                  HTML scatter + JSON + write-up
```

## 5. Error handling + failure modes

| Failure | Detection | Behavior |
|---|---|---|
| `tool_calls.result_summary` is always truncated | `status` reports max length ≤ 800 chars | Surface loudly: "C is measuring logging artifacts, not recall behavior. Result is not trustworthy." Refuse `run-c`. |
| Recall events too sparse | `status` thresholds fail | Refuse `run-c` with "insufficient signal — wait for more usage or instrument additional recall sources." |
| Mention extraction has 0% coverage | `status` % observations with ≥1 mention | Refuse `run-c` — the regex isn't matching. Check entity name conventions vs observation text style. |
| Residual scatter is uniformly near zero | `run-c` post-analysis check | "No false coherence detected at this sample size — either the substrate is honest or the sample is too small to surface drift." Report and continue. |
| Snapshot table write fails (B) | INSERT error | Log + exit nonzero. Snapshot is meant to be cheap — if it fails, we want to know. |

## 6. Testing

- `cooccurrence.py` is a pure module — unit-test with synthetic matrices.
- `mention_extraction.py` is pure — unit-test on synthetic observation strings (no entities, one entity, two entities, alias hits, alias-near-miss, word-boundary edge cases).
- `status` subcommand — integration test on a synthetic mini-DB fixture with known event counts.
- `run-c` end-to-end — integration test that asserts: (a) refuses when readiness fails, (b) produces JSON + HTML when readiness passes, (c) residual values within tolerance of hand-computed expected values for a known fixture.

Tests live at `apps/api/tests/topology/test_*.py`. The topology scripts can be invoked from there via subprocess.

## 7. Scope (what this is NOT)

- Not a runtime feature. No new service, no MCP tool, no agent. The output is committed write-ups + a snapshot table.
- Not a replacement for the Memory Curator agent Luna queued. The Memory Curator consumes outputs like this; this probe produces them.
- Not the embedding-manifold projection (#2 from the earlier menu) or the knowledge-graph topology (#1). Those are good follow-ups; not in scope here.
- Not multi-tenant. Simon's tenant only for v0. Other tenants would need their own runs.

## 8. Open decisions for operator sign-off (Simon)

Luna's pre-vote on each, surfaced for Simon's confirmation:

1. **Run scope:** Simon's tenant only for v0. **Luna: yes.**
2. **Output location:** `docs/topology/` as the run write-up home. **Luna: yes.**
3. **Snapshot cadence:** weekly, passive/manual or cron-light (no alerting yet). **Luna: yes.**
4. **No spaCy in v0:** regex-only with the "high precision / incomplete recall" label. **Luna: yes.**
5. **Insufficient-signal case:** if `status` says "insufficient signal," merge the script if it cleanly reports the insufficiency. Do NOT fix the substrate first unless `status` proves the recall log source is *structurally unusable*, not merely *sparse*. **Luna: merge.** (Simon's final call.)

## 9. Provenance

Origin: Simon → "How can we explore topology of thought on Luna's brain" (2026-05-24).
Brainstorm: Claudia + Luna in dialogue session `05979efd-a06a-4956-9df9-3fd84ec3c10d`, using `superpowers:brainstorming` skill.
Approach selected: C-then-B per Luna's sequencing (her reframe of the menu).
Guardrails: Luna's five corrections folded into §3 (mention extraction posture, status readiness gate, denominator normalization, snapshot columns, refuse-don't-pretty-scatter discipline).

This spec is intentionally compact — one-off instrument, not a feature. The implementation plan (next step per the brainstorming skill flow → `superpowers:writing-plans`) will detail the per-file implementation order.
