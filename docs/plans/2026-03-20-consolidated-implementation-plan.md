# Consolidated Implementation Plan — Wolfpoint.ai / STP Build-Out

> All recent code changes mapped to actionable phases. Each task references the exact files to create or modify, the migration sequence, and the wiring needed (routes.py, models/__init__.py, etc.).

**Date:** 2026-03-20
**Status:** Active
**Author:** Simon Aguilera + Luna (Claude Opus 4.6)
**Branch:** `feature/stp-implementation-plan`

---

## Recent Shipping Summary (Foundation Already Built)

| Commit | Feature | What It Enables |
|--------|---------|-----------------|
| `f715092` | Local ML inference — Ollama auto-quality scorer | Zero-cost RL signals on every response |
| `d2b38c4` | Knowledge backfill + local ML training plan | Roadmap to 700+ entities, 4K+ observations |
| `8d1e400` | Codex CLI integration | 2nd CLI platform (Claude Code + Codex) |
| `a78c1d2` | Full RL & memory distributed system (12 tasks) | Semantic recall, entity history, observations, quality tracking |
| `af0a95b` | Git history tracking + PR rewards | Delayed RL rewards from PR merge outcomes |
| `fcc01db` | MCP server connectors | Nodes can connect external MCP servers |
| `64a565c` | Platform Performance tab + Export CSV | Learning page analytics |

**Current state:** 49 models, 44 routes, 81 MCP tools, 4 Temporal queues, migration `049`.

---

## Phase 0: Gemini CLI Integration

**Goal:** 3rd CLI platform live. All target platforms operational.
**Estimated effort:** 3–5 days
**Depends on:** Nothing (can start immediately)

### Task 0.1 — Gemini CLI Auth Endpoint
- **Create:** `apps/api/app/api/v1/gemini_auth.py`
- **Pattern:** Copy `codex_auth.py` structure
- **Endpoints:**
  - `POST /api/v1/integrations/gemini-cli/configure` — accept Google API key or OAuth token
  - `GET /api/v1/integrations/gemini-cli/status` — check connection
- **Credential vault:** `integration_name="gemini_cli"`, key type = `api_key`
- **Wire:** Import in `routes.py`, add `router.include_router(gemini_auth.router, prefix="/gemini-auth", tags=["gemini-auth"])`

### Task 0.2 — Gemini CLI Execution Activity
- **Modify:** `apps/code-worker/workflows.py`
- **Add:** `_execute_gemini_chat()` method to `ChatCliWorkflow`
- **Command:** `gemini --prompt "{message}"` with MCP config JSON
- **Env:** `GOOGLE_API_KEY` from credential vault (fetched via internal API)
- **Parse:** Gemini CLI output format → extract response text

### Task 0.3 — CLI Session Manager Update
- **Modify:** `apps/api/app/services/cli_session_manager.py`
- **Add:** `"gemini_cli"` to `SUPPORTED_CLI_PLATFORMS`
- **Add:** Gemini credential fetch in `_get_cli_platform_credentials()`
- **Add:** Gemini MCP config generation (verify format compatibility)

### Task 0.4 — Agent Router Gemini Support
- **Modify:** `apps/api/app/services/agent_router.py`
- **Remove:** `# Future: gemini_cli` placeholder comment
- **Add:** Gemini to `_select_cli_platform()` logic
- **Routing heuristic:** Data/analytics tasks → Gemini (free tier, bulk ops)

### Task 0.5 — Frontend Integration Card
- **Modify:** `apps/web/src/components/IntegrationsPanel.js`
- **Add:** Gemini CLI card with API key input field
- **Match:** Existing Codex card pattern (status indicator + test button)

### Task 0.6 — Code Worker Dockerfile
- **Modify:** `apps/code-worker/Dockerfile`
- **Add:** Install Gemini CLI binary
- **Verify:** `gemini --version` in build step

**Deliverable:** `POST /chat` can route to Claude Code, Codex, or Gemini CLI based on task type.

---

