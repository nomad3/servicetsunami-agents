"""
One-time seed script to create the Integral tenant on AgentProvision.
Run once: python -m apps.api.scripts.seed_integral_tenant

Creates:
- Tenant: Integral
- Admin user
- TenantFeatures
- Agent with Luna supervisor
- 3 Agent records (SRE, DevOps, Business Support)
- MCPServerConnector pointing to Integral's SRE MCP server
"""
import sys
import os
import uuid

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.db.session import SessionLocal
from app.models.tenant import Tenant
from app.models.user import User
from app.models.tenant_features import TenantFeatures
from app.models.agent import Agent
from app.models.mcp_server_connector import MCPServerConnector
from app.core.security import get_password_hash
from app.models.chat import ChatSession
from app.services.users import seed_shared_cli_credentials_for_tenant


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
            cli_orchestrator_enabled=True,
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
            config={"skill_slug": "sre"},
        )
        devops_agent = Agent(
            id=uuid.uuid4(),
            name="Integral DevOps",
            description="Release operations — Jenkins CI/CD, Nexus artifacts, deployment orchestration",
            tenant_id=tenant.id,
            role="specialist",
            capabilities=["cicd", "jenkins", "nexus", "deployment", "releases"],
            config={"skill_slug": "devops"},
        )
        biz_agent = Agent(
            id=uuid.uuid4(),
            name="Integral Business Support",
            description="Operations intelligence — transaction tracing, alert translation, system health for non-technical users",
            tenant_id=tenant.id,
            role="specialist",
            capabilities=["transactions", "business_support", "forex", "troubleshooting"],
            config={"skill_slug": "business-support"},
        )
        db.add_all([sre_agent, devops_agent, biz_agent])
        db.flush()
        print("Created 3 agents: SRE, DevOps, Business Support")

        # --- Luna supervisor Agent ---
        luna_agent = Agent(
            id=uuid.uuid4(),
            name="Luna",
            description="Luna supervises SRE, DevOps, and Business Support agents for Integral's FX infrastructure",
            tenant_id=tenant.id,
            role="supervisor",
            status="production",
            autonomy_level="supervised",
            capabilities=["routing", "coordination", "memory"],
            config={
                "skill_slug": "luna",
                "workers": [
                    {"slug": "sre", "agent_id": str(sre_agent.id)},
                    {"slug": "devops", "agent_id": str(devops_agent.id)},
                    {"slug": "business-support", "agent_id": str(biz_agent.id)},
                ],
            },
        )
        db.add(luna_agent)
        db.flush()
        print(f"Created supervisor agent: {luna_agent.name}")

        # --- MCP Server Connector ---
        sre_mcp_url = os.getenv("INTEGRAL_SRE_MCP_URL", "http://control-plane-api:8080")
        connector = MCPServerConnector(
            id=uuid.uuid4(),
            tenant_id=tenant.id,
            name="sre",
            description="Integral SRE Control Plane — 65 MCP tools for infrastructure, Jenkins, Nexus",
            server_url=sre_mcp_url,
            transport="streamable-http",
            auth_type="none",
            status="connected",
        )
        db.add(connector)
        db.flush()
        print(f"Created MCP connector: {connector.name} → {connector.server_url}")

        # --- Welcome Chat Session ---
        chat = ChatSession(
            id=uuid.uuid4(),
            tenant_id=tenant.id,
            title="Chat with Luna",
            agent_id=luna_agent.id,
        )
        db.add(chat)
        db.flush()
        print(f"Created welcome chat session")

        # --- Shared CLI Credentials ---
        seed_shared_cli_credentials_for_tenant(db, tenant.id)
        print("Seeded shared CLI credentials")

        db.commit()
        if admin_password == "changeme":
            print("\n⚠ WARNING: Using default password 'changeme' — change it before production use!")
        print("\nSeed complete. Integral tenant is ready.")
        print(f"  Tenant ID: {tenant.id}")
        print(f"  Admin: {admin_email}")
        print(f"  Supervisor: {luna_agent.name}")
        print(f"  MCP: {connector.server_url}")

    except Exception as e:
        db.rollback()
        print(f"Error: {e}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    seed()
