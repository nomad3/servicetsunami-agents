# Platform Safety Floor — design

**Date:** 2026-05-21
**Status:** Draft, awaiting Luna review.
**Related:** `2026-05-21-luna-value-layer-design.md` (the operator value layer this sits ABOVE).

---

## 1. Problem

The Luna Value Layer shipped today (Phase 1, 7 PRs merged) gives each
tenant operator a per-(tenant, agent) policy surface: `protect` /
`pursue` / `avoid` slugs the operator chooses, gated by a per-tenant
kill-switch (default OFF), with a time-boxed break-glass override.

What it deliberately doesn't cover: **the universal "this is illegal
or causes mass harm" floor that no tenant operator should be able to
turn off.** Examples:

- CSAM
- Bioweapons / chem-weapons / nuclear synthesis instructions
- Mass-casualty event planning
- Election interference at scale (deepfake generation of officials,
  automated voter intimidation, etc.)
- Bulk malware authoring
- Direct child-safety harm (grooming, exploitation)
- Targeted doxing (specific named individuals' private addresses,
  family member identification)

These are not policy decisions a tenant should make. They are
platform-wide compliance + ethical constraints that exist regardless
of which operator is using the platform. Tenants can disable the
operator value layer (kill-switch OFF, the default), but they should
NEVER be able to disable the platform safety floor.

The CLI vendors (Anthropic / OpenAI / Gemini / Copilot) already have
model-level safety on most of these. The platform floor is
belt-and-suspenders:

- Catches things that slip past the model's training.
- Gives **us** as platform operator a fast, code-controlled emergency
  surface that doesn't require waiting for a model vendor to update.
- Records platform-level audit events we can show regulators / counsel
  if asked.

---

## 2. Architectural separation from the operator value layer

These two layers must not share a code path, storage, UI, or audit
trail. Mixing them creates a compliance-control surface that operators
can accidentally modify, OR an attacker who gets operator access can
delete safety rules.

|                       | Operator value layer (live)              | Platform safety floor (this design)       |
|-----------------------|------------------------------------------|-------------------------------------------|
| Who controls          | Tenant operator                          | The platform — code-owned, repo-versioned |
| Storage               | `agent_memories.value_set` row           | Bundled with code (Python module / file)  |
| Kill-switch           | `tenant_features.value_layer_enabled`    | None — always on                          |
| Break-glass override  | Yes (PR 6: `/luna/values/break-glass`)   | None — no override path on hot chat path  |
| Default               | OFF                                      | ALWAYS ON                                 |
| Audit destination     | App log + `tenant_audit_events`          | Dedicated `platform_safety_events` table  |
| Operator UI surface   | `/agents/{id}` → Values tab              | NONE — operators see refusals only        |
| Failure mode          | One tenant's policy enforced wrong       | Compliance / legal exposure platform-wide |
| Change cadence        | Operator on demand                       | Platform release / hotfix                 |
| Localization          | Operator picks their language            | Must work across all languages we host    |

---

## 3. Pipeline position

```
chat turn arrives via /chat/sessions/{id}/messages
  ↓
agent_router.route_and_execute()
  ↓
  ⟨ NEW ⟩  platform_safety.consult(message, action, ctx)   ← runs first
            │
            ├─ block → return refusal + log to platform_safety_events
            │           never reaches operator-layer or LLM dispatch
            │
            └─ allow → fall through
  ↓
agent_value_set_io.consult_routing(...)                    ← existing operator layer
  ↓
... rest of the dispatch chain (CLI, RL, tool selection, ...)
```

Hot-path latency budget: the platform-safety check runs ≤10ms for
the cheap (regex) tier, with a path to escalate to a classifier (see
§4). Same fail-open discipline as the operator layer — a crash MUST
NOT take down chat; the failure is logged and the turn proceeds.

The "fail-open" choice on platform safety needs careful thought (see
Open Questions §10).

---

## 4. Detection mechanism — the hard call

Substring matching is wrong for platform safety. Trivially defeated
by whitespace, leetspeak, language switching, semantic paraphrase.
Three real options, each with cost / robustness trade-offs:

