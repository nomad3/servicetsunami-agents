# CLI Orchestration Pivot — Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run Luna (WhatsApp + web chat) as a Claude Code CLI session with tools served via MCP, replacing the ADK pipeline for one agent as proof of concept.

**Architecture:** Chat Service → Agent Router (Python) → Claude Code CLI subprocess → Unified MCP Server (FastMCP) → PostgreSQL/Gmail/Calendar/Knowledge Graph. Stateless CLI invocations with context injected per-call. Feature-flagged per tenant.

**Tech Stack:** Claude Code CLI, `mcp` Python SDK (FastMCP), Temporal (async tasks only), sentence-transformers (embeddings), PostgreSQL + pgvector, Neonize (WhatsApp).

**Spec:** `docs/plans/2026-03-15-cli-orchestration-pivot-design.md`

---

## File Structure

### New Files
| File | Responsibility |
|------|---------------|
| `apps/mcp-server/src/mcp_tools/email.py` | MCP tools: search_emails, read_email, send_email, download_attachment, deep_scan_emails |
| `apps/mcp-server/src/mcp_tools/calendar.py` | MCP tools: list_calendar_events, create_calendar_event |
| `apps/mcp-server/src/mcp_tools/knowledge.py` | MCP tools: create/find/update entity, relations, observations, search |
| `apps/mcp-server/src/mcp_tools/__init__.py` | Register all MCP tools on FastMCP instance |
| `apps/mcp-server/src/mcp_app.py` | FastMCP server instance + Streamable HTTP transport |
| `apps/mcp-server/src/mcp_auth.py` | Tenant auth from MCP request headers |
| `apps/api/app/services/cli_session_manager.py` | CLI session lifecycle: create, invoke, rotate, cleanup |
| `apps/api/app/services/agent_router.py` | Route messages to CLI platform (deterministic Phase 1) |
| `apps/api/app/skills/agents/luna/skill.md` | Luna's instructions as marketplace skill |
| `apps/api/migrations/047_add_cli_orchestrator_fields.sql` | DB: default_cli_platform, cli_orchestrator_enabled on tenant_features |

### Modified Files
| File | Change |
|------|--------|
| `apps/api/app/models/tenant_features.py` | Add `default_cli_platform`, `cli_orchestrator_enabled` columns |
| `apps/api/app/schemas/tenant_features.py` | Add fields to Base + Update schemas |
| `apps/api/app/services/chat.py` | Branch: if cli_orchestrator_enabled → agent_router, else → ADK (existing) |
| `apps/api/app/services/whatsapp_service.py` | No changes — already calls `chat_service.post_user_message()` |
| `apps/api/app/api/v1/integration_configs.py` | Add `claude_code_cli` to registry (OAuth) |
| `apps/mcp-server/src/server.py` | Mount FastMCP app alongside existing FastAPI |
| `apps/mcp-server/requirements.txt` | Add `mcp>=1.0.0` |
| `docker-compose.yml` | Expose MCP port, add env vars for CLI |

---

## Chunk 1: MCP Server with Luna's Core Tools

### Task 1: FastMCP Server Setup

**Files:**
- Create: `apps/mcp-server/src/mcp_app.py`
- Create: `apps/mcp-server/src/mcp_auth.py`
- Modify: `apps/mcp-server/src/server.py`
- Modify: `apps/mcp-server/requirements.txt`

- [ ] **Step 1: Add mcp dependency**

In `apps/mcp-server/requirements.txt`, add:
```
mcp>=1.0.0
```

- [ ] **Step 2: Create MCP auth module**

```python
# apps/mcp-server/src/mcp_auth.py
"""Tenant authentication for MCP tool calls."""
import os
import logging
from typing import Optional

logger = logging.getLogger(__name__)

INTERNAL_KEY = os.environ.get("MCP_API_KEY", "dev_mcp_key")


def resolve_tenant_id(ctx) -> Optional[str]:
    """Extract tenant_id from MCP request context headers."""
    # FastMCP passes transport headers via request_context
    if hasattr(ctx, 'request_context') and ctx.request_context:
        tid = ctx.request_context.get("tenant_id")
        if tid:
            return tid
    # Fallback: check if passed as tool parameter
    return None


def verify_internal_key(ctx) -> bool:
    """Verify the X-Internal-Key header."""
    if hasattr(ctx, 'request_context') and ctx.request_context:
        key = ctx.request_context.get("internal_key")
        return key == INTERNAL_KEY
    return False
```

