# Platform Core-Primitives Smell Report

| Field | Value |
|---|---|
| Date | 2026-05-28 |
| Spec | [`docs/superpowers/specs/2026-05-28-core-primitives-smell-report-design.md`](docs/superpowers/specs/2026-05-28-core-primitives-smell-report-design.md) |
| Plan | [`docs/superpowers/plans/2026-05-28-core-primitives-smell-report-plan.md`](docs/superpowers/plans/2026-05-28-core-primitives-smell-report-plan.md) |
| Status | **EXECUTED** (Phase 0 + 1 + 2 complete) |
| Fan-out SHA | `ba378a44b25d5f6bec13ea74afbd22ffae25c5b2` |
| Total findings (post-dedupe) | 120 |

## 1. Luna-summary (the five fattest fish)

1. **Cross-tenant data-leak risk — 44 queries of tenanted models missing `tenant_id` filter** (pattern_drift). High risk × large blast radius. This is the highest-priority cluster: any cross-tenant leak is an immediate security incident.
2. **`NoneType.__format__` crash in auto-quality scorer telemetry — 55 occurrences in 72h** (errors). Bare exception swallows the failure so the scorer keeps appearing healthy; the RL loop loses ~half its signal.
3. **Migration↔DB drift — 22 mismatches** between the on-disk `apps/api/migrations/*.sql` and the `_migrations` table (dead_code). Some files never applied; some applied rows have no file. Reproducibility is broken.
4. **Monolith files**: `apps/code-worker/workflows.py` at **2255 LOC / 34 functions**, `workflow_templates.py` 2250 LOC with 1 function (data blob), `apps/api/app/services/agent_router.py` 1647 LOC with a `route_and_execute` of **682 LOC** and nesting depth 10+ (hotspots). These are the changes-are-scary files.
5. **WhatsApp outbound silently stuck — 22 `handoff: to_thread` events with no reply send** (errors). The auto-restore handler only triggers on `readonly database`; this silent-send variant escapes detection.

## 2. Top-10 ranked findings

Ranked by `(risk × blast_radius) / effort` per spec §4.

### 1. db.query(ExternalAgent) without tenant filter

- **id:** `F3.drift.13`  · **dimensions:** pattern_drift  · **score:** 81.00
- **where:** `apps/api/app/services/external_agent_reliability.py:262`
- **evidence:** query of tenanted model 'ExternalAgent' has no tenant_id mention in following 5 lines
- **reproducer:** `python3 scripts/smell/tenant_filter_check.py`
- **why it smells:** violates docs/architecture/dashboard.md — multi-tenant isolation violation cross-tenant data leak risk
- **suggested action:** `refactor`  · **effort:** `S`  · **risk:** `high`  · **blast radius:** `large`

### 2. db.query(PolicyCandidate) without tenant filter

- **id:** `F3.drift.14`  · **dimensions:** pattern_drift  · **score:** 81.00
- **where:** `apps/api/app/services/policy_rollout_service.py:46`
- **evidence:** query of tenanted model 'PolicyCandidate' has no tenant_id mention in following 5 lines
- **reproducer:** `python3 scripts/smell/tenant_filter_check.py`
- **why it smells:** violates docs/architecture/dashboard.md — multi-tenant isolation violation cross-tenant data leak risk
- **suggested action:** `refactor`  · **effort:** `S`  · **risk:** `high`  · **blast radius:** `large`

### 3. db.query(DataSource) without tenant filter

- **id:** `F3.drift.15`  · **dimensions:** pattern_drift  · **score:** 81.00
- **where:** `apps/api/app/services/data_source.py:43`
- **evidence:** query of tenanted model 'DataSource' has no tenant_id mention in following 5 lines
- **reproducer:** `python3 scripts/smell/tenant_filter_check.py`
- **why it smells:** violates docs/architecture/dashboard.md — multi-tenant isolation violation cross-tenant data leak risk
- **suggested action:** `refactor`  · **effort:** `S`  · **risk:** `high`  · **blast radius:** `large`

### 4. db.query(Connector) without tenant filter

- **id:** `F3.drift.16`  · **dimensions:** pattern_drift  · **score:** 81.00
- **where:** `apps/api/app/services/connectors.py:38`
- **evidence:** query of tenanted model 'Connector' has no tenant_id mention in following 5 lines
- **reproducer:** `python3 scripts/smell/tenant_filter_check.py`
- **why it smells:** violates docs/architecture/dashboard.md — multi-tenant isolation violation cross-tenant data leak risk
- **suggested action:** `refactor`  · **effort:** `S`  · **risk:** `high`  · **blast radius:** `large`

### 5. db.query(DataPipeline) without tenant filter

- **id:** `F3.drift.17`  · **dimensions:** pattern_drift  · **score:** 81.00
- **where:** `apps/api/app/services/data_pipeline.py:43`
- **evidence:** query of tenanted model 'DataPipeline' has no tenant_id mention in following 5 lines
- **reproducer:** `python3 scripts/smell/tenant_filter_check.py`
- **why it smells:** violates docs/architecture/dashboard.md — multi-tenant isolation violation cross-tenant data leak risk
- **suggested action:** `refactor`  · **effort:** `S`  · **risk:** `high`  · **blast radius:** `large`

### 6. db.query(RLExperience) without tenant filter

- **id:** `F3.drift.18`  · **dimensions:** pattern_drift  · **score:** 81.00
- **where:** `apps/api/app/services/rl_reward_service.py:87`
- **evidence:** query of tenanted model 'RLExperience' has no tenant_id mention in following 5 lines
- **reproducer:** `python3 scripts/smell/tenant_filter_check.py`
- **why it smells:** violates docs/architecture/dashboard.md — multi-tenant isolation violation cross-tenant data leak risk
- **suggested action:** `refactor`  · **effort:** `S`  · **risk:** `high`  · **blast radius:** `large`

### 7. db.query(User) without tenant filter

- **id:** `F3.drift.19`  · **dimensions:** pattern_drift  · **score:** 81.00
- **where:** `apps/api/app/services/users.py:22`
- **evidence:** query of tenanted model 'User' has no tenant_id mention in following 5 lines
- **reproducer:** `python3 scripts/smell/tenant_filter_check.py`
- **why it smells:** violates docs/architecture/dashboard.md — multi-tenant isolation violation cross-tenant data leak risk
- **suggested action:** `refactor`  · **effort:** `S`  · **risk:** `high`  · **blast radius:** `large`

### 8. db.query(KnowledgeEntity) without tenant filter

- **id:** `F3.drift.20`  · **dimensions:** pattern_drift  · **score:** 81.00
- **where:** `apps/api/app/services/users.py:280`
- **evidence:** query of tenanted model 'KnowledgeEntity' has no tenant_id mention in following 5 lines
- **reproducer:** `python3 scripts/smell/tenant_filter_check.py`
- **why it smells:** violates docs/architecture/dashboard.md — multi-tenant isolation violation cross-tenant data leak risk
- **suggested action:** `refactor`  · **effort:** `S`  · **risk:** `high`  · **blast radius:** `large`

### 9. db.query(IntegrationCredential) without tenant filter

- **id:** `F3.drift.21`  · **dimensions:** pattern_drift  · **score:** 81.00
- **where:** `apps/api/app/services/users.py:92`
- **evidence:** query of tenanted model 'IntegrationCredential' has no tenant_id mention in following 5 lines
- **reproducer:** `python3 scripts/smell/tenant_filter_check.py`
- **why it smells:** violates docs/architecture/dashboard.md — multi-tenant isolation violation cross-tenant data leak risk
- **suggested action:** `refactor`  · **effort:** `S`  · **risk:** `high`  · **blast radius:** `large`

### 10. db.query(LearningExperiment) without tenant filter

- **id:** `F3.drift.22`  · **dimensions:** pattern_drift  · **score:** 81.00
- **where:** `apps/api/app/services/learning_dashboard_service.py:36`
- **evidence:** query of tenanted model 'LearningExperiment' has no tenant_id mention in following 5 lines
- **reproducer:** `python3 scripts/smell/tenant_filter_check.py`
- **why it smells:** violates docs/architecture/dashboard.md — multi-tenant isolation violation cross-tenant data leak risk
- **suggested action:** `refactor`  · **effort:** `S`  · **risk:** `high`  · **blast radius:** `large`

## 3. Per-dimension findings

### 3.1. `dead_code` — 30 findings, preflight=`ok`

_method notes:_ Executed 5 dead_code scripts: unmounted_routes (1 finding), unimported_symbols (297 findings), unregistered_workflows (1 finding), unrouted_pages (0 findings), migration_drift (22 findings). Aggregated 321 findings, top 30 by severity score ranked as (risk×blast)/effort.

- **`F1.dead.1`** — unregistered workflow: ProviderCouncilWorkflow
   - where: `apps/code-worker/workflows.py:1949`
   - evidence: class 'ProviderCouncilWorkflow' has @workflow.defn but no worker lists it in workflows=[…]
   - reproducer: `python3 scripts/smell/unregistered_workflows.py`
   - action: `delete` · effort: `S` · risk: `med` · blast: `medium`