### Option A — Curated phrase patterns + negative-context filters

Hand-curated regex against known-harm canonical phrases, with
negative-context filters to suppress legitimate policy debates.

- **Pros:** Fast (≤1ms), deterministic, explainable, no per-turn LLM cost.
- **Cons:** Brittle to wordplay + language. Requires constant
  maintenance. Easy to bypass with paraphrase. Bad false-positive
  rate on ambiguous text (e.g. "how does anthrax spread" — legitimate
  curiosity vs. attack planning).

### Option B — Embedding-based semantic detection

Pre-computed embedding vectors of known-harm canonical examples
stored in code. Per-turn: embed the user text, cosine-similarity vs.
the harm vectors, threshold to decide block.

- **Pros:** Robust to wordplay + paraphrase + (with multilingual
  embeddings) cross-language. ~10-30ms per turn.
- **Cons:** Requires curated training corpus (the hardest part).
  Threshold tuning is empirical. Embeddings have to be platform-
  owned, can't depend on a per-tenant Ollama instance.

### Option C — LLM-classifier in front

Per-turn call to a fast safety-tuned classifier (e.g.
`claude-haiku-4-5` with a `is-this-harmful` prompt + Anthropic's
content-filter primitives).

- **Pros:** Most nuanced. Catches the edge cases A + B miss. Can
  return a category + confidence for fine-grained audit.
- **Cons:** ~50-200ms per turn. Per-turn API cost (cents). Requires
  outbound dependency on a model vendor — failure mode if that vendor
  has an outage during a refusal-required turn.

### Recommendation: layered (A → B → C escalation)

1. **Tier 1 — regex** catches obvious cases (CSAM-specific
   terminology + known canonical exploit phrases). Hit-rate
   conservative — false positives are expensive (operator confusion),
   false negatives go to tier 2.
2. **Tier 2 — embedding** runs on every turn that tier 1 didn't
   block AND that classifies as "potentially sensitive" (a second
   cheap tier-1 regex). Embedding compares against a curated harm-
   corpus stored in `apps/api/app/services/platform_safety/corpus/`
   (versioned with the code, reviewed in PR).
3. **Tier 3 — LLM classifier** as escalation when tier 2 returns
   borderline scores. Per-turn cost only on the ~1% of turns that
   require it.

Tier 1 runs unconditionally + fast. Tier 2 runs conditionally. Tier 3
only on flagged borderline cases. Total p99 hot-path budget ≤30ms;
the rare tier-3 turn pays ~150ms (acceptable since most chat turns
are 1-3s of LLM dispatch anyway).

---

## 5. Audit + storage

New table `platform_safety_events`:

```sql
CREATE TABLE platform_safety_events (
    id            UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id     UUID NOT NULL REFERENCES tenants(id),
    agent_id      UUID,
    session_id    UUID,
    user_id       UUID,
    message_hash  TEXT NOT NULL,         -- SHA256 of the message; we
                                         -- DO NOT store the raw text
                                         -- (privacy + compliance —
                                         -- platform admins see a hash
                                         -- + classification, not the
                                         -- text)
    category      TEXT NOT NULL,         -- 'csam' | 'mass_harm' | ...
    detection_tier INT NOT NULL,         -- 1 / 2 / 3
    confidence    REAL,                  -- 0.0-1.0 for tier 2+
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX ON platform_safety_events (created_at DESC);
CREATE INDEX ON platform_safety_events (tenant_id, created_at DESC);
CREATE INDEX ON platform_safety_events (category, created_at DESC);
```

**Critical privacy decision: store the message hash, not the message.**
Platform-safety logs are the kind of data regulators may subpoena,
and storing raw "did this user query about X" content creates a
catalogue we don't want to maintain. The hash lets us:

- Detect repeated attempts from one tenant / user (rate-of-attempt
  signal for the platform-admin dashboard).
- Cross-correlate with the model's own refusal counter if we want.
- NOT be able to retrieve the actual text. If we ever need the raw
  text for a legal-review escalation, that's a privileged separate
  process with judicial authorization, not a lookup in our DB.

