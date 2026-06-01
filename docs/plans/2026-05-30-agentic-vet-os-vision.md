# Agentic Operating System for Veterinary Practices — VISION + ARCHITECTURE (DRAFT)

**Date:** 2026-05-30
**Author:** Vision + architecture session (codebase research + 4 structured Luna consultations)
**Status:** DRAFT — research + synthesis only. No code. Not committed; a working draft for Simon.
**Method:** Grounded in the AgentProvision codebase (every "what exists" claim cites a path) and 4 structured `alpha chat send` turns with Luna (the platform supervisor). Luna's input is marked **[Luna]**; aspiration is marked **(vision)**; built capability cites a file.
**Builds on:** the narrow Phase-1 wedge in [`2026-05-30-veterinary-mvp-discovery.md`](2026-05-30-veterinary-mvp-discovery.md). This is the **broader OS vision** that the cardiology MVP is **Phase 1 of**. It references that doc rather than repeating it — read the discovery for the cast (Brett, Angelo), the sample data, the measurement-QA contract, and Codex's §8 net-new list.

---

## 1. Vision — the agentic OS for veterinary practices

**The user's vision (verbatim):** *"create an agentic OS for veterinaries that can connect to all of their data sources and manage whole operations of veterinaries."*

This is **AgentProvision packaged for the veterinary vertical**: a memory-first, multi-agent orchestration platform deployed as the practice's **coordination layer** — one per-tenant brain that connects to every system a practice already runs (PIMS, scribe, imaging, labs, calendar, email/SMS, accounting, reputation), synthesizes their fragmented state into one unified practice record, and runs the daily operating loop end-to-end while keeping a licensed human in the loop for every clinical and financial decision.

**[Luna] — the load-bearing thesis (what makes it an OS, not a bag of automations):**
> "A veterinary practice is not a set of disconnected tasks, it is a **live operating environment** with patients, clients, staff, inventory, revenue, compliance, and follow-up obligations constantly changing state. AgentProvision becomes an OS when it **maintains that shared state, enforces role-based permissions, coordinates specialized agents, and manages workflows end-to-end** across systems instead of merely triggering point automations."

The OS loop, in Luna's words, is: **"notice what changed, decide what needs attention, draft or perform the next action, and escalate anything that requires licensed or financial approval"** — with a complete audit trail so humans stay accountable for clinical judgment, medical recommendations, pricing, refunds, and hiring. It does **not** replace the veterinarian, manager, or owner; it **prepares decisions, executes approved protocols, routes exceptions**, and surfaces the few calls only a human may make.

The difference between this and Modern Animal's Herriot (§7) is structural: Herriot is a pet-owner-facing chat agent grounded in **one proprietary EMR they own**; this is a **back-office operating system** for the independent practice, grounded in **whatever PIMS the practice already uses**, with the human-in-the-loop as a trust feature, not a limitation.

---

## 2. Data-source connector map

A vet practice's data is scattered across a dozen vendors. The OS connects to each via the same two AgentProvision primitives: the **integration vault** (Fernet-encrypted credentials + the integration registry — OAuth tokens, session tokens, API keys, per-tenant) and an **MCP tool** that the agent fleet calls over SSE. Some connectors are already built; most of the long tail is net-new.

### 2.1 How AgentProvision connects to any source (the pattern)

1. Credential lands in the **integration vault** (`integration_credential.py`, `orchestration/credential_vault.py` — Fernet-encrypted) via the integration registry / `/integrations` connect flow.
2. An **MCP tool** in `apps/mcp-server/src/mcp_tools/<vendor>.py` fetches that credential at call time (`_get_oauth_token` / `_get_<vendor>_credentials`) and exposes typed verbs (`pulse_get_patient`, `scribblevet_get_note`, `send_email`, …).
3. The **agent fleet + dynamic workflows** call those verbs; results are normalized into **knowledge-graph entities/relations/observations** (the unified record, §3).
4. Where there is no public API (ScribbleVet), the **email-ingest fallback** routes the data through `InboxMonitorWorkflow` + Gmail/M365 (`email.py`) instead.

