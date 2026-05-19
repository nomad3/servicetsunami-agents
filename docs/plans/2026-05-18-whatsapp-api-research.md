# WhatsApp Open-Source API Landscape — 2026-05-18

**Author:** research pass, no code changes
**Status:** decision memo

## TL;DR — Recommendation

**Stay on neonize for now. Invest one sprint in a proper socket health-check + handshake-watchdog layer on top of it.** The pain points we have hit (silent socket death, QR-regen race, phone-side link rate-limit) are all rooted in the underlying `whatsmeow` MultiDevice protocol — which means **every modern alternative inherits the same WhatsApp-side constraints**. The only options that would have meaningfully changed our PR #299 incident are (a) running **WAHA** in its GOWS engine as a sidecar (still whatsmeow under the hood but with a battle-tested REST surface and external supervision) or (b) writing our own thin Go service on top of `whatsmeow` directly. Migrating to a Web/Puppeteer-based stack (OpenWA, whatsapp-web.js) would trade our current class of bugs for a worse one (Chromium memory leaks, slower throughput, easier WhatsApp bans). **If we ever decide to escape Python+neonize, the migration target is WAHA-GOWS, not OpenWA.**

What I could **not** verify: the literal name "opencw" / "OpenCwA" returns zero matching WhatsApp repos on GitHub. The closest live match — and the one that fits the user's description ("newer open-source WhatsApp API on GitHub") — is **rmyndharis/OpenWA** (created 2026-02-02, 2.9K stars, MIT, single maintainer). I am treating that as the intended candidate and have evaluated it accordingly. If the user meant something different, please reply with the URL and I will re-run the comparison.

---

## Comparison matrix

| Project | Stars | Created | Last release | License | Lang | Protocol approach | Maintainer(s) | Migration effort from neonize |
|---|---|---|---|---|---|---|---|---|
| **neonize** (current) | 391 | 2024-01 | 2026-05-18 (0.3.18) | Apache-2.0 | Python wrapping Go | MultiDevice (wraps whatsmeow) | 1 person (krypton-byte) | — |
| **Baileys** | 9.4K | 2022-01 | 2026-05-13 (v7-rc11) | MIT | TypeScript | MultiDevice (native TS impl) | Team (8+ contributors, WhiskeySockets org) | Large |
| **WAHA** | 6.6K | 2020-10 | 2026-05-07 (2026.4.3) | Apache-2.0 (community) / commercial Plus | TypeScript | Three engines: WEBJS (Puppeteer), NOWEB (Baileys), **GOWS (whatsmeow)** | 2 primary devs (devlikepro, allburov) + commercial backing | Medium |
| **whatsapp-web.js** | 21.8K | 2019-02 | active | Apache-2.0 | JavaScript | Web (Puppeteer-driven WA Web) | Team, many contributors | Large |
| **OpenWA** (rmyndharis/OpenWA) | 2.9K | **2026-02-02** | 2026-05-17 (v0.1.6) | MIT | TypeScript (NestJS) | Web — thin REST wrapper around whatsapp-web.js v1.26 | 1 person + dependabot | Medium (REST contract is easy; underlying bugs inherited from whatsapp-web.js) |
| **whatsmeow** (substrate) | 6.2K | 2021-10 | rolling main | MPL-2.0 | Go | MultiDevice (canonical reverse-engineered Go impl) | tulir (Matrix bridge author) + community | Large (build our own service) |

Repos:
- https://github.com/krypton-byte/neonize
- https://github.com/WhiskeySockets/Baileys
- https://github.com/devlikeapro/waha
- https://github.com/pedroslopez/whatsapp-web.js
- https://github.com/rmyndharis/OpenWA
- https://github.com/tulir/whatsmeow

---

## Per-option analysis

### 1. neonize (our current stack)

