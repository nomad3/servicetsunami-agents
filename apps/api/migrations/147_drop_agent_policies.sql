-- P0b (2026-05-23): drop the agent_policies table.
--
-- The AgentPolicy model + API endpoint + alpha policy CLI subcommand
-- had ZERO enforcement call sites and ZERO rows across all 42 tenants
-- after roughly a year in production. False-comfort surface: operators
-- saw a `policy` CLI return empty arrays and assumed enforcement was
-- happening. It was not.
--
-- All four original policy_type concerns now route to better-fit
-- substrates:
--   input_filter  -> Platform Safety Floor (platform_safety_io)
--   output_filter -> Value Arbitration (standing=tenant_norm, direction=avoid)
--   data_access   -> Value Arbitration (standing=tenant_norm)
--   rate_limit    -> core.rate_limit.limiter (already operational)
--
-- See docs/plans/2026-05-23-p0b-agent-policy-decision.md.
-- See docs/plans/2026-05-23-value-arbitration-design.md.
--
-- Migration 097 stays in history (append-only). Foreign keys point only
-- at the parent tenants/agents tables; no child rows are lost.

DROP TABLE IF EXISTS agent_policies CASCADE;

-- Self-record per the migrations README belt-and-suspenders convention
-- (P0b review B1). Safe if the runner already inserted: ON CONFLICT
-- DO NOTHING. Safe to re-run the DROP either way: IF EXISTS.
INSERT INTO _migrations(filename) VALUES ('147_drop_agent_policies.sql')
ON CONFLICT DO NOTHING;
