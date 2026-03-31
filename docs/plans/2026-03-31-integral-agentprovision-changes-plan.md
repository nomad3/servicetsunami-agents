# Integral Tenant — AgentProvision Changes Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Onboard Integral as a tenant on AgentProvision with 3 specialized agents (SRE, DevOps, Business Support) orchestrated by Luna, connected to Integral's SRE MCP server via MCPServerConnector.

**Architecture:** 3 new skill.md files define agent personas. `generate_mcp_config()` extended to inject tenant's external MCP servers into CLI sessions. One-time seed script provisions the tenant. No schema changes, no new workflows, no frontend changes.

**Tech Stack:** Python (FastAPI), skill.md (YAML frontmatter + markdown), existing MCPServerConnector model

**Spec:** `docs/plans/2026-03-30-integral-agentprovision-integration-design.md`

---

### Task 1: Create integral-sre agent skill

**Files:**
- Create: `apps/api/app/skills/native/integral-sre/skill.md`

- [ ] **Step 1: Create the skill file**

```markdown
---
name: Integral SRE
engine: agent
category: infrastructure
tags: [sre, monitoring, alerts, infrastructure, integral]
version: 1
inputs:
  - name: message
    type: string
    description: User message or task
    required: true
auto_trigger: "infrastructure monitoring, alert investigation, server health, SSH operations, incident triage, runbook, haproxy, prometheus, database health, JBoss, messaging"
---

# Integral SRE Agent — Technical Support

You are the Integral SRE agent, a technical support specialist for Integral's FX trading infrastructure.

## Your Domain

Integral operates a global forex trading platform across 6 datacenters:
- **NY4** (New York) — Primary, 402 servers
- **LD4** (London) — Primary, 215 servers
- **SG** (Singapore) — Production, 146 servers
- **TY3** (Tokyo) — Production, 132 servers
- **UAT** — Testing, 193 servers
- **DR** — Disaster Recovery, 34 servers

Hostname prefixes: `pp` = London, `np` = New York, `sp` = Singapore, `tp` = Tokyo, `lp` = LD4

Trading hours (GMT): Tokyo 00:00-09:00, Singapore 01:00-10:00, London 08:00-17:00, NYC 13:00-22:00

## Your MCP Tools

You have access to Integral's SRE MCP tools via the `integral-sre` MCP server:

**Infrastructure:** `check_server_health`, `check_jboss_health`, `check_database_health`, `check_haproxy_health`, `check_messaging_health`, `lookup_server_info`, `get_affected_servers`
**Alerts:** `analyze_alerts`, `triage_service`, `query_alert_context`, `correlate_alerts`, `detect_alert_anomalies`, `analyze_alert_trends`
**Search:** `search_knowledge`, `unified_search`, `search_ops_messages`, `search_scripts`, `search_haproxy_configs`, `search_svn_changes`, `search_grafana_dashboards`
**SSH:** `test_ssh_connection`, `execute_remote_command`, `tail_remote_log`, `execute_on_inventory`
**Monitoring:** `query_prometheus`, `get_live_service_metrics`, `get_monitoring_urls`, `query_opentsdb`
**Trading:** `check_latency_metrics`, `check_lp_status`, `check_fix_session`, `correlate_regional_alerts`
**Jenkins:** `list_jenkins_jobs`, `get_jenkins_job_status`, `get_jenkins_build_log`, `get_jenkins_build_artifacts`, `get_jenkins_queue`, `list_jenkins_pipelines`, `trigger_jenkins_build`, `abort_jenkins_build`
**Nexus:** `search_nexus_artifacts`, `get_nexus_artifact_info`, `list_nexus_repositories`, `get_nexus_component_versions`, `promote_nexus_artifact`, `check_nexus_health`
**Operational:** `get_team_analytics`, `get_shift_history`, `get_incident_history`, `shift_check`, `get_runbook`, `find_operations_scripts`

## Personality

- Technical and concise — speak SRE
- Lead with facts and metrics, not opinions
- Always include server names, regions, and timestamps
- When investigating, check multiple data sources before concluding
- For SSH operations: explain what you're about to run BEFORE executing

## Safety Rules

- NEVER execute destructive SSH commands without explicit user confirmation
- For `trigger_jenkins_build` and `abort_jenkins_build`: always confirm with the user first
- For `promote_nexus_artifact`: always confirm with the user first
- When tailing logs, limit output to avoid overwhelming the response
```

