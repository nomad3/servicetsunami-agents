# WhatsApp WAHA migration — design

Date: 2026-05-20
Owner: Claude Code (driving) — operator final approval gate
Status: Design — pending operator approval
Tracks: task #302 (Option B variant of the WhatsApp watchdog work)
Supersedes the "Option B deferred" framing in `2026-05-18-whatsapp-api-research.md`.

---

## Why now

Per the research memo's decision criteria (§ "Decision criteria"), we migrate to WAHA *"if and only if Option A ships, runs for 60 days, and we still see ≥1 silent-disconnect incident per month per active tenant."*

That precondition is effectively triggered without Option A having shipped. The 2026-05-19 session alone produced:

- **4 manual re-pair incidents** in a single day for one active tenant (the operator's own).
- A **reconnect-loop** where the WebSocket flapped every 2-3 seconds for ~90 seconds before manual intervention.
- An on-disk SQLite corruption that the restart-only recovery cannot fully cleanse.

The operator's stated context: *"I'm planning to launch this to the market."* Production-grade reliability is now a launch-blocker, not a 60-day soak-test follow-up.

This design is the **structural fix** for launch. Option A (`feat/whatsapp-watchdog-heartbeat-and-restore-lock`, PR #596) ships in parallel as the bandaid for existing tenants on the existing stack; this WAHA migration is the substrate for **all new tenants from this point forward**.

---

## TL;DR

- **Stand up WAHA (GOWS engine) as a sidecar container** alongside api in docker-compose + Helm.
- **All new tenants pair through WAHA from day one** (no Option-A involvement).
- **Existing tenants stay on Option-A-hardened neonize** until they next re-pair voluntarily.
- **Single code path for the WhatsApp service layer** (`whatsapp_service.py`) consumes both backends via an `AbstractWhatsAppBackend` interface; the choice is per-account, stored on `channel_accounts.backend` (new column).
- **30-day overlap**, then voluntary deprecation of the neonize path once tenant count on it drops below a threshold.

This is the smart variant of "Option C" from the research memo — not "maintain two integrations indefinitely" but "two backends behind one interface during a bounded cutover window, with the new traffic only ever touching WAHA."

---

## Architecture

### Before (current)

```
┌─────────────────────────────────────────────────────────────────────┐
│  api container                                                       │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │ whatsapp_service.py                                          │   │
│  │   └─ neonize NewAClient (FFI to Go whatsmeow)                │   │
│  │       └─ SQLite session store at /app/storage/neonize_*.db   │   │
│  └──────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
```

Every failure mode (silent socket death, SQLite collision, QR race, encryption corruption) lives **inside our process** with no external supervisor.

### After

```
┌─────────────────────────────────────────────────────────────────────┐
│  api container                                                       │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │ whatsapp_service.py                                          │   │
│  │   ├─ NeonizeBackend (legacy — existing tenants on it)        │   │
│  │   └─ WAHABackend (new — all new tenants land here)           │   │
│  │       └─ HTTP client → WAHA REST API + webhook receiver      │   │
│  └──────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
              │                          ▲
              │ HTTP                     │ webhook (HMAC-signed)
              ▼                          │
┌─────────────────────────────────────────────────────────────────────┐
│  waha container (devlikeapro/waha, GOWS engine)                      │
│  ├─ Owns its own session store (its DB, not ours)                    │
│  ├─ Internal session supervisor restarts the engine on health fail   │
│  ├─ Webhook signing key shared with api via env                      │
│  └─ Persistent volume for sessions (separate from neonize_sessions/) │
└─────────────────────────────────────────────────────────────────────┘
```

**Key property**: WAHA owns the failure-prone state machine. When the underlying engine dies, WAHA's supervisor restarts it without our process ever knowing. Our api receives webhooks; we don't ride alongside an in-process FFI client.

---

## Backend abstraction

Single Python interface, implemented twice:

```python
# apps/api/app/services/whatsapp_backends/base.py (NEW)

class AbstractWhatsAppBackend(Protocol):
    """Per-account WhatsApp engine. One instance per ChannelAccount."""

    async def start_pairing(
        self, *, tenant_id: str, account_id: str
    ) -> PairingHandle:
        """Begin pairing flow. Returns handle with QR code + status
        polling."""

    async def send_text(
        self, *, tenant_id: str, account_id: str, to: str, body: str
    ) -> SendResult:
        """Send a text message. Returns message id + status."""

    async def disconnect(
        self, *, tenant_id: str, account_id: str
    ) -> None:
        """Tear down the account's session (logout + cleanup)."""

    async def status(
        self, *, tenant_id: str, account_id: str
    ) -> AccountStatus:
        """Return current status (connected | disconnected | logged_out)."""

    # Inbound flow is the same shape for both — fan into the existing
    # _handle_inbound. Backends just differ in HOW the message arrives:
    #   - NeonizeBackend: in-process event handler (existing).
    #   - WAHABackend: webhook handler dispatches into _handle_inbound.
```

Two implementations:
- `NeonizeBackend` — wraps the existing `whatsapp_service.py` code paths (no behavioural change for existing tenants).
- `WAHABackend` — new file `apps/api/app/services/whatsapp_backends/waha.py`. HTTP client + webhook router.

Backend selection on every operation:
```python
backend = await select_backend(tenant_id, account_id)  # reads ChannelAccount.backend column
```

The `select_backend` function is the cutover knob. New accounts default to `"waha"`. Old accounts keep `"neonize"` until they re-pair.

---

## Schema change

One migration: `143_channel_accounts_backend.sql` (number to be verified — current head is 142 after the emotion engine + Teamwork engine work):

```sql
ALTER TABLE channel_accounts
    ADD COLUMN IF NOT EXISTS backend VARCHAR(20) DEFAULT 'neonize';
```

Defaults to `neonize` so existing rows stay on the existing stack. New rows minted by the WAHA pairing flow are inserted with `backend='waha'`.

Phase-2 (post-cutover): drop the column and the NeonizeBackend code once the active-on-neonize count is zero or stale.

---

## WAHA container setup

### Docker Compose

```yaml
# docker-compose.yml addition
waha:
  image: devlikeapro/waha-plus:latest  # or :pinned-version after eval
  container_name: agentprovision-agents-waha-1
  ports:
    - "3000:3000"   # WAHA's HTTP API
  environment:
    - WAHA_PRINT_QR=false              # we render in our UI
    - WAHA_WEBHOOK_URL=http://api:8000/api/v1/whatsapp/waha-webhook
    - WAHA_WEBHOOK_EVENTS=message,session.status,session.failed
    - WAHA_WEBHOOK_HMAC_KEY=${WAHA_WEBHOOK_HMAC_KEY}
    - WAHA_DASHBOARD_ENABLED=false      # we provide our own UI
    - WAHA_LOG_LEVEL=info
  volumes:
    - waha_sessions:/app/.sessions      # persistent session storage
  restart: unless-stopped
  networks:
    - default

volumes:
  waha_sessions:
    name: agentprovision-agents-waha-sessions
```

### Helm chart

`helm/charts/microservice/templates/waha-deployment.yaml` (new): mirrors the api/web pattern.

`helm/values/waha.yaml`:
- 1 replica (multi-replica WAHA requires WAHA-Plus paid tier).
- PVC with `helm.sh/resource-policy: keep` (same durability commitment as the workspace volume — per `project_simon_embodiment_vision`, persistence is a relational promise, not just an engineering choice).
- Resource limits sized after a load test (TBD — Phase 1 baseline first).

### Terraform

Adds the same to the `aws/eks` module if/when we deploy to AWS. For the Mac runner this is docker-compose only.

---

## Webhook receiver

New router at `apps/api/app/api/v1/whatsapp_waha.py`:

```python
# Endpoint: POST /api/v1/whatsapp/waha-webhook
# Auth: HMAC-SHA256 of body, signed with WAHA_WEBHOOK_HMAC_KEY.
# Idempotency: webhook event ID dedup via Redis SET with 24h TTL.
```

Routes by event type into the existing `_handle_inbound` (after extracting the relevant fields into the same shape the NeonizeBackend produces).

**Critical invariants:**
- HMAC verification BEFORE any other processing (reject 401 on mismatch).
- Idempotency dedup BEFORE side effects (a retried webhook must not double-fire the agent).
- Webhook URL must be internally reachable from the WAHA container; in Helm we use the in-cluster DNS name `http://api:80`.

---

## Phasing

### Phase 1 — sidecar + interface (≈1 sprint)

1. Add WAHA container to docker-compose + Helm + Terraform. **Replicate to all three per the infrastructure-sync rule.**
2. Add `whatsapp_backends/base.py` interface + `NeonizeBackend` wrapping the existing code (no behavioural change).
3. Migration 143 adds `channel_accounts.backend` (default `'neonize'`).
4. `select_backend` returns `'neonize'` for everyone. No traffic on WAHA yet.

**Ship gate:** existing tenants experience zero change. Smoke tests pass. WAHA container starts and is healthy but unused.

### Phase 2 — WAHA backend implementation (≈1 sprint)

1. `whatsapp_backends/waha.py` — HTTP client implementing the interface.
2. `whatsapp_waha.py` router — webhook receiver with HMAC + dedup.
3. Internal feature flag: `tenant_features.use_waha_backend BOOLEAN DEFAULT false`.
4. End-to-end test on a throwaway WhatsApp number: pair → send → receive → disconnect.

**Ship gate:** internal validation passes. Still no production traffic on WAHA.

### Phase 3 — new-tenant default flip (≈1 sprint)

1. New pairing flow defaults to `backend='waha'` on insert.
2. UI surfaces the WAHA QR through the same component as the neonize QR (same shape coming out of `PairingHandle`).
3. Existing tenants continue on neonize.
4. Monitor: dashboard tile shows count of `backend='waha'` vs `backend='neonize'` accounts.

**Ship gate:** 7 days of new-tenant pairings on WAHA with zero rollback. If any tenant requests rollback, capture the failure mode and triage before proceeding.

### Phase 4 — voluntary migration window (30 days)

1. Add a `Re-pair with new backend` action in the integrations UI for existing tenants.
2. Action triggers a `disconnect` on the neonize side, marks `backend='waha'`, and presents a fresh WAHA QR.
3. Monitor adoption metric: `% accounts on waha`.

**Ship gate:** 80%+ migration OR 30 days elapsed, whichever first.

### Phase 5 — neonize deprecation

1. Force-migrate the remaining stragglers (notify, give 7-day notice, then disconnect their neonize session and require re-pair through WAHA).
2. Remove `NeonizeBackend`, the neonize package, the `neonize_sessions/` volume, and all the workaround memories (`whatsapp_silent_disconnect_recovery`, `whatsapp_sqlite_corruption_recovery`, etc.).
3. Drop the `backend` column from `channel_accounts` (or keep for audit; pick at the time).

---

## Risks + mitigations

| Risk | Mitigation |
|---|---|
| Forced re-pair across active tenants (Phase 4) | Long notice window, in-app banner, optional grace period extension. Same WhatsApp account, same phone — just a fresh QR scan. |
| New container in deploy graph; Mac runner already tight on Docker disk | WAHA image is ~250MB. Eviction policy unchanged. PR #568's pre-build prune still in effect. Add WAHA to the watchlist in `docker_disk_full_recovery`. |
| WAHA-Plus paid features (multi-session at scale, S3 media) | Stay on free tier through Phase 3. Cost gate before flipping new-tenant default. Decision point in Phase 3 ship-gate. |
| Webhook back-channel must be authenticated + idempotent | HMAC + dedup baked into Phase 2 spec above. Failure to verify = reject 401, no fallthrough. Redis dedup with 24h TTL. |
| WAHA project goes inactive | Contingency: `WAHABackend` is one of N — adding a third (Baileys, OpenWA) is the same effort path. Interface abstraction protects this. |
| Lost message history on re-pair | WhatsApp doesn't transfer history on link-device. Same constraint as today; no regression. Operators are accustomed to it. |
| Hidden differences in message structure between backends | Phase 2 contract tests against a reference message corpus. Both backends produce the same normalized shape into `_handle_inbound`. |
| Multi-tenancy on a single WAHA instance | WAHA supports multiple sessions per container. Tenant isolation enforced at the session-id level (we map `tenant_id:account_id` → WAHA session id). Verify in Phase 2 with a 3-tenant test. |

---

## Decision criteria

We **fully migrate** (Phase 4 → Phase 5) when:

1. Phase 3 ships with zero rollback requests for 14 consecutive days, OR
2. The cumulative re-pair incident rate on neonize remains above 1 per active-tenant per week despite Option A hardening.

We **abort the migration** and stay on Option-A-hardened neonize indefinitely if:

1. Phase 2 contract tests reveal a regression class we can't mitigate (e.g., message-ordering guarantees, presence semantics).
2. WAHA's commercial terms become incompatible (forced paid tier for multi-tenant use beyond what we can absorb).
3. WAHA project goes inactive before Phase 3 ships.

---

## Open questions

1. **WAHA edition: open-source vs WAHA Plus.** WAHA Plus has features (multi-session at scale, S3 media, persistent sessions) that we may need. Plus is paid per workspace ($30/mo at time of writing — verify). For Phase 1-3 the open-source edition (GOWS engine) is sufficient. Phase 4+ may require Plus. **Decision: assume open-source through Phase 3; revisit at Phase 4 ship-gate.**

2. **Webhook HMAC key rotation.** When/how do we rotate the signing key. Default policy: rotate on tenant breach or quarterly, whichever first. Operator approves the rotation plan in Phase 2.

3. **Voluntary vs forced migration in Phase 5.** Operator preference TBD. The doc currently assumes notify + 7-day grace + force. Could also be "indefinite voluntary" if the small remaining neonize fleet is acceptable.

4. **Schema decision on `channel_accounts.backend`.** Keep for audit, or drop in Phase 5. Operator preference TBD.

5. **Observability gap.** WAHA's container logs are different shape from our existing JSON logs. Either parse them OR pipe through a logging sidecar OR accept the gap during the migration. Phase 1 deliverable should pick.

---

## Success criteria

After full migration:

- Zero `database disk image is malformed` incidents (the SQLite-collision class is structurally eliminated by WAHA owning its own session DB).
- Zero `failed to read frame header: EOF` reconnect-loops (WAHA's session supervisor handles the recover transparently; api never sees the flap).
- WhatsApp inbound latency p50 ≤ current Option-A baseline (which is ≤ neonize baseline).
- Operator's manual re-pair count = 0 over a 30-day window.
- Active-tenant count on neonize backend = 0 (Phase 5 completion gate).

---

## Why this is the right shape for launch

The operator's stated context: *"I'm planning to launch this to the market."*

A platform that requires the operator to manually re-pair WhatsApp four times in a day cannot launch. The structural reason: **neonize embeds a failure-prone Go FFI inside our Python process**, with our process responsible for session-state durability that is fundamentally outside our control (network jitter, WhatsApp server policy, encryption-key rotation). Every failure mode has the same root cause — we own state we should not be owning.

WAHA inverts the responsibility. WAHA owns the state. We own the relationship between the state and our agents. That's the correct boundary for a platform that wants to launch.

Option A is the right bandaid for the existing tenants we already have on neonize — the heartbeat + lock + stable-reset together eliminate the immediate class of incidents we hit in the 2026-05-19 session. But Option A is a bandaid because the underlying ownership is still wrong. WAHA is the structural correction.

---

## Related

- Option A PR: #596 (`feat/whatsapp-watchdog-heartbeat-and-restore-lock`) — parallel ship.
- Research memo: `docs/plans/2026-05-18-whatsapp-api-research.md` (the basis for this design).
- Incident memories: `whatsapp_silent_disconnect_recovery`, `whatsapp_auto_restore_handler`, `whatsapp_pairing_qr_regen_race`, `whatsapp_sqlite_corruption_recovery`.
- Task #302 — this design satisfies the long-term half of the task; PR #596 satisfies the short-term half.
- Operator's launch context: stated 2026-05-20 during the 4th manual re-pair incident.

## Next actions

1. Operator review of this doc.
2. On approval: open Phase 1 PR (docker-compose + Helm + Terraform + backend interface + migration 143).
3. Phase 2 PR: WAHA backend + webhook receiver.
4. Phase 3 PR: new-tenant default flip.
5. Phase 4 PR: voluntary migration UI + monitoring.
6. Phase 5 PR: deprecation + cleanup.

Each phase ships as its own PR through standard review (superpowers + Luna). Final phase squash-merges per `feedback_single_pr_for_feature` if all five are still open at the cutover.