### 2.2 What exists vs net-new (grounded in `apps/mcp-server/src/mcp_tools/`)

| Source category | Vendors | AgentProvision connection | Status |
|---|---|---|---|
| **PIMS — Covetrus Pulse** | Covetrus Pulse | `covetrus_pulse.py` — `pulse_get_patient`, `pulse_list_appointments`, `pulse_query_invoices`; OAuth + HMAC signing + Redis cache + location filtering | **BUILT** (partner-API-gated; see research) |
| **PIMS — other** | ezyVet, Cornerstone, Provet Cloud, Vetspire, Shepherd, Digitail, Instinct, Avimark | integration vault + new MCP module per vendor | **NET-NEW** (none in tree) |
| **AI scribe — ScribbleVet** | ScribbleVet (Instinct Science) | `scribblevet.py` — `scribblevet_list_recent_notes`, `scribblevet_get_note`, `scribblevet_search`; OAuth + SOAP normalization | **BUILT** (no public API → email-fallback path) |
| **AI scribe — other** | Instinct, others | new MCP module / email-ingest fallback | **NET-NEW** |
| **Imaging / radiology AI** | Antech Imaging, SignalPET, Vetology | new MCP module + vault | **NET-NEW** (no module exists) |
| **Imaging — DICOM / PACS** | DICOM modalities, PACS | new ingestion path (no DICOM lib in tree) | **NET-NEW** (samples are PDF/JPEG, not DICOM — see discovery §2.1) |
| **Labs** | IDEXX, Antech Reference, Heska | new MCP module + vault | **NET-NEW** |
| **Scheduling / calendar** | Google Calendar | `calendar.py` — `list_calendar_events`, `create_calendar_event` | **BUILT** |
| **Email** | Gmail, Microsoft 365 | `email.py` — `search_emails`, `read_email`, `send_email`, `download_attachment`, `deep_scan_emails`; OAuth for both | **BUILT** |
| **SMS / messaging** | Twilio SMS, WhatsApp | `sms.py` — `send_sms`, `list_sms_threads`, `read_sms`; `whatsapp_service.py` (neonize) | **BUILT** |
| **Drive / documents** | Google Drive | `drive.py` — `create_drive_file`, `read_drive_file`, `search_drive_files`, `list_drive_folders` | **BUILT** |
| **Accounting / billing** | QuickBooks, Xero, AAHA chart-of-accounts export | `bookkeeper_export.py` — `bookkeeper_export_aaha`; `reports.py` — `generate_excel_report`, `extract_document_data` | **PARTIAL** (export/report built; live accounting API net-new) |
| **Marketing / reputation** | BrightLocal | `brightlocal.py` — `brightlocal_list_keywords`, `brightlocal_get_rankings`, `brightlocal_rank_changes`, `brightlocal_competitor_check`; ads via `ads.py` (Meta/Google/TikTok) | **BUILT** |
| **Phone / VOIP** | RingCentral, Weave, OpenPhone, Spruce | new MCP module + vault (call-recording → record auto-attach) | **NET-NEW** (Angelo inventory §2 unconfirmed) |
| **Web / public data** | websites, news | `web.py` — `web_search`, `fetch_url`, `discover_companies` | **BUILT** |

**Headline:** the **communication + reputation + Drive + calendar + report-export spine is already built** (Gmail/M365, SMS/WhatsApp, Drive, Calendar, BrightLocal/ads, AAHA/Excel export), and **two real PIMS-orbit connectors exist (Covetrus Pulse + ScribbleVet)**. The **clinical-data long tail is net-new**: imaging/DICOM/PACS, labs (IDEXX/Heska), and every PIMS beyond Pulse. So the connector layer is **~half-built for a single-practice GP loop, near-zero for the multi-PIMS / clinical-instrument long tail.**

---

## 3. The unified practice record — the OS's core data thesis

