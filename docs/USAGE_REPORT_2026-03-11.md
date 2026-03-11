# ServiceTsunami Platform Usage Report

**Generated**: 2026-03-11
**Source**: Production database (all tenants)

---

## Tenants & Users

| Tenant | Users | ID |
|--------|-------|----|
| servicetsunami | 1 | `a009ccf9-8bfb-41cb-bc66-41353701a896` |
| Demo Enterprise | 1 | `c024fddd-cfc6-401a-a6cf-e738d001484e` |
| Web | 1 | `e3abd6a9-bda9-4f87-94ca-b0eaaf1ab492` |
| Aremko (x2) | 2 | `80b0fbbc...`, `27a2d0db...` |
| Carol Cubillos | 1 | `4569534c-d888-4c0f-a00f-a0928380d446` |
| AgentProvision (x2) | 2 | `284782e7...`, `4e4c0301...` |
| Brett The Cardio Vet | 1 | `c8a2aff8-67f8-4a90-bd95-b97a31510fae` |
| Ecolonco | 1 | `a57d1844-316b-4782-a819-d898892084b6` |
| AJCC | 1 | `acab69ce-9f7a-4296-ad38-a7a3ed3723f1` |
| Jack Tanners | 1 | `78636d60-54f1-416c-a4f0-3b5ad67edb19` |
| Acme Lead Generation Corp | 1 | `6b3c3119-1c07-40c1-9bd6-4bd222c42c81` |
| E2E Test Tenants (x3) | 3 | test artifacts |

**Total: 17 tenants, 17 users**

---

## Chat Activity by Tenant

| Tenant | Sessions | Messages | Tokens Used |
|--------|----------|----------|-------------|
| servicetsunami | 27 | 607 | 47,091,010 |
| Demo Enterprise | 40 | 291 | 3,551,909 |
| Carol Cubillos | 3 | 56 | 1,181,000 |
| Web | 3 | 92 | 1,146,810 |
| Aremko | 4 | 60 | 954,123 |
| AgentProvision | 1 | 2 | 0 |
| Others | 1 | 0 | 0 |

---

## Global Totals

| Metric | Value |
|--------|-------|
| Chat sessions | 79 |
| Chat messages | 1,108 |
| Agent tasks | 481 |
| Execution traces | 1,409 |
| Total tokens (tasks) | 50,938,007 |
| Total cost (tasks) | $7.77 |
| Knowledge entities | 379 |
| Knowledge relations | 69 |
| Agents | 23 |
| Agent kits | 35 |
| Datasets | 11 |
| Data sources | 3 |
| Integrations enabled | 20 |

---

## Workflow Breakdown (199 Temporal Workflows)

| Type | Count | Completed | Failed | Running |
|------|-------|-----------|--------|---------|
| Chat (agent tasks) | 96 | 76 (79%) | 20 (21%) | — |
| InboxMonitorWorkflow | 68 | 64 | 0 | 22 |
| FollowUpWorkflow | 18 | — | — | 18 |
| CodeTaskWorkflow | 14 | 14 (100%) | 0 | — |
| Research | 1 | 1 | 0 | — |
| Analyze | 1 | 0 | 1 | — |
| Generate | 1 | 0 | 0 | — |

---

## Token Usage by Workflow Type

| Source | Tokens | Cost |
|--------|--------|------|
| Chat tasks | 3,551,909 | $0.54 |
| Research task | 12,450 | $0.06 |
| Analyze task | 3,200 | $0.02 |
| Code Agent (14 runs) | — (Claude Max subscription) | $0.00 |
| **Total (API-billed)** | **3,567,559** | **$0.62** |

---

## Token Usage Concentration

- **servicetsunami** tenant: 87% of all chat tokens (47M of 54M)
- Top 3 tenants: 96% of all tokens
- Average tokens per message: ~48,000 (includes ADK multi-agent traces)
- Total platform cost across all tenants: **$7.77**

---

## Key Observations

1. **Code Agent** has 100% success rate (14/14 workflows completed, all PRs created)
2. **Chat failure rate** is 21% (20/96) — primarily auth/timeout issues during development
3. **InboxMonitorWorkflow** has 22 long-running instances (continue_as_new pattern, expected)
4. **Token usage is heavily concentrated** — one tenant (servicetsunami) drives 87% of consumption
5. **Knowledge graph** has 379 entities and 69 relations across all tenants
6. **20 integrations** are enabled across the platform (Gmail, GitHub, Jira, etc.)