- [ ] **Step 3: Create FastMCP app**

```python
# apps/mcp-server/src/mcp_app.py
"""Unified MCP server for ServiceTsunami tools."""
from mcp.server.fastmcp import FastMCP

mcp = FastMCP(
    "ServiceTsunami",
    stateless_http=True,
    json_response=True,
)

# Tools are registered via imports in __init__.py
```

- [ ] **Step 4: Mount FastMCP alongside existing FastAPI**

In `apps/mcp-server/src/server.py`, add after existing FastAPI app setup:

```python
# Mount MCP server alongside existing REST endpoints
from src.mcp_app import mcp as mcp_app

# Mount at /mcp path
app.mount("/mcp", mcp_app.streamable_http_app())
```

- [ ] **Step 5: Verify MCP server starts**

```bash
cd apps/mcp-server && python -c "from src.mcp_app import mcp; print(f'MCP server: {mcp.name}')"
```

- [ ] **Step 6: Commit**

```bash
git add apps/mcp-server/
git commit -m "feat: add FastMCP server alongside existing MCP REST endpoints"
```

---

### Task 2: MCP Knowledge Tools

**Files:**
- Create: `apps/mcp-server/src/mcp_tools/__init__.py`
- Create: `apps/mcp-server/src/mcp_tools/knowledge.py`

Port the knowledge tools from `apps/adk-server/tools/knowledge_tools.py` to MCP format. These are the most critical — used by every agent.

- [ ] **Step 1: Create MCP tools package**

```python
# apps/mcp-server/src/mcp_tools/__init__.py
"""Register all MCP tools on the FastMCP instance."""
from src.mcp_app import mcp

# Import tool modules to register @mcp.tool() decorators
from src.mcp_tools import knowledge
from src.mcp_tools import email
from src.mcp_tools import calendar
```

- [ ] **Step 2: Port knowledge tools to MCP**

Create `apps/mcp-server/src/mcp_tools/knowledge.py` — port the 13 functions from `apps/adk-server/tools/knowledge_tools.py` using `@mcp.tool()` decorator. Same logic, MCP protocol.

Key functions to port:
- `create_entity`, `find_entities`, `update_entity`, `merge_entities`
- `create_relation`, `find_relations`, `get_neighborhood`
- `record_observation`, `get_entity_timeline`
- `search_knowledge`, `ask_knowledge_graph`

Each function: replace `tenant_id` parameter with `ctx: Context` extraction, keep the DB logic via the existing `knowledge_graph.py` service.

- [ ] **Step 3: Verify knowledge tools register**

```bash
cd apps/mcp-server && python -c "
from src.mcp_tools import knowledge
from src.mcp_app import mcp
print(f'Registered tools: {len(mcp._tools)}')
for name in mcp._tools:
    print(f'  - {name}')
"
```

- [ ] **Step 4: Commit**

```bash
git add apps/mcp-server/src/mcp_tools/
git commit -m "feat: port knowledge graph tools to MCP format"
```

---

### Task 3: MCP Email + Calendar Tools

**Files:**
- Create: `apps/mcp-server/src/mcp_tools/email.py`
- Create: `apps/mcp-server/src/mcp_tools/calendar.py`

Port email tools from `apps/adk-server/tools/google_tools.py`.

- [ ] **Step 1: Port email tools**

Create `apps/mcp-server/src/mcp_tools/email.py` with:
- `search_emails`, `read_email`, `send_email`, `download_attachment`, `deep_scan_emails`
- `list_connected_email_accounts`

Same OAuth token fetching via internal API endpoint. Same auto-entity extraction on read.

- [ ] **Step 2: Port calendar tools**

Create `apps/mcp-server/src/mcp_tools/calendar.py` with:
- `list_calendar_events`, `create_calendar_event`

- [ ] **Step 3: Verify all tools register**

```bash
cd apps/mcp-server && python -c "
from src.mcp_tools import knowledge, email, calendar
from src.mcp_app import mcp
print(f'Total MCP tools: {len(mcp._tools)}')
"
```

Expected: ~20 tools registered.

- [ ] **Step 4: Commit**

```bash
git add apps/mcp-server/src/mcp_tools/
git commit -m "feat: port email and calendar tools to MCP format"
```

---

## Chunk 2: Agent Skill Extraction + CLI Session Manager

### Task 4: Extract Luna as Skill

**Files:**
- Create: `apps/api/app/skills/agents/luna/skill.md`

