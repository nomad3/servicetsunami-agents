# Vet OS — specialist-referral workflow framework (generalize Brett's flow)

**Date:** 2026-05-31
**Status:** Design (multi-agent design pass + adversarial review; needs Simon decision on scope)
**Owner:** Simon · **Lead:** Luna
**Origin:** Simon — "Brett's cardiology flow is just one workflow; for a real vet OS, use the *same workflow design* and extend it to *any* specialist." Produced by a grounded design workflow: ground in the real `cardiology_v1` code → stress-test across 7 vet specialties in parallel → synthesize → adversarial clinical-safety critique.

## The architecture (Simon's instinct is correct)

Brett's loop is a generic **referral-in → acquire artifact → deterministic extract + QA gate → draft-under-persona → human approval → send-back & close** pipeline. The *control flow* is specialty-agnostic; only the *data* varies. So the design is:

1. **A specialty-agnostic engine** — the 6-stage pipeline + the 5 generic agent roles (supervisor, intake, specialist-diagnostics, comms&recall, referral-liaison) + the QA-record contract `{field, value, unit, source_page, confidence, outlier_flag}` — all expressible today on `DynamicWorkflowExecutor`.
2. **A declarative `specialist_manifest`** (one JSON/dataclass) that fully parameterizes the engine: `input_modality`, `extraction_schema` (label anchors), `qa_contract` (required fields + outlier/consistency rules), `report_template` (sections + immutable boilerplate + sign-off), `never_autonomous_rules`, `specialist_persona/credentials`, `connector_slots`, `send_back`. **Cardiology = `cardiology_v1`, the first instance.**
3. **A parameterized provisioner** — `vet_manifest.py`'s engine-constant parts become `provision_specialist_practice(db, tenant, profile, manifest)`; `cardiology_v1` moves into a manifest module; `VetPracticeProfile.practice_type → specialty_key` selects the manifest. **Onboarding a specialist = a new manifest, not new code.**

The Brett §8 reshape stops being a one-off: **it becomes `cardiology_v1` — the first config that *proves the engine*.**

## ⚠️ HONEST SCOPE — this is NOT a 7-specialty engine (adversarial verdict)

The survey + critique are unambiguous: **the framework is sound only for the "cardiology-shaped class"** — single-encounter, single deterministic-extract modality, point-in-time *diagnostic readout*. Forcing the others in is dangerous.

| Specialty | Fits the skeleton? | Why |
|---|---|---|
| **Cardiology** | ✅ reference | echo PDF, numeric table, single readout |
| **Neurology / Dermatology / Ophthalmology** | ✅ *with parameterization* | derm/ophtho **invert** "numbers are truth, images are context" → image-primary extraction needed |
| **Oncology** | ❌ | **case-accumulation** (histopath+imaging+clin-path over *days*), **free-text** diagnosis, **conditional + chemo-dose safety** gate tree, **longitudinal** RECIST baseline |
| **Internal medicine** | ❌ | no single canonical table; evidence spread across many docs |
| **Diagnostic imaging** | ❌ | the read *is* the referral — no separate specialist visit |
| **Surgery / Behavior / Emergency** | ❌ (not surveyed-in) | prospective plan / narrative-only / time-critical (the 30-day approval timeout is *catastrophic* for urgent) |

**Conclusion:** build the engine, but **scope v1 to the cardiology-shaped class** (cardiology + prove config-not-code with one structurally-similar second specialty). Treat oncology/IM/imaging/surgery/behavior/emergency as **explicit escape-hatch tracks**, NOT crammed into the linear 6-stage shape. Don't market "onboarding = config not code" until a *non-cardiology, non-table* specialty ships as a pure manifest (the real generalization gate).

## 🚨 SAFETY BLOCKERS — must fix BEFORE generalizing (generalizing multiplies them)

The critique found the human-in-the-loop is **a convention, not an engine guarantee** — and the platform's own safety layer is downgraded inside workflows. These are hard blockers:

1. **The approval gate doesn't actually stop delivery.** `dynamic_executor.py` human_approval returns `{approved}`, the loop *stores it and continues* — a rejected/timed-out approval does NOT halt a following `send_email`/`create_drive_file` unless the author hand-wires a `condition` step around every delivery step. Across N specialties × templates that's N+ chances to mis-wire the one gate preventing an unreviewed clinical report from reaching a vet. **Fix: make send-back STRUCTURALLY gated at the engine level (the compiler emits the approval-condition automatically; not author-wired).**
2. **Workflow-channel safety is reduced to logging.** `dynamic_step.py` treats `require_review`/`require_confirmation` as `allow_with_logging` inside workflows → no defense-in-depth; everything leans on the one manual approval step. **Fix: re-enable the safety floor for side-effecting tools in clinical workflows.**
3. **never-autonomous rules are soft prompt prose, not deterministic vetoes.** The source discovery doc itself says hard rules ("never assign ACVIM stage without measurements") must be **value-arbitration `tenant_norm` VETOES**, but value-arbitration is pure-library (not runtime-wired). **Fix: a deterministic post-draft veto/gate (regex/classifier), and wire value-arbitration enforcement, before any specialty relaxes the approval gate.**
4. **Conflating safety- and quality-gates** into one `needs_review` boolean (a missing creatinine before a chemo dose == a low-confidence prose phrasing) — fatigue erodes the signal. **Fix: severity tiering on the gate.**
5. **Role/credential of the approver is unenforced** (`deps.py` — role-principal not enforced) → any user could approve a DACVIM report. **Fix: enforce approver credential before multi-specialty (DACVIM vs DACVO vs surgeon).**

## Implementation plan (9 chained PRs — cardiology-shaped class)

1. **Attachment-bytes contract** (specialty-agnostic prerequisite): `download_attachment` → per-page structured `{pages:[{page,text,tables}], raw_ref}` + raw bytes (not `content[:10000]`). Unblocks page-anchored extraction for all.
2. **`SpecialistManifest` dataclass + QA-record types** + `validate_manifest()` (REQUIRE non-empty required_fields + ≥1 outlier_rule + a consistency_check; default unsigned-manifest → everything `needs_review`).
3. **Generic extractor MCP tool** `extract_specialist_case` — label-anchored parse + unit-normalize + outlier/range + completeness + consistency, driven entirely by the manifest; persist to KG with provenance.
4. **`cardiology_v1` manifest** — real echo anchors (LVIDd/LA:Ao/FS…), ranges (LA:Ao≤1.7, FS 25-45%), DACVIM sections + immutable boilerplate, the never-autonomous rules, Brett's persona/sign-off. = **the Brett §8 reshape as data**.
5. **`build_referral_workflow(manifest)`** compiler → emits the 6-stage step JSON; **structurally wraps every delivery step in the approval condition** (blocker #1). Replace the placeholder Cardiac Report Generator; run end-to-end through the gate.
6. **Table-review approval UI** (generic — reads the QA-record shape): per-field value/unit/source_page/confidence/outlier separate from the prose; approve/edit/reject → `approval_decision` signal; immutable boilerplate shown verbatim.
7. **Parameterize the provisioner** — `provision_specialist_practice(...)` + `MANIFEST_REGISTRY`; `practice_type → specialty_key`; carry the §9 idempotency fixes.
8. **`alpha provision specialist-practice <tenant> --specialty cardiology`** + internal endpoint; run Brett's tenant through provision→referral→approve→send-back as the acceptance test.
9. **Prove generalization with a 2nd config** (a structurally-different, ideally non-table specialty) authored as a *pure manifest* — acceptance = **zero engine/provisioner/executor lines changed**. This is the real "config not code" gate.

**Safety blockers #1-#3 are prerequisites woven into PRs 2/5 — they ship with cardiology, not after.**

## Decision needed from Simon
1. **Scope confirm:** build the engine but ship v1 for the **cardiology-shaped class** only (cardiology + one similar 2nd specialty), with oncology/imaging/surgery/emergency as explicit later escape-hatch tracks — agree?
2. **Safety blockers first:** OK to make the structural-approval-gate + workflow-safety-floor + deterministic-veto part of the cardiology build (PRs 2/5), not deferred?
3. Which **2nd specialty** to prove config-not-code (neurology fits cleanest; diagnostic-imaging stresses it hardest)?
