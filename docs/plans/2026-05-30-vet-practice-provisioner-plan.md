# Vet-Practice Tenant Provisioner — DESIGN / PLAN (DRAFT)

**Date:** 2026-05-30
**Author:** Provisioner design session (codebase research + 2 structured Luna consultations)
**Status:** DRAFT — plan only, NO implementation code. Not committed; a working draft for Simon.
**Phase:** Advances **Phase ② of the agentic-vet-OS build** (Luna's order: landing → **provisioner** → Brett demo).
**Method:** Grounded in the AgentProvision codebase (every "what exists" claim cites a path) and 2 `alpha chat send` turns with Luna (the platform lead). Luna's input is marked **[Luna]**; aspiration is **(vision)**; built capability cites a file.
**Builds on:**
- [`2026-05-30-agentic-vet-os-vision.md`](2026-05-30-agentic-vet-os-vision.md) — OS vision, the §4 operations→agent-fleet map, the §2 connector map, the §3 provenance-first thesis.
- [`2026-05-30-veterinary-mvp-discovery.md`](2026-05-30-veterinary-mvp-discovery.md) — the cardiology wedge, the measurement-QA contract, the §8 reshape of the Cardiac Report Generator.

---

## TL;DR

The provisioner is a **manifest-driven seed service** — `apps/api/app/services/provisioning/vet_practice.py` — that takes a `tenant_id` + a practice profile and idempotently seeds a ready-to-run veterinary practice: the agent fleet, connector slots (`integration_config` rows, `enabled=false`), vet workflow templates, role/permission defaults, and provenance/audit scaffolding. It **generalizes the existing one-off `seed_animaldoctor_agent_fleet.py`** (a hardcoded 5-agent vet seeder) into a parameterized, reusable provisioner, callable from both an **operator-run internal endpoint** (v1) and — later — the self-serve register path.

Nothing here is new platform *primitive* code: the Agent model, `tool_groups.py` (which **already** ships the vet groups `pulse` / `scribblevet` / `patient_records` / `communication` / `bookkeeper_export`), `integration_config`, `seed_native_templates()`, `AgentPermission`, `human_approval` workflow steps, `write_audit_log`, and the `tenant_norm` value-arbitration veto all exist today. The provisioner is **composition + a manifest**, not a greenfield build.

**[Luna] — the load-bearing principle:**
> "**Nothing is born ownerless.** Every seeded object should answer: who owns it, what may it touch, what may it do without approval, what must it cite, what gets logged, and who reviews exceptions."

---

## 1. Mechanism — how provisioning runs

### 1.1 Recommendation: a manifest-driven **seed service**, fronted by an operator-run internal endpoint

**[Luna] (turn 1, verbatim shape):**
> "I'd name the implementation `app/services/provisioning/vet_practice.py` with a manifest-driven shape: `VetPracticeProvisioningManifest` { agents, workflow_templates, integration_slots, roles, permissions, tenant_norms, audit_policies }. That lets `seed_animaldoctor_agent_fleet.py` become either a wrapper around the service or a deprecated compatibility entrypoint. The provisioner becomes the Phase 2 foundation, not another one-off seed script."

This is the cleanest fit because it reuses every existing seed path:

| Layer | New code | Reuses |
|---|---|---|
| **Service** | `apps/api/app/services/provisioning/vet_practice.py` — `provision_vet_practice(db, tenant_id, profile) -> ProvisioningResult` | the upsert pattern from `seed_animaldoctor_agent_fleet.py:293` (`upsert_agent`, idempotent on `(tenant_id, name)`) |
| **Manifest** | `apps/api/app/services/provisioning/vet_manifest.py` — the declarative `VetPracticeProvisioningManifest` (the §2 fleet, §3 slots, §4 templates, §5 guardrails as data) | — |
| **Endpoint (v1 trigger)** | `POST /api/v1/provision/vet-practice/internal` (internal-key auth, `verify_internal_key` dep like `dynamic_workflows.py:343`) | the internal-endpoint + `X-Internal-Key` pattern |
| **alpha verb (operator UX)** | `alpha provision vet-practice <tenant_id> [--profile cardiology]` → thin call to the internal endpoint | Alpha-CLI-kernel rule (CLAUDE.md): a verb that delegates to the same Python entrypoint |
| **Register hook (deferred to self-serve)** | branch in `create_user_with_tenant` (`users.py:148`) when `tenant_in.practice_type == "veterinary"` → call `provision_vet_practice(...)` instead of the bare single-Luna seed | the existing default-agent seed path |

**Why a service (not just an endpoint, not just register):** the same entrypoint must serve **three callers** — the operator endpoint (v1), the alpha verb (operator UX), and the register hook (self-serve, later). A pure endpoint can't be reached from `create_user_with_tenant`; a register-only hook can't re-provision an existing tenant or be operator-triggered. The service is the single Python entrypoint all three delegate to — exactly the Alpha-CLI-kernel pattern ("the v1 HTTP route is thin — it delegates to the same Python entrypoint the `alpha` binary calls").

### 1.2 Idempotency contract

Mirror `seed_animaldoctor_agent_fleet.py` exactly: every seeded object is **upserted keyed on a natural identity**, never blind-inserted:

- **Agents** — key `(tenant_id, name)`; on re-run, drift-check the `_MANAGED_FIELDS` set (role, description, capabilities, personality, persona_prompt, tool_groups, default_model_tier, autonomy_level, status, version) and write only changed fields. Never delete a pre-existing agent; never clobber human-set `owner_user_id` / `escalation_agent_id` on an existing row (they're outside the managed set).
- **Integration configs** — key `(tenant_id, integration_name)`; insert `enabled=false` only if absent (don't flip a tenant who already connected the credential back to disabled).
- **Workflow templates** — reuse `install_template_internal` semantics (`dynamic_workflows.py:339`): copy the native template into the tenant as `tier="custom"` with `source_template_id`, skip if a copy with the same `source_template_id` already exists for the tenant.
- **Permissions / value-sets** — `AgentPermission` keyed `(agent_id, principal_type, principal_id, permission)`; value-sets are **append-only versioned** writes (`agent_value_set_io.write_value_set`, `added_by="seed"`) — re-running seeds a new version only if the desired set drifts.

`provision_vet_practice` returns a per-object `{created | updated | unchanged}` count (the `seed_fleet` return shape, `seed_animaldoctor_agent_fleet.py:344`) so a re-run is observable and safe.

### 1.3 Practice profile (the parameter)

```python
@dataclass
class VetPracticeProfile:
    practice_name: str                 # "BB Cardiology"
    practice_type: str = "cardiology"  # "cardiology" | "gp" | "multi_specialty"  → selects a manifest variant
    owner_user_id: uuid.UUID | None    # the human who owns every seeded agent (Brett); falls back to tenant admin
    intake_mailbox: str | None         # "btcvetmobile@gmail.com" — wired into Diagnostics/Comms tool params
    lead_clinician_name: str | None    # "Dr. Brett Boorstin" — persona + report sign-off + branding
    fleet_variant: str = "cardiology_v1"  # which manifest fleet cut to apply (see §2)
```

The profile selects a **manifest variant** (`cardiology_v1` ships first; `gp_full` is the Phase-2 Angelo cut). The manifest is the source of truth; the profile is the per-tenant binding (owner, mailbox, clinician name).

---

## 2. The vet agent fleet (concrete)

### 2.1 V1 cut — the Brett-cardiology beachhead

**[Luna] (turn 2):** *"The five-agent v1 set maps cleanly to the Brett loop."* — agreed on Luna Supervisor + Diagnostics/Specialist-Report + Comms/Recall + Referral + Intake/Triage, with one flag:

> **[Luna]** "Intake/Triage in a cardiologist-only workflow is a **classification layer, not a full intake flow** — in v1 it can be thin (classify inbound referral email as echo case, route to Diagnostics agent, flag anything that doesn't match expected format). Keep it, but seed it as a **lightweight routing step**, not a full clinical triage module. Defer the rest — Front-Desk/Scheduling, Scribe, Billing, Inventory, Marketing are all blocked by PIMS, partner gates, or irrelevant to this workflow."

So the v1 manifest seeds **5 agents** (1 supervisor + 4 function agents). All 9 functions are documented; only the 5 below are *seeded* in `cardiology_v1`. `tool_groups` are drawn **only from groups that exist in `tool_groups.py` today** (verified — no net-new group needed for v1).

| Function | Agent (name) | Role | tool_groups (all exist today) | persona gist | **Approval / permission point (who signs off)** | Escalation target | Tier |
|---|---|---|---|---|---|---|---|
| **Supervisor** | **Luna** | `supervisor` | `knowledge`, `email`, `meta`, `a2a` | Routes inbound referral work to the right function agent; maintains case context; never makes a clinical claim itself | n/a (routes; never the final clinical authority) | — (top of tree) | light |
| **Intake & triage** (thin, per Luna) | **Referral Intake Agent** | `triage` | `email`, `knowledge_readonly`, `a2a` | Classify inbound mail as an echo-referral case; extract referrer/clinic/patient; route to Diagnostics; flag malformed/non-echo mail for a human | **Licensed staff** confirms urgency only if flagged; routine routing is autonomous | Diagnostics & Specialist Report Agent | light |
| **Diagnostics & specialist reports** (the core) | **Cardiac Diagnostics Agent** | `specialist` | `email`, `drive`, `knowledge`, `calendar` | Deterministically extract the echo measurement table (measurement-QA contract), draft the DACVIM report in Brett's template + few-shot + RAG precedent; **never invent measurements**; enter "needs Brett review" when key fields missing/low-confidence | **Veterinarian (Brett) approves the clinical interpretation** — `human_approval` gate before any send | Luna (for re-routing) | full |
| **Client communication / recall** | **Comms & Recall Agent** | `communication` | `email`, `communication`, `calendar`, `knowledge_readonly` | On approval, email the finalized report back to the referring GP; schedule follow-up echo/recall; draft owner-ready summary | **Human approves sensitive medical messages** before send (gated by the workflow's post-approval send step) | Cardiac Diagnostics Agent | light |
| **Referral management** | **Referral Liaison Agent** | `coordinator` | `email`, `drive`, `knowledge_readonly`, `a2a` | Assemble + track the referral package (study in → report back); maintain the send-back loop + per-clinic delivery rules; close the loop in the KG | **Veterinarian (Brett) approves the referral package** before it leaves | Cardiac Diagnostics Agent | full |

**Wiring notes (provisioner sets these at seed time, not the human):**
- `escalation_agent_id` is resolved **after** all agents are inserted (two-pass: insert by name, then set FK by name lookup) — the same pattern the doc-vision §4 implies but `seed_animaldoctor_agent_fleet.py` left unset (it's outside `_MANAGED_FIELDS`).
- `owner_user_id = profile.owner_user_id` on **every** agent (Luna's "nothing is born ownerless"). Mirrors `agents.py:246` (`item.owner_user_id = current_user.id`).
- `status="production"`, `version=1`, `autonomy_level="supervised"` for all (the `seed_animaldoctor` defaults).
- `tool_groups_review_required=TRUE` for every seeded function agent **except** the Luna supervisor (which is operator-curated by design, `users.py:200`) — so the operator must confirm each function agent's tool surface before it goes hot. This matches migration 153's column-default flip and the P0a posture in `155_seed_simon_work_fleet_agents.sql`.

### 2.2 Deferred fleet members (documented in manifest, seeded in later variants)

| Function | Future agent | Blocked on | Lands in |
|---|---|---|---|
| Front desk & scheduling | Front Desk Agent | PIMS appointments (`pulse`, partner-gated) | `gp_full` (Phase 2 / Angelo) |
| Clinical docs (scribe→records) | SOAP Note Agent | ScribbleVet (`scribblevet`, no public API → email fallback) | `gp_full` |
| Billing & collections | Billing Agent | Pulse invoices + live accounting API | `gp_full` |
| Inventory & pharmacy | Inventory & Pharma Agent | pharmacy/controlled-substance vendor (net-new) | `gp_full` / Phase 3 |
| Marketing & reputation | Reputation Agent | (works today via `brightlocal`/`ads`) — deferred only because it's irrelevant to the cardiac wedge | `gp_full` |

> Note: `seed_animaldoctor_agent_fleet.py` already encodes Front-Desk / SOAP / Billing / Cardiac-Specialist / Inventory-Pharma personas for tenant `7f632730`. Those personas are the **starting corpus for the `gp_full` manifest variant** — the provisioner should lift them verbatim into the manifest and retire the standalone script per Luna's "wrapper or deprecated compatibility entrypoint."

---

## 3. Connector slots (`integration_config` rows to seed)

The provisioner inserts one `integration_config` row per connector, **`enabled=false`**, awaiting credentials (the credential lands later via the `/integrations` connect flow into the Fernet vault — `integration_credential.py`). Seeding the row means the slot, its `requires_approval` posture, and `rate_limit` are present from t=0; the dashboard shows the practice exactly what it can connect.

| Connector | `integration_name` | `requires_approval` | Real MCP tools today? | Seed in `cardiology_v1`? |
|---|---|---|---|---|
| Google / Gmail | `gmail` | false | **YES** (`email.py`) | **YES** (the intake + send-back spine) |
| Google Drive | `google_drive` | false | **YES** (`drive.py`) | **YES** (finalized Doc out) |
| Google Calendar | `google_calendar` | false | **YES** (`calendar.py`) | **YES** (follow-up/recall) |
| Microsoft 365 | `microsoft` | false | **YES** (`email.py` dual-provider) | optional (alt to Gmail) |
| SMS (Twilio) | `twilio_sms` | true | **YES** (`sms.py` — `send_sms`) | optional (owner reminders) |
| WhatsApp | `whatsapp` | true | **YES** (`whatsapp_service.py`) | optional |
| Covetrus Pulse (PIMS) | `covetrus_pulse` | true | **YES but partner-gated** (`covetrus_pulse.py`; Covetrus Connect 6–8 wk) | **NO** (Phase 2 / `gp_full`) |
| ScribbleVet (scribe) | `scribblevet` | true | **YES but no public API** (`scribblevet.py`; email fallback) | **NO** (`gp_full`) |
| BrightLocal (reputation) | `brightlocal` | false | **YES** (`brightlocal.py`) | **NO** (irrelevant to cardiac wedge) |
| Accounting (QuickBooks/Xero) | `quickbooks` / `xero` | true | **PARTIAL** (export only via `bookkeeper_export.py`; live API net-new) | **NO** (`gp_full`) |
| Imaging / Antech | `antech_imaging` | true | **NET-NEW** (no module) | **NO** (Phase 3) |
| Labs / IDEXX | `idexx` | true | **NET-NEW** (no module) | **NO** (Phase 3) |

**Headline:** the entire `cardiology_v1` connector need — Gmail + Drive + Calendar (+ optional SMS/WhatsApp) — is **already real**. No net-new connector is required to demo the Brett loop. The net-new long tail (imaging, labs, non-Pulse PIMS) is documented in the manifest but seeded in later variants, so the slots are visible without implying false capability.

---

## 4. Workflow templates to seed (install-on-provision)

The provisioner installs per-tenant copies of native templates via the `install_template_internal` path (`dynamic_workflows.py:339` — copies the native `DynamicWorkflow` into the tenant as `tier="custom"`, `source_template_id` set, idempotent on that FK). Native templates themselves are defined in `workflow_templates.py:NATIVE_TEMPLATES` and seeded platform-wide by `seed_native_templates()`.

| Template | Status today | Seed in `cardiology_v1`? | Notes |
|---|---|---|---|
| **Cardiac Report Generator** (`workflow_templates.py:222`) | EXISTS — but per discovery §8 it has **no extraction step, no `human_approval` gate, no send-back**. | **YES — install the RESHAPED version** | The provisioner should install the **measurement-QA-contract** reshape (discovery §3.4 + §8): split the single Luna step → `extract_echo_structured_case` (deterministic table parse → KG case artifact w/ confidence + source-page) → `generate_dacvim_report_from_template` → **`human_approval`** (Brett reviews table + draft separately) → `create_drive_file` → **`send_email`** send-back. This reshape is itself a net-new workflow-template edit (see §6). |
| **ScribbleVet Note Sync** (`workflow_templates.py` ~1787) | EXISTS (draft, gated on ScribbleVet) | NO (`gp_full`) | Lands when scribe connector comes online. |
| **Monthly Billing** (`workflow_templates.py:293`) | EXISTS | NO (`gp_full`) | Needs Pulse invoices. |
| **Reminders / Recall** | partial (the `Follow-Up` / reminder pattern) | optional | Comms & Recall Agent can drive a thin recall workflow from Calendar + Gmail today. |

The manifest's `workflow_templates` field lists template **names**; the provisioner resolves them to the platform-native `DynamicWorkflow` rows and installs tenant copies. Trigger config (manual for Cardiac Report Generator) carries over from the native template.

---

## 5. Provenance / audit scaffolding (Luna's hard requirement)

The provenance-first thesis (vision §3: *every KG fact needs source, timestamp, confidence, ownership, reconciliation status*) must hold for **seeded agents/workflows from t=0**, not just for runtime KG facts. The provisioner writes four provenance layers per the existing primitives:

### 5.1 Ownership — every object has an owner
- Each seeded Agent gets `owner_user_id = profile.owner_user_id` (Brett) — same assignment as the canonical create path (`agents.py:246`). The Luna supervisor included.
- Each seeded `DynamicWorkflow` copy records the operator who provisioned it (`created_by`, set on the install path).

### 5.2 Permissions — capability declaration (`AgentPermission`)
- Seed `AgentPermission` rows (`agent_permission.py`) binding the owner and roles to each agent: `principal_type ∈ {user, team, role}`, `permission ∈ {invoke, edit, promote, deprecate, admin}`, `granted_by` = provisioning operator.
- **[Luna] (turn 2):** *"This is the **capability declaration** — a static operator-set gate defining what the agent is authorized to attempt. Without it, the human_approval gate is floating with no authority model backing it."*
- Roles seeded per the manifest (Luna's turn-1 list): `practice_owner`, `practice_manager`, `veterinarian`, `csr_lead`. These map to `principal_type="role"` permission rows.

### 5.3 Runtime gate — `human_approval` workflow step
- The reshaped Cardiac Report Generator carries a `human_approval` step (a real step type: schema + executor `dynamic_executor.py:134-136,292-340`, confirmed in discovery §8) immediately after the draft. Brett's per-instance sign-off.
- **[Luna] (turn 2):** *"Permissions say the agent **can** draft and send; approval says Brett **did** review this instance. These are different axes."*

### 5.4 Hard floor — `tenant_norm` veto (Value Arbitration)
- The provisioner seeds the practice's hard guardrails as a per-agent value-set via `agent_value_set_io.write_value_set(... added_by="seed")` (append-only versioned, `agent_value_set_io.py:369`), and the platform's `tenant_norm` source class (`value_arbitration.py:108`) enforces them as **non-bypassable vetoes**.
- Seed values (the discovery §4-Q6 / risk-table hard rules) as `protect`/`avoid` items: *never assign an ACVIM stage when key measurements are missing*, *never send a report before vet approval*, *never alter the boilerplate risk paragraphs*, *no autonomous diagnosis from image thumbnails*.
- **[Luna] (turn 2):** *"This is the **non-bypassable hard floor** — 'no autonomous diagnosis, no send without approval' regardless of what permissions declare or what Brett approved in a prior run. It's the guardrail that survives misconfiguration, replay attacks, or future permission expansion."*

### 5.5 Audit trail — `write_audit_log`
- The provisioning run itself emits audit rows (`audit_log.write_audit_log`, `agent_audit_log.py`) — one per seeded agent/workflow/permission, with `invoked_by_user_id` = operator, `invocation_type="api"`, an `input_summary` naming the manifest variant. So "who provisioned this practice, when, with what manifest" is queryable.
- Runtime agent invocations continue to write `agent_audit_logs` (the existing chat/workflow path), and the KG case record carries its own provenance (source mailbox, study accession, extraction confidence, source page) per the measurement-QA contract.

### 5.6 Why all three permission layers (not redundant)
**[Luna] (turn 2, verbatim):**
> "The three layers are deliberately **defense-in-depth across different failure modes: permission leak, approval bypass, and operator misconfiguration**. Collapsing any one of them into another creates a single point of failure in the provenance chain."

| Layer | Axis | Failure mode it covers |
|---|---|---|
| `agent_permissions` | what the agent is *authorized* to attempt | permission leak / unauthorized capability |
| `human_approval` step | did the human *review this instance* | approval bypass at runtime |
| `tenant_norm` veto | the *non-bypassable floor* | operator misconfiguration / future permission expansion / replay |

---

## 6. Scaffold-vs-deep (what's seedable NOW vs needs real integration)

| Capability | Seedable NOW (definitions/slots — demoable) | Needs real (deep) integration |
|---|---|---|
| **Agent fleet (5 v1 agents)** | ✅ Agent rows + personas + tool_groups + owner + permissions + escalation — all via existing model | — (configs, not code) |
| **Connector slots** | ✅ `integration_config` rows, `enabled=false`, for Gmail/Drive/Calendar/SMS/WhatsApp/Pulse/ScribbleVet/etc. | Imaging (`antech_imaging`), labs (`idexx`), live accounting API, non-Pulse PIMS = **net-new MCP modules** |
| **Connectors that actually work** | ✅ Gmail, Drive, Calendar, SMS, WhatsApp, BrightLocal, AAHA export (real MCP tools today) | Pulse + ScribbleVet exist **but are partner-gated** (credential, not code, blocks them) |
| **Workflow templates** | ✅ install tenant copies of native templates (Cardiac Report Generator, etc.) | The **reshaped** Cardiac Report Generator (extraction step + OCR fallback + approval-review UI + send-back) is the discovery §8 net-new build — the provisioner installs it, but the reshape itself must ship first |
| **Provenance layers** | ✅ owner / `AgentPermission` / `human_approval` step / `tenant_norm` seed / audit rows — all existing primitives | — |
| **KG case record** | ✅ entities/relations/observations with provenance fields | the deterministic echo-table extractor feeding it = discovery §8 net-new |

**Net:** the provisioner is **fully demoable now** for the Brett beachhead — it seeds a real 5-agent cardiology fleet, real Gmail/Drive/Calendar slots, the (reshaped) Cardiac Report Generator, and the full provenance stack. The only deep dependencies are (a) the discovery §8 Cardiac-Report reshape (a separate, already-scoped build) and (b) partner-gated/net-new connectors that are **documented slots, not blockers**.

---

## 7. Open decisions for Simon

1. **Multi-tenant placement.** Where does the provisioner seed the Brett beachhead — a **fresh BB Cardiology tenant** (cleanest provenance, recommended), the existing **HealthPets** tenant, **Angelo's** `7f632730-…` (that's the GP tenant — wrong owner for a cardiology fleet), or **Simon's** `752626d9-…` (test only)? This decides where the fleet, KG, credentials, and audit live. *(Open in both prior docs: vision §8.5, discovery §4-Q7.)* **Recommendation:** fresh BB Cardiology tenant, owner = Brett.
2. **v1 fleet + connector cut — confirm Luna's call.** Ship 5 agents (Luna + Referral-Intake + Cardiac-Diagnostics + Comms/Recall + Referral-Liaison) and the Gmail/Drive/Calendar slots in `cardiology_v1`; defer Front-Desk/Scribe/Billing/Inventory/Marketing and the PIMS/scribe/imaging/labs slots to `gp_full`. Confirm, or pull any deferred member forward.
3. **Self-serve vs operator-run.** **[Luna]:** operator-run internal endpoint for v1 (one curated tenant), self-serve-at-signup as a Phase-2 unlock *"after you've run at least one real tenant through the full Brett loop end-to-end."* Confirm v1 is operator-run only (the register hook stays dark until then).
4. **Does the Cardiac-Report reshape land before or with the provisioner?** The provisioner *installs* the reshaped template, but the reshape (discovery §8: attachment-bytes change, OCR fallback, approval-review UI, send-back) is its own build. Sequence: reshape first → provisioner installs it? Or provisioner ships installing the current best-effort template and the reshape lands next?
5. **Manifest as data vs code.** The `VetPracticeProvisioningManifest` can be a Python module (versioned with the code, like `NATIVE_TEMPLATES`) or a DB-backed / JSON manifest (operator-editable without deploy). **Recommendation:** Python module for v1 (matches `seed_animaldoctor` + `NATIVE_TEMPLATES`; cheapest, fully auditable in git); revisit DB-backed if practices need per-tenant manifest edits.
6. **Retire `seed_animaldoctor_agent_fleet.py`?** Per Luna, it becomes a wrapper over the service or a deprecated compatibility entrypoint, and its 5 GP personas become the `gp_full` manifest corpus. Confirm we fold it in rather than keep two seed paths.

---

## 8. Build-on assets (concrete pointers)

- **Default-agent seed (the path to generalize):** `apps/api/app/services/users.py:148` (`create_user_with_tenant` → single Luna agent), `apps/api/scripts/seed_integral_tenant.py` (whole-tenant seed script precedent).
- **Idempotent vet-fleet seeder (the direct prior art):** `apps/api/scripts/seed_animaldoctor_agent_fleet.py` — `AGENT_FLEET` spec, `upsert_agent` (`:293`), `_MANAGED_FIELDS` (`:261`), `seed_fleet` (`:344`).
- **Agent model + fields:** `apps/api/app/models/agent.py` (owner_user_id, tool_groups, persona_prompt, memory_domains, status, escalation_agent_id, tool_groups_review_required).
- **Tool groups registry (vet groups already present):** `apps/api/app/services/tool_groups.py` — `pulse`, `scribblevet`, `patient_records`, `communication`, `bookkeeper_export` (`:232-271`); `resolve_tool_names` (`:318`).
- **Connector slots:** `apps/api/app/models/integration_config.py`, `apps/api/app/services/integration_configs.py` (`create_tenant_integration_config`); vault: `integration_credential.py`, `orchestration/credential_vault.py`.
- **Workflow templates + install:** `apps/api/app/services/workflow_templates.py` (`NATIVE_TEMPLATES`, `Cardiac Report Generator` `:222`, `seed_native_templates` `:2210`); `apps/api/app/api/v1/dynamic_workflows.py:339` (`install_template_internal`).
- **Provenance primitives:** `apps/api/app/models/agent_permission.py`; `apps/api/app/services/audit_log.py` (`write_audit_log`); `apps/api/app/models/agent_audit_log.py`; `apps/api/app/services/agent_value_set_io.py:369` (`write_value_set`, `added_by="seed"`); `apps/api/app/services/value_arbitration.py:108` (`tenant_norm` source class); `human_approval` step (dynamic-workflow executor).
- **Internal-endpoint pattern:** `apps/api/app/api/v1/dynamic_workflows.py:343` (`verify_internal_key` dep); onboarding-state precedent `apps/api/app/api/v1/onboarding.py`.
- **Migration-based seed precedent:** `apps/api/migrations/155_seed_simon_work_fleet_agents.sql` (idempotent `WHERE NOT EXISTS` agent insert + `tool_groups_review_required=TRUE` posture).
- **The two source docs:** [`2026-05-30-agentic-vet-os-vision.md`](2026-05-30-agentic-vet-os-vision.md) (§3 provenance, §4 fleet map, §2 connectors), [`2026-05-30-veterinary-mvp-discovery.md`](2026-05-30-veterinary-mvp-discovery.md) (§3.4 reshape, §8 net-new pieces, measurement-QA contract).

---

## 9. Codex review — corrections (fold in before build)

The agent-fleet upsert prior art is solid (`seed_animaldoctor_agent_fleet.py:293`, idempotent on `(tenant_id,name)`), but the plan **over-assumed** the same idempotency + *enforced* provenance exist for the other seeded objects. They don't — the build must handle:

- **BLOCKER — `tenant_norm` veto is NOT wired.** `value_arbitration.py:6` is "PURE LIBRARY ONLY — NO RUNTIME WIRING." `write_value_set` seeds values but they enforce **nothing at runtime**, so the "non-bypassable floor" is aspirational. **v1's real enforced guardrails = `human_approval` (runtime gate) + user-principal `agent_permissions`.** Design v1 around those; seed value sets as *declared-not-enforced* and track arbitration-wiring as a separate task.
- **Role permissions aren't enforced.** `deps.py:141` only checks `principal_type=="user"` (+owner/superuser); seeded `role` rows (`practice_owner`/`veterinarian`) do nothing despite the model advertising `user|team|role` (`agent_permission.py:16`). Seed **user-principal** perms for v1 (or wire role enforcement first).
- **Workflow-template install is NOT idempotent.** `dynamic_workflows.py:339` blind-inserts; no uniqueness on `(tenant_id, source_template_id)`. The provisioner must check-then-install (or add a uniqueness guard) or re-runs duplicate templates.
- **Connector slots NOT idempotent.** `create_tenant_integration_config()` (`integration_configs.py:26`) blind-inserts; no uniqueness on `(tenant_id, integration_name)`. Provisioner must upsert/guard.
- **Self-serve hook not implementable as written.** `TenantCreate` is `name`-only (`tenant.py:4`); `practice_type=="veterinary"` has no schema support. v1 is operator-run anyway (Luna) → defer self-serve + that schema change.

**Net for the build:** generalize the agent upsert; **build idempotency into the provisioner itself** for workflows + connectors; seed **user-principal** permissions; seed value sets as *declared* (flag arbitration-not-wired); defer self-serve. Keeps the provisioner valuable + honest about enforced-vs-declared.

---

*Plan only — no code written. Luna (project lead) consulted across 2 structured `alpha chat send` turns: (1) mechanism + manifest shape + fleet principle ("nothing is born ownerless", `VetPracticeProvisioningManifest`), (2) v1 fleet cut + operator-run trigger + the three-layer defense-in-depth provenance model. Her input is marked **[Luna]**. Codex review folded into §9. This draft is the input for Simon's placement + scope decision before any implementation.*
