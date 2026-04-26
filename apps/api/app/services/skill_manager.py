"""SkillManager v2 — three-tier skill system with versioning."""
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import httpx
import yaml

from app.schemas.file_skill import FileSkill, SkillInput

logger = logging.getLogger(__name__)

# Skill / agent file roots. Match Claude Code's split:
#   apps/api/app/agents/   -> AGENT.md files (frontmatter has `engine: agent`)
#   apps/api/app/skills/   -> SKILL.md files (everything else)
# Each root has _bundled/ (read-only, ships with image) and _tenant/<uuid>/.
_APP_DIR = Path(__file__).parent.parent
SKILLS_BASE = Path(os.environ.get("DATA_STORAGE_PATH", str(_APP_DIR))) / "skills"
AGENTS_BASE = Path(os.environ.get("DATA_STORAGE_PATH", str(_APP_DIR))) / "agents"
# Backward-compat alias — some other modules import this constant
BUNDLED_SKILLS_DIR = _APP_DIR / "skills"

VALID_CATEGORIES = {"sales", "marketing", "data", "coding", "communication", "automation", "general", "productivity", "infrastructure", "devops", "support"}

# Map external category names to our valid set
CATEGORY_MAP = {
    "productivity": "productivity",
    "email": "communication",
    "calendar": "communication",
    "storage": "data",
    "collaboration": "communication",
}


def _find_skill_md(skill_dir: Path) -> Optional[Path]:
    """Find skill.md or SKILL.md (case-insensitive) in a directory."""
    for name in ("skill.md", "SKILL.md", "Skill.md"):
        p = skill_dir / name
        if p.exists():
            return p
    return None


def _normalize_external_metadata(metadata: dict) -> dict:
    """Adapt external skill formats (e.g. GWS SKILL.md) to our frontmatter schema."""
    # If metadata already has 'engine', it's likely our format — skip
    if "engine" in metadata:
        return metadata

    normalized = dict(metadata)

    # GWS puts description in frontmatter, we use it in body
    # Keep it in frontmatter — _parse_skill_md will handle it

    # Version: semver string "1.0.0" → integer 1
    version = normalized.get("version", 1)
    if isinstance(version, str) and "." in version:
        try:
            normalized["version"] = int(version.split(".")[0])
        except ValueError:
            normalized["version"] = 1

    # Engine: GWS skills are CLI instruction files → markdown
    normalized.setdefault("engine", "markdown")

    # Category: nested in metadata.openclaw.category → top-level
    openclaw = normalized.pop("metadata", {}).get("openclaw", {}) if isinstance(normalized.get("metadata"), dict) else {}
    raw_category = openclaw.get("category", "general")
    normalized["category"] = CATEGORY_MAP.get(raw_category, raw_category)
    if normalized["category"] not in VALID_CATEGORIES:
        normalized["category"] = "general"

    # Tags: derive from name if not present
    if not normalized.get("tags"):
        name = normalized.get("name", "")
        normalized["tags"] = [t for t in name.replace("-", " ").split() if t and t != "gws"]

    # Auto-trigger: use description if available
    if not normalized.get("auto_trigger") and normalized.get("description"):
        normalized["auto_trigger"] = normalized["description"]

    # Store requires info in properties for reference
    requires = openclaw.get("requires", {})
    if requires:
        normalized["requires"] = requires

    return normalized


