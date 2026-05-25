-- 155_seed_simon_work_fleet_agents.sql
--
-- Seed Simon's day-to-day work fleet for the 2026-05-26 Innovus pivot
-- plus the standing Integral and Levi's tracks.
--
-- Scope: Simon's tenant only (752626d9-8b2c-4aa2-87ef-c458d48bd38a).
-- The bundled FileSkill slugs live under apps/api/app/agents/_bundled/.
--
-- P0a posture: agents are inserted with explicit tool_groups and
-- tool_groups_review_required=TRUE. They should be cleared only after
-- an operator confirms the groups match the advertised responsibilities.

BEGIN;

INSERT INTO agents (
    id,
    name,
    description,
    tenant_id,
    role,
    capabilities,
    config,
    autonomy_level,
    max_delegation_depth,
    tool_groups,
    default_model_tier,
    memory_domains,
    status,
    version,
    tool_groups_review_required
)
SELECT
    gen_random_uuid(),
    spec.name,
    spec.description,
    '752626d9-8b2c-4aa2-87ef-c458d48bd38a'::uuid,
    spec.role,
    spec.capabilities::json,
    json_build_object('skill_slug', spec.skill_slug),
    'supervised',
    2,
    spec.tool_groups::jsonb,
    'full',
    spec.memory_domains::jsonb,
    'production',
    1,
    TRUE
FROM (
    VALUES
    (
        'Innovus AWS Platform',
        'Lead DevOps platform engineering agent for Innovus Labs AWS operations: landing-zone discovery, EKS/ECS/RDS/IAM/networking analysis, platform runbooks, observability, and day-1 onboarding support.',
        'innovus_aws_platform',
        '["aws", "devops-platform", "platform-engineering", "eks", "ecs", "rds", "iam", "observability", "runbooks"]',
        'innovus-aws-platform',
        '["github", "knowledge_readonly", "drive", "meta"]',
        '["innovus", "aws-platform", "devops-platform", "onboarding", "runbooks"]'
    ),
    (
        'Innovus Terraform Infrastructure',
        'Infrastructure-as-code agent for Innovus Labs: Terraform module review, state/backends, plan safety, drift analysis, CI policy checks, and AWS IaC change planning.',
        'innovus_terraform_infra',
        '["terraform", "iac", "aws", "module-review", "drift", "policy-as-code", "ci-cd"]',
        'innovus-terraform-infrastructure',
        '["github", "knowledge_readonly", "drive", "meta"]',
        '["innovus", "terraform", "iac", "aws-platform", "change-safety"]'
    ),
    (
        'Integral SRE Ops',
        'Standing Integral SRE agent for FXCW, Jenkins, Nexus, Grafana, OpenTSDB, HAProxy, alert triage, RCA drafting, and capacity-management follow-through.',
        'integral_sre_ops',
        '["integral", "sre", "fxcw", "jenkins", "nexus", "grafana", "opentsdb", "haproxy", "alerts", "rca"]',
        'integral-sre-ops',
        '["github", "knowledge_readonly", "drive", "meta"]',
        '["integral", "fxcw", "alerting", "rca", "capacity-management", "runbooks"]'
    ),
    (
        'Levi SRE Platform',
        'Standing Levi''s SRE platform agent for ai-sre-platform work: MDM support, calendar tracker context, incident preparation, repo hygiene, and executive-ready status synthesis.',
        'levi_sre_platform',
        '["levis", "sre", "mdm", "ai-sre-platform", "incident-triage", "status-reporting", "repo-hygiene"]',
        'levi-sre-platform',
        '["github", "knowledge_readonly", "drive", "meta"]',
        '["levis", "ai-sre-platform", "mdm", "service-now", "weekly-trackers"]'
    ),
    (
        'Levi MDM PC9 Triage',
        'Specialist Levi''s MDM/PC9 triage agent for product activation gaps, affiliate/drop indicators, S4 plant assignment follow-up, and ServiceNow-ready evidence packets.',
        'levi_mdm_pc9_triage',
        '["levis", "mdm", "pc9", "s4", "service-now", "affiliate-activation", "incident-triage"]',
        'levi-mdm-pc9-triage',
        '["github", "knowledge_readonly", "drive", "meta"]',
        '["levis", "mdm", "pc9", "s4", "service-now", "plant-2011"]'
    )
) AS spec(name, description, role, capabilities, skill_slug, tool_groups, memory_domains)
WHERE NOT EXISTS (
    SELECT 1
    FROM agents a
    WHERE a.tenant_id = '752626d9-8b2c-4aa2-87ef-c458d48bd38a'
      AND lower(a.name) = lower(spec.name)
);

INSERT INTO _migrations(filename) VALUES ('155_seed_simon_work_fleet_agents.sql')
ON CONFLICT DO NOTHING;

COMMIT;