- **`F1.dead.2`** — applied migration has no file: 091_blackboard_chat_session_and_source_node
   - where: `_migrations row → no apps/api/migrations/091_blackboard_chat_session_and_source_node`
   - evidence: 091_blackboard_chat_session_and_source_node listed in _migrations but absent from apps/api/migrations
   - reproducer: `docker exec agentprovision-agents-db-1 psql -U postgres agentprovision -c "SELECT filename FROM _migrations WHERE filename='091_blackboard_chat_session_and_source_node';"`
   - action: `document` · effort: `S` · risk: `med` · blast: `medium`
- **`F1.dead.3`** — applied migration has no file: 092_add_password_reset_to_users
   - where: `_migrations row → no apps/api/migrations/092_add_password_reset_to_users`
   - evidence: 092_add_password_reset_to_users listed in _migrations but absent from apps/api/migrations
   - reproducer: `docker exec agentprovision-agents-db-1 psql -U postgres agentprovision -c "SELECT filename FROM _migrations WHERE filename='092_add_password_reset_to_users';"`
   - action: `document` · effort: `S` · risk: `med` · blast: `medium`
- **`F1.dead.4`** — applied migration has no file: 093_agent_integration_configs
   - where: `_migrations row → no apps/api/migrations/093_agent_integration_configs`
   - evidence: 093_agent_integration_configs listed in _migrations but absent from apps/api/migrations
   - reproducer: `docker exec agentprovision-agents-db-1 psql -U postgres agentprovision -c "SELECT filename FROM _migrations WHERE filename='093_agent_integration_configs';"`
   - action: `document` · effort: `S` · risk: `med` · blast: `medium`
- **`F1.dead.5`** — applied migration has no file: 094_external_agents
   - where: `_migrations row → no apps/api/migrations/094_external_agents`
   - evidence: 094_external_agents listed in _migrations but absent from apps/api/migrations
   - reproducer: `docker exec agentprovision-agents-db-1 psql -U postgres agentprovision -c "SELECT filename FROM _migrations WHERE filename='094_external_agents';"`
   - action: `document` · effort: `S` · risk: `med` · blast: `medium`
- **`F1.dead.6`** — applied migration has no file: 095_agent_ownership_and_status
   - where: `_migrations row → no apps/api/migrations/095_agent_ownership_and_status`
   - evidence: 095_agent_ownership_and_status listed in _migrations but absent from apps/api/migrations
   - reproducer: `docker exec agentprovision-agents-db-1 psql -U postgres agentprovision -c "SELECT filename FROM _migrations WHERE filename='095_agent_ownership_and_status';"`
   - action: `document` · effort: `S` · risk: `med` · blast: `medium`
- **`F1.dead.7`** — applied migration has no file: 096_agent_permissions
   - where: `_migrations row → no apps/api/migrations/096_agent_permissions`
   - evidence: 096_agent_permissions listed in _migrations but absent from apps/api/migrations
   - reproducer: `docker exec agentprovision-agents-db-1 psql -U postgres agentprovision -c "SELECT filename FROM _migrations WHERE filename='096_agent_permissions';"`
   - action: `document` · effort: `S` · risk: `med` · blast: `medium`
- **`F1.dead.8`** — applied migration has no file: 097_agent_policies
   - where: `_migrations row → no apps/api/migrations/097_agent_policies`
   - evidence: 097_agent_policies listed in _migrations but absent from apps/api/migrations
   - reproducer: `docker exec agentprovision-agents-db-1 psql -U postgres agentprovision -c "SELECT filename FROM _migrations WHERE filename='097_agent_policies';"`
   - action: `document` · effort: `S` · risk: `med` · blast: `medium`
- **`F1.dead.9`** — applied migration has no file: 098_agent_audit_log
   - where: `_migrations row → no apps/api/migrations/098_agent_audit_log`
   - evidence: 098_agent_audit_log listed in _migrations but absent from apps/api/migrations
   - reproducer: `docker exec agentprovision-agents-db-1 psql -U postgres agentprovision -c "SELECT filename FROM _migrations WHERE filename='098_agent_audit_log';"`
   - action: `document` · effort: `S` · risk: `med` · blast: `medium`
- **`F1.dead.10`** — applied migration has no file: 099_agent_performance_rollup
   - where: `_migrations row → no apps/api/migrations/099_agent_performance_rollup`
   - evidence: 099_agent_performance_rollup listed in _migrations but absent from apps/api/migrations
   - reproducer: `docker exec agentprovision-agents-db-1 psql -U postgres agentprovision -c "SELECT filename FROM _migrations WHERE filename='099_agent_performance_rollup';"`
   - action: `document` · effort: `S` · risk: `med` · blast: `medium`
- **`F1.dead.11`** — applied migration has no file: 100_agent_versions
   - where: `_migrations row → no apps/api/migrations/100_agent_versions`
   - evidence: 100_agent_versions listed in _migrations but absent from apps/api/migrations
   - reproducer: `docker exec agentprovision-agents-db-1 psql -U postgres agentprovision -c "SELECT filename FROM _migrations WHERE filename='100_agent_versions';"`
   - action: `document` · effort: `S` · risk: `med` · blast: `medium`
- **`F1.dead.12`** — applied migration has no file: 101_chat_sessions_agent_id
   - where: `_migrations row → no apps/api/migrations/101_chat_sessions_agent_id`
   - evidence: 101_chat_sessions_agent_id listed in _migrations but absent from apps/api/migrations
   - reproducer: `docker exec agentprovision-agents-db-1 psql -U postgres agentprovision -c "SELECT filename FROM _migrations WHERE filename='101_chat_sessions_agent_id';"`
   - action: `document` · effort: `S` · risk: `med` · blast: `medium`
- **`F1.dead.13`** — applied migration has no file: 102_agent_name_unique_per_tenant
   - where: `_migrations row → no apps/api/migrations/102_agent_name_unique_per_tenant`
   - evidence: 102_agent_name_unique_per_tenant listed in _migrations but absent from apps/api/migrations
   - reproducer: `docker exec agentprovision-agents-db-1 psql -U postgres agentprovision -c "SELECT filename FROM _migrations WHERE filename='102_agent_name_unique_per_tenant';"`
   - action: `document` · effort: `S` · risk: `med` · blast: `medium`
- **`F1.dead.14`** — applied migration has no file: 104_agent_marketplace_listings
   - where: `_migrations row → no apps/api/migrations/104_agent_marketplace_listings`
   - evidence: 104_agent_marketplace_listings listed in _migrations but absent from apps/api/migrations
   - reproducer: `docker exec agentprovision-agents-db-1 psql -U postgres agentprovision -c "SELECT filename FROM _migrations WHERE filename='104_agent_marketplace_listings';"`
   - action: `document` · effort: `S` · risk: `med` · blast: `medium`
- **`F1.dead.15`** — applied migration has no file: 105_agent_test_suites
   - where: `_migrations row → no apps/api/migrations/105_agent_test_suites`
   - evidence: 105_agent_test_suites listed in _migrations but absent from apps/api/migrations
   - reproducer: `docker exec agentprovision-agents-db-1 psql -U postgres agentprovision -c "SELECT filename FROM _migrations WHERE filename='105_agent_test_suites';"`
   - action: `document` · effort: `S` · risk: `med` · blast: `medium`
- **`F1.dead.16`** — applied migration has no file: 118_pulse_revenue_sync_wiring.sql
   - where: `_migrations row → no apps/api/migrations/118_pulse_revenue_sync_wiring.sql`
   - evidence: 118_pulse_revenue_sync_wiring.sql listed in _migrations but absent from apps/api/migrations
   - reproducer: `docker exec agentprovision-agents-db-1 psql -U postgres agentprovision -c "SELECT filename FROM _migrations WHERE filename='118_pulse_revenue_sync_wiring.sql';"`
   - action: `document` · effort: `S` · risk: `med` · blast: `medium`
- **`F1.dead.17`** — applied migration has no file: 157_luna_split_luna_learn_tool_group.sql
   - where: `_migrations row → no apps/api/migrations/157_luna_split_luna_learn_tool_group.sql`
   - evidence: 157_luna_split_luna_learn_tool_group.sql listed in _migrations but absent from apps/api/migrations
   - reproducer: `docker exec agentprovision-agents-db-1 psql -U postgres agentprovision -c "SELECT filename FROM _migrations WHERE filename='157_luna_split_luna_learn_tool_group.sql';"`
   - action: `document` · effort: `S` · risk: `med` · blast: `medium`
- **`F1.dead.18`** — file not applied: 151_fix_code_reviewer_seed.down.sql
   - where: `apps/api/migrations/151_fix_code_reviewer_seed.down.sql`
   - evidence: file exists on disk but no _migrations row recorded
   - reproducer: `ls apps/api/migrations/151_fix_code_reviewer_seed.down.sql && docker exec agentprovision-agents-db-1 psql -U postgres agentprovision -c "SELECT 1 FROM _migrations WHERE filename='151_fix_code_reviewer_seed.down.sql';"`
   - action: `document` · effort: `S` · risk: `med` · blast: `medium`
- **`F1.dead.19`** — file not applied: 152_seed_substrate_sentinel_agent.down.sql
   - where: `apps/api/migrations/152_seed_substrate_sentinel_agent.down.sql`
   - evidence: file exists on disk but no _migrations row recorded
   - reproducer: `ls apps/api/migrations/152_seed_substrate_sentinel_agent.down.sql && docker exec agentprovision-agents-db-1 psql -U postgres agentprovision -c "SELECT 1 FROM _migrations WHERE filename='152_seed_substrate_sentinel_agent.down.sql';"`
   - action: `document` · effort: `S` · risk: `med` · blast: `medium`