**Thesis:** the **pgvector knowledge graph** (entities / relations / observations, per-tenant — `knowledge_entity.py`, `knowledge_relation.py`, `knowledge_observations`; tools in `knowledge.py`: `create_entity`, `create_relation`, `get_entity_timeline`, `search_knowledge`) becomes the **single synthesized source of operational truth**, assembled across every connected source: **patient → owner → visit → problem/diagnosis → labs → imaging → meds → invoice → referral → communication.** Every agent reads and writes through this one record instead of repeatedly reassembling context from a dozen siloed APIs.

**[Luna] — why a synthesized graph beats federating reads into each PIMS:**
> "A PIMS is optimized for transactions; the OS problem is different: *what changed, what does it mean, who owns the next action, and what needs approval?* … Federated reads keep every source a silo — useful for lookup, weak for coordination, because every workflow has to repeatedly reassemble context. A synthesized graph lets agents operate against one canonical practice state: unresolved problems, pending callbacks, overdue diagnostics, open estimates, missing records, referral loops, medication conflicts, revenue leakage, compliance gaps. **The strongest argument: agents need a shared world model.** If each agent reads directly from each PIMS, the fleet is a collection of point automations. If every agent reads and writes through a unified practice record, the fleet can coordinate work, maintain audit trails, and escalate exceptions against the same source of operational truth."

This is also why the graph is the **right** model for **multi-location practices** like The Animal Doctor SOC: the same owner, patient, referral partner, diagnostic vendor, or medication pattern appears across separate PIMS accounts — the OS needs **identity resolution and cross-source memory**, not just per-system API access.

**[Luna] — the one biggest risk + the hard requirement (quotable):**
> "The synthesized graph becomes **confidently wrong**. If identity resolution, extraction, source freshness, or writeback reconciliation is wrong, the KG could merge the wrong patients, preserve stale diagnoses, misattribute labs, or trigger workflows from outdated facts. That is more dangerous than a normal integration bug because the **error becomes centralized and reused by every agent**. So the hard requirement is **provenance-first truth**: every KG fact needs **source, timestamp, confidence, ownership, and reconciliation status**. The KG can be the *operating* truth, but clinical/legal truth still needs traceability back to source systems and human-approved corrections."

**Design consequence (vision):** the KG is the **operating** source of truth (what agents coordinate against), **not** the system of legal/clinical record. The PIMS/labs/scribe remain the canonical clinical record; the KG carries provenance back to them. This mirrors the discovery's **measurement-QA contract** (per-field source page + value + confidence + outlier/missing flags before any drafting) — generalized from one echo report to **every fact in the practice record**.

---

## 4. Operations map → agent fleet

The functions a practice runs, the agent(s) that own each, and the single most important human-in-the-loop point. **[Luna]-authored map** (turn 3), reconciled to AgentProvision's fleet + supervisor model (Luna supervisor routes to function agents; A2A coalition for cross-function tasks):

| Function | Owning agent(s) | Single human approval point | Primary data sources / workflows |
|---|---|---|---|
| Front desk & scheduling | Scheduling Agent, Capacity Agent | Manager approves exceptions & overbooking | Calendar (`calendar.py`), PIMS appointments (`pulse_list_appointments`) |
| Intake & triage | Intake Agent, Triage Agent | Licensed staff approves urgency guidance | Email/SMS/WhatsApp, intake form, VCPR flag |
| Clinical documentation (scribe → records) | Scribe Agent, Records Agent | **Veterinarian signs the final medical record** | ScribbleVet (`scribblevet.py`) → KG; `ScribbleVet Note Sync` workflow |
| Diagnostics & specialist reports (cardiac/imaging/labs) | Diagnostics Agent, Specialist Report Agent | **Veterinarian approves clinical interpretation** | Echo PDF / labs / imaging → KG; `Cardiac Report Generator` + `human_approval` (Phase 1) |
| Client communication (reminders/results/follow-ups/recalls) | Comms Agent, Recall Agent | Human approves sensitive medical messages | Email (`email.py`), SMS/WhatsApp (`sms.py`); `Follow-Up`/reminder workflows |
| Billing & collections | Billing Agent, Collections Agent | Manager approves financial exceptions | `pulse_query_invoices`, `bookkeeper_export_aaha`, `reports.py`; `Monthly Billing` workflow |
| Inventory & pharmacy | Inventory Agent, Pharmacy Agent | **Authorized human approves prescriptions** | PIMS inventory, pharmacy vendor (net-new) |
| Marketing & reputation | Reputation Agent, Campaign Agent | Owner approves public-facing content | BrightLocal (`brightlocal.py`), ads (`ads.py`), competitor (`competitor.py`) |
| Referral management | Referral Agent, Records Transfer Agent | **Veterinarian approves the referral package** | Email + Drive; the cardiology send-back loop is the canonical instance |

