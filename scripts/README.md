# scripts/

Utility scripts. Categorized by purpose. Each is self-contained — read its top-of-file docstring before running.

## Deployment

| Script | Purpose |
|--------|---------|
| `deploy_k8s_local.sh` | Full local K8s deploy on Rancher Desktop: builds images, runs Helm, applies migrations, brings up the Cloudflare tunnel. Flags: `--skip-build`, `--infra-only`. |
| `local-deploy.sh` | Standalone local Kubernetes deploy (Rancher Desktop). Functional overlap with `deploy_k8s_local.sh` — prefer the latter; this is a known cleanup item. |
| `backup_db.sh` | Daily PostgreSQL backup of the local AgentProvision database. |

## Testing

| Script | Purpose |
|--------|---------|
| `e2e_test_production.sh` | End-to-end test suite (22 test cases). `BASE_URL=<url> ./e2e_test_production.sh`. |
| `test_critical_flows.sh` | Critical-flows smoke test — auth, chat, agents, integrations. |
| `production_health_check.sh` | Read-only health check against a deployed environment. |
| `verify_implementation.sh` | Comprehensive verification across phases 1–6 of the platform build-out. |
| `test_automations_api.py` | Exercise the workflow + automations API surface. |
| `verify_settings_integrations.py` | Smoke-test the integrations endpoint. |
| `verify_users_me.py` | Smoke-test `/users/me` and JWT issuance. |

## Data + memory

| Script | Purpose |
|--------|---------|
| `backfill_embeddings.py` | Backfill missing embeddings on `knowledge_entities` and `knowledge_observations`. |
| `backfill_knowledge_from_sessions.py` | Extract knowledge from Claude Code sessions and feed it into Luna's knowledge graph. |
| `migrate_skills_layout.py` | One-shot migration to the Skills v2 `_bundled/` + `_tenant/<uuid>/` layout. Idempotent. |
| `create_agent.py` | CLI helper to create an Agent record outside the wizard. |
| `check_datasets.py` | Inspect dataset rows + sync state. |
| `check_db_stats.py` | Print row counts across the multi-tenant tables. |

## Benchmarks + reports

| Script | Purpose |
|--------|---------|
| `benchmark_luna.py` | End-to-end Luna chat latency benchmark. Outputs go to `benchmarks/`. |
| `simulate_ceo_journey.py` | Scripted CEO-persona walkthrough — used to record demos and stress the chat path. |
| `fabrication_report.py` | Per-tenant fabrication-candidate report from the audit pipeline. |

## Demos + tenant-specific

| Script | Purpose |
|--------|---------|
| `run_demo_workflow.sh` | Run an end-to-end demo workflow using the seeded demo credentials. |
| `check_aremko_availability.py` | Curated availability checks for the Aremko tenant. |

## Conventions

- Bash scripts use `set -euo pipefail` and exit non-zero on failure.
- Python scripts include a top-of-file docstring describing args + side effects.
- Anything that mutates production data names that explicitly in its docstring and prompts before running.

## See also

- [`../docs/KUBERNETES_DEPLOYMENT.md`](../docs/KUBERNETES_DEPLOYMENT.md) — full deployment runbook.
- [`../apps/api/migrations/`](../apps/api/migrations/) — manual SQL migrations applied separately from these scripts.