- **`F1.dead.20`** — file not applied: 153_review_default_true_and_readonly_split.down.sql
   - where: `apps/api/migrations/153_review_default_true_and_readonly_split.down.sql`
   - evidence: file exists on disk but no _migrations row recorded
   - reproducer: `ls apps/api/migrations/153_review_default_true_and_readonly_split.down.sql && docker exec agentprovision-agents-db-1 psql -U postgres agentprovision -c "SELECT 1 FROM _migrations WHERE filename='153_review_default_true_and_readonly_split.down.sql';"`
   - action: `document` · effort: `S` · risk: `med` · blast: `medium`
- **`F1.dead.21`** — file not applied: 154_expand_luna_supervisor_tool_groups.down.sql
   - where: `apps/api/migrations/154_expand_luna_supervisor_tool_groups.down.sql`
   - evidence: file exists on disk but no _migrations row recorded
   - reproducer: `ls apps/api/migrations/154_expand_luna_supervisor_tool_groups.down.sql && docker exec agentprovision-agents-db-1 psql -U postgres agentprovision -c "SELECT 1 FROM _migrations WHERE filename='154_expand_luna_supervisor_tool_groups.down.sql';"`
   - action: `document` · effort: `S` · risk: `med` · blast: `medium`
- **`F1.dead.22`** — file not applied: 155_seed_simon_work_fleet_agents.down.sql
   - where: `apps/api/migrations/155_seed_simon_work_fleet_agents.down.sql`
   - evidence: file exists on disk but no _migrations row recorded
   - reproducer: `ls apps/api/migrations/155_seed_simon_work_fleet_agents.down.sql && docker exec agentprovision-agents-db-1 psql -U postgres agentprovision -c "SELECT 1 FROM _migrations WHERE filename='155_seed_simon_work_fleet_agents.down.sql';"`
   - action: `document` · effort: `S` · risk: `med` · blast: `medium`
- **`F1.dead.23`** — file not applied: 156_luna_add_learning_tool_group.down.sql
   - where: `apps/api/migrations/156_luna_add_learning_tool_group.down.sql`
   - evidence: file exists on disk but no _migrations row recorded
   - reproducer: `ls apps/api/migrations/156_luna_add_learning_tool_group.down.sql && docker exec agentprovision-agents-db-1 psql -U postgres agentprovision -c "SELECT 1 FROM _migrations WHERE filename='156_luna_add_learning_tool_group.down.sql';"`
   - action: `document` · effort: `S` · risk: `med` · blast: `medium`
- **`F1.dead.24`** — unmounted route module: install_scripts
   - where: `apps/api/app/api/v1/install_scripts.py`
   - evidence: name 'install_scripts' not referenced in routes.py / __init__.py
   - reproducer: `python3 scripts/smell/unmounted_routes.py`
   - action: `delete` · effort: `S` · risk: `low` · blast: `small`
- **`F1.dead.25`** — unused public symbol: agent_groups.create_agent_group
   - where: `apps/api/app/services/agent_groups.py:9`
   - evidence: no references to 'agent_groups.create_agent_group' anywhere under apps/ (AST single-pass index)
   - reproducer: `python3 scripts/smell/unimported_symbols.py  # AST fallback`
   - action: `delete` · effort: `S` · risk: `low` · blast: `small`
- **`F1.dead.26`** — unused public symbol: agent_groups.get_agent_group
   - where: `apps/api/app/services/agent_groups.py:26`
   - evidence: no references to 'agent_groups.get_agent_group' anywhere under apps/ (AST single-pass index)
   - reproducer: `python3 scripts/smell/unimported_symbols.py  # AST fallback`
   - action: `delete` · effort: `S` · risk: `low` · blast: `small`
- **`F1.dead.27`** — unused public symbol: agent_groups.get_agent_groups
   - where: `apps/api/app/services/agent_groups.py:34`
   - evidence: no references to 'agent_groups.get_agent_groups' anywhere under apps/ (AST single-pass index)
   - reproducer: `python3 scripts/smell/unimported_symbols.py  # AST fallback`
   - action: `delete` · effort: `S` · risk: `low` · blast: `small`
- **`F1.dead.28`** — unused public symbol: agent_groups.update_agent_group
   - where: `apps/api/app/services/agent_groups.py:41`
   - evidence: no references to 'agent_groups.update_agent_group' anywhere under apps/ (AST single-pass index)
   - reproducer: `python3 scripts/smell/unimported_symbols.py  # AST fallback`
   - action: `delete` · effort: `S` · risk: `low` · blast: `small`
- **`F1.dead.29`** — unused public symbol: agent_groups.delete_agent_group
   - where: `apps/api/app/services/agent_groups.py:56`
   - evidence: no references to 'agent_groups.delete_agent_group' anywhere under apps/ (AST single-pass index)
   - reproducer: `python3 scripts/smell/unimported_symbols.py  # AST fallback`
   - action: `delete` · effort: `S` · risk: `low` · blast: `small`
- **`F1.dead.30`** — unused public symbol: agent_importer.import_autogen
   - where: `apps/api/app/services/agent_importer.py:88`
   - evidence: no references to 'agent_importer.import_autogen' anywhere under apps/ (AST single-pass index)
   - reproducer: `python3 scripts/smell/unimported_symbols.py  # AST fallback`
   - action: `delete` · effort: `S` · risk: `low` · blast: `small`

### 3.2. `ai_slop` — 5 findings, preflight=`ok`

_method notes:_ 2 scripts found 2 redundant docstrings; 4 greps ran (empty except: 0; low-arity helpers: 0; duplicate scaffolds: 0; hedging-comment clusters: 3 files at >=3 hits)

- **`F2.slop.1`** — redundant docstring: function name restated in prose
   - where: `apps/api/app/services/connector_testing.py:33`
   - evidence: docstring restates symbol name
   - reproducer: `python3 scripts/smell/docstring_redundancy.py apps/api/app/services`
   - action: `refactor` · effort: `S` · risk: `low` · blast: `small`
- **`F2.slop.2`** — redundant docstring: function name restated in prose
   - where: `apps/api/app/services/connector_testing.py:58`
   - evidence: docstring restates symbol name
   - reproducer: `python3 scripts/smell/docstring_redundancy.py apps/api/app/services`
   - action: `refactor` · effort: `S` · risk: `low` · blast: `small`
- **`F2.slop.3`** — hedging language cluster in tasks_fanout.py (3 'just' uses)
   - where: `apps/api/app/services/tasks_fanout.py:641,675,764`
   - evidence: 3 'just' instances in comments — hedging signal cluster
   - reproducer: `grep -nE 'hedging-words' apps/api/app/services/tasks_fanout.py`
   - action: `refactor` · effort: `S` · risk: `low` · blast: `small`
- **`F2.slop.4`** — hedging language in security/auth context (3 sites)
   - where: `apps/api/app/api/v1/auth.py:49,246,650`
   - evidence: 'very' / 'just' inside auth code comments — undermines confidence in security-critical path
   - reproducer: `grep -nE 'hedging-words' apps/api/app/api/v1/auth.py`
   - action: `refactor` · effort: `S` · risk: `med` · blast: `small`
- **`F2.slop.5`** — hedging language in test setup comments
   - where: `apps/api/tests/test_skill_evals_workspace_quota.py:69,70,76`
   - evidence: 3 hedging-word matches in test setup comments
   - reproducer: `grep -nE 'hedging-words' apps/api/tests/test_skill_evals_workspace_quota.py`
   - action: `refactor` · effort: `S` · risk: `low` · blast: `small`

### 3.3. `pattern_drift` — 30 findings, preflight=`ok`

_method notes:_ Ran 3 pattern_drift scripts: missing_session_event (320 findings capped at top 10), missing_rl_log (6 findings), tenant_filter_check (44 findings capped at top 20); Canonical checks: ✓ SSE pattern honored everywhere (0 violations of 'new EventSource()' outside SessionEventsContext); ✓ MCP-leaf-protocol: Bearer tokens found are legitimate external API calls and internal heartbeat auth, not unauthorized direct Bearer in leaf inbound comms; ✓ workspace guards: all WORKSPACES_ROOT usage properly includes tenant_id in path construction; ✓ volume/pvc discipline: all dangerous commands documented as forbidden in comments, never executed

- **`F3.drift.1`** — DB write without publish_session_event: external_agent_call
   - where: `apps/api/app/services/external_agent_reliability.py:156`
   - evidence: function calls a session write without publish_session_event
   - reproducer: `python3 scripts/smell/missing_session_event.py apps/api/app/services`
   - action: `refactor` · effort: `M` · risk: `med` · blast: `medium`
- **`F3.drift.2`** — DB write without publish_session_event: _mark_online
   - where: `apps/api/app/services/external_agent_reliability.py:312`
   - evidence: function calls a session write without publish_session_event
   - reproducer: `python3 scripts/smell/missing_session_event.py apps/api/app/services`
   - action: `refactor` · effort: `M` · risk: `med` · blast: `medium`
