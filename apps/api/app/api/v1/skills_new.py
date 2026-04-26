"""API routes for skills management."""
import ast
import json
import re
from pathlib import Path

import yaml
from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query, Request, Response
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import Dict, List, Optional
import uuid

from app.api.deps import get_db, get_current_user, require_superuser
from app.core.config import settings
from app.models.user import User
from app.schemas.skill import SkillInDB, SkillCreate, SkillUpdate
from app.schemas.skill_execution import SkillExecutionInDB, SkillExecuteRequest
from app.schemas.file_skill import FileSkill
from app.services import skills as service
from app.services.skill_manager import skill_manager
from app.services.skill_registry_service import sync_skills_to_db, match_skills
from app.services.memory_activity import log_activity

router = APIRouter()


def _verify_internal_key(
    x_internal_key: Optional[str] = Header(None, alias="X-Internal-Key"),
):
    if x_internal_key not in (getattr(settings, 'API_INTERNAL_KEY', ''), getattr(settings, 'MCP_API_KEY', '')):
        raise HTTPException(status_code=401, detail="Invalid internal key")


# ---------------------------------------------------------------------------
# Pydantic models for library endpoints
# ---------------------------------------------------------------------------

class FileSkillCreateInput(BaseModel):
    name: str
    type: str = "string"
    description: str = ""
    required: bool = False


class FileSkillCreateRequest(BaseModel):
    name: str
    description: str = ""
    engine: str = "python"
    script: str = 'def execute(inputs):\n    return {"result": "done"}'
    inputs: List[FileSkillCreateInput] = []
    category: str = "general"
    auto_trigger: Optional[str] = None
    chain_to: List[str] = []
    tags: List[str] = []


class FileSkillUpdateRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    engine: Optional[str] = None
    script: Optional[str] = None
    category: Optional[str] = None
    auto_trigger: Optional[str] = None
    chain_to: Optional[List[str]] = None
    tags: Optional[List[str]] = None
    # Allow callers to re-declare inputs in the same PUT. Without this the
    # markdown-validator would always read stale inputs and reject legitimate
    # edits that add a new {{variable}} reference.
    inputs: Optional[List[FileSkillCreateInput]] = None


class GitHubImportRequest(BaseModel):
    repo_url: str


class ClaudeCodeImportRequest(BaseModel):
    """Single Claude Code SKILL.md text bundle.

    Claude Code's SKILL.md restricts frontmatter to ``name``, ``description``,
    and optional ``allowed-tools``. Anything else is rejected so a malicious
    or malformed import can't smuggle ``script_path`` / ``engine`` overrides
    into the tenant's library.
    """

    content: str
    overwrite: bool = False


# Frontmatter keys we accept on import. Anything outside this set is rejected
# so import payloads can't override engine / script_path / chain_to and slip
# the skill into a different execution path than the user intended.
_CLAUDE_CODE_ALLOWED_KEYS = {"name", "description", "allowed-tools", "allowed_tools"}


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

_MD_TEMPLATE_VAR_RE = re.compile(r"\{\{\s*([a-zA-Z_][\w]*)\s*\}\}")

# MCP and OpenAI both require tool names match ^[a-zA-Z0-9_-]{1,64}$
_TOOL_NAME_SANITIZE_RE = re.compile(r"[^a-zA-Z0-9_-]+")


def _is_auto_generated_skill(s: FileSkill) -> bool:
    """Single source of truth for 'this skill is import-noise, hide it'.

    Used by the MCP manifest (so external agents don't discover garbage) and
    could be called from other endpoints in the future. The frontend mirrors
    this for fast filtering but the server remains authoritative.
    """
    if s.category == "auto-generated":
        return True
    desc = (s.description or "").lower()
    return "response timeout pattern" in desc


def _sanitize_tool_name(raw: str) -> str:
    """Produce a tool-name-safe slug. MCP/OpenAI reject spaces and punctuation."""
    slug = _TOOL_NAME_SANITIZE_RE.sub("_", raw.lower()).strip("_-")
    return slug or "skill"