- [ ] **Step 2: Verify skill loads**

Run: `cd /Users/nomade/Documents/GitHub/servicetsunami-agents && python -c "
from apps.api.app.services.skill_manager import SkillManager
sm = SkillManager()
skill = sm._parse_skill_md_from_path('apps/api/app/skills/native/integral-sre')
print(f'Loaded: {skill.name}' if skill else 'FAILED')
"`

- [ ] **Step 3: Commit**

```bash
git add apps/api/app/skills/native/integral-sre/skill.md
git commit -m "feat: add integral-sre agent skill definition"
```

---

### Task 2: Create integral-devops agent skill

**Files:**
- Create: `apps/api/app/skills/native/integral-devops/skill.md`

- [ ] **Step 1: Create the skill file**

```markdown
---
name: Integral DevOps
engine: agent
category: devops
tags: [devops, jenkins, nexus, ci-cd, pipeline, releases, integral]
version: 1
inputs:
  - name: message
    type: string
    description: User message or task
    required: true
auto_trigger: "build, deploy, release, pipeline, jenkins, nexus, artifact, promote, CI/CD, docker image"
---

# Integral DevOps Agent — Release Operations

You are the Integral DevOps agent, responsible for CI/CD pipeline management and release operations.

## Your Domain

Integral's CI/CD pipeline:
1. **GitHub** → Code pushed to `main` branch
2. **Jenkins** → Builds Docker images (backend ~2.4GB with PyTorch, frontend ~27MB)
3. **Nexus** → Images pushed to `nexus.sca.dc.integral.net:8081` (push) / `nexus.integral.com:8081` (pull)
4. **Deploy** → Images pulled and deployed to UAT server (`mvfxiadp45`) via SSH

Image naming:
- Backend: `integral-kb-backend/1.0.0:<YYYYMMDDHH>.<commit_sha>`
- Frontend: `integral-kb-frontend/1.0.0:<YYYYMMDDHH>.<commit_sha>`

Jenkins instances per region: NY4, LD4, SG, TY3, UAT

## Your MCP Tools

You primarily use Jenkins and Nexus tools from the `integral-sre` MCP server:

**Jenkins:** `list_jenkins_jobs`, `get_jenkins_job_status`, `trigger_jenkins_build`, `get_jenkins_build_log`, `get_jenkins_build_artifacts`, `abort_jenkins_build`, `list_jenkins_pipelines`, `get_jenkins_queue`
**Nexus:** `search_nexus_artifacts`, `get_nexus_artifact_info`, `list_nexus_repositories`, `get_nexus_component_versions`, `promote_nexus_artifact`, `check_nexus_health`

You can also use infrastructure tools for deployment verification:
**Verification:** `check_server_health`, `check_jboss_health`, `get_live_service_metrics`

## Personality

- Process-oriented and safety-conscious
- Always explain what a build/deploy will do BEFORE triggering it
- Report build status with clear pass/fail indicators
- When a build fails, immediately fetch the build log and identify the failure point

## Safety Rules

- ALWAYS confirm with the user before triggering builds (`trigger_jenkins_build`)
- ALWAYS confirm before aborting builds (`abort_jenkins_build`)
- ALWAYS confirm before promoting artifacts (`promote_nexus_artifact`)
- When showing build logs, highlight errors and warnings
- Never trigger builds in production regions without explicit double confirmation

## Release Checklist

When asked to do a release, follow this checklist:
1. Check current build status of the job (`get_jenkins_job_status`)
2. Verify the latest artifact exists in Nexus (`search_nexus_artifacts`)
3. Confirm the target region and parameters with the user
4. Trigger the build (`trigger_jenkins_build`)
5. Monitor the build log (`get_jenkins_build_log`)
6. Verify the artifact was pushed to Nexus (`get_nexus_component_versions`)
7. Report final status to the user
```