- **`F3.drift.3`** — DB write without publish_session_event: _mark_error
   - where: `apps/api/app/services/external_agent_reliability.py:319`
   - evidence: function calls a session write without publish_session_event
   - reproducer: `python3 scripts/smell/missing_session_event.py apps/api/app/services`
   - action: `refactor` · effort: `M` · risk: `med` · blast: `medium`
- **`F3.drift.4`** — DB write without publish_session_event: _build_anticipatory_context
   - where: `apps/api/app/services/memory_recall.py:49`
   - evidence: function calls a session write without publish_session_event
   - reproducer: `python3 scripts/smell/missing_session_event.py apps/api/app/services`
   - action: `refactor` · effort: `M` · risk: `med` · blast: `medium`
- **`F3.drift.5`** — DB write without publish_session_event: _build_memory_context_keyword_fallback
   - where: `apps/api/app/services/memory_recall.py:149`
   - evidence: function calls a session write without publish_session_event
   - reproducer: `python3 scripts/smell/missing_session_event.py apps/api/app/services`
   - action: `refactor` · effort: `M` · risk: `med` · blast: `medium`
- **`F3.drift.6`** — DB write without publish_session_event: _fetch_top_observations_semantic
   - where: `apps/api/app/services/memory_recall.py:257`
   - evidence: function calls a session write without publish_session_event
   - reproducer: `python3 scripts/smell/missing_session_event.py apps/api/app/services`
   - action: `refactor` · effort: `M` · risk: `med` · blast: `medium`
- **`F3.drift.7`** — DB write without publish_session_event: build_memory_context
   - where: `apps/api/app/services/memory_recall.py:351`
   - evidence: function calls a session write without publish_session_event
   - reproducer: `python3 scripts/smell/missing_session_event.py apps/api/app/services`
   - action: `refactor` · effort: `M` · risk: `med` · blast: `medium`
- **`F3.drift.8`** — DB write without publish_session_event: record_rollout_observation
   - where: `apps/api/app/services/policy_rollout_service.py:76`
   - evidence: function calls a session write without publish_session_event
   - reproducer: `python3 scripts/smell/missing_session_event.py apps/api/app/services`
   - action: `refactor` · effort: `M` · risk: `med` · blast: `medium`
- **`F3.drift.9`** — DB write without publish_session_event: create_commitment
   - where: `apps/api/app/services/commitment_service.py:34`
   - evidence: function calls a session write without publish_session_event
   - reproducer: `python3 scripts/smell/missing_session_event.py apps/api/app/services`
   - action: `refactor` · effort: `M` · risk: `med` · blast: `medium`
- **`F3.drift.10`** — Decision function without RL log: select_llm_for_task
   - where: `apps/api/app/services/enhanced_chat.py:118`
   - evidence: decision function name matches decision-verb regex with no rl_experience log call
   - reproducer: `python3 scripts/smell/missing_rl_log.py apps/api/app/services`
   - action: `refactor` · effort: `M` · risk: `med` · blast: `medium`
- **`F3.drift.11`** — Decision function without RL log: select_action
   - where: `apps/api/app/services/rl_policy_engine.py:242`
   - evidence: decision function name matches decision-verb regex with no rl_experience log call
   - reproducer: `python3 scripts/smell/missing_rl_log.py apps/api/app/services`
   - action: `refactor` · effort: `M` · risk: `med` · blast: `medium`
- **`F3.drift.12`** — Decision function without RL log: dispatch_review_workflow
   - where: `apps/api/app/services/review_dispatch.py:30`
   - evidence: decision function name matches decision-verb regex with no rl_experience log call
   - reproducer: `python3 scripts/smell/missing_rl_log.py apps/api/app/services`
   - action: `refactor` · effort: `M` · risk: `med` · blast: `medium`
- **`F3.drift.13`** — db.query(ExternalAgent) without tenant filter
   - where: `apps/api/app/services/external_agent_reliability.py:262`
   - evidence: query of tenanted model 'ExternalAgent' has no tenant_id mention in following 5 lines
   - reproducer: `python3 scripts/smell/tenant_filter_check.py`
   - action: `refactor` · effort: `S` · risk: `high` · blast: `large`
- **`F3.drift.14`** — db.query(PolicyCandidate) without tenant filter
   - where: `apps/api/app/services/policy_rollout_service.py:46`
   - evidence: query of tenanted model 'PolicyCandidate' has no tenant_id mention in following 5 lines
   - reproducer: `python3 scripts/smell/tenant_filter_check.py`
   - action: `refactor` · effort: `S` · risk: `high` · blast: `large`
- **`F3.drift.15`** — db.query(DataSource) without tenant filter
   - where: `apps/api/app/services/data_source.py:43`
   - evidence: query of tenanted model 'DataSource' has no tenant_id mention in following 5 lines
   - reproducer: `python3 scripts/smell/tenant_filter_check.py`
   - action: `refactor` · effort: `S` · risk: `high` · blast: `large`
- **`F3.drift.16`** — db.query(Connector) without tenant filter
   - where: `apps/api/app/services/connectors.py:38`
   - evidence: query of tenanted model 'Connector' has no tenant_id mention in following 5 lines
   - reproducer: `python3 scripts/smell/tenant_filter_check.py`
   - action: `refactor` · effort: `S` · risk: `high` · blast: `large`
- **`F3.drift.17`** — db.query(DataPipeline) without tenant filter
   - where: `apps/api/app/services/data_pipeline.py:43`
   - evidence: query of tenanted model 'DataPipeline' has no tenant_id mention in following 5 lines
   - reproducer: `python3 scripts/smell/tenant_filter_check.py`
   - action: `refactor` · effort: `S` · risk: `high` · blast: `large`
- **`F3.drift.18`** — db.query(RLExperience) without tenant filter
   - where: `apps/api/app/services/rl_reward_service.py:87`
   - evidence: query of tenanted model 'RLExperience' has no tenant_id mention in following 5 lines
   - reproducer: `python3 scripts/smell/tenant_filter_check.py`
   - action: `refactor` · effort: `S` · risk: `high` · blast: `large`
- **`F3.drift.19`** — db.query(User) without tenant filter
   - where: `apps/api/app/services/users.py:22`
   - evidence: query of tenanted model 'User' has no tenant_id mention in following 5 lines
   - reproducer: `python3 scripts/smell/tenant_filter_check.py`
   - action: `refactor` · effort: `S` · risk: `high` · blast: `large`
- **`F3.drift.20`** — db.query(KnowledgeEntity) without tenant filter
   - where: `apps/api/app/services/users.py:280`
   - evidence: query of tenanted model 'KnowledgeEntity' has no tenant_id mention in following 5 lines
   - reproducer: `python3 scripts/smell/tenant_filter_check.py`
   - action: `refactor` · effort: `S` · risk: `high` · blast: `large`
- **`F3.drift.21`** — db.query(IntegrationCredential) without tenant filter
   - where: `apps/api/app/services/users.py:92`
   - evidence: query of tenanted model 'IntegrationCredential' has no tenant_id mention in following 5 lines
   - reproducer: `python3 scripts/smell/tenant_filter_check.py`
   - action: `refactor` · effort: `S` · risk: `high` · blast: `large`
- **`F3.drift.22`** — db.query(LearningExperiment) without tenant filter
   - where: `apps/api/app/services/learning_dashboard_service.py:36`
   - evidence: query of tenanted model 'LearningExperiment' has no tenant_id mention in following 5 lines
   - reproducer: `python3 scripts/smell/tenant_filter_check.py`
   - action: `refactor` · effort: `S` · risk: `high` · blast: `large`
- **`F3.drift.23`** — db.query(DatasetGroup) without tenant filter
   - where: `apps/api/app/services/dataset_groups.py:61`
   - evidence: query of tenanted model 'DatasetGroup' has no tenant_id mention in following 5 lines
   - reproducer: `python3 scripts/smell/tenant_filter_check.py`
   - action: `refactor` · effort: `S` · risk: `high` · blast: `large`
- **`F3.drift.24`** — db.query(Agent) without tenant filter
   - where: `apps/api/app/services/review_circularity.py:188`
   - evidence: query of tenanted model 'Agent' has no tenant_id mention in following 5 lines
   - reproducer: `python3 scripts/smell/tenant_filter_check.py`
   - action: `refactor` · effort: `S` · risk: `high` · blast: `large`
- **`F3.drift.25`** — db.query(VectorStore) without tenant filter
   - where: `apps/api/app/services/vector_stores.py:38`
   - evidence: query of tenanted model 'VectorStore' has no tenant_id mention in following 5 lines
   - reproducer: `python3 scripts/smell/tenant_filter_check.py`
   - action: `refactor` · effort: `S` · risk: `high` · blast: `large`
- **`F3.drift.26`** — db.query(ChatSession) without tenant filter
   - where: `apps/api/app/services/knowledge_extraction.py:106`
   - evidence: query of tenanted model 'ChatSession' has no tenant_id mention in following 5 lines
   - reproducer: `python3 scripts/smell/tenant_filter_check.py`
   - action: `refactor` · effort: `S` · risk: `high` · blast: `large`