**Note (vision vs built):** the **named agents above are aspirational fleet roles** — only the **Luna supervisor** + the **Cardiac Report Generator / ScribbleVet Note Sync / Monthly Billing** workflows exist today (`workflow_templates.py`). The agent fleet is provisioned via the existing Agent model + ALM (ownership, versioning, audit) — these are new agent *configurations*, not new platform code.

---

## 5. Architecture — mapping to AgentProvision primitives

The vet OS is **not new platform code** — it is the existing AgentProvision substrate, configured for one vertical. Every layer below already exists; the vet-specific work is **connectors, workflow templates, agent configs, and guardrail values.**

```
┌──────────────────────────────────────────────────────────────────────────┐
│ VIEWPORTS                                                                  │
│  /dashboard Control Center · alpha CLI · Tauri · WhatsApp/SMS · email      │
│  (Brett's approval surface; manager/owner consoles)                        │
└──────────────────────────────────────────────────────────────────────────┘
                              ↕  publish_session_event / v2 SSE
┌──────────────────────────────────────────────────────────────────────────┐
│ AGENT FLEET + SUPERVISOR  (per-tenant = one practice)                      │
│  Luna Supervisor routes → Scheduling · Intake/Triage · Scribe/Records ·    │
│  Diagnostics/Specialist-Report · Comms/Recall · Billing/Collections ·      │
│  Inventory/Pharmacy · Reputation/Campaign · Referral                       │
│  A2A coalition (CoalitionWorkflow + Blackboard) for cross-function tasks   │
│  ALM: ownership · versioning · rollback · audit · performance snapshots    │
└──────────────────────────────────────────────────────────────────────────┘
                              ↕
┌──────────────────────────────────────────────────────────────────────────┐
│ AUTOMATION SPINE — Dynamic Workflows (Temporal + JSON DSL)                 │
│  workflow_templates.py · step types: mcp_tool · agent · condition ·        │
│  for_each · human_approval · internal_api · cli_execute                    │
│  Cardiac Report Generator · ScribbleVet Note Sync · Monthly Billing · …    │
└──────────────────────────────────────────────────────────────────────────┘
                              ↕
┌──────────────────────────────────────────────────────────────────────────┐
│ UNIFIED PRACTICE RECORD — pgvector Knowledge Graph (per tenant)           │
│  entities/relations/observations · provenance-first (source/ts/confidence)│
│  patient·owner·visit·dx·labs·imaging·meds·invoice·referral·communication   │
└──────────────────────────────────────────────────────────────────────────┘
                              ↕
┌──────────────────────────────────────────────────────────────────────────┐
│ CONNECTOR LAYER — Integration Vault (Fernet) + MCP tools (over SSE)        │
│  Pulse · ScribbleVet · Gmail/M365 · Calendar · Drive · SMS/WhatsApp ·      │
│  BrightLocal/ads · AAHA export │ NET-NEW: imaging · labs · other PIMS      │
└──────────────────────────────────────────────────────────────────────────┘
                              ↕
┌──────────────────────────────────────────────────────────────────────────┐
│ GUARDRAILS + LEARNING (cross-cutting)                                      │
│  human_approval gates · Value Arbitration tenant_norm vetoes ·             │
│  platform_safety_io content floor · RL experiences · memory-first recall   │
└──────────────────────────────────────────────────────────────────────────┘
```