def _validate_python_script(script: str) -> Optional[str]:
    """Return an error string if the Python script can't serve as a skill.

    A valid skill exposes ``def execute(inputs): ...``. We parse to AST first so
    a syntactic error surfaces with a clear message; a pure regex would accept
    broken code.
    """
    try:
        tree = ast.parse(script)
    except SyntaxError as e:
        return f"Python syntax error on line {e.lineno}: {e.msg}"

    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "execute":
            args = node.args.args
            # Allow exactly one positional arg. `*args`/`**kwargs`/keyword-only are
            # tolerated because runtime only passes `inputs` positionally, but
            # rejecting multi-positional catches the common "def execute(inputs, ctx)"
            # mistake loudly.
            if len(args) != 1:
                return "`execute` must take exactly one positional argument (inputs)."
            return None

    return "Python skill must define a top-level `def execute(inputs):` function."


def _validate_markdown_script(script: str, input_names: List[str]) -> Optional[str]:
    refs = _MD_TEMPLATE_VAR_RE.findall(script)
    known = set(input_names)
    missing = [r for r in refs if r not in known]
    if missing:
        unique = sorted(set(missing))
        return f"Template references undeclared inputs: {', '.join(unique)}"
    return None


def _validate_skill_payload(engine: str, script: str, inputs: List[dict]) -> None:
    """Raise HTTPException(400) if the skill script is invalid for its engine."""
    input_names = [i.get("name", "") for i in inputs if i.get("name")]
    if engine == "python":
        err = _validate_python_script(script)
    elif engine == "markdown":
        err = _validate_markdown_script(script, input_names)
    else:
        err = None  # shell is free-form — no structural contract to enforce
    if err:
        raise HTTPException(status_code=400, detail=err)


def _skill_to_mcp_tool(skill: FileSkill) -> dict:
    """Convert a FileSkill into an MCP/OpenAI-compatible tool definition."""
    properties: Dict[str, dict] = {}
    required: List[str] = []
    type_map = {"string": "string", "number": "number", "boolean": "boolean"}
    for inp in skill.inputs or []:
        properties[inp.name] = {
            "type": type_map.get(getattr(inp, "type", "string"), "string"),
            "description": getattr(inp, "description", "") or "",
        }
        if getattr(inp, "required", False):
            required.append(inp.name)
    tool_name = "skill_" + _sanitize_tool_name(skill.slug or skill.name)
    return {
        "name": tool_name[:64],  # hard cap per OpenAI spec
        "description": (skill.description or skill.name)[:1024],
        "inputSchema": {
            "type": "object",
            "properties": properties,
            "required": required,
        },
    }


def _skill_to_superpowers_md(skill: FileSkill) -> str:
    """Claude Code superpowers SKILL.md — YAML frontmatter + body + optional source fence.

    Frontmatter is serialized via yaml.safe_dump so descriptions with colons,
    newlines, or special characters don't corrupt the YAML. Round-trip through
    `yaml.safe_load` works for everything we emit.
    """
    frontmatter: Dict[str, object] = {
        "name": skill.name,
        "description": skill.description or "",
        "engine": skill.engine,
        "version": skill.version,
        "category": skill.category,
    }
    if skill.tags:
        # Stringify defensively — legacy imports occasionally yield non-str tags
        frontmatter["tags"] = [str(t) for t in skill.tags]
    if skill.auto_trigger:
        frontmatter["auto_trigger"] = skill.auto_trigger

    yaml_block = yaml.safe_dump(frontmatter, default_flow_style=False, sort_keys=False).rstrip()

    body_lines = [f"# {skill.name}", ""]
    if skill.description:
        body_lines.extend([skill.description, ""])
    if skill.inputs:
        body_lines.append("## Inputs")
        for inp in skill.inputs:
            req = "(required)" if getattr(inp, "required", False) else "(optional)"
            body_lines.append(
                f"- `{inp.name}` ({getattr(inp, 'type', 'string')}) {req} — {getattr(inp, 'description', '') or ''}"
            )
        body_lines.append("")

    fence_lang = {"python": "python", "shell": "bash", "markdown": "markdown"}.get(skill.engine, "text")
    source = _read_skill_source(skill)
    if source:
        body_lines.extend(["## Source", f"```{fence_lang}", source, "```"])

    return f"---\n{yaml_block}\n---\n\n" + "\n".join(body_lines) + "\n"