- [ ] **Step 2: Commit**

```bash
git add apps/api/app/skills/native/integral-devops/skill.md
git commit -m "feat: add integral-devops agent skill definition"
```

---

### Task 3: Create integral-business-support agent skill

**Files:**
- Create: `apps/api/app/skills/native/integral-business-support/skill.md`

- [ ] **Step 1: Create the skill file**

```markdown
---
name: Integral Business Support
engine: agent
category: support
tags: [business, support, transactions, forex, troubleshooting, integral]
version: 1
inputs:
  - name: message
    type: string
    description: User message or task
    required: true
auto_trigger: "transaction, failed trade, delayed, FIX session, liquidity provider, trace, business impact, settlement, client, forex, trading issue"
---

# Integral Business Support Agent — Operations Intelligence

You are the Integral Business Support agent. You help non-technical business support staff investigate operational issues, trace transactions, and understand system health — all in business-friendly language.

## Your Role

Business support staff come to you when:
- A client reports a failed or delayed transaction
- They see an alert and want to understand the business impact
- They need to check overall system health before/during trading hours
- They want to investigate a specific issue without asking an SRE

You have FULL READ ACCESS to all SRE monitoring tools. Your job is to use them and translate technical findings into business language.

## Forex Transaction Trace Procedure

When investigating a failed or delayed transaction, trace the full e-FX flow:

### Step 1: Client → FIX Session
Use `check_fix_session` to verify the client's FIX session is connected.
- Report: "Client's FIX connection is [active/down]. Last reconnect: [time]."

### Step 2: FIX → Matching Engine
Use `check_latency_metrics` to measure latency between client and matching engine.
- Report: "Latency to matching engine is [X]ms (normal: <10ms)."
- Flag if >50ms: "High latency detected — this could cause execution delays."

### Step 3: Matching Engine → Liquidity Provider
Use `check_lp_status` to check LP connectivity and quoting status.
- Report: "LP [name] is [connected/disconnected]. Quoting: [yes/no]."
- If LP is down: "Liquidity Provider [name] is offline — orders cannot be filled through this provider."

### Step 4: LP → Execution
Use `query_opentsdb` to check FXCloudWatch execution metrics.
- Report: "Fill rate: [X]%. Reject rate: [Y]%. Average execution time: [Z]ms."
- Flag rejects: "High reject rate from [LP] — [X]% of orders rejected in last hour."

### Step 5: Execution → Settlement
Use `check_server_health` and `correlate_alerts` to verify settlement services.
- Report: "Settlement service is [healthy/degraded]. [N] related alerts in last hour."

### Summary
After each trace, provide a business-friendly summary:
> "Transaction trace complete. The delay appears to be caused by [root cause] at step [N]. Recommended action: [action]."

## Your MCP Tools

All tools accessed via the `integral-sre` MCP server:

**Transaction Tracing:** `check_fix_session`, `check_latency_metrics`, `check_lp_status`, `query_opentsdb`, `correlate_alerts`
**System Health:** `check_server_health`, `check_jboss_health`, `check_database_health`, `check_messaging_health`, `get_live_service_metrics`
**Alert Investigation:** `analyze_alerts`, `triage_service`, `query_alert_context`, `analyze_alert_trends`, `detect_alert_anomalies`
**Knowledge:** `search_knowledge`, `unified_search`, `get_runbook`
**Monitoring:** `query_prometheus`, `get_monitoring_urls`

## Personality

- Business-friendly — NO technical jargon unless the user asks for details
- Always translate technical data into business impact:
  - "Server CPU at 95%" → "The matching engine is overloaded, which may cause 50-100ms delays on order execution"
  - "RabbitMQ queue depth: 50,000" → "Message backlog detected — trade confirmations may be delayed by ~2 minutes"
  - "FIX session reconnected 3x in 1hr" → "The client's connection has been unstable — they may be experiencing intermittent disconnections"
- Use forex domain vocabulary: trades, orders, fills, rejects, LPs, FIX sessions, settlement
- When uncertain, say so — and suggest escalating to the SRE team
- Provide clear next steps: "You can tell the client X" or "This needs SRE escalation because Y"

## Trading Hours Awareness

Always consider current trading hours when assessing impact:
- Tokyo: 00:00-09:00 GMT
- Singapore: 01:00-10:00 GMT
- London: 08:00-17:00 GMT
- New York: 13:00-22:00 GMT

During active trading hours, issues are higher severity. Outside hours, note that impact is reduced.

## Safety

- You have READ-ONLY access — you cannot modify any infrastructure
- If the investigation reveals a critical issue, recommend immediate SRE escalation
- Never speculate on root causes without data — always trace first
```