- [ ] **Step 1: Extract Luna's instructions**

Read `apps/adk-server/servicetsunami_supervisor/personal_assistant.py` lines 74-300 (the instruction string). Convert to skill.md format:

```yaml
---
name: Luna
engine: agent
platform_affinity: claude_code
fallback_platform: gemini_cli
category: personal_assistant
tags: [whatsapp, copilot, business, email, calendar, knowledge]
---

[Full instruction text from personal_assistant.py, verbatim]
[Remove ADK-specific references, keep all behavioral rules]
[Add MCP tool usage instructions instead of ADK tool names]
```

- [ ] **Step 2: Verify skill loads**

```bash
cd apps/api && python -c "
from app.services.skill_manager import _parse_skill_md
from pathlib import Path
skill = _parse_skill_md(Path('app/skills/agents/luna'))
print(f'Skill: {skill.name}, engine: {skill.engine}, category: {skill.category}')
print(f'Description length: {len(skill.description)} chars')
"
```

- [ ] **Step 3: Commit**

```bash
git add apps/api/app/skills/agents/luna/
git commit -m "feat: extract Luna agent as marketplace skill"
```

---

### Task 5: CLI Session Manager

**Files:**
- Create: `apps/api/app/services/cli_session_manager.py`

The session manager handles: generating CLAUDE.md from skill, creating MCP config, invoking CLI subprocess, parsing response.

- [ ] **Step 1: Create session manager**