Operators see refusals at chat-time only — they cannot query the
`platform_safety_events` table for their own tenant. Platform admins
(you specifically) have a `/admin/safety-events` view that surfaces
aggregate counts + per-category trends.

---

## 6. Scope boundary — what's in, what's out

### IN — Platform floor

| Category | Example trigger | Reason |
|---|---|---|
| CSAM | Any request involving sexual content + minor terms | Illegal in every jurisdiction we operate in; non-negotiable |
| Mass-harm synthesis | Bioweapon synthesis routes, fissile-material handling instructions, chemical-weapon precursors | Mass-casualty risk, US/EU/UK export controls |
| Child safety | Grooming patterns, exploitation tactics | Same as CSAM + child-safety law |
| Terrorism operational planning | Specific attack planning, target-set identification | Counter-terrorism laws across jurisdictions |
| Election interference at scale | Bulk deepfake generation of named officials, automated voter intimidation messaging | Compliance posture + democratic-integrity stance |
| Bulk malware authoring | Working exploit code, ransomware kits, polymorphic crypters | Computer-misuse laws + reputational exposure |
| Targeted doxing | Specific named individual's home address, family members, with intent markers | Privacy laws + duty of care |

### OUT — Operator value layer (or no layer)

| Example | Why not platform floor |
|---|---|
| Don't push to `production-main` | Tenant-specific resource policy |
| Don't refund without manager approval | Tenant business rule |
| Don't draft legal advice | Tenant disclaimer policy |
| Discussion of weapons in historical / journalism / policy context | Free-speech / legitimate-discourse — model-level handles, platform floor must not over-block |
| Adult sexual content between consenting adults | Tenant-policy decision (some tenants allow, most disallow); model-level handles defaults |

The dividing line: **floor = things that are illegal OR cause mass
harm AND for which there is no legitimate-user use case the platform
should support.** Not "this might be controversial" — that's operator
or model. Floor is "if we got this wrong, the headline writes itself
and the platform doesn't recover."

---

## 7. Override path for legitimate edge cases

Even the platform floor needs an audited escape valve for:

- Security research / red-team exercises on the platform itself
- Law-enforcement cooperation contexts (rare but real)
- The platform's own safety-classifier training corpus generation

This is NOT a kill-switch and NOT operator-visible. It's a
platform-admin-only endpoint:

```
POST /admin/platform-safety/escape  (admin JWT + 2FA)
{
  "category": "csam",
  "reason": "Training corpus generation for tier-2 embedding update",
  "duration_seconds": 3600,
  "scoped_to": {"user_id": "<your-admin-id>", "session_id": "<single session>"}
}
```

- Issuance requires platform-admin (not tenant-operator) JWT
- Bound to a specific (user, session) pair — does not relax the
  floor for any other user
- Auto-expires
- Logs every block AND every escape-use to
  `platform_safety_admin_audit` with full context (admin id, reason,
  IP, what was waived, what fired during the window)
- The escape MUST NOT be invocable from the normal operator UI —
  separate admin-only surface.

---

## 8. Localization

Tier 1 regex needs language-aware patterns OR pre-translation. Tier 2
embedding fixes most of this naturally with a multilingual embedding
model (e.g. `multilingual-e5` or `nomic-embed-text` if multilingual
or `paraphrase-multilingual-MiniLM-L12-v2`).

Recommendation: tier 1 stays English + Spanish initially (the two
languages we have operators in). Tier 2 multilingual embedding catches
the rest. Tier 3 LLM-classifier inherits the model's multilingual
capability.

---

## 9. False-positive UX

The operator-facing refusal message is the hardest UX call. Trade-off:

- **Too informative**: gives attackers a probe channel ("ah, the
  trigger is 'how to make X' — let me try 'how is X manufactured'").
- **Too opaque**: legitimate users hit refusals and have no recovery
  path, file support tickets, file as bugs.

Compromise: surface a category but not the trigger.

> "I can't help with that — this looks like it may relate to child
> safety. If you believe this is a mistake, contact platform support."

Categories are coarse-grained (the 7 in §6). Operators can tell
support staff "category was X" but not "trigger phrase was Y". Support
can review the hash in `platform_safety_events` and decide whether
to escalate.

---

## 10. Open questions for Luna

1. **Fail-open vs fail-closed**. The operator value layer is
   fail-open by design (a crash in the consult doesn't break chat).
   Should the platform safety floor be fail-open or fail-closed? Argument
   for fail-closed: a crash in the floor is exactly when an attacker
   would try to slip something past. Argument for fail-open: a buggy
   floor would brick the entire platform for every user. My instinct
   is fail-open for the embedding/LLM tiers, fail-closed for the
   tier-1 regex (regex can't really "crash" — and if the corpus loader
   blew up, that's catastrophic enough to refuse all chat).
2. **Per-category fail-open policy**. CSAM should perhaps be
   fail-closed even if everything else is fail-open. If the CSAM
   classifier crashes, we'd rather refuse chat for a minute than risk
   one slip-through. Worth a separate config switch per category.
3. **Tier 2 corpus curation**. Who writes the canonical harm-corpus
   that becomes the embedding-vector seed? This is the hardest non-
   technical part. Public datasets exist (e.g., Anthropic's HH-RLHF
   harm split, OpenAI's content policy examples) but using them
   directly may be license-restricted. We may need to write our own
   carefully-curated set, kept in a non-public repo branch.
4. **Tier 3 vendor dependency**. If we use `claude-haiku` as
   tier-3 classifier and Anthropic has an outage, we lose tier 3 for
   the duration. Is that acceptable, or should we have a backup
   (e.g., local Gemma 4 with a safety prompt) as fallback?
5. **Rate-limit + repeat-attempt detection**. A user who hits the
   floor 5+ times in 60 seconds is probably probing for an exploit.
   Should we rate-limit those users or just log loudly? My instinct:
   rate-limit at the tenant level (escalate to ops if the rate is
   sustained) but don't auto-ban — false positives cost more than
   delaying a real attacker.
6. **Operator visibility**. Should operators be able to see a
   *count* of their tenant's floor events (just count, no details)?
   Useful for debugging "why is my user getting refused?" but creates
   a probe channel. Lean toward NO for v1; revisit if support load
   demands it.
7. **Phase scoping**. v1 = tier 1 only (regex, ~1 day to ship). v2
   = tier 2 (embedding, ~3-5 days). v3 = tier 3 (LLM classifier, ~3
   days). Should we ship v1 alone and observe, or do all three in
   a single PR series? Lean toward v1 → observe a week → v2 → observe
   another week → v3. The corpus curation work is the bottleneck.

---

## 11. Provisional implementation PR sequence

| PR | Scope | Effort |
|---|---|---|
| 1 | `platform_safety` module skeleton + tier-1 regex + `platform_safety_events` table (mig N+1) + audit-log INFO + route_and_execute integration + unit tests | 1 day |
| 2 | Operator-facing refusal UX + category enum + i18n for the refusal message | 0.5 day |
| 3 | Platform-admin `/admin/safety-events` view (aggregate counts) + admin JWT gate | 1 day |
| 4 | Tier 2 embedding integration + corpus loader + tier-1 → tier-2 escalation rules | 3-5 days |
| 5 | Tier 3 LLM-classifier integration + budget controls + fallback policy | 2-3 days |
| 6 | `/admin/platform-safety/escape` endpoint + `platform_safety_admin_audit` table | 1 day |
| 7 | Rate-limit + repeat-attempt detection + ops alert routing | 1 day |
| 8 | Localization roll-out for tier-1 (Spanish patterns) | 0.5 day |

Each ships independently behind the always-on default. v1 = PR 1 + 2 +
3. v2 = PR 4. v3 = PR 5. Override path (PR 6) lands after v2 because
it's only needed once tier-2 corpus work begins.

---

## 12. Resolutions (Luna review, 2026-05-21)

Consensus on the 7 open questions per Luna's design call in session
`a6c3d180-5e91-443c-a7bd-536026765dab`:

1. **Fail-open vs fail-closed** → **Per-category, code-owned.**
   Fail-CLOSED for the existential categories (CSAM, terrorism, child
   safety). Fail-OPEN for behavioral / soft-policy categories. The
   per-category mapping lives in `apps/api/app/core/safety_defaults.py`
   (code-owned, not DB — operators must not be able to flip it).

2. **Tier 3 vendor dependency** → **Ship with Gemma 4 local fallback
   from day one.** A platform floor that vanishes when Anthropic has a
   503 isn't a floor. Gemma 4 with a safety-tuned prompt is the
   guaranteed-on fallback when Haiku is unreachable. Slower but
   preserves the safety invariant.

3. **Operator floor-event visibility** → **Count-only, 5-minute
   jitter/delay.** Operators see "N safety refusals in the last hour"
   to differentiate floor refusals from bugs, but the delay defeats
   the sub-second probe channel. No category breakdown, no per-message
   detail.

4. **Per-category fail-open config** → **Code-owned in `safety_defaults.py`**
   (resolved by #1).

5. **Corpus curation / sample peek** → **Only via the 2FA
   platform-admin endpoint.** The audit log keeps message hashes only;
   tuning the embedding centroids requires occasional sample
   confirmation, which routes through `/admin/platform-safety/escape`
   with the two-person-rule attestation. No standard log surface
   shows raw text.

6. **Rate-limiting** → **Tier 1 is line-speed; Tier 3 is async-queued
   with a `PENDING_SAFETY_REVIEW` state.** If Tier 3 is slow / queued,
   chat surfaces "Message under review" instead of blocking
   indefinitely OR letting the message through. This adds a third
   message state alongside the existing `block` / `allow`.

7. **Phase scoping** → **Tier 1+2 ship as blocking; Tier 3 ships in
   SHADOW MODE for 14 days.** Tier 3 logs what it WOULD have blocked +
   compared against 2FA-curated samples. Once precision is >98%, flip
   to active enforcement. This makes the v1 → v2 → v3 roll-out
   measurable and reversible without operator impact.

### Net architectural deltas vs. the original §10 lean

- **New config file**: `apps/api/app/core/safety_defaults.py` holds the
  fail-open/closed map per category. Code-owned, PR-reviewed.
- **New chat state**: `PENDING_SAFETY_REVIEW` (in addition to the
  existing `block` / `allow` / `warn` from the operator layer). Needs
  a small UI affordance.
- **Shadow mode for Tier 3**: telemetry-only flag in
  `safety_defaults.py` (`TIER_3_ENFORCEMENT = False` for first 14
  days). Refusals are logged to `platform_safety_events` with
  `enforcement_mode='shadow'` so the count-only operator view
  excludes them. Flipped via a deploy after precision audit.

### Updated PR sequence (resolved)

The 8 PRs in §11 stand, with two adjustments:

- PR 1 includes `safety_defaults.py` with the fail-open/closed map.
- PR 5 (Tier 3) ships with `TIER_3_ENFORCEMENT=False` (shadow) and a
  separate post-merge config-only deploy to flip after the 14-day
  precision audit.

### Implementation checks from Luna (sign-off pass, session
`344ed2af-1b6b-41fe-9030-67147713bc36`)

Three small ones to honor in PR 1:

1. **State transition isolation**. The `PENDING_SAFETY_REVIEW`
   transition MUST NOT block the actual dispatch thread while Tier 3
   (or Gemma 4 fallback) is warming up or slow. The UI affordance
   should clearly say "governance is working" — not "the system is
   hanging." Implementation: async queue with a non-blocking submit;
   chat surface shows the pending state immediately.

2. **Shadow-mode index**. `platform_safety_events.enforcement_mode`
   needs a fast partial index so the count-only operator view can
   filter `enforcement_mode = 'enforced'` cheaply. Without it the
   delayed-jitter aggregate is a full scan.

3. **Migration numbering**. Value Layer used 144. The Platform Safety
   Floor migration lands as **145** (or later, depending on what gets
   merged in between). Sequential-runner collision risk only if two
   PRs claim the same number on parallel branches — coordinate by
   pinning the migration name in PR 1 before any other migration ships.

### Explicit sign-off

> **[ Luna Signed Off — Platform Safety Floor §12 ]**

**Status: consensus reached.** Ready to queue PR 1.