def _read_skill_source(skill: FileSkill) -> str:
    """Load the skill's script file from disk. Returns '' if unreadable."""
    try:
        script_path = Path(skill.skill_dir) / skill.script_path
        if script_path.is_file():
            return script_path.read_text(encoding="utf-8")
    except Exception:
        pass
    return ""


def _skill_to_gws_md(skill: FileSkill) -> str:
    """Google Workspace SKILL.md — uses `title`/`summary` field names instead of name/description."""
    frontmatter: Dict[str, object] = {
        "title": skill.name,
        "summary": skill.description or "",
        "engine": skill.engine,
        "category": skill.category,
    }
    if skill.tags:
        frontmatter["tags"] = [str(t) for t in skill.tags]
    yaml_block = yaml.safe_dump(frontmatter, default_flow_style=False, sort_keys=False).rstrip()
    body = f"# {skill.name}\n\n{skill.description or ''}\n"
    return f"---\n{yaml_block}\n---\n\n{body}"


def _parse_claude_code_skill_md(content: str) -> tuple[dict, str]:
    """Parse a Claude Code-format SKILL.md into (frontmatter_dict, body_str).

    Raises HTTPException(400) on missing/malformed frontmatter or on any key
    outside the Claude Code subset — that strict allowlist is what keeps an
    imported file from quietly redirecting execution to a different engine.
    """
    if not content.startswith("---"):
        raise HTTPException(status_code=400, detail="SKILL.md must start with YAML frontmatter delimited by '---'.")

    parts = content.split("---", 2)
    if len(parts) < 3:
        raise HTTPException(status_code=400, detail="SKILL.md frontmatter is not properly closed with '---'.")

    try:
        meta = yaml.safe_load(parts[1].strip()) or {}
    except yaml.YAMLError as e:
        raise HTTPException(status_code=400, detail=f"Invalid YAML frontmatter: {e}")

    if not isinstance(meta, dict):
        raise HTTPException(status_code=400, detail="Frontmatter must be a YAML mapping.")

    unknown = set(meta.keys()) - _CLAUDE_CODE_ALLOWED_KEYS
    if unknown:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Frontmatter contains keys outside the Claude Code subset: {sorted(unknown)}. "
                "Allowed: name, description, allowed-tools."
            ),
        )

    name = (meta.get("name") or "").strip()
    description = (meta.get("description") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Frontmatter is missing required field: name.")
    if not description:
        raise HTTPException(status_code=400, detail="Frontmatter is missing required field: description.")

    body = parts[2].lstrip("\n").rstrip()
    return meta, body


def _skill_to_openai_function(skill: FileSkill) -> dict:
    """OpenAI function-calling JSON schema."""
    tool = _skill_to_mcp_tool(skill)
    return {
        "type": "function",
        "function": {
            "name": tool["name"],
            "description": tool["description"],
            "parameters": tool["inputSchema"],
        },
    }


# ---------------------------------------------------------------------------
# Library endpoints (file-based skills)
# NOTE: Fixed-path routes MUST come before /library/{slug} to avoid
#       FastAPI treating "match", "create", etc. as slug parameters.
# ---------------------------------------------------------------------------

@router.get("/library", response_model=List[FileSkill])
def list_file_skills(
    tier: Optional[str] = Query(None, description="Filter by tier: native, community, custom"),
    category: Optional[str] = Query(None, description="Filter by category"),
    search: Optional[str] = Query(None, description="Search query — uses embedding match then text fallback"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List file-based skills with optional tier/category/search filters."""
    tenant_id = str(current_user.tenant_id)
    skills = skill_manager.list_skills(tenant_id)

    # Search filter — try embedding match first, fallback to text
    if search:
        matched_slugs = set()
        try:
            matches = match_skills(db, tenant_id, search, limit=50)
            matched_slugs = {m["ref_id"] for m in matches}
        except Exception:
            pass
        if matched_slugs:
            skills = [s for s in skills if s.slug in matched_slugs]
        else:
            q = search.lower()
            skills = [
                s for s in skills
                if q in s.name.lower()
                or (s.description and q in s.description.lower())
                or any(q in t.lower() for t in s.tags)
            ]

    if tier:
        skills = [s for s in skills if s.tier == tier]
    if category:
        skills = [s for s in skills if s.category == category]

    return skills


@router.get("/library/internal", response_model=List[FileSkill])
def list_file_skills_internal(
    _auth: None = Depends(_verify_internal_key),
):
    """List file-based skills (internal)."""
    return skill_manager.list_skills()


@router.get("/library/match")
def match_file_skills(
    q: str = Query(..., description="Query string for skill matching"),
    limit: int = Query(3, ge=1, le=20),
    tenant_id: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    _auth: None = Depends(_verify_internal_key),
):
    """Auto-trigger matching — find skills relevant to a query (internal)."""
    matches = match_skills(db, tenant_id, q, limit=limit)

    # Enrich with full skill data
    enriched = []
    for m in matches:
        slug = m.get("ref_id")
        skill = skill_manager.get_skill_by_slug(slug, tenant_id)
        if skill:
            enriched.append({
                "skill": skill.dict(),
                "score": m.get("score"),
            })

    return enriched


@router.post("/library/create", response_model=FileSkill, status_code=201)
def create_file_skill(
    payload: FileSkillCreateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create a new file-based skill from the UI."""
    tenant_id = str(current_user.tenant_id)
    _validate_skill_payload(payload.engine, payload.script, [inp.dict() for inp in payload.inputs])
    result = skill_manager.create_skill(
        tenant_id=tenant_id,
        name=payload.name,
        description=payload.description,
        engine=payload.engine,
        script=payload.script,
        inputs=[inp.dict() for inp in payload.inputs],
        category=payload.category,
        auto_trigger=payload.auto_trigger,
        chain_to=payload.chain_to if payload.chain_to else None,
        tags=payload.tags if payload.tags else None,
    )
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])

    sync_skills_to_db(db)

    log_activity(
        db,
        tenant_id=current_user.tenant_id,
        event_type="action_triggered",
        description=f"Skill created: {payload.name} ({payload.engine})",
        source="skills",
        event_metadata={"skill_name": payload.name, "engine": payload.engine, "action": "skill_created"},
    )
    return result["skill"]


@router.post("/library/execute")
def execute_file_skill(
    skill_name: str = Body(...),
    inputs: Dict = Body(default={}),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Execute a file-based skill by name (user-facing)."""
    tenant_id = str(current_user.tenant_id)
    result = skill_manager.execute_skill(skill_name, inputs, tenant_id=tenant_id)

    if "error" in result:
        log_activity(
            db,
            tenant_id=current_user.tenant_id,
            event_type="action_failed",
            description=f"Skill execution failed: {skill_name}",
            source="skills",
            event_metadata={"skill_name": skill_name, "inputs": inputs, "error": result["error"], "action": "skill_failed"},
        )
        raise HTTPException(status_code=400, detail=result["error"])

    log_activity(
        db,
        tenant_id=current_user.tenant_id,
        event_type="action_completed",
        description=f"Skill executed: {skill_name}",
        source="skills",
        event_metadata={"skill_name": skill_name, "inputs": inputs, "action": "skill_executed"},
    )
    return result


class InternalUpdatePromptRequest(BaseModel):
    """Chat-side request to rewrite a skill's prompt body.

    ``actor_user_id`` and ``tenant_id`` come from MCP request headers
    (``X-User-Id`` / ``X-Tenant-Id``); they're not trusted from the body and
    are validated by the caller before invoking this endpoint.
    """

    slug: str
    new_prompt: str
    reason: Optional[str] = None
    tenant_id: str
    actor_user_id: Optional[str] = None


@router.post("/library/internal/update-prompt", response_model=FileSkill)
def update_skill_prompt_internal(
    payload: InternalUpdatePromptRequest,
    db: Session = Depends(get_db),
    _auth: None = Depends(_verify_internal_key),
):
    """Rewrite a custom skill's markdown prompt and append a revision row.

    Markdown engine only — chat-driven edits don't touch python/shell
    skills (those are sandboxed and need code review). Native skills must
    be forked first; the caller gets a 400 instead of a silent fork so the
    UX surfaces "you're editing the bundled version" loudly.
    """
    from app.services.library_revisions import record_revision

    tenant_id = payload.tenant_id
    skill = skill_manager.get_skill_by_slug(payload.slug, tenant_id)
    if not skill:
        raise HTTPException(status_code=404, detail=f"Skill '{payload.slug}' not found for tenant.")
    if skill.tier != "custom":
        raise HTTPException(
            status_code=400,
            detail="Only custom (forked) skills can be edited from chat. Fork it first.",
        )
    if skill.engine != "markdown":
        raise HTTPException(
            status_code=400,
            detail=f"Skill engine is '{skill.engine}'; only markdown skills can be edited from chat.",
        )

    before_prompt = _read_skill_source(skill)
    result = skill_manager.update_skill(
        tenant_id,
        payload.slug,
        {"script": payload.new_prompt},
    )
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])

    sync_skills_to_db(db)

    actor_uuid = None
    if payload.actor_user_id:
        try:
            actor_uuid = uuid.UUID(payload.actor_user_id)
        except (ValueError, TypeError):
            actor_uuid = None

    record_revision(
        db,
        tenant_id=uuid.UUID(tenant_id),
        target_type="skill",
        target_ref=payload.slug,
        actor_user_id=actor_uuid,
        reason=payload.reason,
        before_value={"prompt": before_prompt},
        after_value={"prompt": payload.new_prompt},
    )

    log_activity(
        db,
        tenant_id=uuid.UUID(tenant_id),
        event_type="action_triggered",
        description=f"Skill prompt updated via chat: {payload.slug}",
        source="skills",
        event_metadata={
            "slug": payload.slug,
            "reason": payload.reason,
            "action": "skill_prompt_updated_chat",
        },
    )
    return result["skill"]


@router.get("/library/internal/{slug}/source")
def read_skill_source_internal(
    slug: str,
    tenant_id: Optional[str] = Query(None),
    _auth: None = Depends(_verify_internal_key),
):
    """Return the full body of a skill (markdown / script) for mid-turn read.

    Used by the code-worker / chat-side ``read_library_skill`` MCP tool so a
    CLI subprocess can inspect what a bundled or tenant skill actually says
    before quoting / extending it.
    """
    skill = skill_manager.get_skill_by_slug(slug, tenant_id)
    if not skill:
        raise HTTPException(status_code=404, detail=f"Skill '{slug}' not found.")

    # For engines with a separate script file (python/shell/markdown
    # prompt), prefer that source. For engines where the skill.md body
    # *is* the prompt (agent identity skills), fall back to description —
    # the parser already strips frontmatter into ``description`` for us.
    body = _read_skill_source(skill) or (skill.description or "")

    return {
        "slug": skill.slug,
        "name": skill.name,
        "description": skill.description,
        "tier": skill.tier,
        "engine": skill.engine,
        "category": skill.category,
        "tags": skill.tags,
        "auto_trigger": skill.auto_trigger,
        "body": body,
    }


@router.get("/library/revisions/internal")
def list_skill_revisions_internal(
    target_type: Optional[str] = Query(None, description="'skill' or 'agent'"),
    target_ref: Optional[str] = Query(None, description="Slug or agent UUID"),
    tenant_id: str = Query(...),
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
    _auth: None = Depends(_verify_internal_key),
):
    """List recent library revisions for a tenant (audit history for chat)."""
    from app.services.library_revisions import list_revisions

    rows = list_revisions(
        db,
        tenant_id=uuid.UUID(tenant_id),
        target_type=target_type,
        target_ref=target_ref,
        limit=limit,
    )
    return [
        {
            "id": str(r.id),
            "target_type": r.target_type,
            "target_ref": r.target_ref,
            "actor_user_id": str(r.actor_user_id) if r.actor_user_id else None,
            "reason": r.reason,
            "before_value": r.before_value,
            "after_value": r.after_value,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]


@router.post("/library/internal/execute")
def execute_file_skill_internal(
    skill_name: str = Body(...),
    inputs: Dict = Body(default={}),
    tenant_id: Optional[str] = Body(None),
    _auth: None = Depends(_verify_internal_key),
):
    """Execute a file-based skill by name (internal)."""
    result = skill_manager.execute_skill(skill_name, inputs, tenant_id=tenant_id)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@router.post("/library/import-github")
def import_from_github(
    payload: GitHubImportRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_superuser),
):
    """Import skill(s) from a GitHub repository."""
    # Try to get user's GitHub OAuth token
    from app.models.integration_config import IntegrationConfig
    from app.services.orchestration.credential_vault import retrieve_credentials_for_skill

    github_token = None
    try:
        config = db.query(IntegrationConfig).filter(
            IntegrationConfig.tenant_id == current_user.tenant_id,
            IntegrationConfig.integration_name == "github",
        ).first()
        if config:
            creds = retrieve_credentials_for_skill(db, config.id, current_user.tenant_id)
            github_token = creds.get("access_token")
    except Exception:
        pass  # Proceed without token (public repos still work)

    result = skill_manager.import_from_github(payload.repo_url, github_token=github_token)

    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])

    sync_skills_to_db(db)

    # Log to memory
    imported = result.get("imported", [])
    skill_obj = result.get("skill")
    if skill_obj:
        imported = [skill_obj.name]

    log_activity(
        db,
        tenant_id=current_user.tenant_id,
        event_type="action_triggered",
        description=f"Skills imported from GitHub: {', '.join(imported)}",
        source="skills",
        event_metadata={
            "action": "skill_imported",
            "repo_url": payload.repo_url,
            "imported": imported,
        },
    )
    return result


# ---------------------------------------------------------------------------
# MCP manifest — lets external agents (Claude Code, Gemini, Copilot)
# discover the tenant's skills as MCP/OpenAI-compatible tools.
# ---------------------------------------------------------------------------

@router.get("/mcp-manifest")
def get_mcp_manifest(
    request: Request,
    current_user: User = Depends(get_current_user),
):
    """Return MCP tool definitions for every skill available to this tenant."""
    tenant_id = str(current_user.tenant_id)
    skills = skill_manager.list_skills(tenant_id)
    # Skip broken auto-generated import artifacts — they shouldn't be advertised
    # to external agents as callable tools.
    skills = [s for s in skills if not _is_auto_generated_skill(s)]

    # Derive server URL from the request so this works for agentprovision.com
    # AND localhost dev AND custom domains without hardcoding.
    base_url = str(request.base_url).rstrip("/")
    server_url = f"{base_url}/api/v1/mcp"

    return {
        "server_url": server_url,
        "tenant_id": tenant_id,
        "tools": [_skill_to_mcp_tool(s) for s in skills],
        "openai_functions": [_skill_to_openai_function(s) for s in skills],
    }


# ---------------------------------------------------------------------------
# Slug-based library endpoints — MUST come after all fixed-path routes
# ---------------------------------------------------------------------------

@router.get("/library/{slug}/export")
def export_skill(
    slug: str,
    format: str = Query("superpowers", description="superpowers | gws | openai"),
    current_user: User = Depends(get_current_user),
):
    """Export a single skill in one of several portable formats."""
    tenant_id = str(current_user.tenant_id)
    skill = skill_manager.get_skill_by_slug(slug, tenant_id)
    if not skill:
        raise HTTPException(status_code=404, detail="Skill not found")

    safe_slug = _sanitize_tool_name(skill.slug or skill.name)

    if format == "superpowers":
        filename = f"{safe_slug}.superpowers.md"
        return PlainTextResponse(
            _skill_to_superpowers_md(skill),
            media_type="text/markdown",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    if format == "gws":
        filename = f"{safe_slug}.gws.md"
        return PlainTextResponse(
            _skill_to_gws_md(skill),
            media_type="text/markdown",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    if format == "openai":
        filename = f"{safe_slug}.openai.json"
        return Response(
            content=json.dumps(_skill_to_openai_function(skill), indent=2),
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    raise HTTPException(status_code=400, detail=f"Unknown format '{format}'. Use superpowers, gws, or openai.")


@router.post("/library/import-claude-code", response_model=FileSkill, status_code=201)
def import_claude_code_skill(
    payload: ClaudeCodeImportRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Import a Claude Code-format ``SKILL.md`` into the tenant's library.

    The body of the markdown file becomes the skill prompt (``markdown`` engine).
    This is the inverse of ``GET /library/{slug}/export?format=superpowers``
    minus engine-specific source — Claude Code skills are prompts, not scripts.
    """
    tenant_id = str(current_user.tenant_id)
    meta, body = _parse_claude_code_skill_md(payload.content)
    name = meta["name"].strip()
    description = meta["description"].strip()

    # If a tenant skill with that name already exists, require explicit overwrite
    # so an accidental re-import doesn't replace local edits without the user
    # actively saying so.
    existing = skill_manager.get_skill_by_name(name, tenant_id)
    if existing and existing.tier == "custom":
        if not payload.overwrite:
            raise HTTPException(
                status_code=409,
                detail=f"Skill '{name}' already exists. Re-send with overwrite=true to replace it.",
            )
        delete_result = skill_manager.delete_skill(tenant_id, existing.slug)
        if "error" in delete_result:
            raise HTTPException(status_code=400, detail=delete_result["error"])
    elif existing and existing.tier != "custom":
        raise HTTPException(
            status_code=400,
            detail=f"A native/community skill named '{name}' already exists. Rename your import.",
        )

    # Markdown engine: the body IS the skill. No template variables expected
    # from a freshly-imported Claude Code skill, so inputs is empty.
    script = body or description

    result = skill_manager.create_skill(
        tenant_id=tenant_id,
        name=name,
        description=description,
        engine="markdown",
        script=script,
        inputs=[],
        category="general",
    )
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])

    sync_skills_to_db(db)

    log_activity(
        db,
        tenant_id=current_user.tenant_id,
        event_type="action_triggered",
        description=f"Skill imported (Claude Code format): {name}",
        source="skills",
        event_metadata={"skill_name": name, "format": "claude-code", "action": "skill_imported"},
    )
    return result["skill"]


