# Veterinary MVP — Discovery + Plan (DRAFT)

**Date:** 2026-05-30
**Author:** Discovery session (codebase research + Luna consultation)
**Status:** DRAFT — discovery + synthesis only. No code, no implementation. Not committed; left as a working draft for Simon.
**Method:** Grounded in the existing AgentProvision + `health-pets` codebases and 5 structured `alpha chat send` consultations with Luna (the platform orchestrator). Where a claim is Luna's opinion it is marked; where it is codebase-verified it cites a path.

---

## TL;DR

The veterinary thread already has a real, narrow, valuable wedge hiding in plain sight: **Dr. Brett Boorstin's mobile cardiology referral practice (BB Cardiology / BTC Vet Mobile)** and its **DACVIM cardiac-report production pipeline**. The MVP is not a GP-practice chatbot, not a PMS consolidation, not a pet-owner concierge — it is a **cardiology referral-report operations assistant** for Brett: inbound echo PDF → structured extraction → DACVIM draft in Brett's exact template → **Brett approves** → finalized Google Doc + email back to the referring GP.

The platform **primitives** we need all exist (the `Cardiac Report Generator` workflow, Gmail/Drive MCP tools, the pgvector knowledge graph, a real `human_approval` step type, `pdfplumber` PDF text extraction, Brett's template + finalized samples) — but, per Codex review (§8), this is a **focused build on those primitives, not a config tweak**. The current cardiac workflow has **no** extraction step, **no** approval gate, and **no** send-back; the Gmail attachment tool truncates the PDF to `content[:10000]` plain text, which must change before deterministic table extraction + page-traceability are possible. Realistically **~4 concrete additions** (§8), not a greenfield build.

---

## 1. What we have today (codebase-grounded)

### 1.1 The people (corrected from the brief)

The actual sample data clarifies the cast:

- **Dr. Brett Boorstin, DVM, DACVIM (Cardiology)** — the **veterinary cardiologist** and primary domain expert. Brand: **BB Cardiology** (institution string on echo reports reads `BB CARDIOLOGY`); intake mailbox `btcvetmobile@gmail.com` ("BTC Vet Mobile"). He runs a **mobile specialty practice** that receives referrals from many GP practices. The report sign-off is literally `Sincerely, Brett Boorstin, DVM, DACVIM (Cardiology)`.
- **Dr. Angelo Castillo** — a **referring/partner GP vet**, owner of **The Animal Doctor SOC** (3 locations: Anaheim, Buena Park, Mission Viejo; tenant `7f632730-1a38-41f1-9f99-508d696dbcf1`). He runs **Covetrus Pulse** (PIMS) + **ScribbleVet** (AI scribe). He is the *recipient/test partner* of cardiac reports and the anchor for a separate, larger "GP-practice ops" thread — **not** the primary user of the cardiac MVP.
- **Brett** is therefore the cardiac-MVP user; **Angelo** is the referral-side recipient and a future second product surface.

> Note: the brief framed "Brett" as business owner and "Dr. Angelo" as the cardiologist. The artifacts invert this: **Brett is the cardiologist**, Angelo is the GP. The "DACVIM report voice" is **Brett's**.

### 1.2 Sample data (real, on disk in the `health-pets` sibling repo)

`/Users/nomade/Documents/GitHub/health-pets/docs/data/`:
- **`MR B2 (TEMPLATE).pdf`** — Brett's **DACVIM Cardiac Evaluation Report template** with `[BRACKET]` slot placeholders: `[MILD/MODERATE/SEVERE]` thickening, `[ACVIM Stage]`, fixed boilerplate **Anesthesia/Fluid/Steroid Risk** paragraphs, standard pimobendan/Vetmedin dosing language, fixed sign-off. This is the literal style skeleton.
- **`Winnie Nieto.pdf`** — a **real finalized report**: 13y FS Chihuahua, ACVIM Stage B1, mild MMVD. Includes the prose narrative **and** the raw machine echo output: an **"Adult Echo: Measurements and Calculations"** table (2D: LVIDd, LVPWd, EF, IVSd, LA Area, LA/Ao; MMode; Doppler: LVOT/RVOT/MR Vmax, MV E/A, IVRT, TR Vmax) plus **~12 echo image thumbnails** (B-mode + color + spectral Doppler tracings).
- **`21560820260214_WINNIE_NIETO_...pdf`** — the underlying machine export (filename = study accession `21560820260214`).
- **`Invoice WMAH 2-14-26.pdf`** — a clinic invoice (West Main Animal Hospital) → confirms the billing thread.
- `/Users/nomade/Documents/GitHub/health-pets/backend/uploads/ecg-samples/ecg_12lead.jpg` — a **12-lead ECG image** (JPEG). ECG exists as **images**, not raw signal/waveform data.

**Data-shape conclusion:** The echo "report" is a **machine-generated PDF** carrying both a **structured measurements table** (extractable as text) and **image thumbnails**. ECG is **image-only** (no waveform stream). There is **no DICOM** in the samples.

### 1.3 Existing agents / workflows / tools (verified)

- **`Cardiac Report Generator` dynamic workflow** — `apps/api/app/services/workflow_templates.py` (lines ~221–291). Steps: `search_emails` → `read_email` → `download_attachment` (echo PDF) → **`luna` agent step** with a DACVIM-cardiologist prompt (produces History / Exam / Assessment / Plan with ACVIM/HCM staging) → `create_drive_file` (Google Doc). Trigger: manual. Inputs: `patient_name`, `visit_date`, `email_query`, `account_email`, `drive_folder_id`. **This is the MVP skeleton already in the tree.**
- **`Monthly Billing` workflow** — same file (~292+). Cron `0 6 1 * *`; aggregates visits → invoices → email → follow-ups. (Originally a Temporal `MonthlyBillingWorkflow`; the ADK-era plan is `docs/plans/2026-02-28-healthpets-agents-implementation.md`.)
- **`ScribbleVet Note Sync` workflow** — same file (~1787+). Every 15 min pull finalized SOAP notes → knowledge-graph entities (`source_ref="scribblevet:<note_id>"`). Draft/gated on ScribbleVet integration.
- **MCP tools (real, substantial):**
  - `apps/mcp-server/src/mcp_tools/covetrus_pulse.py` — `pulse_get_patient`, `pulse_list_appointments`, `pulse_query_invoices`; full OAuth + HMAC signing + Redis caching + location filtering. **Partner-API-gated** (see research below).
  - `apps/mcp-server/src/mcp_tools/scribblevet.py` — `scribblevet_list_recent_notes`, `scribblevet_get_note`, `scribblevet_search`; OAuth + SOAP-text normalization. **No public API** (email-fallback path).
  - Gmail (`email.py`: `search_emails`, `read_email`, `download_attachment`, `send_email`), Drive (`drive.py`: `create_drive_file`), Calendar, plus `bookkeeper_export.py`, `brightlocal.py`, `sms.py`.
- **Knowledge graph**: pgvector entities/relations/observations, per-tenant; the canonical place to store the structured case record and Brett's report precedents.
- **Dynamic-workflow step types** include `human_approval` and `agent` and `mcp_tool` and `internal_api` — everything the MVP needs already exists as primitives.

### 1.4 The ADK legacy (context, not current)

An earlier build (`docs/plans/2026-02-28-healthpets-agents-implementation.md`, and the `health-pets/docs/plans/2026-02-28-mobile-cardiologist-workflow-design.md` design) created `cardiac_analyst` (Claude-vision ECG analysis), `billing_agent`, `vet_supervisor`, breed ECG reference seed data (17 breeds), and a two-platform pattern (`health-pets` domain app ↔ agent backend). **ADK was fully removed 2026-03-18**; CLI orchestration + dynamic workflows is the sole path now. The vet capability survived as the **`Cardiac Report Generator` JSON workflow**. The breed-reference idea and the `analyze_ecg_image` vision tool are **design precedent**, not live code, but are reusable.

### 1.5 Prior vet-vertical research already done (do not redo)

- `docs/research/2026-05-09-covetrus-pulse-api-research.md` — Pulse API is gated behind **Covetrus Connect** partner program, **6–8 week** approval, OAuth `client_id/client_secret`. Scraping rejected.
- `docs/research/2026-05-09-scribblevet-api-research.md` — ScribbleVet has **no public API**; acquired by Instinct Science (Jan 2026). Path = partner application via Instinct, or **email-ingest fallback** into `InboxMonitorWorkflow`.
- `docs/research/2026-05-09-modern-animal-harriet-sierra.md` — competitor brief: **Modern Animal's "Herriot"** (Sierra-powered) is **corporate-only, pet-owner-facing**; independent practices can't buy it. Key competitive gaps for us: multi-PMS (not proprietary EMR), independent-practice brand voice, SMS as a peer channel, outcomes-based pricing.
- `docs/onboarding/2026-05-09-angelo-platform-inventory.md` + `docs/plans/2026-05-09-platform-inventory-questionnaire.md` — Angelo's stack map (Pulse, ScribbleVet, Antech Imaging, BrightLocal, Genius Events, VMG membership, Google Calendar). This is the **GP-practice** thread, broader than the cardiac MVP.

---

## 2. Discovery findings — the four areas

### 2.1 Electrocardiograms / echocardiograms

**Data formats (verified from samples):**
- **Echocardiogram = machine-generated PDF** with (a) a structured measurements/calculations table and (b) ~12 image thumbnails + an ACVIM/HCM stage. Text-extractable.
- **ECG = image (JPEG)**, e.g. 12-lead. No waveform/signal stream, no DICOM in hand.

**MVP ingestion/interpretation approach (Luna, codebase-aligned):** *structured table extraction first, LLM interpretation second, vision/signal processing later.*
1. **Echo PDFs** → extract the measurement table **deterministically** (PDF text/table parse + OCR fallback). Normalize `LVIDd, LVPWd, EF, LA/Ao, MR Vmax, E/A`, units, species/weight, ACVIM stage. Then LLM **explains/compares against ranges and drafts** — it does not invent measurements.
2. **Echo thumbnails** → **not** a diagnostic source in MVP. Use for QA/context/human review ("image present / plausible modality / label visible"), not as source of truth.
3. **ECG JPEGs** → LLM vision is **risky** for diagnosis. MVP: ingest + human annotation + summary support. Real automated ECG interpretation needs signal processing or calibrated image digitization (grid scale, paper speed, gain, QRS detection) — **out of MVP scope**.

**Accuracy risks (Luna):** OCR/table misreads, missing units, wrong species/weight normalization, machine measurements that are clinically wrong, **overconfident LLM interpretation**, ACVIM-stage mismatch, ECG image scale errors.

**Human-in-the-loop must catch:** extracted values/units, outlier measurements, ACVIM stage, final diagnosis, treatment implications, arrhythmia calls, any "urgent" flags — **before release**.

### 2.2 Dr. Brett's notes / report VOICE

**Shape (verified):** Highly **templated, formulaic** DACVIM prose. Fixed sections (History → Exam → Assessment → Plan), severity slot choices (`[MILD/MODERATE/SEVERE]`), standard boilerplate paragraphs (anesthesia/fluid/steroid risk, pimobendan dosing), consistent sign-off. This is **structured clinical language, not creative prose** — which makes it very capturable.

**Mechanism (Luna's synthesis — quotable):**
> "template = structure, few-shot = voice, RAG = precedent, knowledge graph = clinical routing and guardrails."

- **Rigid template with controlled slot-filling** = the core. Preserve the skeleton exactly; generate only into approved fields. (`MR B2 (TEMPLATE).pdf` is the literal skeleton.)
- **Few-shot finalized reports** (Winnie Nieto, etc.) = teach micro-style: phrasing of impressions, conciseness, echo-findings→recommendations transitions, ACVIM-stage handling.
- **RAG selectively** = retrieve prior similar cases ("small-breed Stage B1," "mild MMVD," "anesthesia-risk paragraph") for precedent — **not** as the free-form writer.
- **Knowledge graph = control layer**: patient → breed/age → diagnosis → ACVIM stage → echo findings → meds → risk → eligible template sections. Constrains generation (e.g., Stage B1 + no CHF + mild LA enlargement → only certain recommendation patterns are eligible).

### 2.3 Vet software (PIMS, scribe, imaging, labs) — where the MVP plugs in

**Practices/vendors in this specific orbit:** Covetrus Pulse (Angelo's PIMS; mobile specialists often have **none** of their own), ScribbleVet (scribe), Antech Imaging (radiology AI), IDEXX/Heska/Antech (labs). Broader market PIMS: ezyVet, Cornerstone, Provet Cloud, Vetspire, Shepherd, Digitail, Instinct, Avimark — relevant only when scaling across many referring GPs.

**Does the MVP need a PIMS integration? Luna: NO, not to start.**
- The cardiac-report core loop is **referral intake → case context → interpretation → report → send-back**. **Gmail-in + attachment parsing + Google-Doc-out + email-back-to-referrer is enough** to validate the highest-value wedge (faster specialist turnaround).
- **Where PIMS actually matters (later):** GP-side source of truth (demographics, prior records, meds, labs); **closed-loop write-back** of the final report into the GP's chart; scheduling/billing; and **multi-practice scaling** (each PIMS becomes a messy integration surface). For MVP, PIMS is a **convenience/retention layer, not the critical path.**
- **Realistic integration sequence (Luna):** (1) no-PIMS Gmail flow; (2) lightweight intake form (referrer/clinic/patient/owner/reason/urgency/attachments) to tame email chaos without an API; (3) per-clinic delivery rules (email, format, naming, CCs); (4) pilot Covetrus export/import (attach report or manual upload); (5) pursue Covetrus Connect API **only after** repeat usage + volume justify the 6–8 week gated path.

### 2.4 Modern vets — market + workflow + buyer

**A cardiology referral practice's reality:** Mobile specialist (Brett) travels to GP clinics or receives echo studies remotely; the bottleneck is **report turnaround** — GPs and owners wait days for the cardiac write-up that drives treatment decisions. Every stalled cardiac case is **leaked revenue and eroded client trust** for the GP.

**Pain the MVP removes:** cardiac-report turnaround time; manual re-typing of templated reports; inconsistent formatting; the referral handoff loop (study in → report back).

**Buyer/user split (Luna):**
- **MVP user** = Brett (the cardiologist).
- **Eventual economic buyer** (referral-service fork) = the **GP hospital owner / medical director / practice manager** who loses revenue + client trust when cardiac cases stall. Their buying logic: faster consults, fewer referrals leaking elsewhere, better client experience, more confidence managing cardiac patients.
- **Competitive frame:** Modern Animal's Herriot proves AI-vet demand but is corporate-only + owner-facing. The independent-specialist + referral-network lane is **wide open**; our structural advantages are multi-PMS, independent brand voice, SMS, and outcomes-based pricing (per the competitor brief).

---

## 3. Proposed MVP

### 3.1 One-liner

A **cardiology referral-report operations assistant** for BB Cardiology: it turns an inbound echo study into a Brett-voiced DACVIM draft, gates delivery behind Brett's approval, and emails the finalized report back to the referring GP.

### 3.2 Primary user & core value

- **Primary user:** Dr. Brett Boorstin (cardiologist).
- **Core value:** **Report turnaround time** collapses (hours of typing → minutes of review) while **Brett stays in control** of every clinical claim. Secondary value: every completed report becomes **structured clinical + referral data** (knowledge graph) that powers later products.

### 3.3 The thinnest end-to-end slice (Luna's MVP boundary: one specialty, one doctor, one document type, one intake channel, one approval step, one outbound destination)

```
Inbound echo PDF (Gmail)
   → deterministic structured echo-table extraction (case artifact: patient, owner,
     referrer, measurements + units, abnormalities, missing/low-confidence fields)
   → DACVIM draft generated from Brett's template (+ few-shot + RAG precedent)
   → HUMAN APPROVAL: Brett reviews the extracted table AND the draft (approve / edit / reject)
   → on approval: finalized Google Doc  +  email back to the referring vet
```

### 3.4 Mapping to AgentProvision primitives (Luna-confirmed)

The **single concrete change** that converts the current best-effort `Cardiac Report Generator` into the MVP: **make structured extraction a required intermediate artifact, then gate outbound delivery behind `human_approval`.** Concretely, split the one Luna step into two and insert the gate:

| Slice stage | Primitive | Notes |
|---|---|---|
| Inbound echo PDF | MCP tools `search_emails` → `read_email` → `download_attachment` (`email.py`) | already in the workflow |
| **Structured extraction (NEW)** | new `agent`/`mcp_tool` step `extract_echo_structured_case` → output JSON, persist to **knowledge graph (pgvector)** as the case record | the missing intermediate artifact; deterministic table parse + OCR fallback, with confidence flags + source-page traceability |
| DACVIM draft | existing `agent` step, re-scoped to `generate_dacvim_report_from_template` (inputs: case artifact + `MR B2` template + few-shot finalized reports + RAG of prior cases) | template = structure, few-shot = voice, RAG = precedent |
| **Approval gate (NEW)** | dynamic-workflow `human_approval` step **immediately after draft** | Brett sees the extracted table **separately** from the prose; approve/edit/reject |
| Finalize + deliver | `create_drive_file` (`drive.py`) + `send_email` (`email.py`) to the referring vet | only fires on approval |

Knowledge-graph usage: store each case as patient → breed/age → ACVIM stage → echo findings → meds → risk; this both (a) feeds RAG precedent for future drafts and (b) becomes the structured asset for the referral-service and analytics later.

### 3.5 Strategic wedge (Luna, opinionated)

> "Do **C (referral-service)**, powered by **A (internal tool)**, with **B (SaaS)** as the expansion path."

- **A — internal tool** is the *build environment*: make Brett faster, instrument report time / cases-per-day / revision rate / GP satisfaction. Too small to be the wedge on its own (proves efficiency, not pull).
- **C — referral-service** is the *wedge*: BB Cardiology becomes the **fast, reliable cardiac-reporting layer** GP hospitals route to; AgentProvision powers intake, extraction, drafting, follow-up, client-ready summaries. Buyer = GP hospital owner/medical director.
- **Network expansion:** add more cardiologists behind the service; BB = brand/trust layer, AgentProvision = the operating system.
- **B — SaaS to other specialist groups** comes *after* templates/QA/economics are proven (small buyer pool, workflow variance, liability sensitivity make it a harder first market).

---

## 4. Open questions + decisions for Simon

1. **WHERE / WHAT is the data, exactly? (lead question)** Confirmed sample shape: echo = machine-PDF with a structured measurements table + image thumbnails; ECG = JPEG images; no DICOM, no waveform stream. **But:** Is the production intake genuinely **Gmail attachments** (as the current workflow assumes, `btcvetmobile@gmail.com`)? Or does the echo machine/software export somewhere else (a vendor portal, a shared drive, DICOM PACS)? **Which echo machine/software** generates the table (the format of that table drives the extraction parser)? Are there **more finalized reports** (beyond Winnie/template) to use as few-shot/RAG corpus, and how many?
2. **Target user/practice type for the wedge.** Confirm MVP user = **Brett** and that **Angelo is the first referral-side recipient/test partner**, not the first user. Is there a second cardiologist (or specialist) realistically available to validate the network-expansion step, or is BB Cardiology solo for the foreseeable MVP?
3. **The wedge fork decision.** Endorse Luna's "C powered by A, B later"? Specifically: is the near-term goal **Brett's own efficiency** (internal) or a **packaged fast-turnaround report service** GP practices pay for? This decides whether MVP instrumentation (turnaround metrics, per-clinic delivery rules) is in-scope now or deferred.
4. **Approval surface for Brett.** Where does Brett do the `human_approval` review — the `/dashboard` Control Center, email reply, WhatsApp, or the legacy `health-pets` specialist UI (`/reports/[id]` editor in the design doc)? This is the single biggest UX decision and gates adoption.
5. **Integration priorities.** Agree PIMS is **post-MVP**? If yes, do we still kick off the Covetrus Connect partner application now (6–8 week lead time) so it's ready when volume justifies it — or hold? Same question for the ScribbleVet/Instinct partner path.
6. **Liability / scope-of-claim posture.** What does Brett want the system to **never** do autonomously (e.g., never assign ACVIM stage when key measurements are missing; never send without approval; never alter his risk-paragraph boilerplate)? These become hard guardrails (Value Arbitration `tenant_norm` veto signals), not prompt prose.
7. **Multi-tenant placement.** Does this live in the existing HealthPets-style tenant, a fresh BB Cardiology tenant, or Simon's current tenant (`752626d9-...`)? Affects where the workflow template, knowledge graph, and credentials live.

---

## 5. Risks

| Risk | Severity | Mitigation |
|---|---|---|
| **Wrong measurements confidently embedded in a polished medical report** (Luna: "the one thing most likely to make Brett abandon it — destroys trust faster than bad prose") | **Critical** | Show extracted echo table **separately** from prose; confidence flags; **source page/value traceability**; required Brett approval before send; **no autonomous diagnosis when key measurements are missing/uncertain.** |
| OCR/table-extraction misreads of the machine PDF | High | Deterministic parse + OCR fallback; flag low-confidence fields; human reviews the table, not just the narrative. |
| Over-reliance on LLM vision for ECG/echo thumbnails | High | MVP does not diagnose from images; thumbnails are QA/context only; ECG image interpretation is out of scope. |
| Building for Angelo's broad GP-ops (chatbot, PMS consolidation, billing) instead of the wedge | High | Hard MVP boundary: one specialty, one doctor, one document type, one channel, one approval, one destination. Defer everything else. |
| PIMS-integration rabbit hole (6–8 wk Covetrus gate) blocking the MVP | Medium | PIMS is post-MVP; Gmail-in/Doc-out is the critical path. Optionally start partner app in parallel for lead time, but never block on it. |
| Clinical/regulatory liability (a wrong report reaches a GP/owner) | Medium–High | Human-in-the-loop gate is mandatory; explicit "draft — not yet reviewed by Dr. Boorstin" watermark until approved; audit trail of who approved what. |
| Single-user dependency (whole MVP value = Brett's time) | Medium | Instrument turnaround/cases-per-day early so value is provable and the referral-service case to GPs is data-backed. |

---

## 6. Phasing

**MVP (the thinnest slice — re-shape, don't rebuild):**
- Convert `Cardiac Report Generator` → add `extract_echo_structured_case` step (deterministic table parse → KG case artifact with confidence flags) + insert `human_approval` before delivery.
- Load Brett's `MR B2` template as the slot-filling skeleton; seed few-shot from the finalized reports on hand.
- Outbound = finalized Google Doc + email to the referring GP. Pick Brett's approval surface (Q4).
- Instrument: report turnaround, cases/day, revision rate.

**Next (referral-service wedge, fork C):**
- Lightweight intake form (referrer/clinic/patient/owner/reason/urgency/attachments) to reduce email chaos (no API).
- Per-clinic delivery rules (email, format, naming, CCs); client-ready owner summary as an optional second artifact.
- RAG precedent maturing as the case corpus grows in the knowledge graph; guardrails as Value Arbitration `tenant_norm` signals.

**Later (scale + SaaS, forks toward B):**
- Pilot Covetrus Pulse import/write-back so the report lands in the GP chart (close the loop); start the partner application if not already.
- Add more cardiologists behind the BB brand; add ECG-image annotation support; explore signal-grade ECG only if real demand appears.
- Package as infrastructure for other specialist groups (cardiology → oncology/neurology referral reports) once templates/QA/economics are proven.

---

## 7. Build-on assets (concrete pointers)

- `apps/api/app/services/workflow_templates.py` — `Cardiac Report Generator` (the MVP skeleton) + `Monthly Billing` + `ScribbleVet Note Sync`.
- `apps/mcp-server/src/mcp_tools/`: `email.py`, `drive.py`, `covetrus_pulse.py`, `scribblevet.py` (all real).
- Dynamic-workflow `human_approval` + `agent` + `mcp_tool` step types.
- Knowledge graph (pgvector) for the case artifact + RAG precedent.
- `/Users/nomade/Documents/GitHub/health-pets/docs/data/` — Brett's template + real finalized reports + machine echo export + 12-lead ECG sample.
- Prior research: `docs/research/2026-05-09-{covetrus-pulse,scribblevet,modern-animal-harriet-sierra}*.md`; `docs/onboarding/2026-05-09-angelo-platform-inventory.md`.
- Design precedent (ADK-era, removed but reusable ideas): `docs/plans/2026-02-28-healthpets-agents-implementation.md`; `health-pets/docs/plans/2026-02-28-mobile-cardiologist-workflow-design.md` (clinic/visit/report/invoice data model, breed ECG references, `analyze_ecg_image` vision tool).

---

## 8. Review feedback (Codex + Luna) — folded in 2026-05-30

**Luna (validates the plan):** "Yes, this faithfully captures the discovery." The thinnest-slice boundary is right, and the exclusions are correct (PIMS, automated ECG interpretation, autonomous diagnosis, multi-clinic dashboards, pet-owner comms, billing, scheduling, SaaS — all correctly out of MVP). Her **one add before green-light**:
- **Define an explicit "measurement QA contract" for the structured artifact.** Before any drafting, enforce: required fields present, units, source page + source value, per-field confidence, and outlier/missing flags. **If critical measurements are missing or low-confidence → the draft enters a "needs Brett review" state and avoids strong diagnostic language.** This is the concrete mechanism behind the #1 risk; it turns the MVP from "LLM writes cardiology reports" into "trusted extraction + controlled drafting under specialist approval." → adopt as a first-class artifact contract + a Value-Arbitration `tenant_norm` guardrail.

**Codex (validates the direction; corrects the optimism, with file:line):** the platform **primitives are real** — `human_approval` is a genuine step in schema + executor (`dynamic_executor.py:134-136,292-340`), workflow→KG persistence already works (`Prospect Auto-Pilot` uses `create_entity`, `workflow_templates.py:1992-2055`; KG tools `knowledge.py`), and `pdfplumber` is in-tree (`requirements.in:43`, used in `email.py:829-843`). But "mostly a reshape / ~80%" was **overstated**. The **~4 concrete net-new pieces**:
1. **Attachment/tool contract change (biggest miss, prerequisite).** `download_attachment` extracts plain text and returns only `content[:10000]` (`email.py:829-870`), and the cardiac workflow feeds only `{{echo_pdf.content}}` to Luna (`workflow_templates.py:252-267`). Deterministic table parsing, OCR fallback, and **page-level traceability are impossible** until the tool preserves raw PDF bytes / structured per-page output. Do this first.
2. **OCR fallback is net-new** — no OCR dependency in-tree today. Failure modes: broken table geometry, wrapped headers, missing units, OCR digit confusions, species/weight normalization.
3. **Approval-review UI is net-new.** Backend is fine (step outputs persist in `workflow_step_logs.output_data`, exposed by run-detail APIs), but approval notifications are generic metadata and the bell UI renders only title/body (`NotificationBell.js:157-189`). "Show the extracted table **separately** with confidence flags + source-page traceability" requires new frontend wiring — this is the §4-Q4 approval-surface decision made concrete.
4. **Send-back / delivery loop is absent.** The template stops at Drive save (`workflow_templates.py:270-280`) — no final `send_email`. The **referral-service wedge (fork C) needs the operational intake → delivery → SLA loop, not just drafting.**

Codex confirms the strategy (C powered by A, B later) is sound; the hole is operational (the service is a workflow, not just a model).

**Net corrected scope:** MVP = (a) change the attachment tool to preserve the PDF artifact, (b) add the structured-extraction step + **measurement QA contract**, (c) re-scope the draft step to template+few-shot+RAG, (d) insert the `human_approval` gate **with new table-review UI**, (e) add the final `send_email` send-back. Primitives exist; this is a focused, well-bounded build — not greenfield, but more than a config change.

---

*Discovery only. Reviewed by Luna (faithful-capture + the measurement-QA-contract add) and Codex (primitives confirmed; 4 net-new pieces + the operational-loop hole identified). No code was written; this draft is the input for Simon's go/no-go and scoping decision.*
