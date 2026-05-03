# AgentProvision Documentation

Index for everything inside `docs/`. Source of truth for **architecture** is [`../CLAUDE.md`](../CLAUDE.md) at repo root.

## Folder Map

| Folder | Purpose |
|--------|---------|
| [`changelog/`](changelog/) | Weekly digests of shipped features. Start here for "what's new". |
| [`plans/`](plans/) | Design docs + implementation plans (per feature, dated `YYYY-MM-DD-<slug>.md`) |
| [`report/`](report/) | Security audits, pentest reports, system health snapshots |
| [`archive/`](archive/) | Older docs kept for reference |
| [`demo/`](demo/) | Demo scripts + scenarios |
| [`leads/`](leads/) | Sales leads / outreach artifacts |
| [`pitch/`](pitch/) | Pitch materials |
| [`images/`](images/) | Screenshots, diagrams |

## Start Here

- **New contributor?** Read [`../CLAUDE.md`](../CLAUDE.md) for the full architecture.
- **What shipped recently?** [`changelog/2026-04-19-to-2026-05-03.md`](changelog/2026-04-19-to-2026-05-03.md) (latest), [`changelog/2026-04-12-to-2026-04-19.md`](changelog/2026-04-12-to-2026-04-19.md) (prior).
- **Quick agent reference?** [`../AGENTS.md`](../AGENTS.md)
- **Deploying?** [`KUBERNETES_DEPLOYMENT.md`](KUBERNETES_DEPLOYMENT.md)
- **Adding a new feature?** Follow the [writing-plans skill](#design-first-workflow) and save the plan to `plans/YYYY-MM-DD-<slug>.md`.

## Recent Plans (April 2026)

| Date | Plan | Purpose |
|------|------|---------|
| 2026-04-02 | [`plans/2026-04-02-gemma-4-integration-plan.md`](plans/2026-04-02-gemma-4-integration-plan.md) | Replace Qwen2.5-Coder with Gemma 4 for local inference |
| 2026-04-03 | [`plans/2026-04-03-dynamic-workflows-visual-builder-design.md`](plans/2026-04-03-dynamic-workflows-visual-builder-design.md) / [`plan`](plans/2026-04-03-dynamic-workflows-visual-builder-plan.md) | Visual ReactFlow workflow builder + static→dynamic migration |
| 2026-04-03 | [`plans/2026-04-03-luna-optimization-agent-driven-runtime-design.md`](plans/2026-04-03-luna-optimization-agent-driven-runtime-design.md) | Model-tier routing, agent-driven runtime fields |
| 2026-04-04 | [`plans/2026-04-04-luna-benchmark-results.md`](plans/2026-04-04-luna-benchmark-results.md) | Luna optimization benchmarks |
| 2026-04-07 | [`plans/2026-04-07-memory-first-agent-platform-design.md`](plans/2026-04-07-memory-first-agent-platform-design.md) | Memory-first architecture, Rust gRPC services |
| 2026-04-10 | [`plans/2026-04-10-memory-first-phase-2-plan.md`](plans/2026-04-10-memory-first-phase-2-plan.md) + [`cutover`](plans/2026-04-10-phase-2-cutover-criteria.md) | Rust memory migration cutover |
| 2026-04-11 | [`plans/2026-04-11-memory-rl-pipeline-fixes.md`](plans/2026-04-11-memory-rl-pipeline-fixes.md) | Memory + RL pipeline stabilization |
| 2026-04-11 | [`plans/2026-04-11-whatsapp-voice-commands-plan.md`](plans/2026-04-11-whatsapp-voice-commands-plan.md) | WhatsApp voice transcription pipeline |
| 2026-04-12 | [`plans/2026-04-12-a2a-collaboration-demo-design.md`](plans/2026-04-12-a2a-collaboration-demo-design.md) / [`impl`](plans/2026-04-12-a2a-collaboration-implementation.md) | **A2A Collaboration System** |
| 2026-04-12 | [`plans/2026-04-12-spatial-knowledge-exploration-design.md`](plans/2026-04-12-spatial-knowledge-exploration-design.md) | **Luna OS Spatial HUD** design |
| 2026-04-17 | [`plans/2026-04-17-landing-page-redesign-design.md`](plans/2026-04-17-landing-page-redesign-design.md) / [`plan`](plans/2026-04-17-landing-page-redesign-plan.md) | Marketing site rewrite |
| 2026-04-18 | [`plans/2026-04-18-agent-lifecycle-management-platform-plan.md`](plans/2026-04-18-agent-lifecycle-management-platform-plan.md) | **Agent Lifecycle Management Platform** |
| 2026-04-18 | `plans/2026-04-18-agent-fleet-enhancement-plan.md` *(local-only)* | AgentsPage fleet restructure + AgentDetailPage |
| 2026-04-18 | `plans/2026-04-18-chat-ui-redesign-plan.md` *(local-only)* | Chat UI modernization |
| 2026-04-18 | `plans/2026-04-18-memory-entities-seed-plan.md` *(local-only)* | Entity backfill strategy |
| 2026-04-18 | `plans/2026-04-18-skills-marketplace-redesign-plan.md` *(local-only)* | Skills marketplace UX (superseded by [`2026-04-26-skills-fleet-alignment-plan.md`](plans/2026-04-26-skills-fleet-alignment-plan.md)) |
| 2026-04-18 | `plans/2026-04-18-security-fixes.md` *(local-only)* / [`remediation`](plans/2026-04-18-security-remediation-plan.md) | Security hardening + open-item tracker |
| 2026-04-20 | [`plans/2026-04-20-design-system-audit.md`](plans/2026-04-20-design-system-audit.md) | Ocean theme audit (unification plan still local-only) |
| 2026-04-22 | [`plans/2026-04-22-codebase-cleanup-plan.md`](plans/2026-04-22-codebase-cleanup-plan.md) | Repo-wide cleanup pass |
| 2026-04-23 | [`plans/2026-04-23-luna-latency-reduction-plan.md`](plans/2026-04-23-luna-latency-reduction-plan.md) | **Latency campaign** — greeting fast-path, prompt trim, KV cache probe |
| 2026-04-25 | [`plans/2026-04-25-luna-hallucination-reduction-plan.md`](plans/2026-04-25-luna-hallucination-reduction-plan.md) | **Hallucination reduction** |
| 2026-04-26 | [`plans/2026-04-26-external-agents-and-a2a-enhancement-plan.md`](plans/2026-04-26-external-agents-and-a2a-enhancement-plan.md) | **External Agents + A2A v2** — Workflows-as-spine, Microsoft import |
| 2026-04-26 | [`plans/2026-04-26-skills-fleet-alignment-plan.md`](plans/2026-04-26-skills-fleet-alignment-plan.md) | **Skills Marketplace v2** — `_bundled/` + `_tenant/<uuid>/`, library_revisions |
| 2026-05-03 | [`plans/2026-05-03-visibility-roadmap-plan.md`](plans/2026-05-03-visibility-roadmap-plan.md) | Visibility roadmap — Tier 1 shipped, Tier 2-5 + ops + marketing scoped |
| 2026-05-03 | [`plans/2026-05-03-documentation-refresh-plan.md`](plans/2026-05-03-documentation-refresh-plan.md) | This documentation refresh |

## Recent Reports

| Date | Report | Verdict |
|------|--------|---------|
| 2026-04-13 | [`report/2026-04-13-a2a-coalition-verification-report.md`](report/2026-04-13-a2a-coalition-verification-report.md) | A2A demo verified working |
| 2026-04-17 | [`report/2026-04-17-platform-security-audit.md`](report/2026-04-17-platform-security-audit.md) | Initial security audit (container + infra) |
| 2026-04-18 | `report/2026-04-18-full-security-audit.md` *(local-only)* | Full 8-finding audit (application + auth + container) |
| 2026-04-18 | [`report/2026-04-18-pentest-verification.md`](report/2026-04-18-pentest-verification.md) | Black-hat verification — 6 fixes confirmed, 4 open items tracked |

## Key References

| Topic | Doc |
|-------|-----|
| Architecture | [`../CLAUDE.md`](../CLAUDE.md) |
| K8s deployment | [`KUBERNETES_DEPLOYMENT.md`](KUBERNETES_DEPLOYMENT.md) |
| MCP integration | [`MCP_INTEGRATION.md`](MCP_INTEGRATION.md) |
| LLM integration | [`LLM_INTEGRATION_README.md`](LLM_INTEGRATION_README.md) |
| Tool framework | [`TOOL_FRAMEWORK_README.md`](TOOL_FRAMEWORK_README.md) |
| Context management | [`CONTEXT_MANAGEMENT_README.md`](CONTEXT_MANAGEMENT_README.md) |
| Patent disclosure | [`PATENT_DISCLOSURE_2026-04-04.md`](PATENT_DISCLOSURE_2026-04-04.md) |

## Design-First Workflow

1. **Brainstorm** the feature (open question, not an answer)
2. **Write a plan** — save to `plans/YYYY-MM-DD-<feature-slug>.md` with: Goal, Architecture, Tech Stack, Task breakdown (checkboxes), File Structure, Commit steps
3. **Execute** — fresh subagent per task, review between tasks
4. **Write a report** if the feature involves verification — save to `report/YYYY-MM-DD-<slug>.md`
5. **Update the changelog** in `changelog/<week-range>.md` at week close

Never add planning docs, tests, or scripts to the repo root. Dedicated folders only.