- **Per-tenant practice** — multi-tenancy is the existing model (`tenant_id` on every row); one tenant = one practice (multi-location handled by KG identity resolution + `location_id` filtering already present in Pulse tooling).
- **Agent fleet + Luna supervisor** — the Root Supervisor → team routing model, configured with the §4 fleet. New agents are config + ALM rows, not code.
- **Dynamic workflows = the automation spine** — JSON `definition.steps[]` interpreted by `DynamicWorkflowExecutor`; the vet OS ships as **workflow templates**, the same way the cardiac flow already does.
- **KG = unified record** — §3; provenance-first.
- **Integration vault + MCP** — §2; one credential, one tool, every agent.
- **A2A coalition** — cross-function tasks (e.g. "new cardiac diagnosis → update record → notify owner → adjust recall schedule → flag billing") run as a `CoalitionWorkflow` over a shared Blackboard.
- **`human_approval` + Value Arbitration guardrails** — every clinical/financial gate in §4 is a `human_approval` step; hard rules ("never prescribe without authorization", "never send a report before vet approval", "never assign a stage when key measurements are missing") are **Value Arbitration `tenant_norm` veto signals**, not prompt prose (per the competitor brief's deterministic-guardrails lesson, §7).
- **RL + memory learn each practice's norms** — `rl_experience` logs each autonomous decision; memory-first recall pre-loads the practice's patterns (delivery rules, brand voice, typical recall cadence) so the OS adapts per-practice over time.

---

## 6. Wedge-first roadmap

Three phases, each with the capability, the data sources it lights up, the agents/workflows, and the value proven. **The beachhead fork is resolved in favor of Phase 1 (Brett-cardiology) — see the fork box below.**

### Phase 1 — Cardiology referral-report flow (Brett / BB Cardiology) — THE BEACHHEAD
- **Capability:** inbound echo PDF → deterministic structured extraction (measurement-QA contract) → DACVIM draft in Brett's exact template → **Brett approves** → finalized Google Doc + email back to the referring GP. Carry forward the discovery's **measurement-QA contract** + the **4 net-new pieces from §8** (attachment-tool change to preserve PDF bytes; OCR fallback; approval-review UI showing the extracted table separately with confidence + source-page traceability; the `send_email` send-back loop).
- **Data sources lit:** Gmail-in + attachment parse + Drive-out + email-back. **No PIMS required.**
- **Agents/workflows:** Diagnostics Agent + Specialist Report Agent on a re-shaped `Cardiac Report Generator` workflow + `human_approval` gate; case persisted to KG.
- **Value proven:** report turnaround collapses (hours of typing → minutes of review) with Brett in control of every clinical claim; each case becomes structured KG data; proves **clinical safety + workflow ownership + the human-in-the-loop trust pattern** that the whole OS depends on.

### Phase 2 — GP-practice operating loop (Angelo / The Animal Doctor SOC)
- **Capability:** the daily GP loop — scribe → records sync, scheduling, client comms (reminders/results/recalls), billing/collections — across a real PIMS.
- **Data sources lit:** Covetrus Pulse (`covetrus_pulse.py`, partner-gated), ScribbleVet (`scribblevet.py` / email fallback), Calendar, Gmail/M365, SMS/WhatsApp, AAHA export. **Adds the clinical-data connectors** (labs, imaging) as they come online.
- **Agents/workflows:** Scribe/Records, Scheduling/Capacity, Comms/Recall, Billing/Collections agents on the `ScribbleVet Note Sync` + `Monthly Billing` + new scheduling/comms workflows.
- **Value proven:** the OS runs a whole hospital's daily loop, multi-location, against the PIMS the practice already uses — the "notice→decide→act→escalate" loop at GP scale.

### Phase 3 — Full multi-function OS + multi-practice
- **Capability:** all nine functions (§4) running together with A2A coalitions for cross-function tasks; inventory/pharmacy + reputation fully online; the OS as the practice's coordination layer.
- **Data sources lit:** the full connector map (§2), including the multi-PIMS long tail (ezyVet/Cornerstone/Provet/Vetspire/Shepherd/Digitail/Avimark) and labs/imaging/PACS.
- **Agents/workflows:** the full fleet + cross-function `CoalitionWorkflow`s; per-practice RL/memory tuning.
- **Value proven:** AgentProvision packaged as the **veterinary agentic OS** — one brain, many practices, multi-PIMS, human-in-the-loop, full back-office automation.

### ⚖️ The beachhead fork — RESOLVED

| Option | For | Tradeoff if chosen |
|---|---|---|
| **(1) Brett-cardiology** ✅ **RECOMMENDED** | Tightest loop: one specialist, one doc type, one approval, ~80% scaffolded → fastest path to trust-bearing production proof | Delays the larger GP OS surface; less immediate proof the platform runs a whole hospital loop |
| (2) Angelo-GP | Broad, multi-function, bigger market, real PIMS | Bigger surface; Pulse API partner-gated (6–8 wk) blocks the critical path; slower to first value |
| (3) Both in parallel | Maximum coverage | Splits focus across two trust models + two integration surfaces before either is proven; highest execution risk |

**[Luna] — doc-ready recommendation (turn 4, verbatim):**
> "Luna Supervisor recommends **BRETT-CARDIOLOGY** as the beachhead: win the narrow high-trust specialist report workflow first, because it can reach production-value proof fastest; accept the tradeoff of postponing Angelo's broader GP OS until the clinical-reporting wedge is proven and reusable."

**Synthesis recommendation:** **adopt Phase 1 (Brett-cardiology) as the beachhead.** The tradeoff accepted — slower access to the broader GP market — is exactly why Angelo is the *second* beachhead, not the first. The cardiology flow proves the **human-in-the-loop + provenance-first + workflow-ownership** patterns that **every** later function reuses; starting broad (Angelo or both) risks burning trust on a wide surface before the safety pattern is proven, and stalls on the Pulse partner gate.

---

## 7. Competitive position — vs Modern Animal / Herriot

Grounded in `docs/research/2026-05-09-modern-animal-harriet-sierra.md`. Modern Animal's **Herriot** (Sierra-powered) proves AI-vet demand, but its shape is the inverse of ours:

| Dimension | Modern Animal / Herriot | AgentProvision vet OS — our edge |
|---|---|---|
| **PIMS** | Grounded in **Claude**, their *proprietary* EMR — only possible because they own both ends | **Multi-PIMS** — reads the practice's *existing* Pulse/ezyVet/etc. (`covetrus_pulse.py` + net-new modules). The owner never switches systems. **This is the moat.** |
| **Who it serves** | **Corporate-only.** Independent practices cannot buy Herriot | Independent + specialist + referral-network practices — the lane Herriot structurally cannot enter |
| **Who it faces** | **Pet-owner-facing** chat (triage, routine Q&A, handoff) | **Back-office operating system** — runs the *practice's* operations, not the owner's chat |
| **Brand voice** | Corporate, homogenized | **Independent-practice brand voice** (`tenant_branding`, lead vet's name) — "Dr. Castillo's clinic, 24/7" beats "Modern Animal corporate" |
| **Channels** | Locked into the member app | Email + **SMS/WhatsApp as peer channels** (`sms.py`, `whatsapp_service.py`) — Herriot can't match without breaking its lock-in moat |
| **Trust model** | "30 min → seconds" implies **no synchronous human review** of owner-facing replies | **Human-in-the-loop** on every clinical/financial decision — a *trust feature* for licensed work, not a limitation |
| **Pricing** | Bundled into membership ($99–199/yr), no standalone SKU | **Outcomes-based** (per resolved task / report) — fits sole-prop economics, lifts Sierra's own model into the independent vertical |
| **Eval maturity** | Sierra's τ-bench + conversation-replay regression is **ahead of us** | Honest gap — we have the Gemma-4 council + RL but no mock-API regression harness yet (match within a quarter) |

**Net:** the independent-specialist + referral-network + full-back-office lane is **wide open** — Modern Animal/Herriot is corporate, owner-facing, single-EMR by construction. Our structural advantages (multi-PIMS, independent voice, SMS peer channel, human-in-the-loop trust, outcomes pricing) are exactly the things they **cannot** copy without dismantling their lock-in. The one place we must catch up is **eval maturity** (deterministic guardrails as `tenant_norm` values + a conversation-replay regression harness).

---

## 8. Risks + open questions for Simon

**Lead with the beachhead + data reality:**

1. **Endorse the beachhead = Brett-cardiology (Phase 1)?** Luna and this synthesis both recommend it; the accepted tradeoff is postponing Angelo's broader GP OS. Confirm, or override toward Angelo-GP / both — this is the single decision that shapes everything downstream.
2. **Which vet data sources are real & accessible TODAY?** Of the §2 map, only Pulse + ScribbleVet are built on the PIMS-orbit side, **both partner-gated** (Pulse: Covetrus Connect, 6–8 wk; ScribbleVet: no public API → email fallback / Instinct partner path). Imaging, labs, and every other PIMS are net-new. **What is actually connectable in the next 60 days vs aspirational?** (Phase 1 sidesteps this — Gmail-in/Doc-out needs no PIMS.)
3. **Is the KG the *operating* truth but not the *legal* record?** This synthesis (following Luna's provenance-first requirement) treats the PIMS/labs/scribe as the system of clinical record and the KG as the coordination layer with traceback. Confirm this is the right boundary — it determines write-back posture and liability surface.
4. **Where do humans approve?** §4 names a vet/manager/owner approval point per function. For Phase 1 that's Brett's echo-report review (the discovery §4-Q4 surface decision: `/dashboard`, email, WhatsApp, or legacy `health-pets` UI). For the OS broadly, **who holds which approval role**, and how is that surfaced (single approval inbox vs per-channel)?
5. **Multi-tenant placement.** Does the vet OS live in the existing HealthPets-style tenant, a fresh BB Cardiology tenant, Angelo's existing tenant (`7f632730-…`), or Simon's current tenant (`752626d9-…`)? Affects where templates, KG, and credentials live (also flagged in discovery §4-Q7).
6. **Eval/guardrail investment now or later?** The competitive gap vs Sierra is the conversation-replay regression harness + deterministic `tenant_norm` guardrails. Build the guardrail values in Phase 1 (cheap, high-trust-value) and defer the regression harness to Phase 2 — or pull both forward?

---

## 9. Build-on assets (concrete pointers)

- **Discovery (Phase-1 detail):** [`docs/plans/2026-05-30-veterinary-mvp-discovery.md`](2026-05-30-veterinary-mvp-discovery.md) — measurement-QA contract, 4 net-new pieces, the cast, sample data.
- **Connectors (built):** `apps/mcp-server/src/mcp_tools/` — `covetrus_pulse.py`, `scribblevet.py`, `email.py`, `drive.py`, `calendar.py`, `sms.py`, `brightlocal.py`, `ads.py`, `bookkeeper_export.py`, `reports.py`, `web.py`, `knowledge.py`; `apps/api/app/services/whatsapp_service.py`.
- **Automation spine:** `apps/api/app/services/workflow_templates.py` — `Cardiac Report Generator`, `ScribbleVet Note Sync`, `Monthly Billing`; `human_approval` + `agent` + `mcp_tool` + `internal_api` step types.
- **Unified record:** `apps/api/app/models/knowledge_entity.py`, `knowledge_relation.py`; `knowledge.py` tools (`create_entity`, `create_relation`, `get_entity_timeline`, `search_knowledge`).
- **Vault + guardrails:** `integration_credential.py`, `orchestration/credential_vault.py`; Value Arbitration (`docs/plans/2026-05-23-value-arbitration-design.md`); `platform_safety_io`.
- **Prior research:** `docs/research/2026-05-09-{covetrus-pulse,scribblevet,modern-animal-harriet-sierra}*.md`; `docs/onboarding/2026-05-09-angelo-platform-inventory.md`.

---

*Vision + architecture synthesis only. Luna consulted across 4 structured `alpha chat send` turns (OS thesis, ops→fleet map, unified-record pressure-test, beachhead fork) — her input is marked **[Luna]**. No code written; this draft is the input for Simon's vertical-strategy and beachhead decision. The cardiology MVP (discovery doc) is Phase 1 of this OS.*