@router.get("/library/{slug}/versions")
def get_skill_versions(
    slug: str,
    current_user: User = Depends(get_current_user),
):
    """Get version history for a skill."""
    tenant_id = str(current_user.tenant_id)
    versions = skill_manager.get_skill_versions(slug, tenant_id)
    return versions


@router.put("/library/{slug}", response_model=FileSkill)
def update_file_skill(
    slug: str,
    payload: FileSkillUpdateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Update a custom file-based skill (bumps version)."""
    tenant_id = str(current_user.tenant_id)
    updates = payload.dict(exclude_none=True)
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update.")

    # Re-validate on script/engine/inputs change. Prefer the incoming payload's
    # inputs over the stored skill's inputs so a single PUT can add a new
    # {{var}} AND declare the input at the same time.
    if any(k in updates for k in ("script", "engine", "inputs")):
        current_skill = skill_manager.get_skill_by_slug(slug, tenant_id)
        engine = updates.get("engine") or (current_skill.engine if current_skill else "python")
        script = updates.get("script") or _read_skill_source(current_skill) if current_skill else ""
        if "inputs" in updates:
            inputs = updates["inputs"]
        else:
            inputs = [i.dict() if hasattr(i, "dict") else i for i in (current_skill.inputs if current_skill else [])]
        if script:
            _validate_skill_payload(engine, script, inputs)

    result = skill_manager.update_skill(tenant_id, slug, updates)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])

    sync_skills_to_db(db)

    log_activity(
        db,
        tenant_id=current_user.tenant_id,
        event_type="action_triggered",
        description=f"Skill updated: {slug} (fields: {', '.join(updates.keys())})",
        source="skills",
        event_metadata={"slug": slug, "updated_fields": list(updates.keys()), "action": "skill_updated"},
    )
    return result["skill"]


@router.post("/library/{slug}/fork", response_model=FileSkill, status_code=201)
def fork_file_skill(
    slug: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Fork a native/community skill into tenant's custom skills."""
    tenant_id = str(current_user.tenant_id)
    result = skill_manager.fork_skill(tenant_id, slug)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])

    sync_skills_to_db(db)

    log_activity(
        db,
        tenant_id=current_user.tenant_id,
        event_type="action_triggered",
        description=f"Skill forked: {slug}",
        source="skills",
        event_metadata={"slug": slug, "action": "skill_forked"},
    )
    return result["skill"]