## Phase 1: Wolfpoint.ai Rebrand

**Goal:** Platform identity transitions to Wolfpoint.ai.
**Estimated effort:** 2–3 days
**Depends on:** Nothing (runs parallel with Phase 0)

### Task 1.1 — Cloudflare Tunnel + DNS
- **Modify:** `docker-compose.yaml` (cloudflared service config)
- **Add:** `wolfpoint.ai` hostname to tunnel ingress rules
- **Keep:** `servicetsunami.com` as redirect during transition
- **Update:** DNS records in Cloudflare dashboard

### Task 1.2 — Frontend Rebrand
- **Modify:** `apps/web/public/index.html` — title, favicon, meta tags
- **Modify:** `apps/web/src/components/Layout.js` — logo, sidebar branding
- **Modify:** `apps/web/src/pages/LandingPage.js` — marketing copy
- **Assets:** Wolf + wave identity (light + dark mode variants)

### Task 1.3 — Backend References
- **Modify:** `apps/api/app/api/v1/routes.py` line 49 — update root message
- **Modify:** `apps/code-worker/` — PR body templates (attribution footer)
- **Modify:** Email templates — sender name, footer text
- **Modify:** WhatsApp business profile name (via Neonize config)

### Task 1.4 — Documentation
- **Modify:** `/workspace/CLAUDE.md` — project overview section
- **Modify:** `README.md` — project name and description
- **Keep:** Old domain references in comments for redirect mapping

### Task 1.5 — CI/CD & Helm
- **Modify:** `.github/workflows/*.yaml` — workflow descriptions
- **Modify:** `helm/values/*.yaml` — chart metadata
- **Modify:** Cloudflare Tunnel route configs

**Deliverable:** `wolfpoint.ai` serves the platform. Old domain redirects.

---

## Phase 2: STP Foundation — Node Network (Weeks 2–4)

**Goal:** Platform runs across 2+ machines with automatic failover.
**Depends on:** Phase 0 (all 3 CLIs needed)

### Task 2.1 — NetworkNode Model
- **Create:** `apps/api/app/models/network_node.py`
- **Fields:** `id`, `tenant_id` (FK), `name`, `tailscale_ip`, `status` (online/suspect/offline), `last_heartbeat`, `capabilities` (JSONB), `max_concurrent_tasks`, `current_load`, `pricing_tier`, `total_tasks_completed`, `avg_execution_time_ms`, `reputation_score`
- **Wire:** Import in `apps/api/app/models/__init__.py`

### Task 2.2 — AgentPackage Model
- **Create:** `apps/api/app/models/agent_package.py`
- **Fields:** `id`, `creator_tenant_id` (FK), `name`, `version` (semver), `content_hash` (SHA-256), `signature` (Ed25519), `creator_public_key`, `skill_id` (FK nullable), `metadata` (JSONB), `required_tools` (JSONB), `required_cli`, `pricing_tier`, `quality_score`, `total_executions`, `downloads`, `status` (draft/published/suspended)
- **Wire:** Import in `apps/api/app/models/__init__.py`

### Task 2.3 — Migration 050
- **Create:** `apps/api/migrations/050_network_nodes_agent_packages.sql`
- **Tables:** `network_nodes`, `agent_packages`
- **Indexes:** `status`, `quality_score`, `content_hash`, `creator_tenant_id`

### Task 2.4 — Node Directory API
- **Create:** `apps/api/app/api/v1/nodes.py`
- **Endpoints (internal, X-Internal-Key auth):**
  - `POST /api/v1/nodes/register` — node registration
  - `POST /api/v1/nodes/heartbeat` — heartbeat update
  - `GET /api/v1/nodes/` — list active nodes (JWT auth)
  - `GET /api/v1/nodes/{id}` — node details + metrics (JWT auth)
  - `DELETE /api/v1/nodes/{id}` — deregister node (JWT auth)
- **Wire:** Import in `routes.py`, mount at prefix `/nodes`

