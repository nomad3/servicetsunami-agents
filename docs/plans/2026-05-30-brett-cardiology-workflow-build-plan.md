# Brett Cardiology Workflow — BUILD PLAN (the §8 reshape)

**Date:** 2026-05-30
**Author:** Build-plan session (codebase research + 1 `alpha chat send` consult with Luna, project lead)
**Status:** PLAN — no implementation code in this doc. Working draft for Simon's go-ahead.
**Phase:** Advances **Phase ③** of the agentic-vet-OS build (Luna's order: landing → provisioner → **Brett deep workflow**). Landing (#739) + provisioner (#740) are MERGED to `main`; this is the last unbuilt piece.
**Builds on:**
- [`2026-05-30-veterinary-mvp-discovery.md`](2026-05-30-veterinary-mvp-discovery.md) — §3.4 reshape table, §8 4-net-new list, measurement-QA contract, the cast + sample data.
- [`2026-05-30-agentic-vet-os-vision.md`](2026-05-30-agentic-vet-os-vision.md) — Phase-1 beachhead, provenance-first thesis.
- [`2026-05-30-vet-practice-provisioner-plan.md`](2026-05-30-vet-practice-provisioner-plan.md) — `cardiology_v1` fleet + the §4 note that the provisioner **installs this reshaped template**.

---

## TL;DR

The provisioner (#740) already seeds the 5-agent `cardiology_v1` fleet, the Gmail/Drive/Calendar connector slots, the declared value-sets, and **installs a tenant copy of `Cardiac Report Generator`** — but that template, on `main` today (`workflow_templates.py:222`), is still the best-effort version: `search_emails → read_email → download_attachment → one Luna agent step → save_to_drive`. It has **no structured extraction**, **no measurement-QA contract**, **no `human_approval` gate**, and **no send-back**. So the provisioner currently installs a template that does *not yet* run the trustworthy Brett loop.

This build delivers the §8 reshape so that the template the provisioner installs is the real one. **Five net-new pieces** (discovery §8, restated and verified against current code):

1. **Attachment-tool contract change** (prerequisite-ish) — `download_attachment` returns only `content[:10000]` plain text (`email.py:866-872`); page-level traceability + deterministic table parse are impossible until it preserves per-page structured output / raw bytes.
2. **Deterministic echo-table extractor + measurement-QA contract** (the core new step).
3. **OCR fallback** — net-new dependency (none in tree).
4. **`human_approval` gate with table-review UI** — the step type is real & durable (Temporal signals, 30-day timeout, `dynamic_executor.py:134-136, 292-340`); the table-review *surface* is net-new frontend.
5. **`send_email` send-back loop** — the template stops at Drive save; the referral-service wedge needs the delivery leg.

**[Luna] sequencing call (this session):** the extractor and the transport contract are **decoupled** — prototype deterministic extraction **now, in parallel**, against the on-disk `Winnie Nieto.pdf`; the attachment-bytes fix is a one-line integration point when it lands. This is the LIGHT, host-safe first move and needs no Docker/deploy.

---

## 0. Current state — verified against `origin/main` (2026-05-30)

| Piece | State on `main` | Evidence |
|---|---|---|
| Landing page (vet) | **MERGED** | PR #739 (`feat(web): vet practice-OS landing`) |
| Provisioner `cardiology_v1` | **MERGED** | PR #740; `apps/api/app/services/provisioning/vet_manifest.py` (5 agents, slots, value-sets), `_CARDIOLOGY_V1_WORKFLOW_TEMPLATES = ["Cardiac Report Generator"]` |
| `human_approval` step type | **REAL + durable** | `dynamic_executor.py:62` (30-day timeout), `:134-136` (dispatch), `:292-340` (signal handlers `approve_step`/`approval_decision` + `_wait_for_approval` with `notify_approval_requested`) |
| `Cardiac Report Generator` reshape | **NOT BUILT** | `workflow_templates.py:222-291` — still `search_emails → read_email → download_attachment → 1 Luna step → save_to_drive`; ends at `save_to_drive` (line 270), no extraction/approval/send_email |
| `download_attachment` PDF handling | **truncates** | `email.py:830-843` pdfplumber `page.extract_text()` joined, then `content[:10000]` (`:869`); no per-page map, no table geometry, no bytes returned |
| OCR | **absent** | no OCR dep in `requirements.in` |

**Net:** the provisioner installs a template that is a placeholder for this build. The reshape is the difference between "demo that emails a Drive doc" and "trustworthy specialist-approved report loop."

---

## 1. Sample data on disk (REAL — build against this NOW, no Gmail/PIMS needed)

`/Users/nomade/Documents/GitHub/health-pets/docs/data/`:
- **`Winnie Nieto.pdf`** (484 KB) — finalized report: 13y FS Chihuahua, ACVIM Stage B1, mild MMVD; prose narrative **+ "Adult Echo: Measurements and Calculations" table** (2D LVIDd/LVPWd/EF/IVSd/LA Area/LA:Ao; MMode; Doppler LVOT/RVOT/MR Vmax, MV E/A, IVRT, TR Vmax) **+ 12 image thumbnails**.
- **`21560820260214_WINNIE_NIETO_...pdf`** (1.36 MB) — underlying **machine export** (filename = study accession `21560820260214`). **This is the parser's real target shape** — the export, not the finalized prose.
- **`MR B2 (TEMPLATE).pdf`** (104 KB) — Brett's DACVIM template skeleton with `[MILD/MODERATE/SEVERE]`, `[ACVIM Stage]`, fixed anesthesia/fluid/steroid + pimobendan boilerplate, fixed sign-off.
- **`extracted_images/img-000..011.jpg`** — the 12 echo thumbnails already split out (QA-only, not a diagnostic source in MVP).
- **`veterinary-cardiology-intake-template.md`** — the structured intake field set (Patient/History/Referral Diagnostics/Physical Exam/Echo+ECG/Classification/Plan) — **maps directly to the case-artifact schema** below.
- **`Invoice WMAH 2-14-26.pdf`** — billing thread (out of scope for this build).

> **SAMPLE vs REAL:** everything in this build can be prototyped against `Winnie Nieto.pdf` + the machine export. What is **gated on Simon** (§4): whether the *production* echo machine/software emits the same table layout, and whether intake is genuinely Gmail. The parser must be written against the sample but **kept layout-tolerant** (label-anchored, not coordinate-hardcoded) until a second real export confirms the format.

---

## 2. Target workflow shape (the reshape)

Replace the single Luna step with **extract → QA-gate → draft → approve → finalize → send-back**:

```
find_patient_email      (search_emails)            — unchanged
read_patient_email      (read_email)               — unchanged
extract_echo_pdf        (download_attachment*)      — *needs the bytes/per-page contract change (§3.1)
extract_echo_structured (NEW: deterministic parse → case artifact + measurement-QA contract → KG)
  └─ condition: QA gate — if required fields missing OR any field LOW confidence
       → mark draft "needs Brett review", suppress strong diagnostic language
generate_dacvim_report  (re-scoped agent step: case artifact + MR B2 template + few-shot + RAG)
approve_report          (NEW: human_approval — Brett reviews TABLE separately + draft; approve/edit/reject)
save_to_drive           (create_drive_file)        — only on approval
send_back_to_referrer   (NEW: send_email to referring GP)  — only on approval
```

### 2.1 Measurement-QA contract (the case artifact schema)

Per-field record (Luna-confirmed this session as the right minimal floor):

```jsonc
{
  "field": "LA:Ao",
  "value": 1.4,
  "unit": "ratio",
  "source_page": 3,
  "confidence": "HIGH",          // categorical HIGH|MEDIUM|LOW — Luna: safer than float for v1
  "outlier_flag": false,
  "outlier_reason": null         // optional: e.g. "LA:Ao 3.2 exceeds reference ceiling 1.7" — Luna's low-cost add
}
```
Plus an artifact-level **required-fields completeness gate** (Luna: keep it): the draft stage is **blocked** unless the key measurements are present (candidate set: LVIDd, LVIDs, LA:Ao, FS/EF, plus species/weight for normalization). Missing key field → artifact enters `needs_review` and the draft avoids strong diagnostic / ACVIM-staging language. This is the concrete mechanism behind discovery's #1 risk (a wrong measurement confidently embedded in a polished report).

Persist the artifact to the **KG** (`create_entity`/`create_relation`, `knowledge.py`) with provenance: `source_mailbox`, `study_accession`, per-field `source_page`/`confidence`, patient → breed/age → ACVIM stage → echo findings → meds. This both gates drafting and seeds RAG precedent for later cases.

---

## 3. The five pieces — build now vs gated

### 3.1 Attachment-tool contract change — `download_attachment`
- **What:** stop flattening to `content[:10000]`. Return either raw PDF bytes (base64) or a **per-page structured map** (`{page: n, text, tables[]}`) using `pdfplumber.extract_tables()` (already in-tree, `email.py:835`) so the extractor gets page-anchored tables.
- **Build now?** The *tool edit* is small and host-safe (Python only). **[Luna]:** not a hard blocker for the extractor — they're decoupled; do the parser against the on-disk file first, wire bytes in as a one-line integration point. **Risk Luna flagged:** if the old tool was returning *corrupted/truncated* bytes (not just wrong shape), the on-disk prototype won't surface it — catch at integration-test time.
- **Gated on Simon?** No for the change itself; the *real* intake path (Gmail vs vendor portal/PACS — §4-Q) determines whether this tool is even the ingestion point in production.

### 3.2 Deterministic echo-table extractor + QA contract — NEW step
- **What:** parse the "Adult Echo: Measurements and Calculations" table → normalized per-field records (§2.1) → completeness gate → KG persist. Label-anchored parsing (find row labels LVIDd/LA:Ao/etc.), unit normalization, species/weight normalization, outlier ranges from breed/species reference.
- **Build now?** **YES — start here (Luna).** Prototype as a standalone parser against `Winnie Nieto.pdf` + the machine export; validate the output schema before wiring into the workflow or touching Gmail. Pure-Python, host-safe, no Docker.
- **Gated on Simon?** Production table *layout* (which echo machine/software) — keep parser layout-tolerant until a 2nd real export confirms.

### 3.3 OCR fallback — NEW dependency
- **What:** when `extract_tables()` yields broken geometry / image-only pages, fall back to OCR (e.g. `pytesseract` + `pdf2image`, or a cloud OCR). Failure modes: wrapped headers, missing units, digit confusion (0/O, 1/l), merged cells.
- **Build now?** Scaffold the **interface** + low-confidence flagging now; the actual OCR dep install + tuning is a follow-up (and a build/deploy step — defer past the LIGHT pass). The text-table path covers the sample, which has extractable text.
- **Gated on Simon?** Only matters if production exports are image-only — confirm with the real machine.

### 3.4 `human_approval` gate + table-review UI
- **Backend:** **DONE primitive** — add a `human_approval` step after the draft (`dynamic_executor.py` handles it; step outputs persist in `workflow_step_logs.output_data`). Brett's signal = `approval_decision`/`approve_step`.
- **Frontend (NET-NEW):** the discovery §8-#3 gap — the bell renders only title/body (`NotificationBell.js`). The review surface must show the **extracted table separately** from the prose, with per-field confidence + `source_page`, and approve / edit / reject. **This is §4-Q (approval surface) made concrete** — pick `/dashboard` run-detail vs email vs WhatsApp before building the UI.
- **Build now?** Backend step wiring yes; the table-review UI is gated on the surface decision (§4) and is frontend work (no heavy host load, but needs the decision first).

### 3.5 `send_email` send-back — NEW step
- **What:** on approval, `send_email` (`email.py`) the finalized report to the referring GP (+ per-clinic delivery rules later). The operational delivery leg the referral-service wedge (fork C) needs.
- **Build now?** Yes — trivial template addition once the approval gate exists. Recipient/format = per-clinic rules (deferred to fork C); v1 = email the referrer parsed from intake.

---

## 4. Gated on Simon (carry from discovery §4 / vision §8 / provisioner §7 — restate, don't re-decide)

These block *production wiring*, not the on-disk prototype:
1. **Echo data specifics** — which echo **machine/software** emits the table (drives parser layout); is intake genuinely **Gmail attachments** (`btcvetmobile@gmail.com`) or a vendor portal / shared drive / DICOM PACS; **how many finalized reports** exist for few-shot/RAG (beyond Winnie + template) and can we have them.
2. **Brett's approval surface** — `/dashboard` run-detail vs email reply vs WhatsApp vs legacy `health-pets` `/reports/[id]`. **Single biggest UX gate; blocks 3.4's UI.**
3. **Brett's hard "never autonomously" rules** — confirm the seeded declared value-set (`_DIAGNOSTICS_VALUES`): never assign ACVIM stage when key measurements missing; never send before approval; never alter risk-paragraph boilerplate; no diagnosis from thumbnails. (Note provisioner §9 BLOCKER: `tenant_norm` veto is **declared, not runtime-enforced** — v1's *enforced* floor = `human_approval` + user-principal `agent_permissions`.)
4. **Tenant placement** — fresh BB Cardiology tenant (provisioner §7 recommendation) vs HealthPets vs Angelo's `7f632730` (wrong owner) vs Simon's `752626d9` (test). Decides where the reshaped template + KG + creds live.
5. **Wedge fork** — endorse "C (referral-service) powered by A (internal tool), B later" → decides whether turnaround/per-clinic-delivery instrumentation is in this build or deferred.
6. **Confirm the 5-agent `cardiology_v1` cut** (Luna + Referral-Intake + Cardiac-Diagnostics + Comms/Recall + Referral-Liaison) — already shipped in #740; confirm none pulled forward from `gp_full`.

---

## 5. Mapping to the cardiology_v1 fleet (who owns each new step)

| New/changed step | Owning agent (seeded by #740) | Enforced gate |
|---|---|---|
| `extract_echo_structured` + QA contract | **Cardiac Diagnostics Agent** (`specialist`; tool_groups `email,drive,knowledge,calendar`) | completeness/confidence gate suppresses strong language |
| `generate_dacvim_report` | **Cardiac Diagnostics Agent** | — (drafts only) |
| `approve_report` (human_approval) | **Brett** (owner; reviews) | the runtime gate |
| `send_back_to_referrer` | **Comms & Recall Agent** (sends only post-approval) / **Referral Liaison** (package) | downstream of approval step |
| inbound classify/route | **Referral Intake Agent** (thin) | flags malformed/non-echo for human |

The provisioner already seeds these agents, their personas, owner, escalation, and the declared `_DIAGNOSTICS_VALUES`. This build only changes the **workflow template** they run + the **attachment tool** + the **review UI** — no new agent/platform code.

---

## 6. Sequenced steps (LIGHT-first; host-safe ordering)

1. **(NOW, host-safe)** Standalone parser prototype against `Winnie Nieto.pdf` + machine export → validate the §2.1 case-artifact schema, label-anchored table extraction, unit/species normalization, outlier ranges. **No Docker, no deploy.** ← Luna's recommended first move.
2. **(NOW)** `download_attachment` contract change — per-page structured output / bytes (small Python edit). Integration-test against the prototype.
3. Re-scope the Luna agent step → `generate_dacvim_report` consuming the case artifact + `MR B2` template + few-shot (Winnie) + RAG precedent (KG). Persist artifact to KG with provenance.
4. Insert `human_approval` step (backend wiring only — primitive exists). Confirm Brett's surface (§4-Q2), then build the **table-review UI** (separate table + confidence + source-page).
5. Add `send_email` send-back (post-approval).
6. Scaffold OCR-fallback interface (full OCR dep install/tuning deferred — it's a build step).
7. Re-point the native `Cardiac Report Generator` template to the reshaped definition so the provisioner installs the real loop (it already lists this template by name — no manifest change needed).
8. Instrument turnaround / cases-per-day / revision rate if fork C confirmed (§4-Q5).

**Each piece ships as its own PR off a feature branch (chain branches — they touch the same `workflow_templates.py` + `email.py`), Codex+Luna review per house rule. No commits to main.**

---

## 7. Build-on assets

- `apps/api/app/services/workflow_templates.py:222` — `Cardiac Report Generator` (reshape target).
- `apps/mcp-server/src/mcp_tools/email.py:780-877` — `download_attachment` (pdfplumber at `:835`; `content[:10000]` at `:869`); `send_email`.
- `apps/api/app/workflows/dynamic_executor.py:134-136, 292-340` — `human_approval` dispatch + signals + `_wait_for_approval`.
- `apps/api/app/services/provisioning/vet_manifest.py` — `cardiology_v1` fleet, `_CARDIOLOGY_V1_WORKFLOW_TEMPLATES`, `_DIAGNOSTICS_VALUES`.
- `apps/mcp-server/src/mcp_tools/knowledge.py` — `create_entity`/`create_relation` for the KG case artifact.
- `apps/web/src/components/NotificationBell.js` — current approval surface (title/body only → table-review UI is net-new).
- `/Users/nomade/Documents/GitHub/health-pets/docs/data/` — `Winnie Nieto.pdf`, machine export, `MR B2 (TEMPLATE).pdf`, `extracted_images/`, intake template.

---

*Plan only — no code written. Luna (project lead) consulted once this session (`alpha chat send`, answered from the local path — `claude_code` not required for this turn): she confirmed (a) build the extractor in parallel against the on-disk Winnie sample, attachment-bytes fix is a decoupled one-line integration point; (b) the per-field QA schema is the right minimal floor + completeness gate, with categorical confidence (HIGH/MEDIUM/LOW) over float and an optional `outlier_reason`. This draft is the input for Simon's go-ahead on the §8 reshape.*