```python
# apps/api/app/services/cli_session_manager.py
"""CLI session lifecycle manager.

Handles: skill → CLAUDE.md generation, MCP config, CLI subprocess invocation,
response parsing. Stateless — each call is independent.
"""
import json
import logging
import os
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Optional, Dict, Any, Tuple

from sqlalchemy.orm import Session

from app.core.config import settings
from app.services.skill_manager import skill_manager
from app.services.memory_recall import build_memory_context

logger = logging.getLogger(__name__)

MCP_SERVER_URL = os.environ.get("MCP_SERVER_URL", "http://mcp-server:8000")


def generate_claude_md(
    skill_body: str,
    tenant_name: str = "",
    user_name: str = "",
    channel: str = "web",
    conversation_summary: str = "",
    memory_context: str = "",
) -> str:
    """Generate CLAUDE.md from agent skill body + tenant context."""
    sections = [skill_body]

    if conversation_summary:
        sections.append(f"\n## Recent Conversation\n{conversation_summary}")

    if memory_context:
        sections.append(f"\n## Recalled Context\n{memory_context}")

    sections.append(f"""
## Session Context
- Tenant: {tenant_name}
- User: {user_name}
- Channel: {channel}

## MCP Tools
All tools are provided via the ServiceTsunami MCP server.
Use the MCP tools directly — do NOT make HTTP calls.
""")

    return "\n".join(sections)


def generate_mcp_config(tenant_id: str, internal_key: str) -> dict:
    """Generate MCP config JSON for CLI session."""
    return {
        "mcpServers": {
            "servicetsunami": {
                "url": f"{MCP_SERVER_URL}/mcp",
                "headers": {
                    "X-Tenant-Id": tenant_id,
                    "X-Internal-Key": internal_key,
                }
            }
        }
    }


def invoke_claude_cli(
    message: str,
    session_dir: str,
    oauth_token: str,
    timeout: int = 120,
) -> Tuple[Optional[str], Dict[str, Any]]:
    """Invoke Claude Code CLI as a stateless subprocess.

    Returns (response_text, metadata) or (None, error_dict).
    """
    env = os.environ.copy()
    env["CLAUDE_CODE_OAUTH_TOKEN"] = oauth_token

    cmd = [
        "claude", "-p", message,
        "--output-format", "json",
        "--project-dir", session_dir,
    ]

    # Add MCP config if it exists
    mcp_config = os.path.join(session_dir, "mcp.json")
    if os.path.exists(mcp_config):
        cmd.extend(["--mcp-config", mcp_config])

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=timeout, env=env, cwd=session_dir,
        )

        if result.returncode != 0:
            logger.warning("Claude CLI returned %d: %s", result.returncode, result.stderr[:500])
            return None, {"error": result.stderr[:500], "returncode": result.returncode}

        # Parse JSON output
        try:
            output = json.loads(result.stdout)
            response_text = output.get("result", output.get("text", result.stdout))
            metadata = {
                "tokens_used": output.get("usage", {}).get("total_tokens", 0),
                "prompt_tokens": output.get("usage", {}).get("input_tokens", 0),
                "completion_tokens": output.get("usage", {}).get("output_tokens", 0),
                "model": output.get("model", "claude-code"),
                "platform": "claude_code",
            }
            return response_text, metadata
        except json.JSONDecodeError:
            # Raw text output
            return result.stdout.strip(), {"platform": "claude_code"}

    except subprocess.TimeoutExpired:
        return None, {"error": "CLI timeout", "timeout": timeout}
    except FileNotFoundError:
        return None, {"error": "claude CLI not found in PATH"}
    except Exception as e:
        return None, {"error": str(e)}


def run_agent_session(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    agent_slug: str,
    message: str,
    channel: str = "web",
    sender_phone: str = None,
    conversation_summary: str = "",
) -> Tuple[Optional[str], Dict[str, Any]]:
    """Full agent session: load skill → generate files → invoke CLI → return response."""

    # 1. Load agent skill
    skill = skill_manager.get_skill_by_slug(agent_slug)
    if not skill:
        return None, {"error": f"Agent skill '{agent_slug}' not found"}

    # 2. Get tenant's Claude Code OAuth token
    from app.models.integration_config import IntegrationConfig
    from app.services.orchestration.credential_vault import retrieve_credentials_for_skill

    config = db.query(IntegrationConfig).filter(
        IntegrationConfig.tenant_id == tenant_id,
        IntegrationConfig.integration_name == "claude_code",
        IntegrationConfig.enabled == True,
    ).first()

    if not config:
        return None, {"error": "Claude Code not connected. Go to Integrations to connect it."}

    creds = retrieve_credentials_for_skill(db, config.id, tenant_id)
    oauth_token = creds.get("oauth_token") or creds.get("session_token")
    if not oauth_token:
        return None, {"error": "Claude Code token expired. Please reconnect in Integrations."}

    # 3. Build memory context
    memory_text = ""
    try:
        mem = build_memory_context(db, tenant_id, message)
        parts = []
        for entity in mem.get("relevant_entities", [])[:5]:
            parts.append(f"- {entity.get('name', '')}: {entity.get('description', '')[:100]}")
        for memory in mem.get("relevant_memories", [])[:3]:
            parts.append(f"- Memory: {memory.get('description', '')[:100]}")
        memory_text = "\n".join(parts)
    except Exception:
        pass

    # 4. Create session directory + files
    session_dir = tempfile.mkdtemp(prefix="st_cli_")
    try:
        # Generate CLAUDE.md
        claude_md = generate_claude_md(
            skill_body=skill.description or "",
            tenant_name=str(tenant_id)[:8],
            channel=channel,
            conversation_summary=conversation_summary,
            memory_context=memory_text,
        )
        Path(session_dir, "CLAUDE.md").write_text(claude_md)

        # Generate MCP config
        mcp_config = generate_mcp_config(
            tenant_id=str(tenant_id),
            internal_key=settings.MCP_API_KEY or "dev_mcp_key",
        )
        Path(session_dir, "mcp.json").write_text(json.dumps(mcp_config, indent=2))

        # 5. Invoke CLI
        response_text, metadata = invoke_claude_cli(
            message=message,
            session_dir=session_dir,
            oauth_token=oauth_token,
            timeout=120,
        )

        return response_text, metadata

    finally:
        # Cleanup session directory
        import shutil
        shutil.rmtree(session_dir, ignore_errors=True)
```

- [ ] **Step 2: Commit**

```bash
git add apps/api/app/services/cli_session_manager.py
git commit -m "feat: add CLI session manager for stateless agent invocations"
```

---

### Task 6: Agent Router

**Files:**
- Create: `apps/api/app/services/agent_router.py`

- [ ] **Step 1: Create deterministic router**