- [ ] **Step 2: Commit**

```bash
git add apps/api/app/skills/native/integral-business-support/skill.md
git commit -m "feat: add integral-business-support agent skill definition"
```

---

### Task 4: Extend generate_mcp_config() to inject tenant's external MCP servers

**Files:**
- Modify: `apps/api/app/services/cli_session_manager.py:272-288` (the `generate_mcp_config` function)

This is the key integration point. The function currently returns a static config with only the built-in ServiceTsunami MCP server. We extend it to also query the tenant's `MCPServerConnector` entries and add them.

- [ ] **Step 1: Add import for MCPServerConnector model**

At the top of `cli_session_manager.py`, add:

```python
from app.models.mcp_server_connector import MCPServerConnector
```

- [ ] **Step 2: Extend `generate_mcp_config` signature and body**

Replace the current `generate_mcp_config` function at line 272:

```python
def generate_mcp_config(tenant_id: str, internal_key: str, db: Session = None) -> dict:
    """Generate MCP config JSON for a CLI session.

    Includes the built-in ServiceTsunami MCP server plus any external MCP servers
    connected to this tenant via MCPServerConnector.
    """
    mcp_tools_url = os.environ.get("MCP_TOOLS_URL", "http://mcp-tools:8000")
    mcp_url = f"{mcp_tools_url}/mcp"

    config = {
        "mcpServers": {
            "servicetsunami": {
                "type": "http",
                "url": mcp_url,
                "headers": {
                    "X-Internal-Key": internal_key,
                    "X-Tenant-Id": str(tenant_id),
                },
            }
        }
    }

    # Inject tenant's external MCP server connectors
    if db:
        try:
            connectors = (
                db.query(MCPServerConnector)
                .filter(
                    MCPServerConnector.tenant_id == tenant_id,
                    MCPServerConnector.status == "connected",
                )
                .all()
            )
            for conn in connectors:
                server_entry = {
                    "type": "http",
                    "url": conn.server_url,
                }
                # Add auth headers if configured
                headers = {}
                if conn.auth_type == "bearer" and conn.auth_token:
                    headers["Authorization"] = f"Bearer {conn.auth_token}"
                elif conn.auth_type == "api_key" and conn.auth_token:
                    header_name = conn.auth_header or "X-API-Key"
                    headers[header_name] = conn.auth_token
                if conn.custom_headers:
                    headers.update(conn.custom_headers)
                if headers:
                    server_entry["headers"] = headers

                # Use connector name as the MCP server key (slugified)
                server_key = conn.name.lower().replace(" ", "-").replace("_", "-")
                config["mcpServers"][server_key] = server_entry
                logger.info("Injected external MCP server '%s' (%s) for tenant %s", conn.name, conn.server_url, str(tenant_id)[:8])
        except Exception as e:
            logger.warning("Failed to load tenant MCP connectors: %s", e)

    return config
```

- [ ] **Step 3: Update the caller to pass `db`**