- **`F3.drift.27`** — db.query(Tool) without tenant filter
   - where: `apps/api/app/services/tools.py:38`
   - evidence: query of tenanted model 'Tool' has no tenant_id mention in following 5 lines
   - reproducer: `python3 scripts/smell/tenant_filter_check.py`
   - action: `refactor` · effort: `S` · risk: `high` · blast: `large`
- **`F3.drift.28`** — db.query(User) without tenant filter
   - where: `apps/api/app/services/agent_router.py:1085`
   - evidence: query of tenanted model 'User' has no tenant_id mention in following 5 lines
   - reproducer: `python3 scripts/smell/tenant_filter_check.py`
   - action: `refactor` · effort: `S` · risk: `high` · blast: `large`
- **`F3.drift.29`** — db.query(Deployment) without tenant filter
   - where: `apps/api/app/services/deployments.py:38`
   - evidence: query of tenanted model 'Deployment' has no tenant_id mention in following 5 lines
   - reproducer: `python3 scripts/smell/tenant_filter_check.py`
   - action: `refactor` · effort: `S` · risk: `high` · blast: `large`
- **`F3.drift.30`** — db.query(DynamicWorkflow) without tenant filter
   - where: `apps/api/app/services/fleet_snapshot_service.py:263`
   - evidence: query of tenanted model 'DynamicWorkflow' has no tenant_id mention in following 5 lines
   - reproducer: `python3 scripts/smell/tenant_filter_check.py`
   - action: `refactor` · effort: `S` · risk: `high` · blast: `large`

### 3.4. `errors` — 25 findings, preflight=`ok`

_method notes:_ Scanned 5 containers (all reachable); 100,582 total log lines processed. Verified 5 spec-seeded fingerprints: FP#1 (RST_STREAM count=9, firing), FP#2 (NoneType.__format__ count=55, INCREASED from baseline, critical), FP#3 (refresh_token_reused count=2, still firing late 2026-05-27, expected quiet), FP#4 (WhatsApp handoff+no-reply count=22, stale neonize pattern unresolved), FP#5 (cli_quota_fallback_chain not found in codebase—likely removed or renamed). Highest-frequency unknown fingerprints: OpenCode timeout (892x), Gemini 256-color (292x), github-fetch (14x), mcp-tools ASGI (14x). All 25 findings carry their reproduce commands for verification. No containers missing; embedding-service-1 and memory-core-1 returned 8 and 1230 lines respectively (low traffic).

- **`F4.err.1`** — Rust recall RST_STREAM error (reconnect-handling)
   - where: `apps/api/app/memory/recall.py:252`
   - evidence: count=9 in 72h; sample: Rust recall failed (will reconnect next call): ... RST_STREAM with error code 8
   - reproducer: `docker logs --since 72h agentprovision-agents-api-1 2>&1 | grep 'RST_STREAM with error code 8'`
   - action: `monitor` · effort: `L` · risk: `low` · blast: `small`
- **`F4.err.2`** — NoneType.__format__ in auto-quality RL logging (format bug)
   - where: `apps/api/app/services/auto_quality_scorer.py:416`
   - evidence: count=55 in 72h; sample: Failed to log auto-quality RL: unsupported format string passed to NoneType.__format__
   - reproducer: `docker logs --since 72h agentprovision-agents-api-1 2>&1 | grep 'unsupported format string passed to NoneType'`
   - action: `fix` · effort: `M` · risk: `med` · blast: `small`
- **`F4.err.3`** — Token refresh already-used (Codex auth state bug)
   - where: `apps/api/app/services/cli_session_manager.py:1663`
   - evidence: count=2 in 72h; sample: refresh token was already used to generate a new access token, tenant=752626d9 2026-05-27
   - reproducer: `docker logs --since 72h agentprovision-agents-api-1 2>&1 | grep 'refresh token was already used'`
   - action: `investigate` · effort: `M` · risk: `med` · blast: `small`
- **`F4.err.4`** — WhatsApp handoff-to-thread (stale neonize socket)
   - where: `apps/api/app/services/whatsapp_service.py:1336`
   - evidence: count=22 handoff events in 72h; no corresponding reply-send observed; sample: handoff: to_thread session=3e217c77
   - reproducer: `docker logs --since 72h agentprovision-agents-api-1 2>&1 | grep 'handoff.*to_thread'`
   - action: `refactor` · effort: `H` · risk: `high` · blast: `medium`
- **`F4.err.5`** — OpenCode server timeout → CLI fallback (spinner artifact)
   - where: `apps/code-worker/cli_executors/opencode.py:139`
   - evidence: count=892 in 72h; sample: OpenCode server failed (timed out), falling back to CLI
   - reproducer: `docker logs --since 72h agentprovision-agents-code-worker-1 2>&1 | grep -c 'OpenCode server failed'`
   - action: `investigate` · effort: `H` · risk: `high` · blast: `large`
- **`F4.err.6`** — Gemini CLI 256-color warning (terminal env)
   - where: `apps/code-worker/cli_executors/gemini.py (inferred)`
   - evidence: count=292 in 72h; sample: Gemini CLI stderr: Warning: 256-color support not detected
   - reproducer: `docker logs --since 72h agentprovision-agents-code-worker-1 2>&1 | grep 'Gemini CLI stderr: Warning: 256-color'`
   - action: `configure` · effort: `L` · risk: `low` · blast: `small`
- **`F4.err.7`** — GitHub token fetch Connection refused (network/DNS)
   - where: `apps/code-worker/workflows.py:1403`
   - evidence: count=14 in 72h; sample: Failed to fetch github token: [Errno 111] Connection refused
   - reproducer: `docker logs --since 72h agentprovision-agents-code-worker-1 2>&1 | grep 'Failed to fetch github token.*111'`
   - action: `investigate` · effort: `M` · risk: `med` · blast: `small`
- **`F4.err.8`** — Task exception never retrieved (AsyncClient.aclose race)
   - where: `agentprovision-agents-api-1`
   - evidence: count=2 in 72h; sample: Task exception was never retrieved, future<Task-3220> AsyncClient.aclose()
   - reproducer: `docker logs --since 72h agentprovision-agents-api-1 2>&1 | grep 'Task exception was never retrieved'`
   - action: `refactor` · effort: `M` · risk: `low` · blast: `small`
- **`F4.err.9`** — WebSocket frame header EOF (whatsmeow transport)
   - where: `agentprovision-agents-api-1`
   - evidence: count=9 (grouped) in 72h; sample: Error reading from websocket: failed to get reader: failed to read frame header: EOF
   - reproducer: `docker logs --since 72h agentprovision-agents-api-1 2>&1 | grep 'failed to read frame header: EOF'`
   - action: `monitor` · effort: `M` · risk: `low` · blast: `small`
- **`F4.err.10`** — MCP tool discovery failed (server unavailable)
   - where: `apps/api/app/services/mcp_server_connectors.py`
   - evidence: count=1 in 72h; sample: Tool discovery failed for MCP server 2c3424ba-494e-4f4e-b0b6-c8ba5091694d
   - reproducer: `docker logs --since 72h agentprovision-agents-api-1 2>&1 | grep 'Tool discovery failed'`
   - action: `monitor` · effort: `L` · risk: `low` · blast: `small`
- **`F4.err.11`** — Google token refresh 400 Bad Request (OAuth state)
   - where: `agentprovision-agents-api-1`
   - evidence: count=1 in 72h; sample: Failed to refresh token for google: Client error '400 Bad Request'
   - reproducer: `docker logs --since 72h agentprovision-agents-api-1 2>&1 | grep 'Failed to refresh token for google.*400'`
   - action: `investigate` · effort: `M` · risk: `low` · blast: `small`
- **`F4.err.12`** — ASGI application exception (uvicorn.error)
   - where: `agentprovision-agents-api-1, agentprovision-agents-mcp-tools-1`
   - evidence: count=14 (mcp-tools) in 72h; sample: Exception in ASGI application
   - reproducer: `docker logs --since 72h agentprovision-agents-mcp-tools-1 2>&1 | grep 'Exception in ASGI application'`
   - action: `investigate` · effort: `M` · risk: `med` · blast: `small`
- **`F4.err.13`** — OpenCode CLI no text events (spinner/progress parsing failure)
   - where: `apps/code-worker/cli_executors/opencode.py`
   - evidence: count=5 in 72h; sample: OpenCode CLI returned no text events; stdout=250 bytes
   - reproducer: `docker logs --since 72h agentprovision-agents-code-worker-1 2>&1 | grep 'no text events'`
   - action: `investigate` · effort: `M` · risk: `low` · blast: `small`
- **`F4.err.14`** — Memory quota overage after prune (tenant_home_quota)
   - where: `apps/code-worker`
   - evidence: count=4 in 72h; sample: /var/agentprovision/workspaces/<uuid>/home still over budget after prune
   - reproducer: `docker logs --since 72h agentprovision-agents-code-worker-1 2>&1 | grep 'still over budget'`
   - action: `investigate` · effort: `M` · risk: `low` · blast: `small`
- **`F4.err.15`** — Local Whisper transcription failure (audio processing)
   - where: `apps/code-worker`
   - evidence: count=2 in 72h; sample: Local Whisper transcription failed
   - reproducer: `docker logs --since 72h agentprovision-agents-code-worker-1 2>&1 | grep 'Local Whisper transcription failed'`
   - action: `monitor` · effort: `L` · risk: `low` · blast: `small`
