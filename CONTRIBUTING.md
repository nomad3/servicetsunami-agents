# Contributing to AgentProvision

Thanks for working on AgentProvision. This file is the short version of the rules — the full picture lives in [`CLAUDE.md`](CLAUDE.md) (architecture + patterns) and [`AGENTS.md`](AGENTS.md) (agent-system layout — CLI runtimes vs platform agents, ALM, A2A, Skills v2, MCP tools). If something here disagrees with `CLAUDE.md`, `CLAUDE.md` wins.

## Hard rules (no exceptions)

1. **Never commit to `main`.** Always feature-branch + PR. Assign PRs to `nomad3` (the GitHub login; the user goes by "nomade" elsewhere).
2. **Never add `Co-Authored-By: Claude` (or any AI credit — Gemini, Codex, Copilot) to commits, PR descriptions, or code comments.**
3. **Never add docs / plans / tests / scripts to the repo root.** Use the dedicated folders:
   - `docs/plans/YYYY-MM-DD-<slug>.md` for design docs and implementation plans.
   - `docs/report/YYYY-MM-DD-<slug>.md` for verification + audit reports.
   - `docs/changelog/<week-range>.md` for weekly digests.
   - `scripts/` for utility scripts.
   - Tests live next to the code they exercise.
4. **Every multi-tenant query must filter by `tenant_id`.** No exceptions. `current_user.tenant_id` comes from the JWT via `deps.get_current_user`. Missing the filter is a multi-tenancy break.
5. **Never add `Co-Authored-By: Claude` (or any AI credit)**. (Repeated because it's the rule that gets forgotten the most.)
6. **Mirror manual changes into Helm + Git + Terraform.** Drift between these three is the single biggest source of post-deploy surprises.
7. **Don't build production Tauri DMGs locally.** Push to `main` and let `.github/workflows/luna-client-build.yaml` produce the signed macOS ARM64 DMG. Local builds aren't signed and won't ingest the auto-updater feed.
8. **Don't bypass git hooks** (`--no-verify`, `--no-gpg-sign`, etc.) unless the user explicitly asks for it. If a hook fails, fix the underlying issue.
9. **Don't skip review.** Even doc-only PRs benefit from a second pair of eyes — see *Code review* below.

## Local setup

The primary local runtime is **docker-compose**.

```bash
git clone https://github.com/nomad3/servicetsunami-agents.git
cd servicetsunami-agents

# 1. Configure secrets — all three are REQUIRED, no defaults
cp apps/api/.env.example apps/api/.env
# Generate hex secrets:
#   python -c "import secrets; print(secrets.token_hex(32))"
# Generate Fernet:
#   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
# Edit apps/api/.env to set SECRET_KEY, API_INTERNAL_KEY, MCP_API_KEY, ENCRYPTION_KEY.

# 2. Start the stack
docker compose up -d

# 3. Apply migrations (manual SQL, no Alembic — see "Migrations" below)
PG=$(docker compose ps -q db)
for f in apps/api/migrations/*.sql; do
  docker exec -i "$PG" psql -U postgres agentprovision < "$f"
done

# Endpoints (host ports)
# Web:    http://localhost:8002    or https://agentprovision.com (via tunnel)
# API:    http://localhost:8000    or https://agentprovision.com/api/v1/
# Luna:   http://localhost:8009    or https://luna.agentprovision.com
# Demo:   test@example.com / password
```

For the Rancher Desktop K8s + Helm path see [`docs/KUBERNETES_DEPLOYMENT.md`](docs/KUBERNETES_DEPLOYMENT.md) and [`scripts/deploy_k8s_local.sh`](scripts/deploy_k8s_local.sh).

## Branching + PRs

```bash
# 1. New branch off main
git checkout main && git pull
git checkout -b <type>/<slug>      # feat/, fix/, docs/, refactor/, chore/

# 2. Commit. NEVER add AI-credit lines.
git commit -m "feat(area): one-line summary

Why this change matters in 2-3 sentences.

- bullet of key change
- bullet of another"

# 3. Push + open PR assigned to nomad3
git push -u origin HEAD
gh pr create --assignee nomad3 --title "<type>(<area>): <summary>" --body "..."
```

PR description template:

```markdown
## Summary
- Bullet of what changed and why.

## Test plan
- [ ] What you ran locally to verify.
- [ ] Edge cases checked.

(Optional) ## Out of scope
- Things deliberately not changed in this PR.
```

For long-running parallel work, use `git worktree add -b <branch> ../<dir> origin/main` to keep the main checkout free for other agents.

## Migrations

Manual SQL — no Alembic.

```bash
# 1. Find the next number
ls apps/api/migrations/*.sql | sort | tail -1
# Add SQL at apps/api/migrations/NNN_<slug>.sql

# 2. Force-add (the repo .gitignore catches *.sql)
git add -f apps/api/migrations/NNN_*.sql

# 3. Apply against the local DB container
#    Resolves the compose project's db service even if it isn't named "*-db-1"
PG=$(docker compose ps -q db)
docker exec -i "$PG" psql -U postgres agentprovision < apps/api/migrations/NNN_<slug>.sql

# 4. Record the application
docker exec -i "$PG" psql -U postgres agentprovision \
  -c "INSERT INTO _migrations(filename) VALUES ('NNN_<slug>.sql');"
```

The column on `_migrations` is `filename`, not `name`. There is **no** auto-runner in the API container.

## Tests + lint

| App | Test command | Lint command |
|-----|--------------|--------------|
| `apps/api` | `pytest` | `ruff check app` |
| `apps/web` | `npm test -- --ci --watchAll=false` | (CRA built-in) |
| `apps/mcp-server` | `pytest tests/ -v` | (none) |
| `apps/luna-client` | `npm run tauri dev` (manual) | `cd src-tauri && cargo check` |
| Rust services | `cargo check` | `cargo clippy` |

## Code review

Every non-trivial PR gets at least one round of code review. For docs PRs, focus on **factual accuracy** (does the doc match the code?) and **internal-link integrity**, not just prose style.

For agents (Claude / Codex / Gemini / Copilot CLI) running in this repo: dispatch the `superpowers:code-reviewer` agent with a tight brief that includes:
- What was implemented (one paragraph).
- The base + head commit SHAs.
- The specific facts to verify against code (e.g., "Is `postgres_worker.py` actually a file? Does `setup_global_shortcut` live in `lib.rs:392`?").
- A 600-word cap so the response is actionable.

Apply Critical and Important fixes before merging. Document Minor for follow-up.

## Adding a new resource

1. **Model** — `apps/api/app/models/{resource}.py` with a `tenant_id` FK.
2. **Schema** — `apps/api/app/schemas/{resource}.py` (`Create` / `Update` / `InDB`).
3. **Service** — `apps/api/app/services/{resources}.py` extending `BaseService`.
4. **Routes** — `apps/api/app/api/v1/{resources}.py`, mount in `routes.py`.
5. **Migration** — manual SQL (see *Migrations* above).
6. **Frontend** — page in `apps/web/src/pages/`, route in `App.js`, nav in `Layout.js`.
7. **Helm** — values in `helm/values/` if a new service is needed.

Mirror everything into Helm + Terraform if your change affects infrastructure.

## Adding an MCP tool

1. Pick the right module under `apps/mcp-server/src/mcp_tools/` (or add a new one + register in `src/server.py`).
2. Decorate with `@mcp.tool()`. Type-hint the params; FastMCP generates the JSON-Schema.
3. **Always accept `tenant_id` first.** Tools fail without it.
4. Calls into the API use `/api/v1/*/internal/*` endpoints with the `X-Internal-Key` header. (#207, 2026-04-22 — these are blocked from the public internet but still reachable in-cluster.)
5. Add a test in `apps/mcp-server/tests/`.

## CLI runtime routing

The four supported CLI runtimes (Claude Code, Codex, Gemini CLI, GitHub Copilot CLI) all use **tenant subscriptions via OAuth** — zero API credits. Per-tenant default lives in `tenant_features.default_cli_platform`; autodetect + quota fallback (#245) handles rate-limited or unavailable runtimes automatically.

When adding a feature that dispatches a CLI runtime, route through `apps/api/app/services/agent_router.py` — never hardcode a CLI in coalition patterns or workflow templates. A2A patterns are explicitly **CLI-agnostic**.

## When in doubt

- For architecture: [`CLAUDE.md`](CLAUDE.md).
- For agent-system specifics: [`AGENTS.md`](AGENTS.md).
- For deployment: [`docs/KUBERNETES_DEPLOYMENT.md`](docs/KUBERNETES_DEPLOYMENT.md).
- For weekly shipped features: [`docs/changelog/`](docs/changelog/).
- For design docs and implementation plans: [`docs/plans/`](docs/plans/).
- For security audits and pentest verifications: [`docs/report/`](docs/report/).

If a rule isn't here and isn't in the above docs, **ask before assuming** — open a draft PR with the question in the description.