**What it is.** Python wrapper around `whatsmeow` compiled to a shared library via CGo. We import a `.so` / `.dylib` and call into Go. Auth state lives in a per-account SQLite DB on disk (we shadow it into Postgres in `_save_session_to_db` / `_restore_session_from_db` — see `apps/api/app/services/whatsapp_service.py:285,323`).

**Pros**
- Python-native; integrates directly into the FastAPI process. No extra container, no IPC.
- Uses the canonical `whatsmeow` engine under the hood — same protocol implementation Beeper / Matrix bridges trust.
- We already own ~1,500 lines of glue code (`whatsapp_service.py`) that encodes our domain shape: account_id, tenant scoping, reconnect ladder, event log, document extraction & embedding, inbound→agent handoff.

**Cons / pain points we have actually hit**
- **Silent socket death.** Open issue [neonize#173 `event.disconnect() does not work`](https://github.com/krypton-byte/neonize/issues/173) (2026-03-26) is exactly our pattern: the Go side stops delivering events without raising `DisconnectedEv`, so our `_auto_reconnect` (whatsapp_service.py:488) never fires. Our PR #299 watchdog on `readonly database` log lines is a heuristic patch for this.
- **Pair-phone disconnect storm.** Closed issue [neonize#175](https://github.com/krypton-byte/neonize/issues/175) ("PairPhone consistently triggers 503 stream error and websocket disconnect", 2026-03-30) maps to the QR-regen race documented in `whatsapp_pairing_qr_regen_race.md`.
- **Single maintainer.** krypton-byte is one person. Cadence is good (monthly post-releases) but bus factor = 1.
- **Python+protobuf footgun.** We already carry a lazy-import shim (whatsapp_service.py:1505) because the bundled protobuf rev fights our other deps.

**Phone-side rate limit.** Inherited from WhatsApp itself, not the library. Every option below has this same constraint.

### 2. Baileys (`@whiskeysockets/baileys`)

**What it is.** Pure TypeScript native re-implementation of the MultiDevice websocket protocol. No browser, no Go FFI.

**Pros**
- Largest active community in this space (9.4K stars, ~3K forks, broad contributor base).
- Modern v7 line in active RC (v7.0.0-rc11 on 2026-05-13). Built-in `useMultiFileAuthState`, event-driven reconnect, exponential backoff.
- Memory footprint is tiny vs. Puppeteer options.

**Cons**
- TypeScript runtime — adds a Node container to our deploy, plus IPC (HTTP/grpc/socket) between FastAPI and the Baileys process. That IPC is exactly where state-sync bugs happen.
- Open issues [#1910 Stream Errored](https://github.com/WhiskeySockets/Baileys/issues/1910), [#1771 Connection Closed 428](https://github.com/WhiskeySockets/Baileys/issues/1771), [#1938 Connection closed everytime](https://github.com/WhiskeySockets/Baileys/issues/1938) prove the disconnect pattern is **not** unique to neonize — it is the WhatsApp MultiDevice protocol itself misbehaving. Baileys does not fix the underlying problem; it just gives us a different log line.
- Migration would force us to re-encode the entire `whatsapp_service.py` domain layer across a process boundary.

**Verdict.** Not worth the migration unless we are already going polyglot for other reasons.

### 3. WAHA (devlikeapro/waha)

**What it is.** Docker-first REST API gateway. Sessions are managed by HTTP + webhooks. Three pluggable engines:
- `WEBJS` — wraps whatsapp-web.js (Puppeteer)
- `NOWEB` — Baileys-based, no browser
- `GOWS` — whatsmeow-based (same engine neonize wraps), runs as a Go subprocess

**Pros**
- The REST surface is stable and documented (Swagger). Our FastAPI just becomes a client. No FFI, no Python↔Go ABI risk.
- Multi-engine fallback: if our GOWS sessions start failing we can route a single tenant to NOWEB or WEBJS as a hot swap, without changing our domain code.
- Real team behind it: 2 primary maintainers (devlikepro 1,524 commits, allburov 668) plus commercial Plus tier funding ongoing work. Bus factor materially better than neonize.
- Native Docker, Kubernetes health checks, multi-session per container — fits our existing docker-compose / Helm pattern.
- Webhook delivery (HMAC-signed) means inbound message handling is a normal HTTP POST handler instead of a long-lived asyncio callback. **This alone would have prevented our PR #299-class incident**, because a hung WAHA session is visible from outside (no webhooks → external observer alerts), versus a hung neonize coroutine inside our own process (only the database `readonly` log line tipped us off).

**Cons**
- New container in the deploy. We have to operate it (logs, restart policy, session-state volume backups).
- Some advanced features (multi-session at scale, S3 media storage, observability) gate behind paid Plus.
- Adds a network hop for every send/receive — small latency cost.

**Migration effort:** Medium. Replace the `NewAClient`-bound methods in `whatsapp_service.py` (`send_message`, `start_pairing`, `reconnect`, `restore_connections`) with HTTP calls to WAHA. Replace neonize event callbacks (`on_message`, `on_connected`, `on_disconnected`, `on_qr`) with a new FastAPI webhook handler. Keep the entire DB layer (ChannelAccount, sessions, events) unchanged.

### 4. whatsapp-web.js (pedroslopez)

**What it is.** Spawns headless Chromium, drives WhatsApp Web via Puppeteer. The "OG" Node WhatsApp library.

**Pros**
- Huge community (21.8K stars), well-documented, accepts a wide range of WA features quickly because it just rides whatever WA Web supports.

**Cons**
- **Puppeteer-class problems.** Chromium leaks memory under long sessions, headless detection by WA has improved, ban risk for high-volume senders is materially higher than for protocol-native clients.
- A full Chromium container per session does not scale to multi-tenant.
- WA Web visual changes silently break selectors; recovery often requires version pinning Chromium.

**Verdict.** Worse on every dimension we care about than the MultiDevice options. Skip.

### 5. OpenWA (rmyndharis/OpenWA) — likely the "opencw" the user meant

**What it is.** A NestJS REST gateway around `whatsapp-web.js@1.26`. Adds a React dashboard, multi-session, webhooks, API keys, PostgreSQL/SQLite/S3/Redis pluggable adapters, n8n nodes. **v0.1.x** — explicitly pre-1.0.

**Pros**
- Modern, clean architecture (NestJS, TypeORM, BullMQ, Swagger). The product surface is what WAHA was 2 years ago, with arguably nicer code.
- MIT license, fully open (no Plus tier).
- Active: 5 releases in 3 months (v0.1.1 → v0.1.6, 2026-02-17 → 2026-05-17).
- Multi-engine future is plausible — the architecture allows it — but **today it is whatsapp-web.js only**.

**Cons**
- **One maintainer.** Contributor list: rmyndharis (69 commits), dependabot (22), one drive-by (2). Bus factor 1, and the project is 3 months old.
- **Underlying engine is whatsapp-web.js (Puppeteer)** — inherits every Chromium-class problem listed above. The REST wrapper does not change that.
- Pre-1.0. APIs will move.
- No production deployments at scale that I can verify.

**Verdict.** Promising new arrival but not yet a credible migration target. Re-evaluate in 6 months when (a) GOWS/NOWEB engines land, (b) maintainer team expands, (c) hits v1.0. If we want a REST-fronted WhatsApp gateway **today**, WAHA-GOWS is the safer choice; OpenWA would be the choice in mid-2027 if it matures and we can self-host without a vendor relationship.

### 6. whatsmeow (substrate, not an option per se)

We are already running this through neonize. Worth listing because building a thin Go service directly on `whatsmeow` is a viable "escape hatch" if neonize stagnates and WAHA-GOWS does not exist for some reason. ~600–1,200 lines of Go to replicate our current event surface. We probably never want to do this voluntarily.

### 7. Other 2025–2026 entrants

I searched GitHub for repos with `whatsapp` + `api` and stars > 500 created since 2025-01-01. Two hits other than OpenWA:

- **gokapso/whatsapp-cloud-inbox** (665 stars, 2025-10-20, TS) — wraps the **official Meta Cloud API**. Different category: it solves template-message + 24-hour-window flows for businesses on the **paid** WA Business Cloud, not unofficial MultiDevice. Out of scope for us (we don't want the Meta paywall).
- **kinghacker0/WhatsApp-OSINT** (644 stars, 2025-10-16, Python) — OSINT tool using RapidAPI. Not a library, not relevant.

No other ≥500-star library qualifies. The "new entrant" landscape in this space is real but thin: **OpenWA is the only one that matters**, and it is not ready.

---

## Mapping options against our actual incident history

| Incident | Root cause | Stay on neonize + harden | WAHA-GOWS | Baileys | OpenWA |
|---|---|---|---|---|---|
| Silent socket death (PR #299) | whatsmeow event-loop stalls without firing DisconnectedEv | **Mitigated**: external watchdog + heartbeat check via `IsConnected()` call + active ping every 60s | **Prevented at process layer**: WAHA's session supervisor restarts the underlying engine on health failure; webhook absence is externally visible | Not prevented (Baileys has same disconnect class — see issues #1910/#1771/#1938) | Not prevented (Puppeteer hang would be the same class of bug, different shape) |
| QR-regen race | Library regenerates QR mid-scan | **Mitigated**: lock the QR in `start_pairing` for 60s before regen; surface "scan window expired, request new code" in UI | Same fix needed on WAHA's side; WAHA already manages this in its session FSM | Same problem class | Same problem class (inherits whatsapp-web.js) |
| Phone-side "can't link new phones" rate limit | WhatsApp server policy, per phone | **Not fixable in code** — must reduce re-pairing frequency | Same | Same | Same |
| `readonly database` from concurrent SQLite writers | neonize SQLite session file collides with our shadow-restore | **Mitigated**: lock the session-file path; serialize restore through asyncio.Lock per account | Eliminated (WAHA owns its own session state, we never touch its DB) | Eliminated (different DB) | Eliminated |

**Net read:** WAHA-GOWS would eliminate or mitigate **3 of 4** incident classes. Staying on neonize + adding a heartbeat watchdog mitigates **2 of 4**. Baileys/OpenWA mitigate **1 of 4** (the SQLite collision) while introducing new failure modes.

---

## Migration effort, ordered

### Option A — Stay on neonize + harden (recommended)
**Effort: small (1 sprint, ~1 engineer-week)**
1. Add `_socket_heartbeat` coroutine that calls `client.IsConnected()` every 30s and, if it returns False or hangs (asyncio.wait_for 5s), trips the existing `_auto_reconnect` path.
2. Move the PR #299 log-pattern watchdog from "fire on `readonly database`" to "fire on either pattern OR heartbeat failure".
3. Lock the QR re-generation window — once a QR is shown, do not generate a new one for 60s; show user-facing "code expired, request new" instead.
4. Serialize per-account restore with `asyncio.Lock` to kill the SQLite collision class entirely.
5. Add a Prometheus metric `whatsapp_session_last_event_seconds` so silent death is dashboard-visible.

**Risks:** Doesn't solve the underlying whatsmeow protocol bugs; if neonize maintainer goes inactive we have a single point of failure.

### Option B — Migrate to WAHA (GOWS engine) (only worth doing if Option A repeatedly fails)
**Effort: medium (3–4 sprints)**
1. Stand up WAHA container alongside our api container in docker-compose + Helm.
2. Replace the neonize-coupled methods in `whatsapp_service.py` with an HTTP client against WAHA's REST API (~600 LOC churn).
3. Add a webhook endpoint (`POST /api/whatsapp/webhook`) that receives WAHA events and fans them into the existing `_handle_inbound` flow. HMAC-verify with WAHA's signing key.
4. Migrate session state: WAHA stores its own session blobs; we keep our ChannelAccount metadata in Postgres but stop shadowing the neonize SQLite. **One-time pairing-required cutover** — every tenant re-scans QR once.
5. Keep neonize service code in-tree behind a feature flag for 30 days as fallback.

**Risks:**
- Forced re-pairing across all active tenants. We can stagger but each user has to scan a QR once.
- New container in the deploy graph; Mac runner already tight on Docker disk (per `docker_disk_full_recovery.md`).
- WAHA Plus features (multi-session at scale, S3 media) cost money if we cross their free-tier thresholds.
- Webhook back-channel must be authenticated and idempotent — easy to get wrong on first try.

### Option C — Run WAHA alongside neonize (high effort, optional)
**Effort: large (5+ sprints)**
- Route new tenants to WAHA; keep existing on neonize until they re-pair voluntarily.
- Maintain two code paths in `whatsapp_service.py` indefinitely.
- Useful only if we want zero forced re-pairings. Not recommended — operating two integrations is expensive and the cutover pain in Option B is one-time.

### Option D — Migrate to Baileys/OpenWA (not recommended)
**Effort: medium–large**, with worse risk profile than Option B. Skipped.

---

## Decision criteria

We should migrate (Option B) **if and only if** any of the following becomes true:
1. Option A ships, runs for 60 days, and we still see ≥1 silent-disconnect incident per month per active tenant.
2. neonize maintainer activity drops below one release per quarter for two consecutive quarters.
3. A regulatory or compliance need (audit trail, signed webhooks, per-tenant rate limits) forces us off in-process integration anyway.

Until one of those triggers, the harden-in-place plan (Option A) wins on every dimension: lowest effort, no forced re-pairings, no new container, and it addresses the root causes (silent socket health + QR race + SQLite contention) directly.

---

## What I could not verify

- **"opencw" / "OpenCwA" as a literal name.** GitHub search returns zero matching repos. The closest active project that fits the description "newer open-source WhatsApp API on GitHub, 2025/2026" is **rmyndharis/OpenWA** (2026-02), which I treated as the intended candidate. If the user meant a different repo, pointer please.
- **WAHA-GOWS production stability at our tenant count.** Their docs claim it; I did not find independent benchmarks. Worth a 1-week spike before committing to Option B.
- **OpenWA roadmap for non-Puppeteer engines.** No public statement in the README or recent commits. Assume Puppeteer-only for now.

---

## References

- Our current integration: `apps/api/app/services/whatsapp_service.py` (1,510 LOC, key entry points: `_create_client:360`, `_auto_reconnect:488`, `_connection_watchdog:521`, `restore_connections:1455`)
- Prior incident notes: `whatsapp_silent_disconnect_recovery.md`, `whatsapp_auto_restore_handler.md` (PR #299), `whatsapp_pairing_qr_regen_race.md`
- neonize: https://github.com/krypton-byte/neonize ; issues [#173](https://github.com/krypton-byte/neonize/issues/173), [#175](https://github.com/krypton-byte/neonize/issues/175)
- Baileys: https://github.com/WhiskeySockets/Baileys ; issues [#1910](https://github.com/WhiskeySockets/Baileys/issues/1910), [#1771](https://github.com/WhiskeySockets/Baileys/issues/1771), [#1938](https://github.com/WhiskeySockets/Baileys/issues/1938)
- WAHA: https://github.com/devlikeapro/waha ; docs https://waha.devlike.pro/
- whatsapp-web.js: https://github.com/pedroslopez/whatsapp-web.js
- OpenWA: https://github.com/rmyndharis/OpenWA ; site https://www.open-wa.org
- whatsmeow: https://github.com/tulir/whatsmeow