def _parse_skill_md(skill_dir: Path, tier: str = "native", tenant_id: str = None) -> Optional[FileSkill]:
    """Parse a skill.md file and return a FileSkill, or None if malformed."""
    skill_file = _find_skill_md(skill_dir)
    if not skill_file:
        return None
    try:
        content = skill_file.read_text(encoding="utf-8")
        if not content.startswith("---"):
            return None

        parts = content.split("---", 2)
        if len(parts) < 3:
            return None

        metadata = yaml.safe_load(parts[1].strip())
        if not isinstance(metadata, dict):
            return None

        # Normalize external formats (e.g. GWS SKILL.md) to our schema
        metadata = _normalize_external_metadata(metadata)

        body = parts[2].strip()
        # Use frontmatter description if available (GWS pattern), else parse body
        description = metadata.get("description") or body
        if description.startswith("## Description"):
            description = description[len("## Description"):].strip()

        raw_inputs = metadata.get("inputs", []) or []
        inputs = [
            SkillInput(
                name=inp.get("name", ""),
                type=inp.get("type", "string"),
                description=inp.get("description", ""),
                required=bool(inp.get("required", False)),
            )
            for inp in raw_inputs
            if isinstance(inp, dict)
        ]

        return FileSkill(
            name=metadata["name"],
            engine=metadata.get("engine", "python"),
            script_path=metadata.get("script_path", "script.py"),
            description=description or None,
            inputs=inputs,
            skill_dir=str(skill_dir),
            version=metadata.get("version", 1),
            category=metadata.get("category", "general"),
            tags=metadata.get("tags", []),
            auto_trigger=metadata.get("auto_trigger"),
            chain_to=metadata.get("chain_to", []),
            prompts=metadata.get("prompts", []),
            tier=tier,
            slug=skill_dir.name,
            source_repo=metadata.get("source_repo"),
            tool_class=metadata.get("tool_class"),
        )
    except Exception as exc:
        logger.error("Error loading skill from %s: %s", skill_dir, exc)
        return None


_SENSITIVE_ENV_KEYS = frozenset({
    "SECRET_KEY", "DATABASE_URL", "ENCRYPTION_KEY", "ANTHROPIC_API_KEY",
    "MCP_API_KEY", "API_INTERNAL_KEY", "GITHUB_TOKEN", "GITHUB_CLIENT_SECRET",
    "GOOGLE_CLIENT_SECRET", "MICROSOFT_CLIENT_SECRET", "LINKEDIN_CLIENT_SECRET",
    "GOOGLE_API_KEY", "HCA_SERVICE_KEY",
    # Platform-level CLI credentials
    "PLATFORM_CLAUDE_CODE_TOKEN", "PLATFORM_GEMINI_CLI_TOKEN", "PLATFORM_CODEX_AUTH_JSON",
})