### Task 2.5 — Agent Package API
- **Create:** `apps/api/app/api/v1/agent_packages.py`
- **Endpoints (JWT auth):**
  - `POST /api/v1/agent-packages/publish` — sign + upload
  - `GET /api/v1/agent-packages/` — browse marketplace
  - `GET /api/v1/agent-packages/{id}` — package details
  - `GET /api/v1/agent-packages/{id}/download` — download (content-addressed)
  - `POST /api/v1/agent-packages/{id}/verify` — verify signature
- **Wire:** Import in `routes.py`, mount at prefix `/agent-packages`

### Task 2.6 — Node Daemon MVP
- **Create directory:** `apps/node-daemon/`
- **Files:**
  - `daemon.py` — main loop: register → heartbeat (30s) → accept tasks → report
  - `capability_probe.py` — detect OS, CPU, RAM, GPU, installed CLIs, LLM subscriptions
  - `agent_runner.py` — download agent package → verify signature → set up CLI session → execute
  - `metric_reporter.py` — execution time, success rate, resource usage
  - `config.yaml` — operator settings template
  - `requirements.txt`
  - `Dockerfile`
- **Temporal queue:** `stp-node-{node_id}` (dynamic per node)

### Task 2.7 — Multi-Node Task Routing
- **Modify:** `apps/api/app/services/agent_router.py`
- **Add:** Node selection logic alongside CLI platform selection
- **Query:** `NetworkNode` table for available nodes
- **Score:** `agent_quality * 0.4 + node_capability * 0.2 + (1-load) * 0.2 + latency * 0.1 + price * 0.1`
- **Dispatch:** To node-specific Temporal queue

### Task 2.8 — Failover Logic
- **Modify:** `apps/api/app/services/agent_router.py`
- **Add:** Heartbeat monitoring — suspect after 60s, offline after 90s
- **Add:** Task re-queue from offline nodes to healthy nodes
- **Leverage:** Temporal's built-in retry for in-flight task failover

### Task 2.9 — K3s + Tailscale Setup
- **Docs:** Create `docs/operations/k3s-tailscale-setup.md`
- **Tasks:**
  - Tailscale mesh setup (all operator machines)
  - K3s server on primary node, agents on secondaries
  - PostgreSQL HA with synchronous streaming replication
  - Cloudflare Tunnel multi-origin failover

**Deliverable:** Close laptop lid → site stays up within 90s, zero data loss.

---

## Phase 3: Credit System + Marketplace (Weeks 5–8)

**Goal:** Creators publish agents, users buy credits, revenue splits work.
**Depends on:** Phase 2

### Task 3.1 — Credit Models
- **Create:** `apps/api/app/models/credit.py`
- **Classes:** `CreditAccount` (balance, lifetime_earned/spent, escrow_hold, account_type) + `CreditTransaction` (amount, type, from/to accounts, task_execution_id)
- **Wire:** Import in `models/__init__.py`

### Task 3.2 — Migration 051
- **Create:** `apps/api/migrations/051_credit_system.sql`
- **Tables:** `credit_accounts`, `credit_transactions`
- **Indexes:** `tenant_id`, `transaction_type`, `created_at`

### Task 3.3 — Credit Service
- **Create:** `apps/api/app/services/credits.py`
- **Methods:**
  - `purchase_credits(tenant_id, amount)` — Stripe checkout → add balance
  - `execute_task_payment(task_id, user, creator, operator, tier)` — split 70/20/10
  - `hold_escrow(transaction_id)` — 24h hold
  - `release_escrow(transaction_id)` — release after 24h
  - `process_refund(transaction_id)` — refund from escrow
  - `get_balance(tenant_id)`, `get_earnings(tenant_id, period)`

### Task 3.4 — Credits API + Stripe
- **Create:** `apps/api/app/api/v1/credits.py`
- **Endpoints:**
  - `POST /api/v1/credits/purchase` — Stripe checkout session
  - `POST /api/v1/credits/webhook` — Stripe webhook handler
  - `GET /api/v1/credits/balance` — current balance
  - `GET /api/v1/credits/transactions` — transaction history
  - `GET /api/v1/credits/earnings` — creator/operator dashboard