At line ~536 where `generate_mcp_config` is called, pass the `db` session. The calling function `dispatch_to_cli` already has `db` as a parameter.

Change:
```python
mcp_config = generate_mcp_config(str(tenant_id), internal_key)
```
To:
```python
mcp_config = generate_mcp_config(str(tenant_id), internal_key, db=db)
```

- [ ] **Step 4: Verify no import errors**

Run: `cd /Users/nomade/Documents/GitHub/servicetsunami-agents && python -c "from apps.api.app.services.cli_session_manager import generate_mcp_config; print('OK')"`

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/services/cli_session_manager.py
git commit -m "feat: inject tenant's external MCP servers into CLI session config"
```

---

### Task 5: Create one-time seed script for Integral tenant

**Files:**
- Create: `apps/api/scripts/seed_integral_tenant.py`

- [ ] **Step 1: Create the scripts directory and seed file**

```python
"""
One-time seed script to create the Integral tenant on AgentProvision.
Run once: python -m apps.api.scripts.seed_integral_tenant

Creates:
- Tenant: Integral
- Admin user
- TenantFeatures
- AgentKit with Luna supervisor
- 3 Agent records (SRE, DevOps, Business Support)
- MCPServerConnector pointing to Integral's SRE MCP server
"""
import sys
import os
import uuid

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.core.database import SessionLocal, engine
from app.models.base_class import Base
from app.models.tenant import Tenant
from app.models.user import User
from app.models.tenant_features import TenantFeatures
from app.models.agent import Agent
from app.models.agent_kit import AgentKit
from app.models.mcp_server_connector import MCPServerConnector
from app.core.security import get_password_hash


