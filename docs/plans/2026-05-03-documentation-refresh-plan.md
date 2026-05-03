# Documentation Refresh — 2026-05-03

## Context

Docs across the repo had drifted from the implementation. Most-cited symptoms:

- **AGENTS.md** still described the ADK hierarchy and references `apps/adk-server/` — ADK was removed 2026-03-18.
- **`.github/copilot-instructions.md`** last updated 2026-03-25 — references Qwen (replaced by Gemma 4), 81 MCP tools (now 90+), `AgentKitExecution` workflow (AgentKit was removed 2026-04-19), wrong dev port `8001` (host port is 8000).
- **README.md** "Latest" callout stops at the 2026-04-12 → 04-19 week. Two more weeks of major shipped features missing: Skills Marketplace v2 (PRs #182–#193), External Agents + A2A v2 (PRs #194–#205), GitHub Copilot CLI as a runtime (#244), Microsoft Teams channel (#241/#250), latency campaign (PRs #210–#222), security #207.
- **docs/README.md** plan index stops at 2026-04-18.
- **CLAUDE.md** still uses the old three-tier (native/community/custom) skill description; doesn't reflect the `_bundled/` + `_tenant/<uuid>/` layout, `library_revisions` audit, `read_library_skill` MCP tool, or the security #207 fix.
- Several legacy files in `docs/` are stale on Qwen→Gemma 4 and ports — see audit below.

The intended outcome is a single PR that brings all canonical docs current to 2026-05-03 and adds a changelog for the missing fortnight.

## Scope

Update only **canonical living docs**. Dated session summaries, pentest reports, and historical snapshots are left frozen. Nothing in `apps/`, `helm/`, or `infra/` changes.

## Plan

### Audit (already run)

A read-only Explore agent classified every legacy doc in `docs/`:

| Bucket | Files |
|--------|-------|
| **Outdated — fix** | `docs/MCP_INTEGRATION.md`, `docs/LLM_INTEGRATION_README.md`, `docs/CONTEXT_MANAGEMENT_README.md`, `docs/ux-improvements-clevel.md`, `docs/LEVIS_ARCHITECTURE_OVERVIEW.md` |
| **Historically frozen — keep** | `docs/REPORTING_VISUALIZATION_SUMMARY.md`, `docs/IMPLEMENTATION_VERIFICATION_REPORT.md`, `docs/MANUAL_BROWSER_TESTING_CHECKLIST.md`, `docs/TESTING_SUMMARY.md`, `docs/CEO_JOURNEY_SIMULATION_RESULTS.md`, `docs/SESSION_SUMMARY_*`, `docs/DEPLOYMENT_STATUS_2025_11_26.md`, `docs/USAGE_REPORT_2026-03-11.md` |
| **Accurate — no changes** | `docs/KUBERNETES_DEPLOYMENT.md`, `docs/TOOL_FRAMEWORK_README.md`, `docs/CRITICAL_FLOWS_TEST_RESULTS.md`, `helm/README.md`, `apps/web/README.md`, `apps/api/migrations/README.md` |

### Edits

#### Tier 1 — canonical living docs (must be current)

- [x] **`AGENTS.md`** — full rewrite. New structure: CLI runtimes vs Platform Agents, ALM, A2A v2, Skills v2, MCP tool count + tenant_id requirement, code style, hard rules.
- [x] **`.github/copilot-instructions.md`** — full rewrite. Correct ports (8000 host, 50051/50052 Rust gRPC), Gemma 4 instead of Qwen, 90+ MCP tools, ALM, A2A, Skills v2, security required-secrets footgun, manual migration runbook.
- [x] **`README.md`** — surgical edits: bump "Latest" callout to 2026-04-19→05-03 (Skills v2, External Agents v2, Teams, Copilot CLI runtime, latency, #207); add channel "Microsoft Teams"; add Skills v2 + External Agents v2 sections; mark Copilot CLI as full runtime in Platform Auth table; update Recent highlights links.
- [x] **`docs/README.md`** — add 2026-04-20 → 2026-04-26 plans to the Recent Plans index; bump "What shipped" link to the new fortnight changelog; add AGENTS.md quick-link.
- [x] **`docs/changelog/2026-04-19-to-2026-05-03.md`** — new file. Sections: Skills v2, External Agents+A2A v2, Copilot CLI runtime, Microsoft Teams channel, CLI routing resilience, latency campaign, security #207, workflow reliability fixes, memory + observability, Settings UI refresh.
- [x] **`CLAUDE.md`** — update Skill Marketplace section to v2 layout; add TeamsMonitorWorkflow to orchestration worker list; correct security note about internal endpoints; bump tool count + CLI runtime list in opening paragraph.

#### Tier 2 — outdated reference files

- [ ] **`docs/MCP_INTEGRATION.md`** — bump tool count "81" → "90+".
- [ ] **`docs/LLM_INTEGRATION_README.md`** — replace `Qwen` references with `Gemma 4`; correct ports (`8001` → `8000` for API host, `8003` no longer required, MCP on `8086`).
- [ ] **`docs/CONTEXT_MANAGEMENT_README.md`** — replace localhost:8001 in curl examples with localhost:8000; clarify session binding (Agent direct, no AgentKit).
- [ ] **`docs/ux-improvements-clevel.md`** — replace any `/agent-kits` references with `/agents` (AgentKit was removed 2026-04-19); flag any other stale recommendations.
- [ ] **`docs/LEVIS_ARCHITECTURE_OVERVIEW.md`** — replace Qwen with Gemma 4; verify Cloudflare tunnel description matches in-cluster pod model.

### Critical files modified

| File | Status | Change |
|------|--------|--------|
| `AGENTS.md` | Done | Full rewrite |
| `.github/copilot-instructions.md` | Done | Full rewrite |
| `README.md` | Done | Surgical edits |
| `docs/README.md` | Done | Plan index update |
| `docs/changelog/2026-04-19-to-2026-05-03.md` | Done | New file |
| `CLAUDE.md` | Done | Skills v2, Teams, security, CLI runtimes |
| `docs/MCP_INTEGRATION.md` | Pending | Tool count |
| `docs/LLM_INTEGRATION_README.md` | Pending | Gemma 4, ports |
| `docs/CONTEXT_MANAGEMENT_README.md` | Pending | Ports |
| `docs/ux-improvements-clevel.md` | Pending | AgentKit refs |
| `docs/LEVIS_ARCHITECTURE_OVERVIEW.md` | Pending | Gemma 4, tunnel |

## Branch + workflow

- Worktree: `../servicetsunami-agents-docs-refresh`
- Branch: `docs/refresh-2026-05-03`
- PR assigned to `nomade`, no AI co-author tags, no commits to main.

## Verification

- `git diff --stat main` should show only doc files (`*.md`) plus this plan.
- Spot-check that internal links resolve: `grep -nE "\\]\\([^)]+\\.md\\)" README.md AGENTS.md docs/README.md docs/changelog/2026-04-19-to-2026-05-03.md`.
- Confirm `.github/copilot-instructions.md` and `AGENTS.md` no longer mention `apps/adk-server/`, `Qwen`, `AgentKit`, or `81 tools`.
- Open PR; rely on cross-tool review for accuracy of architectural claims.

## Out of scope

- App-level READMEs (`apps/api/`, `apps/code-worker/`, `apps/mcp-server/`, `apps/luna-client/`, `apps/embedding-service/`, `apps/memory-core/`) — none currently exist; not part of this refresh.
- Translating any docs (project is English-only at the doc layer).
- The historical snapshot files in `docs/` (kept for record).
- Helm chart values / Terraform — no infra docs change.
