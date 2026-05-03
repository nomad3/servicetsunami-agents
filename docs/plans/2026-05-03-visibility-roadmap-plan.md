# Visibility Roadmap

**Date opened:** 2026-05-03
**Owner:** Simon Aguilera
**Branch convention:** `feat/visibility-N-<slug>` per tier

## Background

After the 48-hour sprint (PRs #241–#252) + holistic review follow-ups
(PRs #253–#255), the platform captures a lot of useful state — most
of it just isn't surfaced. The data is in the DB or logs already;
this roadmap turns it into product visibility for tenants and ops
visibility for us.

What we capture today vs where it surfaces:

| Already in DB / logs | Where it surfaces |
|---|---|
| `cli_chain_attempted` + `cli_fallback_used` per turn | structured logs only (PR #245 review intentionally kept raw chain out of client metadata) |
| `agent_audit_log` (every invocation, tokens, cost) | Org page Audit tab (#248), AgentDetailPage Audit tab |
| `agent_performance_snapshot` (hourly rollup per agent) | AgentDetailPage Performance tab |
| `rl_experience` (6-dim quality score per response) | Learning page |
| Cooldown state | Redis only — invisible |
| Memory recall stats | logs |
| Teams / Inbox monitor tick results | Temporal UI (admin-only) |
| A2A blackboard | CollaborationPanel during live session |

This is mostly a surfacing problem, not an instrumentation problem.

## Tiers (smallest → biggest leverage, ordered by ship-priority)

### Tier 1 — Routing transparency footer ✅ SHIPPED (PR #256, 2026-05-03)

One-line muted footer under each assistant message:

> Served by **GitHub Copilot CLI** · 891 tokens · $0.0123 · 14.2s

When fallback fired:

> Routed to **GitHub Copilot CLI** after ~~Claude Code~~ returned
> *rate limit / quota exceeded* · 1268 tokens · $0.0185 · 16.1s `[fallback]`

**Curated, not raw.** The footer is backed by a NEW
`metadata.routing_summary` field in `agent_router._build_routing_summary`
that deliberately excludes the raw `cli_chain_attempted` list (PR #245
review's concern about exposing internals). Operators get the full
chain via structured logs (unchanged); customers get the polished
outcome only. A test pins the no-leak invariant.

**Stamped at three sites in `agent_router.route_and_execute`:**
1. Chain dispatch loop on success (with fallback metadata)
2. Greeting template fast-path
3. Local-Gemma short-message fallback

**Verified live (2026-05-03):** AgentProvision tenant chat returns
`routing_summary.served_by="GitHub Copilot CLI"`, `chain_length=1`,
no fallback. No-leak invariant holds.

### Tier 2 — Cost + quota dashboard per tenant + per team

**Why:** Critical Levi-rollout blocker. Once a tenant has 1000 imported
Copilot Studio bots from 100 employees, leadership needs cost
attribution by team. Without this, "import bot → run on Copilot CLI"
becomes a billing surprise.

**What we already have:** `agent_performance_snapshot` rolls up hourly
per agent (success rate, p95 latency, tokens, cost, quality). Every
agent has `team_id` + `owner_user_id` columns from the ALM platform
(PR #153).

**What to build:** new page `/insights/cost`:
- Daily/weekly cost trend by CLI platform (stacked bar)
- Top 10 most-expensive agents
- Per-team rollup
- Per-owner rollup
- Quota burn projection ("at current rate, you'll hit your monthly
  token cap in 8 days")
- Alert thresholds ("tell me when an agent crosses $X/day")

**Estimate:** ~600-800 lines, 2 PRs (backend rollup endpoint + UI).

**Dependencies:** none — data is already there.

### Tier 3 — Imported-agent fleet health

**Why:** Specifically for the Microsoft import case. Once Levi imports
1000 bots, leadership needs to triage — which got called this week,
which are zombies, which drifted from their Copilot Studio source.

**Surface:**
- "Last invoked" timestamp on each agent (`agent.last_used_at` exists)
- "Zombies" filter — bots not called in N days, candidates for cleanup
- Drift indicator — source bot changed in Copilot Studio after import
  (needs the sync workflow from the rollout plan)
- Owner activity — "@bob's Sales Bot was called 423 times this week"

**Estimate:** ~400 lines, 1 PR. Drift detection needs the
`MicrosoftAgentSyncWorkflow` from the original rollout plan.

### Tier 4 — Live activity feed

**Why:** Single most-watched dashboard at any enterprise. Top of the
operations dashboard: rolling tail of "what happened in your tenant
in the last 5 minutes":

```
14:32 Sales Bot replied to @sarah · 2.1s · $0.003
14:31 Triage Agent classified incident #4521 · 1.8s · $0.001
14:30 Cardiac Analyst extracted DACVIM report · 8.4s · $0.024
```

**Backed by:** `agent_audit_log` polling, or SSE if real-time matters
(A2A already has the Redis pub/sub pattern from 2026-04-12).

**Estimate:** ~300 lines, 1 PR.

### Tier 5 — Coalition replay viewer

**Why:** A2A coalition runs are persisted on the Blackboard but only
viewable live. Historical replay lets ops/leadership investigate
"what did the incident-response coalition do" after the fact.

**Surface:** pick any past coalition run, replay the blackboard
timeline with phase markers, see which agents contributed what.

**Estimate:** ~250 lines, 1 PR. Data is already persisted.

## Operational visibility (us-facing)

### Op-1 — Resolver chain Grafana board

Promote `cli_chain_attempted` from `logger.info` to a Prometheus
counter / histogram series. Track:
- Fallback firing rate by tenant
- Cooldown hit rate by CLI
- Mean chain length per turn
- Per-CLI quota-error frequency

Reveals whether autodetect is working in production. ~100 lines
instrumentation + Grafana panel JSON.

### Op-2 — Tenant health page (admin-only)

Per-tenant: chat p50/p95, error rate, cooldown count, last-failed-CLI.
Slice the same data as Tier 2 but by health, not cost.

## Marketing visibility (different problem entirely)

### Mk-1 — SEO + analytics on agentprovision.com

- Add OG tags + Twitter card meta to `<Helmet>` in `LandingPage.js`
- `sitemap.xml` + `robots.txt` (don't have one — confirmed 404)
- Plausible or Fathom analytics snippet (privacy-first, GDPR-friendly)
- Submit to Google Search Console once sitemap exists
- Schema.org `SoftwareApplication` markup

~50 lines + DNS verification step. Half a day total.

## Recommended order

1. **Tier 1 — routing footer** ✅ done (PR #256)
2. **Tier 3 — imported-agent fleet health** — next (small, data already
   there, demonstrates "single pane of glass" pitch for Levi)
3. **Tier 2 — cost dashboard** — Levi-rollout blocker
4. **Tier 4 — live activity feed** — high-watch UI
5. **Op-1, Op-2** — quieter day work
6. **Tier 5 — coalition replay** — once Levi-style A2A is more used
7. **Mk-1 — SEO/analytics** — once cost dashboard exists

## Done items log

- 2026-05-03: PR #256 — routing footer (Tier 1). 8 backend tests +
  6 frontend tests. Verified live on AgentProvision.
- 2026-05-03: PR #260 — routing footer review fixes (C1 served-actual,
  C2 chain-exhausted, I1 first-err attribution, I2 autodetect,
  I3 E2E no-leak, I4 exception classification + M cleanup). 22 backend
  + 9 frontend tests.
- 2026-05-03: Tier 3 (#263) — imported-agent fleet health. 10 backend
  + 6 frontend tests.
- 2026-05-03: Tier 2 (#265) — cost dashboard. 10 backend + 9 frontend
  tests.
- 2026-05-03: Tier 4 (#267) — Live activity feed (`LiveActivityFeed`
  component on dashboard, polls `/audit/agents` every 15s, pause
  toggle, hidden on 403). 6 frontend tests.
- 2026-05-03: Tier 5 (#268) — Coalition replay viewer. New
  `GET /insights/collaborations(/:id)` endpoints + `CoalitionReplayPage`
  (list + detail). Reads persisted blackboard substrate from A2A
  shipped 2026-04-12. 6 backend tests.
- 2026-05-03: Op-1 (#269) — Resolver chain metrics endpoint
  (`GET /insights/resolver-metrics`). Tenant-wide rollup of how the
  resolver served chat turns over the last N hours (≤7d): served-by
  distribution, fallback rate, fallback reasons, chain-exhausted
  count, chain length p50/p95. Curated shape, no message IDs, no raw
  cli_chain_attempted. 10 tests pin invariants.
- 2026-05-03: Op-2 (#271) — Tenant health admin page
  (`GET /admin/tenant-health` superuser-only + `/admin/tenant-health`
  React page). Cross-tenant snapshot — turns 24h, fallback rate,
  chain-exhausted count, last activity, primary CLI, agent + user
  counts. Stalled tenants surface as dimmed rows. 7 backend tests +
  3 frontend tests.
- 2026-05-03: Mk-1 (#273) — SEO + analytics on landing. JSON-LD
  Organization + WebSite + SoftwareApplication structured data,
  canonical link, robots meta, robots.txt, sitemap.xml. Plausible
  analytics shim gated on REACT_APP_PLAUSIBLE_DOMAIN — no third-
  party script loads in dev / preview / self-hosted. CTA tracking
  on hero / nav / footer-CTA. 9 tests pin the analytics gate +
  no-PII behavior + idempotency + queue stub.

**ROADMAP COMPLETE.** All 5 product tiers + 2 ops + 1 marketing
shipped on 2026-05-03.

## Working agreements while this is in flight

- Each tier is its own PR (or 2 PRs for Tier 2), reviewed via the
  same code → tests → superpowers review → fix findings → merge →
  live verify cycle the 48h sprint used.
- Multi-agent parallel work allowed via `isolation: "worktree"` on the
  Agent tool (rule reversed 2026-05-03). Independent tiers can be
  worked simultaneously.
- Curate, don't dump — Tier 1's ROUTING_SUMMARY pattern is the model:
  expose only what helps the customer, log the rest. The same applies
  to cost dashboards (don't show internal cost components, show the
  total + breakdown the customer cares about).