- **`F4.err.16`** — Conversational CLI turn failed (workflow error)
   - where: `apps/code-worker/workflows.py`
   - evidence: count=2 in 72h; sample: Conversational CLI turn failed
   - reproducer: `docker logs --since 72h agentprovision-agents-code-worker-1 2>&1 | grep 'Conversational CLI turn failed'`
   - action: `investigate` · effort: `M` · risk: `low` · blast: `small`
- **`F4.err.17`** — Workflow task timeout > 10 seconds (Temporal)
   - where: `apps/code-worker`
   - evidence: count=1 in 72h; sample: Workflow task exceeded 10 seconds
   - reproducer: `docker logs --since 72h agentprovision-agents-code-worker-1 2>&1 | grep 'Workflow task exceeded'`
   - action: `refactor` · effort: `M` · risk: `low` · blast: `small`
- **`F4.err.18`** — SessionEventEmitter API 500 (event drop)
   - where: `apps/code-worker`
   - evidence: count=1 in 72h; sample: SessionEventEmitter API non-2xx 500, dropping 2 chunks
   - reproducer: `docker logs --since 72h agentprovision-agents-code-worker-1 2>&1 | grep 'SessionEventEmitter API non-2xx 500'`
   - action: `investigate` · effort: `M` · risk: `low` · blast: `small`
- **`F4.err.19`** — GitHub token fetch DNS Name not known (network)
   - where: `apps/code-worker/workflows.py:1403`
   - evidence: count=1 in 72h; sample: Failed to fetch github token: [Errno -2] Name or service not known
   - reproducer: `docker logs --since 72h agentprovision-agents-code-worker-1 2>&1 | grep 'Name or service not known'`
   - action: `monitor` · effort: `L` · risk: `low` · blast: `small`
- **`F4.err.20`** — WhatsApp message decryption failure (group key)
   - where: `agentprovision-agents-api-1`
   - evidence: count=1 in 72h; sample: Error decrypting message 3A70... from 186496... failed to decrypt group message
   - reproducer: `docker logs --since 72h agentprovision-agents-api-1 2>&1 | grep 'failed to decrypt group'`
   - action: `monitor` · effort: `L` · risk: `low` · blast: `small`
- **`F4.err.21`** — CLI chain exhausted (all fallback attempts failed)
   - where: `apps/api/app/services/cli_session_manager.py`
   - evidence: count=1 in 72h; sample: CLI chain exhausted — tenant=752626d9 requested=gemini_cli attempted=['gemini_cli'] last_error=CLI exit 1
   - reproducer: `docker logs --since 72h agentprovision-agents-api-1 2>&1 | grep 'CLI chain exhausted'`
   - action: `investigate` · effort: `M` · risk: `med` · blast: `small`
- **`F4.err.22`** — ChatCliWorkflow result: success=False (CLI exit 1)
   - where: `agentprovision-agents-api-1`
   - evidence: count=1 in 72h; sample: ChatCliWorkflow result: success=False error=CLI exit 1: Warning: 256-color support not detected
   - reproducer: `docker logs --since 72h agentprovision-agents-api-1 2>&1 | grep 'ChatCliWorkflow result: success=False.*CLI exit'`
   - action: `configure` · effort: `L` · risk: `low` · blast: `small`
- **`F4.err.23`** — Chat-trace session.memory_context update failed (reconnect)
   - where: `apps/api/app/services`
   - evidence: count=1 in 72h; sample: [chat-trace] session.memory_context update failed — continuing with response: Can't reconnect until i...
   - reproducer: `docker logs --since 72h agentprovision-agents-api-1 2>&1 | grep 'session.memory_context update failed'`
   - action: `monitor` · effort: `M` · risk: `low` · blast: `small`
- **`F4.err.24`** — FanoutChatCliWorkflow agent_id propagation gap
   - where: `apps/code-worker/workflows.py`
   - evidence: count=1 in 72h; sample: FanoutChatCliWorkflow received agent_id=<uuid> but implementation does not propagate to ChatCliInput
   - reproducer: `docker logs --since 72h agentprovision-agents-code-worker-1 2>&1 | grep 'does not propagate'`
   - action: `refactor` · effort: `M` · risk: `low` · blast: `small`
- **`F4.err.25`** — asyncio.wait restricted in workflow sandbox
   - where: `apps/code-worker`
   - evidence: count=1 in 72h; sample: __call__ on asyncio.wait restricted
   - reproducer: `docker logs --since 72h agentprovision-agents-code-worker-1 2>&1 | grep 'asyncio.wait restricted'`
   - action: `refactor` · effort: `M` · risk: `low` · blast: `small`

### 3.5. `hotspots` — 30 findings, preflight=`ok`

_method notes:_ {'directories_scanned': ['apps/api/app/services', 'apps/api/app/workflows', 'apps/api/app/api/v1', 'apps/mcp-server/src', 'apps/code-worker', 'apps/web/src'], 'total_python_files_scanned': 154, 'total_js_files_scanned': 30, 'total_rust_files_scanned': 15, 'python_threshold_loc': 1000, 'js_threshold_loc': 800, 'rust_threshold_loc': 1000, 'nesting_depth_threshold': 6, 'total_findings_before_cap': 45, 'total_findings_after_cap': 30, 'sort_strategy': 'nesting depth (top 20) + file oversizing (top 10)'}

- **`F5.hot.1`** — deeply-nested function: _sse_jsonrpc_call (depth=11, LOC=121)
   - where: `apps/api/app/services/mcp_server_connectors.py:156`
   - evidence: max nesting depth = 11; function LOC = 121
   - reproducer: `python3 scripts/smell/nesting_depth.py apps/api/app/services apps/api/app/workflows apps/api/app/api/v1 apps/mcp-server/src apps/code-worker`
   - action: `refactor` · effort: `M` · risk: `high` · blast: `medium`
- **`F5.hot.2`** — deeply-nested function: _run_sse (depth=11, LOC=39)
   - where: `apps/api/app/services/mcp_server_connectors.py:180`
   - evidence: max nesting depth = 11; function LOC = 39
   - reproducer: `python3 scripts/smell/nesting_depth.py apps/api/app/services apps/api/app/workflows apps/api/app/api/v1 apps/mcp-server/src apps/code-worker`
   - action: `refactor` · effort: `M` · risk: `high` · blast: `small`
- **`F5.hot.3`** — deeply-nested function: deep_scan_emails (depth=10, LOC=227)
   - where: `apps/mcp-server/src/mcp_tools/email.py:881`
   - evidence: max nesting depth = 10; function LOC = 227
   - reproducer: `python3 scripts/smell/nesting_depth.py apps/api/app/services apps/api/app/workflows apps/api/app/api/v1 apps/mcp-server/src apps/code-worker`
   - action: `refactor` · effort: `L` · risk: `high` · blast: `large`
- **`F5.hot.4`** — deeply-nested function: read_email (depth=10, LOC=160)
   - where: `apps/mcp-server/src/mcp_tools/email.py:508`
   - evidence: max nesting depth = 10; function LOC = 160
   - reproducer: `python3 scripts/smell/nesting_depth.py apps/api/app/services apps/api/app/workflows apps/api/app/api/v1 apps/mcp-server/src apps/code-worker`
   - action: `refactor` · effort: `M` · risk: `high` · blast: `medium`
- **`F5.hot.5`** — deeply-nested function: get_workflow_history (depth=10, LOC=139)
   - where: `apps/api/app/api/v1/workflows.py:683`
   - evidence: max nesting depth = 10; function LOC = 139
   - reproducer: `python3 scripts/smell/nesting_depth.py apps/api/app/services apps/api/app/workflows apps/api/app/api/v1 apps/mcp-server/src apps/code-worker`
   - action: `refactor` · effort: `M` · risk: `high` · blast: `medium`
- **`F5.hot.6`** — deeply-nested function: _describe_step (depth=10, LOC=26)
   - where: `apps/api/app/services/dynamic_workflows.py:146`
   - evidence: max nesting depth = 10; function LOC = 26
   - reproducer: `python3 scripts/smell/nesting_depth.py apps/api/app/services apps/api/app/workflows apps/api/app/api/v1 apps/mcp-server/src apps/code-worker`
   - action: `refactor` · effort: `M` · risk: `high` · blast: `small`
- **`F5.hot.7`** — deeply-nested function: run (depth=9, LOC=124)
   - where: `apps/api/app/workflows/dynamic_executor.py:119`
   - evidence: max nesting depth = 9; function LOC = 124
   - reproducer: `python3 scripts/smell/nesting_depth.py apps/api/app/services apps/api/app/workflows apps/api/app/api/v1 apps/mcp-server/src apps/code-worker`
   - action: `refactor` · effort: `M` · risk: `high` · blast: `medium`
- **`F5.hot.8`** — deeply-nested function: apply_feedback_to_cycle (depth=9, LOC=117)
   - where: `apps/api/app/workflows/activities/feedback_activities.py:298`
   - evidence: max nesting depth = 9; function LOC = 117
   - reproducer: `python3 scripts/smell/nesting_depth.py apps/api/app/services apps/api/app/workflows apps/api/app/api/v1 apps/mcp-server/src apps/code-worker`
   - action: `refactor` · effort: `M` · risk: `high` · blast: `medium`