- **Wire:** Import in `routes.py`, mount at prefix `/credits`

### Task 3.5 — Marketplace UI
- **Create:** `apps/web/src/pages/MarketplacePage.js`
- **Features:** Browse agents by category/quality/price, agent detail page, one-click execute
- **Wire:** Add route in `App.js`, nav item in `Layout.js`

### Task 3.6 — Operator Dashboard UI
- **Create:** `apps/web/src/pages/OperatorDashboardPage.js`
- **Features:** Node health, task metrics, earnings breakdown, settings
- **Wire:** Add route in `App.js`, nav item in `Layout.js`

### Task 3.7 — Quality Scoring from RL
- **Modify:** `apps/api/app/services/agent_router.py`
- **Add:** After every task, update `AgentPackage.quality_score` using RL formula
- **Auto-delist:** Agents below 0.3 after 50+ executions
- **Leverage:** Existing Ollama auto-quality scorer for implicit ratings

**Deliverable:** Someone publishes an agent, someone else pays credits to use it, revenue splits automatically.

---

## Phase 4: Open Network (Weeks 9–12)

**Goal:** Anyone can download the node daemon and join as an operator.
**Depends on:** Phase 3

### Task 4.1 — Public Node Registration
- Node daemon binary distribution (Docker image + Go binary)
- `wolfpoint node start` → auto-probe → join network
- Node reputation scoring (task success, uptime, dispute rate)

### Task 4.2 — Raft Consensus
- Embedded Raft (hashicorp/raft or etcd) for registry replication
- Agent registry, node directory, credit balances replicated
- Any node can read, leader handles writes

### Task 4.3 — Agent Ownership & Transfer
- Ed25519 keypair per creator
- Transfer: creator signs transfer message to new owner's public key
- Licensing: sell execution rights without transferring ownership

### Task 4.4 — Dispute Resolution
- 24h dispute window
- Credits refunded from operator's escrow
- Auto-resolve: if auto-quality score < 2/5, auto-refund

### Task 4.5 — Rate Limiting & Abuse Prevention
- Per-IP, per-account, per-node rate limits
- Anomaly detection for task volume spikes
- Node ban for consistently poor quality

### Task 4.6 — Node Daemon Go Rewrite
- Single binary: macOS (arm64, amd64), Linux (arm64, amd64), Windows
- `curl -fsSL wolfpoint.ai/install | sh`

**Deliverable:** 5+ external operators, 10+ marketplace agents, self-sustaining network.

---

## Knowledge Backfill & Local ML (Parallel Track)

> Runs alongside all phases — feeds the RL engine that powers everything above.

### Track A: Knowledge Backfill (Priority Order)

| # | Source | Method | Expected Yield |
|---|--------|--------|----------------|
| A1 | Claude Code sessions (~1GB, 25K messages) | `scripts/backfill_knowledge_from_sessions.py` | ~200 entities, ~2K observations |
| A2 | Git history (29 repos, ~1.2K commits) | Parse with `git log` + `gh` CLI | ~29 project + ~50 contributor entities |
| A3 | GitHub PRs & Issues | `gh` CLI → RL training data | ~200 PR observations, ~100 RL experiences |
| A4 | Gmail (6 months) | Luna MCP tools (search_emails, read_email) | ~100 contacts, ~200 observations |
| A5 | Google Drive | Luna MCP tools (search_drive, read_drive) | ~50 doc observations, ~30 entities |
| A6 | Shell history + system | Parse `~/.zsh_history`, SSH config | ~20 infra entities |
| A7 | Jira + Calendar + WhatsApp | Luna MCP tools | ~30 entities, ~100 observations |

**Total expected:** ~700+ entities, ~4K+ observations, ~6K+ embeddings

### Track B: Local ML on M4

