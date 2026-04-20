"""API routes for skills management."""
import ast
import json
import re
from pathlib import Path

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query, Request
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


class GitHubImportRequest(BaseModel):
    repo_url: str


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

_MD_TEMPLATE_VAR_RE = re.compile(r"\{\{\s*([a-zA-Z_][\w]*)\s*\}\}")


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
            if len(args) != 1:
                return "`execute` must take exactly one argument (inputs)."
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
    return {
        "name": f"skill_{skill.slug or skill.name}",
        "description": (skill.description or skill.name)[:1024],
        "inputSchema": {
            "type": "object",
            "properties": properties,
            "required": required,
        },
    }


def _skill_to_superpowers_md(skill: FileSkill) -> str:
    """Superpowers/Claude Code SKILL.md with YAML frontmatter + body."""
    frontmatter_lines = [
        "---",
        f"name: {skill.name}",
        f"description: {skill.description or ''}",
        f"engine: {skill.engine}",
        f"version: {skill.version}",
        f"category: {skill.category}",
    ]
    if skill.tags:
        frontmatter_lines.append(f"tags: [{', '.join(skill.tags)}]")
    if skill.auto_trigger:
        frontmatter_lines.append(f"auto_trigger: {skill.auto_trigger}")
    frontmatter_lines.append("---")

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
    # Source script goes in a fenced block so humans can read + Claude Code can parse
    fence_lang = {"python": "python", "shell": "bash", "markdown": "markdown"}.get(skill.engine, "text")
    source = _read_skill_source(skill)
    if source:
        body_lines.extend(["## Source", f"```{fence_lang}", source, "```"])
    return "\n".join(frontmatter_lines + [""] + body_lines) + "\n"


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
    """Google Workspace SKILL.md — almost identical but uses different fields."""
    lines = [
        "---",
        f"title: {skill.name}",
        f"summary: {skill.description or ''}",
        f"engine: {skill.engine}",
        f"category: {skill.category}",
        "---",
        "",
        f"# {skill.name}",
        "",
        skill.description or "",
    ]
    return "\n".join(lines) + "\n"


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
    def _is_auto_generated(s):
        return s.category == "auto-generated" or (
            s.description and "response timeout pattern" in s.description.lower()
        )
    skills = [s for s in skills if not _is_auto_generated(s)]

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

    if format == "superpowers":
        return PlainTextResponse(_skill_to_superpowers_md(skill), media_type="text/markdown")
    if format == "gws":
        return PlainTextResponse(_skill_to_gws_md(skill), media_type="text/markdown")
    if format == "openai":
        return _skill_to_openai_function(skill)
    raise HTTPException(status_code=400, detail=f"Unknown format '{format}'. Use superpowers, gws, or openai.")


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

    # If script or engine changed, re-validate. Inputs come from the current skill
    # unless the update includes new ones.
    if "script" in updates or "engine" in updates:
        current_skill = skill_manager.get_skill_by_slug(slug, tenant_id)
        engine = updates.get("engine") or (current_skill.engine if current_skill else "python")
        script = updates.get("script") or ""
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