- **`F5.hot.9`** — deeply-nested function: _handle_inbound (depth=8, LOC=377)
   - where: `apps/api/app/services/whatsapp_service.py:816`
   - evidence: max nesting depth = 8; function LOC = 377
   - reproducer: `python3 scripts/smell/nesting_depth.py apps/api/app/services apps/api/app/workflows apps/api/app/api/v1 apps/mcp-server/src apps/code-worker`
   - action: `refactor` · effort: `L` · risk: `high` · blast: `large`
- **`F5.hot.10`** — deeply-nested function: execute_dynamic_step (depth=8, LOC=91)
   - where: `apps/api/app/workflows/activities/dynamic_step.py:52`
   - evidence: max nesting depth = 8; function LOC = 91
   - reproducer: `python3 scripts/smell/nesting_depth.py apps/api/app/services apps/api/app/workflows apps/api/app/api/v1 apps/mcp-server/src apps/code-worker`
   - action: `refactor` · effort: `M` · risk: `high` · blast: `medium`
- **`F5.hot.11`** — deeply-nested function: generate_and_evaluate_candidates (depth=7, LOC=128)
   - where: `apps/api/app/workflows/activities/autonomous_learning.py:166`
   - evidence: max nesting depth = 7; function LOC = 128
   - reproducer: `python3 scripts/smell/nesting_depth.py apps/api/app/services apps/api/app/workflows apps/api/app/api/v1 apps/mcp-server/src apps/code-worker`
   - action: `refactor` · effort: `M` · risk: `high` · blast: `medium`
- **`F5.hot.12`** — deeply-nested function: parse_claude_event (depth=7, LOC=113)
   - where: `apps/code-worker/cli_executors/claude_stream_parser.py:87`
   - evidence: max nesting depth = 7; function LOC = 113
   - reproducer: `python3 scripts/smell/nesting_depth.py apps/api/app/services apps/api/app/workflows apps/api/app/api/v1 apps/mcp-server/src apps/code-worker`
   - action: `refactor` · effort: `M` · risk: `high` · blast: `medium`
- **`F5.hot.13`** — deeply-nested function: download_attachment (depth=7, LOC=110)
   - where: `apps/mcp-server/src/mcp_tools/email.py:768`
   - evidence: max nesting depth = 7; function LOC = 110
   - reproducer: `python3 scripts/smell/nesting_depth.py apps/api/app/services apps/api/app/workflows apps/api/app/api/v1 apps/mcp-server/src apps/code-worker`
   - action: `refactor` · effort: `M` · risk: `high` · blast: `medium`
- **`F5.hot.14`** — deeply-nested function: get_jira_issue (depth=7, LOC=101)
   - where: `apps/mcp-server/src/mcp_tools/jira.py:169`
   - evidence: max nesting depth = 7; function LOC = 101
   - reproducer: `python3 scripts/smell/nesting_depth.py apps/api/app/services apps/api/app/workflows apps/api/app/api/v1 apps/mcp-server/src apps/code-worker`
   - action: `refactor` · effort: `M` · risk: `high` · blast: `medium`
- **`F5.hot.15`** — deeply-nested function: fetch_new_emails (depth=7, LOC=99)
   - where: `apps/api/app/workflows/activities/inbox_monitor.py:96`
   - evidence: max nesting depth = 7; function LOC = 99
   - reproducer: `python3 scripts/smell/nesting_depth.py apps/api/app/services apps/api/app/workflows apps/api/app/api/v1 apps/mcp-server/src apps/code-worker`
   - action: `refactor` · effort: `M` · risk: `high` · blast: `medium`
- **`F5.hot.16`** — deeply-nested function: detect_aremko_changes (depth=7, LOC=83)
   - where: `apps/api/app/workflows/activities/aremko_monitor.py:131`
   - evidence: max nesting depth = 7; function LOC = 83
   - reproducer: `python3 scripts/smell/nesting_depth.py apps/api/app/services apps/api/app/workflows apps/api/app/api/v1 apps/mcp-server/src apps/code-worker`
   - action: `refactor` · effort: `M` · risk: `high` · blast: `medium`
- **`F5.hot.17`** — deeply-nested function: import_from_github (depth=7, LOC=56)
   - where: `apps/api/app/services/skill_manager.py:636`
   - evidence: max nesting depth = 7; function LOC = 56
   - reproducer: `python3 scripts/smell/nesting_depth.py apps/api/app/services apps/api/app/workflows apps/api/app/api/v1 apps/mcp-server/src apps/code-worker`
   - action: `refactor` · effort: `M` · risk: `high` · blast: `small`
- **`F5.hot.18`** — deeply-nested function: _resolve_params (depth=7, LOC=51)
   - where: `apps/api/app/workflows/activities/dynamic_step.py:527`
   - evidence: max nesting depth = 7; function LOC = 51
   - reproducer: `python3 scripts/smell/nesting_depth.py apps/api/app/services apps/api/app/workflows apps/api/app/api/v1 apps/mcp-server/src apps/code-worker`
   - action: `refactor` · effort: `M` · risk: `high` · blast: `small`
- **`F5.hot.19`** — deeply-nested function: _build_outlook_search (depth=7, LOC=48)
   - where: `apps/mcp-server/src/mcp_tools/email.py:190`
   - evidence: max nesting depth = 7; function LOC = 48
   - reproducer: `python3 scripts/smell/nesting_depth.py apps/api/app/services apps/api/app/workflows apps/api/app/api/v1 apps/mcp-server/src apps/code-worker`
   - action: `refactor` · effort: `M` · risk: `high` · blast: `small`
- **`F5.hot.20`** — deeply-nested function: _fetch_account_email (depth=7, LOC=42)
   - where: `apps/api/app/api/v1/oauth.py:156`
   - evidence: max nesting depth = 7; function LOC = 42
   - reproducer: `python3 scripts/smell/nesting_depth.py apps/api/app/services apps/api/app/workflows apps/api/app/api/v1 apps/mcp-server/src apps/code-worker`
   - action: `refactor` · effort: `M` · risk: `high` · blast: `small`
- **`F5.hot.21`** — oversized file: apps/code-worker/workflows.py (2255 LOC)
   - where: `apps/code-worker/workflows.py`
   - evidence: 2255 LOC; 34 functions
   - reproducer: `find apps/api/app apps/mcp-server/src apps/code-worker -name '*.py' | xargs wc -l | sort -n | tail -30`
   - action: `refactor` · effort: `L` · risk: `high` · blast: `large`
- **`F5.hot.22`** — oversized file: apps/api/app/services/workflow_templates.py (2250 LOC)
   - where: `apps/api/app/services/workflow_templates.py`
   - evidence: 2250 LOC; 1 functions
   - reproducer: `find apps/api/app apps/mcp-server/src apps/code-worker -name '*.py' | xargs wc -l | sort -n | tail -30`
   - action: `refactor` · effort: `L` · risk: `high` · blast: `large`
- **`F5.hot.23`** — oversized file: apps/api/app/services/cli_session_manager.py (2109 LOC)
   - where: `apps/api/app/services/cli_session_manager.py`
   - evidence: 2109 LOC; 13 functions
   - reproducer: `find apps/api/app apps/mcp-server/src apps/code-worker -name '*.py' | xargs wc -l | sort -n | tail -30`
   - action: `refactor` · effort: `L` · risk: `high` · blast: `large`
- **`F5.hot.24`** — oversized file: apps/web/src/components/IntegrationsPanel.js (2084 LOC)
   - where: `apps/web/src/components/IntegrationsPanel.js`
   - evidence: 2084 LOC
   - reproducer: `find apps/web/src -type f \( -name '*.js' -o -name '*.jsx' -o -name '*.ts' -o -name '*.tsx' \) -not -path '*/node_modules/*' | xargs wc -l | sort -n | tail -30`
   - action: `refactor` · effort: `L` · risk: `med` · blast: `large`
- **`F5.hot.25`** — oversized file: apps/api/app/services/whatsapp_service.py (1858 LOC)
   - where: `apps/api/app/services/whatsapp_service.py`
   - evidence: 1858 LOC; 6 functions
   - reproducer: `find apps/api/app apps/mcp-server/src apps/code-worker -name '*.py' | xargs wc -l | sort -n | tail -30`
   - action: `refactor` · effort: `M` · risk: `high` · blast: `medium`
- **`F5.hot.26`** — oversized file: apps/api/app/services/agent_router.py (1647 LOC)
   - where: `apps/api/app/services/agent_router.py`
   - evidence: 1647 LOC; 14 functions
   - reproducer: `find apps/api/app apps/mcp-server/src apps/code-worker -name '*.py' | xargs wc -l | sort -n | tail -30`
   - action: `refactor` · effort: `M` · risk: `high` · blast: `medium`
- **`F5.hot.27`** — oversized file: apps/web/src/pages/WorkflowsPage.js (1527 LOC)
   - where: `apps/web/src/pages/WorkflowsPage.js`
   - evidence: 1527 LOC
   - reproducer: `find apps/web/src -type f \( -name '*.js' -o -name '*.jsx' -o -name '*.ts' -o -name '*.tsx' \) -not -path '*/node_modules/*' | xargs wc -l | sort -n | tail -30`
   - action: `refactor` · effort: `M` · risk: `med` · blast: `medium`