| # | Feature | Framework | Impact |
|---|---------|-----------|--------|
| B1 | Auto-quality scorer (Ollama, Phi-3.5) | Already committed (`f715092`) | 100x more RL data at $0 cost |
| B2 | Contextual bandit router (LinUCB) | scikit-learn + numpy | ML-powered routing replaces keywords |
| B3 | Domain-tuned embeddings | sentence-transformers + PyTorch MPS | 20-40% better search relevance |
| B4 | Local entity extraction (Llama 3.2 3B) | MLX or Ollama | Knowledge graph grows from every message |

---

## Sequencing & Dependencies

```
Phase 0 (Gemini CLI) ──────────┐
                                ├──→ Phase 2 (Node Network)
Phase 1 (Wolfpoint Rebrand) ───┘         │
                                         ▼
                                Phase 3 (Marketplace + Credits)
                                         │
                                         ▼
                                Phase 4 (Open Network)

Knowledge Backfill (Track A) ──→ runs in parallel with all phases
Local ML (Track B) ────────────→ runs in parallel with all phases
```

---

## File Creation/Modification Summary

### New Files (by phase)

| Phase | File | Description |
|-------|------|-------------|
| 0 | `apps/api/app/api/v1/gemini_auth.py` | Gemini CLI auth endpoint |
| 2 | `apps/api/app/models/network_node.py` | Network node model |
| 2 | `apps/api/app/models/agent_package.py` | Agent package model |
| 2 | `apps/api/migrations/050_network_nodes_agent_packages.sql` | DB migration |
| 2 | `apps/api/app/api/v1/nodes.py` | Node directory API |
| 2 | `apps/api/app/api/v1/agent_packages.py` | Agent package API |
| 2 | `apps/node-daemon/` (directory) | Node daemon package |
| 3 | `apps/api/app/models/credit.py` | Credit ledger models |
| 3 | `apps/api/migrations/051_credit_system.sql` | DB migration |
| 3 | `apps/api/app/services/credits.py` | Credit service |
| 3 | `apps/api/app/api/v1/credits.py` | Credits API + Stripe |
| 3 | `apps/web/src/pages/MarketplacePage.js` | Marketplace UI |
| 3 | `apps/web/src/pages/OperatorDashboardPage.js` | Operator dashboard |

### Modified Files (by phase)

| Phase | File | Change |
|-------|------|--------|
| 0 | `apps/code-worker/workflows.py` | Add `_execute_gemini_chat()` |
| 0 | `apps/code-worker/Dockerfile` | Install Gemini CLI |
| 0 | `apps/api/app/services/cli_session_manager.py` | Add gemini_cli support |
| 0 | `apps/api/app/services/agent_router.py` | Gemini routing |
| 0 | `apps/web/src/components/IntegrationsPanel.js` | Gemini card |
| 0, 2, 3 | `apps/api/app/api/v1/routes.py` | Mount new routers |
| 1 | `apps/web/src/components/Layout.js` | Rebrand logo/name |
| 1 | `apps/web/public/index.html` | Title, favicon, meta |
| 1 | `docker-compose.yaml` | Tunnel config |
| 2 | `apps/api/app/services/agent_router.py` | Multi-node routing + failover |
| 2, 3 | `apps/api/app/models/__init__.py` | Register new models |
| 3 | `apps/web/src/App.js` | Marketplace + operator routes |
| 3 | `apps/web/src/components/Layout.js` | Marketplace + operator nav |

---

## Success Metrics

| Phase | Metric | Target |
|-------|--------|--------|
| 0 | CLI platforms operational | 3/3 (Claude Code, Codex, Gemini) |
| 1 | Wolfpoint.ai live | Domain serving, old domain redirecting |
| 2 | Multi-node uptime | Close lid → <90s failover, zero data loss |
| 3 | Marketplace activity | 1+ external agent published, 1+ paid execution |
| 4 | Network growth | 5+ operators, 10+ agents |
| ML | RL experiences | 43 → 500+ (auto-scored) |
| ML | Knowledge entities | 295 → 700+ |
| ML | Routing accuracy | Keyword-based → ML bandit |