class SkillManager:
    """Singleton — manages three-tier file-based skills."""

    _instance: Optional["SkillManager"] = None

    def __init__(self) -> None:
        self._skills: List[FileSkill] = []

    @classmethod
    def get_instance(cls) -> "SkillManager":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def _bundled_skills_dir(self) -> Path:
        return SKILLS_BASE / "_bundled"

    def _tenant_skills_dir(self, tenant_id: str) -> Path:
        return SKILLS_BASE / "_tenant" / tenant_id

    def _bundled_agents_dir(self) -> Path:
        return AGENTS_BASE / "_bundled"

    def _tenant_agents_dir(self, tenant_id: str) -> Path:
        return AGENTS_BASE / "_tenant" / tenant_id

    def scan(self) -> None:
        """Scan agents/ and skills/ directories and load all definitions.

        Layout (Claude Code-compatible):
          apps/api/app/agents/_bundled/<slug>/skill.md      (engine: agent)
          apps/api/app/agents/_tenant/<uuid>/<slug>/skill.md
          apps/api/app/skills/_bundled/<slug>/skill.md
          apps/api/app/skills/_tenant/<uuid>/<slug>/skill.md
        """
        SKILLS_BASE.mkdir(parents=True, exist_ok=True)
        AGENTS_BASE.mkdir(parents=True, exist_ok=True)
        self._bundled_skills_dir().mkdir(parents=True, exist_ok=True)
        (SKILLS_BASE / "_tenant").mkdir(parents=True, exist_ok=True)
        self._bundled_agents_dir().mkdir(parents=True, exist_ok=True)
        (AGENTS_BASE / "_tenant").mkdir(parents=True, exist_ok=True)

        loaded: List[FileSkill] = []

        # Bundled agents — tier "native" preserved for UI compat
        for entry in sorted(self._bundled_agents_dir().iterdir()) if self._bundled_agents_dir().is_dir() else []:
            if entry.is_dir():
                skill = _parse_skill_md(entry, tier="native")
                if skill:
                    loaded.append(skill)

        # Bundled skills
        for entry in sorted(self._bundled_skills_dir().iterdir()) if self._bundled_skills_dir().is_dir() else []:
            if entry.is_dir():
                skill = _parse_skill_md(entry, tier="native")
                if skill:
                    loaded.append(skill)

        # Tenant agents — _tenant/<uuid>/<slug>/
        tenant_agents_root = AGENTS_BASE / "_tenant"
        if tenant_agents_root.is_dir():
            for tenant_dir in sorted(tenant_agents_root.iterdir()):
                if not tenant_dir.is_dir():
                    continue
                tid = tenant_dir.name
                for entry in sorted(tenant_dir.iterdir()):
                    if entry.is_dir():
                        skill = _parse_skill_md(entry, tier="custom", tenant_id=tid)
                        if skill:
                            loaded.append(skill)

        # Tenant skills — _tenant/<uuid>/<slug>/
        tenant_skills_root = SKILLS_BASE / "_tenant"
        if tenant_skills_root.is_dir():
            for tenant_dir in sorted(tenant_skills_root.iterdir()):
                if not tenant_dir.is_dir():
                    continue
                tid = tenant_dir.name
                for entry in sorted(tenant_dir.iterdir()):
                    if entry.is_dir():
                        skill = _parse_skill_md(entry, tier="custom", tenant_id=tid)
                        if skill:
                            loaded.append(skill)

        self._skills = loaded
        logger.info("SkillManager: %d skill(s) loaded", len(self._skills))

    def list_skills(self, tenant_id: str = None) -> List[FileSkill]:
        """Return skills visible to a tenant: native + community + their custom."""
        if not tenant_id:
            return [s for s in self._skills if s.tier in ("native", "community")]
        # New layout: _tenant/<uuid>/<slug>/   ->  match by exact uuid segment.
        # Old layout: tenant_<uuid>/<slug>/    ->  legacy fallback.
        tenant_seg_new = f"_tenant/{tenant_id}/"
        tenant_seg_old = f"tenant_{tenant_id}/"
        return [
            s for s in self._skills
            if s.tier in ("native", "community")
            or (s.tier == "custom" and (tenant_seg_new in s.skill_dir or tenant_seg_old in s.skill_dir))
        ]

    def get_skill_by_name(self, name: str, tenant_id: str = None) -> Optional[FileSkill]:
        """Find a skill by name from visible skills."""
        for skill in self.list_skills(tenant_id):
            if skill.name.lower() == name.lower():
                return skill
        return None

    def get_skill_by_slug(self, slug: str, tenant_id: str = None) -> Optional[FileSkill]:
        """Find a skill by slug."""
        for skill in self.list_skills(tenant_id):
            if skill.slug == slug:
                return skill
        return None

    def create_skill(self, tenant_id: str, name: str, description: str, engine: str,
                     script: str, inputs: list, category: str = "general",
                     auto_trigger: str = None, chain_to: list = None, tags: list = None) -> dict:
        """Create a new custom skill for a tenant."""
        if self.get_skill_by_name(name, tenant_id):
            return {"error": f"Skill '{name}' already exists."}

        slug = re.sub(r'[^a-z0-9]+', '_', name.lower()).strip('_')
        if not slug:
            return {"error": "Invalid skill name."}

        skill_dir = self._tenant_dir(tenant_id) / slug
        if skill_dir.exists():
            return {"error": f"Directory '{slug}' already exists."}

        script_filenames = {"python": "script.py", "shell": "script.sh", "markdown": "prompt.md"}
        script_file = script_filenames.get(engine, "script.py")

        try:
            skill_dir.mkdir(parents=True, exist_ok=True)

            frontmatter = {
                "name": name,
                "engine": engine,
                "script_path": script_file,
                "version": 1,
                "category": category if category in VALID_CATEGORIES else "general",
            }
            if tags:
                frontmatter["tags"] = tags
            if auto_trigger:
                frontmatter["auto_trigger"] = auto_trigger
            if chain_to:
                frontmatter["chain_to"] = chain_to
            if inputs:
                frontmatter["inputs"] = inputs

            md_content = "---\n" + yaml.dump(frontmatter, default_flow_style=False) + "---\n\n"
            md_content += f"## Description\n{description}\n"

            (skill_dir / "skill.md").write_text(md_content, encoding="utf-8")
            (skill_dir / script_file).write_text(script, encoding="utf-8")

            if engine == "shell":
                os.chmod(skill_dir / script_file, 0o755)

            self.scan()
            created = self.get_skill_by_name(name, tenant_id)
            if created:
                return {"skill": created}
            return {"error": "Skill created but failed to load — check format."}
        except Exception as e:
            logger.exception("Failed to create skill: %s", e)
            if skill_dir.exists():
                shutil.rmtree(skill_dir, ignore_errors=True)
            return {"error": f"Failed to create skill: {str(e)}"}

    def update_skill(self, tenant_id: str, slug: str, updates: dict) -> dict:
        """Update a custom skill. Bumps version, writes CHANGELOG."""
        skill = self.get_skill_by_slug(slug, tenant_id)
        if not skill:
            return {"error": f"Skill '{slug}' not found."}
        if skill.tier != "custom":
            return {"error": "Only custom skills can be edited. Fork it first."}
        if f"_tenant/{tenant_id}/" not in skill.skill_dir and f"tenant_{tenant_id}/" not in skill.skill_dir:
            return {"error": "Not authorized to edit this skill."}

        skill_dir = Path(skill.skill_dir)
        skill_file = skill_dir / "skill.md"
        content = skill_file.read_text(encoding="utf-8")
        parts = content.split("---", 2)
        metadata = yaml.safe_load(parts[1].strip())
        old_version = metadata.get("version", 1)

        # Bump version
        new_version = old_version + 1
        metadata["version"] = new_version

        # Apply updates
        for key in ("name", "description", "category", "auto_trigger", "tags", "chain_to", "engine", "inputs"):
            if key in updates and key != "description":
                metadata[key] = updates[key]

        body = parts[2].strip() if len(parts) > 2 else ""
        if "description" in updates:
            body = f"## Description\n{updates['description']}"

        md_content = "---\n" + yaml.dump(metadata, default_flow_style=False) + "---\n\n" + body + "\n"
        skill_file.write_text(md_content, encoding="utf-8")

        # Update script if provided
        if "script" in updates:
            script_path = skill_dir / metadata.get("script_path", "script.py")
            script_path.write_text(updates["script"], encoding="utf-8")

        # Append to CHANGELOG
        changelog = skill_dir / "CHANGELOG.md"
        entry = f"\n## v{new_version} — {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}\n"
        entry += f"- Updated: {', '.join(updates.keys())}\n"
        if changelog.exists():
            existing = changelog.read_text(encoding="utf-8")
            changelog.write_text(entry + existing, encoding="utf-8")
        else:
            changelog.write_text(f"# Changelog\n{entry}", encoding="utf-8")

        self.scan()
        return {"skill": self.get_skill_by_slug(slug, tenant_id)}

    def fork_skill(self, tenant_id: str, slug: str) -> dict:
        """Fork a native/community skill into tenant's custom skills."""
        skill = self.get_skill_by_slug(slug)
        if not skill:
            return {"error": f"Skill '{slug}' not found."}
        if skill.tier == "custom":
            return {"error": "Skill is already a custom skill."}

        target_dir = self._tenant_dir(tenant_id) / slug
        if target_dir.exists():
            return {"error": f"You already have a skill with slug '{slug}'."}

        try:
            shutil.copytree(skill.skill_dir, str(target_dir))

            # Update the frontmatter to reflect fork
            skill_file = target_dir / "skill.md"
            content = skill_file.read_text(encoding="utf-8")
            parts = content.split("---", 2)
            metadata = yaml.safe_load(parts[1].strip())
            metadata["version"] = 1  # Reset version
            body = parts[2].strip() if len(parts) > 2 else ""
            md_content = "---\n" + yaml.dump(metadata, default_flow_style=False) + "---\n\n" + body + "\n"
            skill_file.write_text(md_content, encoding="utf-8")

            # Add CHANGELOG
            changelog = target_dir / "CHANGELOG.md"
            changelog.write_text(
                f"# Changelog\n\n## v1 — {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}\n"
                f"- Forked from {skill.tier} skill: {skill.name}\n",
                encoding="utf-8",
            )

            self.scan()
            forked = self.get_skill_by_slug(slug, tenant_id)
            if forked:
                return {"skill": forked}
            return {"error": "Fork created but failed to load."}
        except Exception as e:
            if target_dir.exists():
                shutil.rmtree(target_dir, ignore_errors=True)
            return {"error": f"Fork failed: {str(e)}"}

    def delete_skill(self, tenant_id: str, slug: str) -> dict:
        """Delete a custom skill."""
        skill = self.get_skill_by_slug(slug, tenant_id)
        if not skill:
            return {"error": "Skill not found."}
        if skill.tier != "custom":
            return {"error": "Only custom skills can be deleted."}
        if f"_tenant/{tenant_id}/" not in skill.skill_dir and f"tenant_{tenant_id}/" not in skill.skill_dir:
            return {"error": "Not authorized."}

        shutil.rmtree(skill.skill_dir, ignore_errors=True)
        self.scan()
        return {"success": True}

    def get_skill_versions(self, slug: str, tenant_id: str = None) -> list:
        """Read CHANGELOG.md for a skill."""
        skill = self.get_skill_by_slug(slug, tenant_id)
        if not skill:
            return []
        changelog = Path(skill.skill_dir) / "CHANGELOG.md"
        if not changelog.exists():
            return [{"version": skill.version, "note": "Initial version"}]
        return [{"raw": changelog.read_text(encoding="utf-8")}]

    def execute_skill(self, name: str, inputs: dict, tenant_id: str = None) -> dict:
        """Execute a file-based skill by name with given inputs."""
        skill = self.get_skill_by_name(name, tenant_id)
        if not skill:
            available = [s.name for s in self.list_skills(tenant_id)]
            return {"error": f"Skill '{name}' not found. Available: {available}"}

        script_path = os.path.join(skill.skill_dir, skill.script_path)
        if not os.path.exists(script_path):
            return {"error": f"Script not found: {script_path}"}

        try:
            if skill.engine == "python":
                return self._execute_python(skill.name, script_path, inputs)
            elif skill.engine == "shell":
                return self._execute_shell(skill.name, script_path, inputs)
            elif skill.engine == "markdown":
                return self._execute_markdown(skill, inputs)
            elif skill.engine == "tool":
                return self._execute_tool(skill, inputs)
            else:
                return {"error": f"Unsupported engine: {skill.engine}"}
        except Exception as e:
            logger.exception("Skill execution failed: %s", e)
            return {"error": f"Skill execution failed: {str(e)}"}

    def _safe_env(self) -> dict:
        return {k: v for k, v in os.environ.items() if k not in _SENSITIVE_ENV_KEYS}

    def _execute_python(self, name: str, script_path: str, inputs: dict) -> dict:
        import textwrap
        runner = textwrap.dedent(f"""
import sys, json, importlib.util
spec = importlib.util.spec_from_file_location("skill", {repr(str(script_path))})
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
inputs = json.loads(sys.stdin.read())
print(json.dumps(mod.execute(inputs)))
""")
        proc = subprocess.run(
            ["python3", "-c", runner],
            input=json.dumps(inputs),
            capture_output=True, text=True, timeout=60,
            env=self._safe_env(),
        )
        if proc.returncode != 0:
            return {"error": f"Skill exited with code {proc.returncode}", "stderr": proc.stderr[:2000]}
        try:
            return {"success": True, "skill": name, "result": json.loads(proc.stdout)}
        except (json.JSONDecodeError, ValueError):
            return {"error": f"Skill returned non-JSON output: {proc.stdout[:500]}"}

    def _execute_shell(self, name: str, script_path: str, inputs: dict) -> dict:
        env = self._safe_env()
        for k, v in inputs.items():
            env[f"SKILL_INPUT_{k.upper()}"] = str(v)
        proc = subprocess.run(
            ["bash", script_path], capture_output=True, text=True, timeout=60, env=env,
        )
        if proc.returncode != 0:
            return {"error": f"Shell script exited with code {proc.returncode}", "stderr": proc.stderr[:2000]}
        try:
            result = json.loads(proc.stdout)
        except (json.JSONDecodeError, ValueError):
            result = {"output": proc.stdout.strip()}
        return {"success": True, "skill": name, "result": result}

    def _execute_markdown(self, skill: FileSkill, inputs: dict) -> dict:
        """Execute markdown skill — assemble main prompt + sub-prompts."""
        skill_dir = Path(skill.skill_dir)
        content = (skill_dir / skill.script_path).read_text(encoding="utf-8")

        # Append sub-prompts in order
        for prompt_file in skill.prompts:
            prompt_path = skill_dir / "prompts" / prompt_file
            if prompt_path.exists():
                content += "\n\n---\n\n" + prompt_path.read_text(encoding="utf-8")

        # Substitute placeholders
        for k, v in inputs.items():
            content = content.replace(f"{{{{{k}}}}}", str(v))

        return {"success": True, "skill": skill.name, "result": {"prompt": content}}

    def _execute_tool(self, skill: FileSkill, inputs: dict) -> dict:
        """Execute a tool-backed skill via tool_executor."""
        tool_class_name = skill.tool_class
        if not tool_class_name:
            # Fallback: re-parse from skill.md frontmatter
            skill_dir = Path(skill.skill_dir)
            skill_file = skill_dir / "skill.md"
            if skill_file.exists():
                content = skill_file.read_text(encoding="utf-8")
                parts = content.split("---", 2)
                if len(parts) >= 3:
                    meta = yaml.safe_load(parts[1].strip())
                    tool_class_name = meta.get("tool_class") if isinstance(meta, dict) else None

        if not tool_class_name:
            return {"error": f"No tool_class defined for skill {skill.name}"}

        from app.services.tool_executor import TOOL_CLASS_REGISTRY
        tool_cls = TOOL_CLASS_REGISTRY.get(tool_class_name)
        if not tool_cls:
            return {"error": f"Unknown tool class: {tool_class_name}"}

        return {
            "success": True,
            "skill": skill.name,
            "result": {
                "tool_class": tool_class_name,
                "description": skill.description,
                "inputs": [{"name": i.name, "type": i.type, "required": i.required} for i in (skill.inputs or [])],
                "message": f"Tool '{skill.name}' is available. Use the corresponding agent tool to execute it.",
            },
        }

    def execute_chain(self, name: str, inputs: dict, tenant_id: str = None, depth: int = 0) -> dict:
        """Execute a skill and its chain_to skills sequentially."""
        if depth >= 3:
            return {"error": "Max chain depth (3) reached."}

        result = self.execute_skill(name, inputs, tenant_id)
        if "error" in result:
            return result

        skill = self.get_skill_by_name(name, tenant_id)
        if not skill or not skill.chain_to:
            return result

        chain_results = [result]
        current_inputs = result.get("result", {})
        if not isinstance(current_inputs, dict):
            current_inputs = {"previous_result": current_inputs}

        for next_slug in skill.chain_to:
            next_skill = self.get_skill_by_slug(next_slug, tenant_id)
            if not next_skill:
                next_skill = self.get_skill_by_name(next_slug, tenant_id)
            if not next_skill:
                continue
            chain_result = self.execute_chain(next_skill.name, current_inputs, tenant_id, depth + 1)
            chain_results.append(chain_result)
            if "error" in chain_result:
                break
            current_inputs = chain_result.get("result", {})
            if not isinstance(current_inputs, dict):
                current_inputs = {"previous_result": current_inputs}

        return {
            "success": True,
            "skill": name,
            "result": chain_results[-1].get("result"),
            "chain": [r.get("skill") for r in chain_results],
        }

    # --- GitHub Import (updated for community tier) ---

    def import_from_github(self, repo_url: str, github_token: Optional[str] = None) -> dict:
        """Import skill(s) from a GitHub repo into community tier."""
        owner, repo, branch, path = self._parse_github_url(repo_url)
        if not owner or not repo:
            return {"error": f"Could not parse GitHub URL: {repo_url}"}

        headers = {"Accept": "application/vnd.github+json"}
        if github_token:
            headers["Authorization"] = f"Bearer {github_token}"

        try:
            with httpx.Client(timeout=30.0) as client:
                if not branch:
                    repo_resp = client.get(f"https://api.github.com/repos/{owner}/{repo}", headers=headers)
                    if repo_resp.status_code != 200:
                        return {"error": f"Failed to access repo: HTTP {repo_resp.status_code}"}
                    branch = repo_resp.json().get("default_branch", "main")

                api_path = f"https://api.github.com/repos/{owner}/{repo}/contents/{path}"
                resp = client.get(api_path, headers=headers, params={"ref": branch})
                if resp.status_code != 200:
                    return {"error": f"Failed to read repo contents: HTTP {resp.status_code}"}

                contents = resp.json()
                if isinstance(contents, list):
                    file_names_lower = {f["name"].lower() for f in contents if f["type"] == "file"}
                    if "skill.md" in file_names_lower:
                        return self._import_single_skill(client, headers, owner, repo, branch, path, contents)

                    imported, errors = [], []
                    for subdir in [f for f in contents if f["type"] == "dir"]:
                        sub_resp = client.get(
                            f"https://api.github.com/repos/{owner}/{repo}/contents/{subdir['path']}",
                            headers=headers, params={"ref": branch},
                        )
                        if sub_resp.status_code != 200:
                            continue
                        sub_contents = sub_resp.json()
                        sub_names_lower = {f["name"].lower() for f in sub_contents if f["type"] == "file"}
                        if "skill.md" in sub_names_lower:
                            result = self._import_single_skill(client, headers, owner, repo, branch, subdir["path"], sub_contents)
                            if "error" in result:
                                errors.append(result["error"])
                            elif "skill" in result:
                                imported.append(result["skill"].name)

                    if not imported and not errors:
                        return {"error": "No skills found in repository."}
                    return {"imported": imported, "errors": errors, "source": f"{owner}/{repo}"}
                else:
                    return {"error": "Expected a directory, got a file."}
        except httpx.TimeoutException:
            return {"error": "GitHub API request timed out."}
        except Exception as e:
            logger.exception("GitHub import failed: %s", e)
            return {"error": f"Import failed: {str(e)}"}

    def _import_single_skill(self, client, headers, owner, repo, branch, path, contents) -> dict:
        """Download skill files into community directory."""
        files: Dict[str, str] = {}
        for f in contents:
            if f["type"] != "file":
                continue
            raw_resp = client.get(f["download_url"])
            if raw_resp.status_code == 200:
                files[f["name"]] = raw_resp.text

        # Find skill.md case-insensitively (supports SKILL.md from GWS etc.)
        skill_md_key = None
        for key in files:
            if key.lower() == "skill.md":
                skill_md_key = key
                break
        if not skill_md_key:
            return {"error": f"No skill.md in {path}"}

        # Normalize filename to lowercase for our system
        content = files[skill_md_key]
        if skill_md_key != "skill.md":
            files["skill.md"] = content
            del files[skill_md_key]
        if not content.startswith("---"):
            return {"error": f"skill.md in {path} has no YAML frontmatter"}
        parts = content.split("---", 2)
        if len(parts) < 3:
            return {"error": f"Malformed skill.md in {path}"}

        metadata = yaml.safe_load(parts[1].strip())
        # Normalize external formats (GWS, etc.) to our schema
        metadata = _normalize_external_metadata(metadata)
        skill_name = metadata.get("name", "")
        if not skill_name:
            return {"error": f"No name in {path}"}

        slug = re.sub(r'[^a-z0-9]+', '_', skill_name.lower()).strip('_')
        skill_dir = self._community_dir() / slug
        if skill_dir.exists():
            return {"error": f"Community skill '{slug}' already exists."}

        try:
            skill_dir.mkdir(parents=True, exist_ok=True)
            for filename, file_content in files.items():
                (skill_dir / filename).write_text(file_content, encoding="utf-8")

            # Inject source_repo into frontmatter
            metadata["source_repo"] = f"https://github.com/{owner}/{repo}"
            body = parts[2].strip() if len(parts) > 2 else ""
            md_content = "---\n" + yaml.dump(metadata, default_flow_style=False) + "---\n\n" + body + "\n"
            (skill_dir / "skill.md").write_text(md_content, encoding="utf-8")

            if metadata.get("engine") == "shell":
                script_path = metadata.get("script_path", "script.sh")
                script_file = skill_dir / script_path
                if script_file.exists():
                    os.chmod(script_file, 0o755)

            self.scan()
            created = self.get_skill_by_slug(slug)
            if created:
                return {"skill": created}
            return {"error": "Files downloaded but skill failed to load."}
        except Exception as e:
            if skill_dir.exists():
                shutil.rmtree(skill_dir, ignore_errors=True)
            return {"error": f"Failed to write skill files: {str(e)}"}

    @staticmethod
    def _parse_github_url(url: str):
        url = url.strip().rstrip("/")
        m = re.match(r'https?://github\.com/([^/]+)/([^/]+)(?:/tree/([^/]+)(?:/(.*))?)?', url)
        if m:
            return m.group(1), m.group(2), m.group(3), m.group(4) or ""
        parts = url.split("/")
        if len(parts) >= 2:
            return parts[0], parts[1], None, "/".join(parts[2:]) if len(parts) > 2 else ""
        return None, None, None, ""


# Module-level singleton
skill_manager = SkillManager.get_instance()