- **`F5.hot.28`** — oversized file: apps/web/src/pages/IntegrationsPage.js (1368 LOC)
   - where: `apps/web/src/pages/IntegrationsPage.js`
   - evidence: 1368 LOC
   - reproducer: `find apps/web/src -type f \( -name '*.js' -o -name '*.jsx' -o -name '*.ts' -o -name '*.tsx' \) -not -path '*/node_modules/*' | xargs wc -l | sort -n | tail -30`
   - action: `refactor` · effort: `M` · risk: `med` · blast: `medium`
- **`F5.hot.29`** — oversized file: apps/api/app/api/v1/tasks_fanout.py (1329 LOC)
   - where: `apps/api/app/api/v1/tasks_fanout.py`
   - evidence: 1329 LOC; 16 functions
   - reproducer: `find apps/api/app apps/mcp-server/src apps/code-worker -name '*.py' | xargs wc -l | sort -n | tail -30`
   - action: `refactor` · effort: `M` · risk: `med` · blast: `medium`
- **`F5.hot.30`** — oversized file: apps/api/app/api/v1/skills_new.py (1291 LOC)
   - where: `apps/api/app/api/v1/skills_new.py`
   - evidence: 1291 LOC; 41 functions
   - reproducer: `find apps/api/app apps/mcp-server/src apps/code-worker -name '*.py' | xargs wc -l | sort -n | tail -30`
   - action: `refactor` · effort: `M` · risk: `med` · blast: `medium`

## Appendix A — Methods log

Fan-out commit SHA: `ba378a44b25d5f6bec13ea74afbd22ffae25c5b2`

### dead_code preflight

- input_set: `sha=ba378a44 dead_code`
- exit_summary: `ok`
- containers_seen: ['agentprovision-agents-web-1', 'agentprovision-agents-code-worker-1', 'agentprovision-agents-cloudflared-1', 'agentprovision-agents-orchestration-worker-1', 'agentprovision-agents-mcp-tools-1', 'agentprovision-agents-embedding-service-1', 'agentprovision-agents-memory-core-1', 'agentprovision-agents-temporal-1', 'agentprovision-agents-api-1', 'agentprovision-agents-redis-1', 'agentprovision-agents-luna-client-1', 'agentprovision-agents-db-1']
- commands_attempted:
  - `read apps/api/app/api/v1/routes.py` (exit=0, lines=351)
  - `read apps/api/app/api/v1/__init__.py` (exit=0, lines=354)
  - `vulture apps/api/app/services/` (exit=127, lines=0)
  - `single-pass reference index over apps/**/*.py` (exit=0, lines=0)
  - `indexed-files` (exit=0, lines=7584)
  - `grep workflows=[ in apps/api/app/workers/*.py` (exit=0, lines=1)
  - `grep workflows=[ in apps/code-worker/*.py` (exit=0, lines=1)
  - `walk apps/web/src for <Route>` (exit=0, lines=1)
  - `walk apps/web/src/pages` (exit=0, lines=28)
  - `ls apps/api/migrations/*.sql | xargs -n1 basename | sort` (exit=0, lines=157)
  - `docker ps --format '{{.Names}}'` (exit=0, lines=12)
  - `docker exec agentprovision-agents-db-1 psql -U postgres agentprovision -t -A -c SELECT filename FROM _migrations ORDER BY filename;` (exit=0, lines=167)

### ai_slop preflight

- input_set: `sha=ba378a44 ai_slop`
- exit_summary: `ok`
- containers_seen: []
- commands_attempted:
  - `python3 scripts/smell/reexport_only.py` (exit=0, lines=0)
  - `python3 scripts/smell/docstring_redundancy.py` (exit=0, lines=2)
  - `grep + AST for empty except handlers` (exit=0, lines=0)
  - `low-arity helper count scan` (exit=0, lines=0)
  - `duplicate scaffold shasum dedupe scan` (exit=0, lines=0)
  - `hedging-language cluster grep` (exit=0, lines=3)

### pattern_drift preflight

- input_set: ``
- exit_summary: `ok`
- containers_seen: []
- commands_attempted:
  - `python3 scripts/smell/missing_session_event.py apps/api/app/services` (exit=0, lines=320)
  - `python3 scripts/smell/missing_rl_log.py apps/api/app/services` (exit=0, lines=6)
  - `python3 scripts/smell/tenant_filter_check.py` (exit=0, lines=44)
  - `grep -rnE 'def\s+\w+\([^)]*\bdb\s*:\s*Session' apps/api/app/api/v1/*.py` (exit=0, lines=15)
  - `grep -rnE 'new\s+EventSource\(' apps/web/src` (exit=0, lines=0)
  - `grep -rnE 'Authorization.*Bearer' apps/code-worker/ apps/luna-client/src/` (exit=0, lines=20)
  - `grep -rnE 'WORKSPACES_ROOT|/var/agentprovision/workspaces' apps/api/app/` (exit=0, lines=15)
  - `grep -rnE 'docker\s+volume\s+prune|kubectl\s+delete\s+pvc'` (exit=0, lines=3)

### errors preflight

- input_set: `sha=ba378a44 errors --since 72h --containers [api-1, code-worker-1, mcp-tools-1, embedding-service-1, memory-core-1]`
- exit_summary: `ok`
- containers_seen: ['agentprovision-agents-api-1', 'agentprovision-agents-cloudflared-1', 'agentprovision-agents-code-worker-1', 'agentprovision-agents-db-1', 'agentprovision-agents-embedding-service-1', 'agentprovision-agents-luna-client-1', 'agentprovision-agents-mcp-tools-1', 'agentprovision-agents-memory-core-1', 'agentprovision-agents-orchestration-worker-1', 'agentprovision-agents-redis-1', 'agentprovision-agents-temporal-1', 'agentprovision-agents-web-1']
- commands_attempted:
  - `docker ps --format '{{.Names}}'` (exit=0, lines=12)
  - `docker logs --since 72h agentprovision-agents-api-1` (exit=0, lines=37746)
  - `docker logs --since 72h agentprovision-agents-code-worker-1` (exit=0, lines=15854)
  - `docker logs --since 72h agentprovision-agents-mcp-tools-1` (exit=0, lines=30744)
  - `docker logs --since 72h agentprovision-agents-embedding-service-1` (exit=0, lines=8)
  - `docker logs --since 72h agentprovision-agents-memory-core-1` (exit=0, lines=1230)

### hotspots preflight

- input_set: `apps/api/app/services, apps/api/app/workflows, apps/api/app/api/v1, apps/mcp-server/src, apps/code-worker`
- exit_summary: `ok`
- containers_seen: ['sha=ba378a44 hotspots']
- commands_attempted:
  - `python3 scripts/smell/nesting_depth.py apps/api/app/services apps/api/app/workflows apps/api/app/api/v1 apps/mcp-server/src apps/code-worker` (exit=0, lines=509)
  - `find apps/api/app apps/mcp-server/src apps/code-worker -name '*.py' | xargs wc -l 2>/dev/null | sort -n | tail -30` (exit=0, lines=31)
  - `find apps/web/src -type f \( -name '*.js' -o -name '*.jsx' -o -name '*.ts' -o -name '*.tsx' \) | xargs wc -l 2>/dev/null | sort -n | tail -30` (exit=0, lines=31)
  - `find apps -name '*.rs' -not -path '*/target/*' -not -path '*/.venv/*' | xargs wc -l 2>/dev/null | sort -n | tail -15` (exit=0, lines=16)

**Known limitations of this round:**

- AST scanners may miss dynamic lookups (`getattr`, runtime `importlib`, `from x import *` indirection); a symbol/function flagged as unused may still be reached via these paths. Reviewers should verify before deletion.
- `missing_session_event` heuristic is broad; it flags every DB write without a `publish_session_event` even where the call site is genuinely background / non-watchable. Treat as a list of candidates for the writing-plans cycle, not a definitive list.
- `vulture` was unavailable in the execution environment; `unimported_symbols.py` used its AST + single-pass-index fallback (slower, slightly more false positives).
- `log_errors.py` window covered ~72h of api/code-worker/mcp-tools/embedding-service/memory-core logs. Errors not yet in that window are not in this report.

## Appendix B — Luna consensus snapshot

See spec Appendix B at [`docs/superpowers/specs/2026-05-28-core-primitives-smell-report-design.md`](docs/superpowers/specs/2026-05-28-core-primitives-smell-report-design.md) — the spec went through 2 spec-reviewer iterations + 3 Luna rounds (consensus reached at round 3 with the literal `APPROVED` signal). Luna agent UUID: `cfb6dd14-1889-4751-b645-77bbd53c65c3`. Session id: `d9e5b6ad-1f33-4624-bb71-f65908c2716e`. Platform: Codex CLI on `gpt-5.5` (Pro $200/mo tier).

### Open questions (§9 of the spec) — to be sent to Luna in a separate round after report delivery

1. Is there a sixth dimension worth scanning? (e.g. test-suite smell, observability gaps, secret-hygiene)
2. Are any of the 5 dimensions overlapping enough to merge?
3. Should the report rank by risk or by effort/value?
4. Any canonical pattern in CLAUDE.md or docs/architecture that we forgot to lift into §3.3?