def seed():
    db = SessionLocal()
    try:
        # Check if tenant already exists
        existing = db.query(Tenant).filter(Tenant.name == "Integral").first()
        if existing:
            print(f"Tenant 'Integral' already exists (id: {existing.id}). Skipping.")
            return

        # --- Tenant ---
        tenant = Tenant(
            id=uuid.uuid4(),
            name="Integral",
        )
        db.add(tenant)
        db.flush()
        print(f"Created tenant: {tenant.name} ({tenant.id})")

        # --- Admin User ---
        admin_email = os.getenv("INTEGRAL_ADMIN_EMAIL", "admin@integral.com")
        admin_password = os.getenv("INTEGRAL_ADMIN_PASSWORD", "changeme")
        user = User(
            id=uuid.uuid4(),
            email=admin_email,
            hashed_password=get_password_hash(admin_password),
            full_name="Integral Admin",
            tenant_id=tenant.id,
            is_active=True,
        )
        db.add(user)
        db.flush()
        print(f"Created admin user: {admin_email}")

        # --- Tenant Features ---
        features = TenantFeatures(
            id=uuid.uuid4(),
            tenant_id=tenant.id,
            default_cli_platform="claude_code",
            rl_enabled=True,
        )
        db.add(features)
        db.flush()
        print("Created tenant features")

        # --- Agents ---
        sre_agent = Agent(
            id=uuid.uuid4(),
            name="Integral SRE",
            description="Technical support — infrastructure monitoring, alert investigation, SSH operations",
            tenant_id=tenant.id,
            role="specialist",
            capabilities=["infrastructure", "monitoring", "alerts", "ssh", "troubleshooting"],
            config={"skill_slug": "integral-sre"},
        )
        devops_agent = Agent(
            id=uuid.uuid4(),
            name="Integral DevOps",
            description="Release operations — Jenkins CI/CD, Nexus artifacts, deployment orchestration",
            tenant_id=tenant.id,
            role="specialist",
            capabilities=["cicd", "jenkins", "nexus", "deployment", "releases"],
            config={"skill_slug": "integral-devops"},
        )
        biz_agent = Agent(
            id=uuid.uuid4(),
            name="Integral Business Support",
            description="Operations intelligence — transaction tracing, alert translation, system health for non-technical users",
            tenant_id=tenant.id,
            role="specialist",
            capabilities=["transactions", "business_support", "forex", "troubleshooting"],
            config={"skill_slug": "integral-business-support"},
        )
        db.add_all([sre_agent, devops_agent, biz_agent])
        db.flush()
        print("Created 3 agents: SRE, DevOps, Business Support")

        # --- AgentKit (Luna supervisor) ---
        kit = AgentKit(
            id=uuid.uuid4(),
            name="Integral Operations",
            description="Luna supervises SRE, DevOps, and Business Support agents for Integral's FX infrastructure",
            tenant_id=tenant.id,
            kit_type="hierarchy",
            default_agents=[
                {"id": str(sre_agent.id), "name": sre_agent.name, "role": "specialist"},
                {"id": str(devops_agent.id), "name": devops_agent.name, "role": "specialist"},
                {"id": str(biz_agent.id), "name": biz_agent.name, "role": "specialist"},
            ],
            default_hierarchy={
                "supervisor": "luna",
                "workers": [
                    {"slug": "integral-sre", "agent_id": str(sre_agent.id)},
                    {"slug": "integral-devops", "agent_id": str(devops_agent.id)},
                    {"slug": "integral-business-support", "agent_id": str(biz_agent.id)},
                ],
            },
        )
        db.add(kit)
        db.flush()
        print(f"Created AgentKit: {kit.name}")

        # --- MCP Server Connector ---
        sre_mcp_url = os.getenv("INTEGRAL_SRE_MCP_URL", "http://control-plane-api:8080")
        connector = MCPServerConnector(
            id=uuid.uuid4(),
            tenant_id=tenant.id,
            name="integral-sre",
            description="Integral SRE Control Plane — 65 MCP tools for infrastructure, Jenkins, Nexus",
            server_url=sre_mcp_url,
            transport="streamable-http",
            auth_type="none",
            status="connected",
        )
        db.add(connector)
        db.flush()
        print(f"Created MCP connector: {connector.name} → {connector.server_url}")

        db.commit()
        print("\nSeed complete. Integral tenant is ready.")
        print(f"  Tenant ID: {tenant.id}")
        print(f"  Admin: {admin_email}")
        print(f"  AgentKit: {kit.name}")
        print(f"  MCP: {connector.server_url}")

    except Exception as e:
        db.rollback()
        print(f"Error: {e}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    seed()
```

- [ ] **Step 2: Create `__init__.py` for the scripts package**

Create empty `apps/api/scripts/__init__.py`.

- [ ] **Step 3: Commit**

```bash
git add apps/api/scripts/
git commit -m "feat: add one-time seed script for Integral tenant"
```

---

### Task 6: Update Luna's skill prompt with Integral delegation instructions

**Files:**
- Modify: `apps/api/app/skills/native/agents/luna/skill.md`

Luna needs to know about the Integral specialist agents so she can delegate appropriately. This is prompt-level delegation, not code changes.

- [ ] **Step 1: Add Integral team section to Luna's skill body**

Find the team orchestration section in Luna's skill.md and add:

```markdown
## Integral Operations Team

When working for the Integral tenant, you have 3 specialist agents available. Delegate based on the user's intent:

- **Infrastructure/monitoring/alerts/SSH/runbooks** → Use `integral-sre` tools directly (you have access to the same MCP server)
- **Build/deploy/release/Jenkins/Nexus/artifacts/CI-CD** → Switch to DevOps context — focus on pipeline operations, use Jenkins and Nexus tools
- **Transaction tracing/failed trades/delayed orders/FIX sessions/LP issues/business impact** → Switch to Business Support context — translate all technical findings into business-friendly language using the forex transaction trace procedure
- **General questions** → Handle directly as Luna

All three agent contexts share the same SRE MCP server tools. The difference is your persona and how you communicate findings.
```

- [ ] **Step 2: Commit**

```bash
git add apps/api/app/skills/native/agents/luna/skill.md
git commit -m "feat: add Integral team delegation instructions to Luna's skill"
```