```python
# apps/api/app/services/agent_router.py
"""Agent Router — routes messages to CLI platforms.

Phase 1: Deterministic routing (tenant default + agent affinity).
Phase 3: RL-driven routing added on top.
"""
import logging
import uuid
from typing import Optional, Tuple, Dict, Any

from sqlalchemy.orm import Session

from app.models.tenant_features import TenantFeatures
from app.services.cli_session_manager import run_agent_session

logger = logging.getLogger(__name__)

# Default agent for each channel
CHANNEL_AGENT_MAP = {
    "whatsapp": "luna",
    "web": "luna",  # Default, overridable by agent kit
}


def route_and_execute(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    message: str,
    channel: str = "web",
    sender_phone: str = None,
    agent_slug: str = None,
    conversation_summary: str = "",
) -> Tuple[Optional[str], Dict[str, Any]]:
    """Route message to the appropriate CLI platform and execute.

    Returns (response_text, context_metadata).
    """
    # Resolve agent
    if not agent_slug:
        agent_slug = CHANNEL_AGENT_MAP.get(channel, "luna")

    # Check tenant features for CLI platform preference
    features = db.query(TenantFeatures).filter(
        TenantFeatures.tenant_id == tenant_id
    ).first()

    platform = "claude_code"  # Phase 1 default
    if features:
        platform = getattr(features, 'default_cli_platform', None) or "claude_code"

    logger.info(
        "Routing: tenant=%s agent=%s platform=%s channel=%s",
        str(tenant_id)[:8], agent_slug, platform, channel,
    )

    # Phase 1: Only Claude Code CLI supported
    if platform == "claude_code":
        return run_agent_session(
            db,
            tenant_id=tenant_id,
            user_id=user_id,
            agent_slug=agent_slug,
            message=message,
            channel=channel,
            sender_phone=sender_phone,
            conversation_summary=conversation_summary,
        )

    # Future: gemini_cli, codex_cli
    return None, {"error": f"Platform '{platform}' not yet supported"}
```

- [ ] **Step 2: Commit**

```bash
git add apps/api/app/services/agent_router.py
git commit -m "feat: add deterministic agent router for CLI platforms"
```

---

## Chunk 3: Chat Service Wiring + Feature Flag

### Task 7: DB Migration — CLI Feature Flags

**Files:**
- Create: `apps/api/migrations/047_add_cli_orchestrator_fields.sql`
- Modify: `apps/api/app/models/tenant_features.py`
- Modify: `apps/api/app/schemas/tenant_features.py`

- [ ] **Step 1: Create migration**

```sql
-- Migration 047: Add CLI orchestrator feature flags
ALTER TABLE tenant_features
    ADD COLUMN IF NOT EXISTS cli_orchestrator_enabled BOOLEAN DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS default_cli_platform VARCHAR(50) DEFAULT 'claude_code';
```

- [ ] **Step 2: Add columns to model**

In `apps/api/app/models/tenant_features.py`, after `active_llm_provider`:
```python
    # CLI Orchestrator
    cli_orchestrator_enabled = Column(Boolean, default=False)
    default_cli_platform = Column(String(50), default="claude_code")
```

- [ ] **Step 3: Add fields to schemas**

In `apps/api/app/schemas/tenant_features.py`:
- Base: `cli_orchestrator_enabled: Optional[bool] = False` and `default_cli_platform: Optional[str] = "claude_code"`
- Update: same fields with `= None`

- [ ] **Step 4: Commit**

```bash
git add apps/api/migrations/ apps/api/app/models/ apps/api/app/schemas/
git commit -m "feat: add CLI orchestrator feature flags to tenant_features"
```

---

### Task 8: Wire Chat Service with Feature Flag

**Files:**
- Modify: `apps/api/app/services/chat.py`

This is the critical integration point. When `cli_orchestrator_enabled` is true, the chat service routes through the agent router instead of ADK.

- [ ] **Step 1: Add CLI path to `_generate_agentic_response`**

At the top of `_generate_agentic_response()` (after the agent_kit check at ~line 300), add:

```python
    # --- CLI Orchestrator path (feature flag) ---
    features = db.query(TenantFeatures).filter(
        TenantFeatures.tenant_id == session.tenant_id
    ).first()

    if features and features.cli_orchestrator_enabled:
        from app.services.agent_router import route_and_execute

        # Build conversation summary from recent messages
        recent_msgs = (
            db.query(ChatMessage)
            .filter(ChatMessage.session_id == session.id)
            .order_by(ChatMessage.created_at.desc())
            .limit(6)
            .all()
        )
        summary = "\n".join(
            f"{'User' if m.role == 'user' else 'Assistant'}: {m.content[:200]}"
            for m in reversed(recent_msgs)
        )

        response_text, context = route_and_execute(
            db,
            tenant_id=session.tenant_id,
            user_id=user_id,
            message=user_message,
            channel="whatsapp" if sender_phone else "web",
            sender_phone=sender_phone,
            conversation_summary=summary,
        )

        if response_text is None:
            error_msg = (context or {}).get("error", "CLI agent failed")
            return _append_message(
                db, session=session, role="assistant",
                content=error_msg, context=context,
            )

        # Save assistant message with CLI metadata
        return _append_message(
            db, session=session, role="assistant",
            content=response_text, context=context,
        )

    # --- Existing ADK path (unchanged) ---
```