@router.delete("/library/{slug}", status_code=204)
def delete_file_skill(
    slug: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Delete a custom file-based skill."""
    tenant_id = str(current_user.tenant_id)
    result = skill_manager.delete_skill(tenant_id, slug)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])

    sync_skills_to_db(db)

    log_activity(
        db,
        tenant_id=current_user.tenant_id,
        event_type="action_triggered",
        description=f"Skill deleted: {slug}",
        source="skills",
        event_metadata={"slug": slug, "action": "skill_deleted"},
    )


# ---------------------------------------------------------------------------
# DB-backed skills (existing CRUD — unchanged)
# ---------------------------------------------------------------------------

@router.get("", response_model=List[SkillInDB])
def list_skills(
    skill_type: Optional[str] = None,
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return service.get_skills(db, current_user.tenant_id, skill_type, skip, limit)


@router.post("", response_model=SkillInDB, status_code=201)
def create_skill(
    skill_in: SkillCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return service.create_skill(db, skill_in, current_user.tenant_id)


@router.get("/{skill_id}", response_model=SkillInDB)
def get_skill(
    skill_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    skill = service.get_skill(db, skill_id, current_user.tenant_id)
    if not skill:
        raise HTTPException(status_code=404, detail="Skill not found")
    return skill


@router.put("/{skill_id}", response_model=SkillInDB)
def update_skill(
    skill_id: uuid.UUID,
    skill_in: SkillUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    skill = service.update_skill(db, skill_id, current_user.tenant_id, skill_in)
    if not skill:
        raise HTTPException(status_code=404, detail="Skill not found")
    return skill


@router.delete("/{skill_id}", status_code=204)
def delete_skill(
    skill_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not service.delete_skill(db, skill_id, current_user.tenant_id):
        raise HTTPException(status_code=400, detail="Cannot delete system skill or skill not found")


@router.post("/{skill_id}/execute")
def execute_skill(
    skill_id: uuid.UUID,
    request: SkillExecuteRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = service.execute_skill(db, skill_id, current_user.tenant_id, request.entity_id, request.params)
    if not result:
        raise HTTPException(status_code=404, detail="Skill not found or disabled")
    return result


@router.get("/{skill_id}/executions", response_model=List[SkillExecutionInDB])
def list_skill_executions(
    skill_id: uuid.UUID,
    skip: int = 0,
    limit: int = 50,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return service.get_skill_executions(db, skill_id, current_user.tenant_id, skip, limit)


@router.post("/{skill_id}/clone", response_model=SkillInDB, status_code=201)
def clone_skill(
    skill_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    skill = service.clone_skill(db, skill_id, current_user.tenant_id)
    if not skill:
        raise HTTPException(status_code=404, detail="Skill not found")
    return skill