- [ ] **Step 2: Verify ADK path still works when flag is off**

Send a chat message with `cli_orchestrator_enabled = false` (default). Should route through ADK as before.

- [ ] **Step 3: Commit**

```bash
git add apps/api/app/services/chat.py
git commit -m "feat: wire CLI orchestrator into chat service with feature flag"
```

---

### Task 9: Docker Compose + Local Testing

**Files:**
- Modify: `docker-compose.yml`

- [ ] **Step 1: Ensure MCP server exposes FastMCP endpoint**

The MCP server already runs on port 8085. FastMCP is mounted at `/mcp` on the same server. No port changes needed.

- [ ] **Step 2: Enable CLI orchestrator for test tenant**

```bash
TOKEN=$(curl -s http://localhost:8001/api/v1/auth/login -X POST \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d 'username=test@example.com&password=password' | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

curl -s http://localhost:8001/api/v1/features -X PUT \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"cli_orchestrator_enabled": true}'
```

- [ ] **Step 3: Test end-to-end**

Send a WhatsApp message to Luna. Verify:
1. Chat service detects `cli_orchestrator_enabled = true`
2. Agent router selects `claude_code` platform + `luna` agent
3. CLI session manager generates CLAUDE.md + mcp.json
4. Claude Code CLI runs with tenant's OAuth token
5. CLI connects to MCP server for tools
6. Response flows back through chat service to WhatsApp

- [ ] **Step 4: Test fallback to ADK**

Disable CLI orchestrator:
```bash
curl -s http://localhost:8001/api/v1/features -X PUT \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"cli_orchestrator_enabled": false}'
```

Send another message — should route through ADK as before.

- [ ] **Step 5: Commit**

```bash
git add docker-compose.yml
git commit -m "feat: Phase 1 CLI orchestrator end-to-end wiring"
```

---

## Chunk 4: Integration UI + Observability

### Task 10: Claude Code CLI Integration Card

**Files:**
- Modify: `apps/api/app/api/v1/integration_configs.py`

- [ ] **Step 1: Add `claude_code_cli` to integration registry**

In `INTEGRATION_CREDENTIAL_SCHEMAS`, add:
```python
    "claude_code_cli": {
        "display_name": "Claude Code (Subscription)",
        "description": "Connect your Claude Code Pro/Max subscription for AI agent chat",
        "icon": "FaRobot",
        "credentials": [],
        "auth_type": "oauth",
        "oauth_provider": "claude_code",
    },
```

- [ ] **Step 2: Wire OAuth flow for Claude Code**

The OAuth flow for Claude Code subscription needs a device-auth or redirect pattern. For Phase 1, use the existing manual token approach:
- User runs `claude auth status` locally
- Copies their session token
- Pastes it in the integration card

Update credentials to accept manual token:
```python
    "claude_code_cli": {
        "display_name": "Claude Code (Subscription)",
        "description": "Use your Claude Code Pro/Max subscription for AI agents",
        "icon": "FaRobot",
        "credentials": [
            {"key": "oauth_token", "label": "Session Token", "type": "password", "required": True,
             "help": "Run 'claude auth status' and paste your session token"},
        ],
        "auth_type": "manual",
    },
```

- [ ] **Step 3: Commit**

```bash
git add apps/api/app/api/v1/integration_configs.py
git commit -m "feat: add Claude Code CLI integration card to registry"
```

---

## Summary

| Chunk | Tasks | What it delivers |
|-------|-------|-----------------|
| **1: MCP Server** | Tasks 1-3 | FastMCP with 20+ tools (knowledge, email, calendar) |
| **2: Agent Skill + Session Manager** | Tasks 4-6 | Luna as skill.md, CLI subprocess invocation, routing |
| **3: Chat Wiring** | Tasks 7-9 | Feature flag, chat service branching, end-to-end test |
| **4: Integration UI** | Task 10 | Claude Code integration card for credential storage |

**Total: 10 tasks across 4 chunks.**

After Phase 1 ships and is validated with Luna on WhatsApp, Phase 2 plan covers: all 25 agents as skills, Gemini CLI activity, platform file generation, and web chat migration.
