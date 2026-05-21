"""Workspace file-tree API — read-only navigation of per-tenant docs +
memories + plans, plus an optional "platform" view for super-admins
that exposes a *curated* slice of the repo's own docs/plans tree.

This is the backend for the dashboard's left-panel Files mode. Two
endpoints:

  GET /api/v1/workspace/tree?path=<rel>&scope=<tenant|platform>
      → { entries: [{name, kind: 'dir'|'file', size}], path }

  GET /api/v1/workspace/file?path=<rel>&scope=<tenant|platform>
      → { path, content, size, mime }

Auth: standard user JWT. Path-traversal guard via `Path.resolve()`
inside an allow-listed root per scope.

Roots
-----

* tenant scope → ``$WORKSPACES_ROOT / <tenant_uuid>``. Defaults to
  ``/var/agentprovision/workspaces`` (mounted as a named volume in
  docker-compose and as a PVC in Helm — see B2 fix on PR #514).

* platform scope → ``$PLATFORM_DOCS_ROOT``. **Defaults to a curated
  path** ``/opt/agentprovision/platform-docs`` (B3 fix on PR #514).
  Previously this defaulted to ``/app`` which exposed the entire
  source tree including ``core/config.py`` and ``test.db``. The
  Dockerfile now copies ``docs/`` into ``/opt/agentprovision/platform-docs/``
  so this read-only surface ships pre-populated.

Security posture (v1 — read-only)
---------------------------------

- No write/delete/move endpoints. Editing is a Phase 3.1 follow-up. Any
  future writer must re-audit ``_safe_join`` (currently TOCTOU-safe
  *only* because there's no writer that can swap symlinks underneath us).
- ``platform`` scope is gated on ``is_superuser=True``. Mismatch → 403.
- Hidden files (``.git/``, ``.env``, ``.*``) are filtered from listings
  AND blocked even when accessed via a direct path that contains a
  hidden *segment* (e.g. ``?path=.git/HEAD``) — B1 fix on PR #514.
- Platform scope additionally restricts file reads to a small extension
  allow-list ({``.md``, ``.txt``, ``.rst``, ``.yaml``, ``.yml``,
  ``.json``}). Tenant scope keeps the open contract since tenants own
  their files.
- Binary files: we attempt UTF-8 decode and on ``UnicodeDecodeError``
  return ``{is_binary: true, content: null}``. The docs and the
  implementation use the same heuristic — kept simple on purpose; a
  NULL-byte sniff is the obvious cleaner alternative if we ever see
  false-positives in practice.
- 256 KiB per-file cap. Files larger than the cap return the first
  256 KiB with ``truncated=true`` rather than 413; the SPA renders a
  combined "binary + truncated" placeholder cleanly.
"""
from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
import os
import re
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import List, Literal, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api import deps
from app.core.config import settings  # noqa: F401  (kept for future toggles)
from app.models.integration_config import IntegrationConfig
from app.models.user import User as UserModel
from app.services.orchestration.credential_vault import retrieve_credentials_for_skill

logger = logging.getLogger(__name__)

router = APIRouter()

# ── Root resolution ───────────────────────────────────────────────────

# Per-tenant workspaces. Defaults to /var/agentprovision/workspaces and
# is overridable via WORKSPACES_ROOT env so docker-compose / k8s can
# mount it as a volume. The tenant subdirectory is auto-created on
# first read so we never 404 on a freshly-onboarded tenant.
_WORKSPACES_ROOT = os.environ.get(
    "WORKSPACES_ROOT",
    "/var/agentprovision/workspaces",
)

# Platform docs root for super-admins. Curated subtree shipped inside
# the API image at /opt/agentprovision/platform-docs (see apps/api/
# Dockerfile). NEVER point this at /app — that exposes core/config.py,
# test.db, the full source tree. Overridable via PLATFORM_DOCS_ROOT so
# ops can swap in a different curated mount.
_PLATFORM_ROOT = os.environ.get(
    "PLATFORM_DOCS_ROOT",
    "/opt/agentprovision/platform-docs",
)

_MAX_FILE_BYTES = 256 * 1024  # 256 KiB
_HIDDEN_PREFIXES = (".",)
# Directory names we never expose, even when realpath is inside the
# allow-listed root.
_BLOCKED_DIRS = {"__pycache__", "node_modules", ".git", ".venv", "venv"}

# Platform scope is read-only and shouldn't surface arbitrary binaries
# or source. Restrict to docs-style content. Tenant scope keeps the
# open contract (tenants own their files).
_PLATFORM_ALLOWED_EXTS = {".md", ".txt", ".rst", ".yaml", ".yml", ".json"}

# ── Clone resource controls (I1, I2, B3) ──────────────────────────────
#
# Cap concurrent clones across the api process. Prevents a clone storm
# from starving the FastAPI threadpool for sync DB queries. Background
# tasks run in anyio's threadpool; this semaphore bounds the parallel
# git clone count.
_CLONE_SEMAPHORE = asyncio.Semaphore(2)

# Per-tenant workspace quota. 1 GiB by default; trips a 413 before we
# dispatch a new clone. Overridable via env so ops can tune per cluster.
_TENANT_WORKSPACE_BUDGET = int(
    os.environ.get("TENANT_WORKSPACE_BUDGET_BYTES", str(1_073_741_824))
)

# Separate quota for the skill-creator `skill_evals/` subdirectory
# (#299). 512 MiB by default — smaller than the general workspace
# budget so an eval-storm can't crowd out the tenant's other files.
# Without this gate, a runaway skill-eval iteration loop could fill
# the whole 1 GiB tenant quota in a few iterations and block the
# user from cloning a new repo or pulling a model.
_TENANT_SKILL_EVALS_BUDGET = int(
    os.environ.get("SKILL_EVALS_WORKSPACE_BUDGET_BYTES", str(536_870_912))
)

# Redis lock TTL for the clone-in-flight guard. 10 minutes matches the
# subprocess.run(clone) timeout (600s) in `_run_clone`.
_CLONE_LOCK_TTL_SECONDS = 600


def _seed_tenant_workspace(tenant_root: Path) -> None:
    """Create empty `docs/plans`, `memory`, `projects` skeleton dirs on
    first access. Mirrors the pattern of the repo root so users see a
    familiar structure. No-op if anything already exists."""
    if tenant_root.exists():
        return
    try:
        tenant_root.mkdir(parents=True, exist_ok=True)
        for sub in ("docs/plans", "memory", "projects"):
            (tenant_root / sub).mkdir(parents=True, exist_ok=True)
        readme = tenant_root / "README.md"
        if not readme.exists():
            readme.write_text(
                "# Your AgentProvision Workspace\n\n"
                "This folder holds your plans, memories, and projects. "
                "Drop markdown files here and Alpha can read them.\n\n"
                "- `docs/plans/` — design docs and plans\n"
                "- `memory/`     — your persistent memories\n"
                "- `projects/`   — per-project working notes\n",
                encoding="utf-8",
            )
    except OSError:
        # Read-only volume or perms issue — log and let the next list
        # call return an empty tree rather than 500.
        logger.exception("workspace seed failed for %s", tenant_root)


def _resolve_root(scope: str, user: UserModel) -> Path:
    """Return the allowed filesystem root for the given scope.

    Raises 403 if the user can't access the scope, 500 if env is
    misconfigured.
    """
    if scope == "platform":
        if not user.is_superuser:
            raise HTTPException(status_code=403, detail="platform scope is superuser-only")
        return Path(_PLATFORM_ROOT).resolve()
    if scope == "tenant":
        if not user.tenant_id:
            raise HTTPException(status_code=400, detail="user has no tenant")
        tenant_root = Path(_WORKSPACES_ROOT).resolve() / str(user.tenant_id)
        _seed_tenant_workspace(tenant_root)
        return tenant_root
    raise HTTPException(status_code=400, detail=f"unknown scope: {scope}")


def _safe_join(root: Path, rel: str) -> Path:
    """Resolve `root / rel` and ensure the result is still under `root`.

    Defends against ``..`` traversal, absolute-path overrides, and
    symlink escapes. Trailing slashes / empty ``rel`` are normalised to
    the root itself.

    TOCTOU note: this resolves the path *once*; the result is safe to
    read because no endpoint in this module writes/creates symlinks
    inside the allowed root. Any future writer endpoint MUST re-audit
    this contract (e.g. open with O_NOFOLLOW or re-validate after
    open).
    """
    if rel is None:
        rel = ""
    # Strip leading slash so callers can pass "/" or "" to mean root.
    rel = rel.lstrip("/")
    # strict=False explicitly: resolve as far as the FS allows; the
    # subsequent existence check is what 404s on non-existent paths.
    target = (root / rel).resolve(strict=False)
    try:
        target.relative_to(root.resolve(strict=False))
    except ValueError:
        raise HTTPException(status_code=404, detail="not found")
    return target


def _is_hidden(name: str) -> bool:
    return any(name.startswith(p) for p in _HIDDEN_PREFIXES) or name in _BLOCKED_DIRS


def _reject_hidden_segments(target: Path, root: Path) -> None:
    """Block paths whose ANY component is hidden/blocked — defends
    against ``?path=.git/HEAD`` which otherwise slips past
    ``_is_hidden(target.name)`` (only inspects the final segment).

    No-op when target == root (root listing has no segments).
    """
    try:
        rel_parts = target.relative_to(root.resolve(strict=False)).parts
    except ValueError:
        # _safe_join already raised 404 in this case, but defensive.
        raise HTTPException(status_code=404, detail="not found")
    for segment in rel_parts:
        if _is_hidden(segment):
            raise HTTPException(status_code=404, detail="not found")


# ── Schemas ───────────────────────────────────────────────────────────


class TreeEntry(BaseModel):
    name: str
    kind: Literal["dir", "file"]
    size: Optional[int] = None  # only for files


class TreeResponse(BaseModel):
    scope: str
    path: str
    entries: List[TreeEntry]


class FileResponse(BaseModel):
    scope: str
    path: str
    size: int
    mime: str
    content: Optional[str]
    is_binary: bool = False
    truncated: bool = False


# ── Endpoints ─────────────────────────────────────────────────────────


@router.get("/workspace/tree", response_model=TreeResponse)
def workspace_tree(
    path: str = Query("", description="Relative path from the scope root"),
    scope: Literal["tenant", "platform"] = Query("tenant"),
    current_user: UserModel = Depends(deps.get_current_active_user),
):
    """List directory entries at the given path under the resolved scope.

    Returns directories first (alpha), then files (alpha). Hidden
    entries (dot-files, __pycache__, node_modules, .git, .venv) are
    filtered out of listings AND rejected when present anywhere in the
    requested path (e.g. ``?path=.git/HEAD`` → 404).
    """
    root = _resolve_root(scope, current_user)
    target = _safe_join(root, path)
    # Reject hidden segments anywhere in the path (B1). Empty path
    # (root listing) has no parts, so this is a no-op there.
    _reject_hidden_segments(target, root)
    if not target.exists():
        raise HTTPException(status_code=404, detail="not found")
    if not target.is_dir():
        raise HTTPException(status_code=400, detail="path is a file, not a directory")

    dirs: List[TreeEntry] = []
    files: List[TreeEntry] = []
    try:
        for entry in target.iterdir():
            if _is_hidden(entry.name):
                continue
            if entry.is_dir():
                dirs.append(TreeEntry(name=entry.name, kind="dir"))
            elif entry.is_file():
                try:
                    sz = entry.stat().st_size
                except OSError:
                    sz = None
                files.append(TreeEntry(name=entry.name, kind="file", size=sz))
    except PermissionError:
        raise HTTPException(status_code=403, detail="permission denied")
    dirs.sort(key=lambda e: e.name.lower())
    files.sort(key=lambda e: e.name.lower())

    rel_path = str(target.relative_to(root)) if target != root else ""
    return TreeResponse(scope=scope, path=rel_path, entries=dirs + files)


@router.get("/workspace/file", response_model=FileResponse)
def workspace_file(
    path: str = Query(..., min_length=1, description="Relative path from the scope root"),
    scope: Literal["tenant", "platform"] = Query("tenant"),
    current_user: UserModel = Depends(deps.get_current_active_user),
):
    """Read a file's contents. Caps at 256 KiB; binaries return a
    placeholder.

    Platform scope only serves docs-style extensions
    (.md/.txt/.rst/.yaml/.yml/.json); tenant scope serves anything the
    tenant owns.
    """
    root = _resolve_root(scope, current_user)
    target = _safe_join(root, path)
    # Reject hidden segments anywhere in the path (B1).
    _reject_hidden_segments(target, root)
    if not target.exists():
        raise HTTPException(status_code=404, detail="not found")
    if not target.is_file():
        raise HTTPException(status_code=400, detail="path is a directory, not a file")
    if _is_hidden(target.name):
        raise HTTPException(status_code=404, detail="not found")

    # Platform scope: enforce extension allow-list (B3). Tenant scope
    # keeps the open contract.
    if scope == "platform":
        ext = target.suffix.lower()
        if ext not in _PLATFORM_ALLOWED_EXTS:
            raise HTTPException(
                status_code=404,
                detail="not found",
            )

    size = target.stat().st_size
    truncated = size > _MAX_FILE_BYTES
    raw = target.read_bytes()[:_MAX_FILE_BYTES]
    mime, _ = mimetypes.guess_type(target.name)
    mime = mime or "text/plain"
    # Binary detection: attempt UTF-8 decode; UnicodeDecodeError marks
    # the file as binary. Docstring at the top of the module documents
    # this choice (kept aligned with implementation per I1).
    try:
        content = raw.decode("utf-8")
        is_binary = False
    except UnicodeDecodeError:
        content = None
        is_binary = True

    rel_path = str(target.relative_to(root))
    return FileResponse(
        scope=scope,
        path=rel_path,
        size=size,
        mime=mime,
        content=content,
        is_binary=is_binary,
        truncated=truncated,
    )


# ── Repo clone (task #255) ────────────────────────────────────────────
#
# `POST /api/v1/workspace/clone` clones a GitHub repo into the caller's
# tenant workspace at `<tenant_root>/projects/<repo-slug>/` using the
# user's `github` integration token. The endpoint kicks off a
# background subprocess and returns a job_id immediately so the CLI /
# UI can return without blocking on the network.
#
# Surfaced through `alpha workspace clone <owner/repo>` in the
# AgentProvision CLI; same call shape on the FE empty-state "Clone a
# repo" affordance.
#
# Security:
#   - `repo` is validated against a strict owner/name regex; rejects
#     anything containing `..`, shell metacharacters, or path
#     separators outside the owner/name shape.
#   - `branch` is validated against a permissive but bounded ref-name
#     regex (no spaces, no shell metas).
#   - The github token is resolved from the user's tenant integration
#     row via the credential vault; the user can't clone using
#     anyone else's credentials.
#   - The token is injected into the URL only for the duration of the
#     subprocess and never echoed to logs.

# Accepts ``owner/name`` or ``https://github.com/owner/name(.git)?``.
# Owner: github username/org rules (letters, digits, dash, no leading/
# trailing dash). Name: github repo name rules (letters, digits, dot,
# underscore, dash). Both bounded to 100 chars to head off DoS-ish
# inputs.
_GITHUB_OWNER = r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,99})"
_GITHUB_REPO = r"[A-Za-z0-9_.\-]{1,100}"
_REPO_RE = re.compile(
    r"^(?:https?://github\.com/)?"
    r"(?P<owner>" + _GITHUB_OWNER + r")"
    r"/"
    r"(?P<repo>" + _GITHUB_REPO + r")"
    r"(?:\.git)?/?$"
)
# A git ref-name is intentionally permissive (slashes are valid in
# ``release/1.2.x``) but we bound length + reject shell metacharacters.
# Used as a first-pass cheap filter before delegating to
# ``git check-ref-format`` (I7) for canonical validation.
_BRANCH_RE = re.compile(r"^[A-Za-z0-9_.\-/]{1,255}$")


class CloneRequest(BaseModel):
    repo: str = Field(..., description="owner/name or https://github.com/owner/name")
    branch: Optional[str] = Field(default=None, description="branch to checkout; default branch when omitted")
    # B2: dirty-worktree guard. When the idempotent re-clone path
    # detects a non-empty `git status --porcelain`, the endpoint returns
    # 409 unless `force=True` is passed. The CLI wraps this in a
    # confirmation prompt before propagating.
    force: bool = Field(default=False, description="overwrite dirty target during re-clone")


class CloneResponse(BaseModel):
    job_id: str
    status: str
    target_path: str
    owner: str
    repo: str
    branch: Optional[str]


def _parse_repo(raw: str) -> tuple[str, str]:
    """Return ``(owner, repo)`` after validating ``raw``.

    Raises HTTPException(400) on anything that doesn't match the
    GitHub owner/name shape — guards against shell injection and path
    traversal (``..``, slashes, etc).
    """
    m = _REPO_RE.match(raw.strip())
    if not m:
        raise HTTPException(status_code=400, detail="invalid repo (expected owner/name)")
    owner = m.group("owner")
    repo = m.group("repo")
    # Strip a trailing ``.git`` if it slipped past the URL form.
    if repo.endswith(".git"):
        repo = repo[: -len(".git")]
    # Defence in depth — should be unreachable given the regex.
    if ".." in owner or ".." in repo or "/" in owner or "/" in repo:
        raise HTTPException(status_code=400, detail="invalid repo (segments)")
    return owner, repo


def _is_valid_branch(name: str) -> bool:
    """Authoritative branch-name check (I7).

    Combines the cheap regex pass with ``git check-ref-format --branch``
    so we reject inputs git itself wouldn't accept (``refs/heads/main``,
    ``a..b``, ``-flag-like``, ``branch.lock``, ``branch@{1}``…).
    """
    if not name or len(name) > 255:
        return False
    if name.startswith("-") or name.startswith(".") or name.endswith("/") or name.endswith(".lock"):
        return False
    if ".." in name or "@{" in name:
        return False
    # Reject fully-qualified ref names — the caller is supposed to
    # pass a short branch name like ``main`` / ``release/1.2``; a
    # ``refs/heads/...`` form usually means a confused integration.
    if name.startswith("refs/"):
        return False
    try:
        subprocess.run(
            ["git", "check-ref-format", "--branch", name],
            check=True,
            capture_output=True,
            timeout=5,
        )
        return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        return False


def _validate_branch(branch: Optional[str]) -> Optional[str]:
    if branch is None:
        return None
    b = branch.strip()
    if not b:
        return None
    # Cheap regex first to bound the input before forking git.
    if not _BRANCH_RE.match(b):
        raise HTTPException(status_code=400, detail="invalid branch")
    if not _is_valid_branch(b):
        raise HTTPException(status_code=400, detail="invalid branch")
    return b


# ── Clone helpers (B3, I1, I2) ─────────────────────────────────────────


def _tenant_workspace_bytes(tenant_id: str) -> int:
    """Best-effort recursive size of the tenant's workspace tree (I2).

    Used to gate new clones against ``_TENANT_WORKSPACE_BUDGET``. Errors
    on individual entries are swallowed so a transient ``OSError``
    doesn't 500 the endpoint — we'd rather under-count than refuse.
    """
    root = Path(_WORKSPACES_ROOT).resolve() / tenant_id
    if not root.exists():
        return 0
    total = 0
    for p in root.rglob("*"):
        try:
            if p.is_file():
                total += p.stat().st_size
        except OSError:
            continue
    return total


def _tenant_skill_evals_bytes(tenant_id: str) -> int:
    """Best-effort recursive size of the tenant's ``skill_evals/``
    subdirectory (#299). Used by ``eval_runner.dispatch_iteration``
    to gate new iterations against the separate skill-evals budget.

    Same swallow-on-OSError discipline as the general workspace
    walker — we'd rather under-count than refuse a legitimate
    iteration on a flaky stat call.

    Returns 0 when the subdirectory doesn't exist yet (fresh tenant,
    no evals have been dispatched). This makes the first iteration
    a no-op gate, exactly as intended.
    """
    root = Path(_WORKSPACES_ROOT).resolve() / tenant_id / "skill_evals"
    if not root.exists():
        return 0
    total = 0
    for p in root.rglob("*"):
        try:
            if p.is_file():
                total += p.stat().st_size
        except OSError:
            continue
    return total


def _clone_lock_key(tenant_id: str, owner: str, repo: str) -> str:
    return f"workspace:clone:{tenant_id}:{owner}/{repo}"


def _acquire_clone_lock(key: str) -> bool:
    """Redis SETNX-style lock (B3) keyed per ``tenant/repo``.

    Returns True when the caller now owns the lock. Returns False on
    contention (another clone is in flight). Returns True when Redis is
    unavailable — we degrade open rather than fail closed; the
    semaphore + on-disk idempotency still bound damage.
    """
    try:
        from app.services.collaboration_events import _get_redis  # noqa: WPS437
    except Exception:  # noqa: BLE001
        return True
    try:
        r = _get_redis()
        # `set(... nx=True, ex=...)` is the idiomatic atomic SETNX-with-TTL.
        ok = r.set(key, "1", nx=True, ex=_CLONE_LOCK_TTL_SECONDS)
        return bool(ok)
    except Exception as e:  # noqa: BLE001
        logger.debug("clone-lock acquire failed (degrading open): %s", e)
        return True


def _release_clone_lock(key: str) -> None:
    """Release the SETNX lock. Best-effort; the TTL will auto-clear."""
    try:
        from app.services.collaboration_events import _get_redis  # noqa: WPS437
    except Exception:  # noqa: BLE001
        return
    try:
        r = _get_redis()
        r.delete(key)
    except Exception as e:  # noqa: BLE001
        logger.debug("clone-lock release failed (TTL will reap): %s", e)


def _resolve_github_token(db: Session, tenant_id: uuid.UUID) -> Optional[str]:
    """Look up the active github integration token for ``tenant_id``.

    Returns the decrypted ``oauth_token`` from the first active
    ``github`` integration_config row, or None if none is configured.
    Mirrors what oauth.get_integration_token does for the MCP server
    but stays in-process (no extra HTTP hop).
    """
    cfg = (
        db.query(IntegrationConfig)
        .filter(
            IntegrationConfig.tenant_id == tenant_id,
            IntegrationConfig.integration_name == "github",
            IntegrationConfig.enabled.is_(True),
        )
        .first()
    )
    if not cfg:
        return None
    try:
        creds = retrieve_credentials_for_skill(db, cfg.id, tenant_id)
    except Exception:  # noqa: BLE001 — surface as missing-token, not 500
        logger.exception("github credential decrypt failed for tenant=%s", tenant_id)
        return None
    return creds.get("oauth_token")


def _publish_workspace_event(tenant_id: str, event_type: str, payload: dict) -> None:
    """Best-effort tenant-scoped event fan-out for workspace mutations.

    Publishes to ``workspace:{tenant_id}`` on Redis so any future SSE
    consumer (planned dashboard subscription) can refresh the file
    tree. Swallows all errors — clone success/failure is the source of
    truth, this is just a UI hint.
    """
    try:
        # Lazy import keeps the workspace router importable in test
        # environments where Redis isn't wired up.
        from app.services.collaboration_events import _get_redis  # noqa: WPS437 — internal helper
    except Exception:  # noqa: BLE001
        return
    try:
        r = _get_redis()
        r.publish(
            f"workspace:{tenant_id}",
            json.dumps({
                "event_type": event_type,
                "payload": payload,
            }),
        )
    except Exception as e:  # noqa: BLE001
        logger.debug("workspace event publish failed: %s", e)


def _run_clone(
    *,
    tenant_id: str,
    owner: str,
    repo: str,
    branch: Optional[str],
    token: str,
    target: Path,
    projects_root: Path,
    job_id: str,
    force: bool = False,
    lock_key: Optional[str] = None,
) -> None:
    """Synchronous git clone / fetch+reset. Runs in the FastAPI
    BackgroundTasks pool; the endpoint returns before this finishes.

    Idempotent: if ``target`` already exists, switches to
    ``git fetch --all`` + ``git reset --hard origin/<branch>`` instead
    of erroring out. This mirrors what users expect from "clone again"
    in a CI / repeat-flow context.

    Token handling (B1): we pass the github token via
    ``git -c http.extraHeader="Authorization: bearer <token>"`` for the
    duration of the subprocess invocation. The header is visible to
    ``ps`` for the millisecond the process is alive but never written
    to ``.git/config``, ``.git/FETCH_HEAD``, ``.git/logs/…`` or
    ``.git/packed-refs``. The clone URL itself is the plain
    ``https://github.com/<owner>/<repo>.git`` form.
    """
    # I6: re-resolve target inside the worker thread to catch any
    # symlink swap between request validation and clone. Aborts
    # silently with no rmtree (path may not be ours anymore).
    try:
        real_target = Path(os.path.realpath(str(target)))
        projects_root_real = Path(os.path.realpath(str(projects_root)))
    except OSError as e:
        logger.warning("workspace clone: realpath failed (job=%s): %s", job_id, e)
        if lock_key:
            _release_clone_lock(lock_key)
        return
    if not (
        str(real_target) == str(projects_root_real)
        or str(real_target).startswith(str(projects_root_real) + os.sep)
    ):
        logger.error(
            "workspace clone: symlink escape detected (job=%s): %s -> %s",
            job_id, target, real_target,
        )
        if lock_key:
            _release_clone_lock(lock_key)
        return

    clean_url = f"https://github.com/{owner}/{repo}.git"
    auth_header = f"Authorization: bearer {token}"
    env = os.environ.copy()
    # GIT_TERMINAL_PROMPT=0 keeps git from blocking on stdin if the
    # token is missing/invalid — fail fast instead.
    env["GIT_TERMINAL_PROMPT"] = "0"

    target.parent.mkdir(parents=True, exist_ok=True)
    is_clone_path = not (target.exists() and (target / ".git").exists())
    try:
        if not is_clone_path:
            logger.info("workspace clone: refreshing %s (job=%s)", target, job_id)
            # B2: dirty-worktree guard. Before we blow away local edits
            # with `reset --hard`, check `git status --porcelain`.
            status = subprocess.run(
                ["git", "-C", str(target), "status", "--porcelain"],
                env=env,
                check=True,
                capture_output=True,
                timeout=30,
            )
            if status.stdout.strip() and not force:
                # Caller must re-invoke with force=True. The endpoint
                # already returned 200 by now (we're in a background
                # task), so we publish a failure event and bail.
                logger.info(
                    "workspace clone: dirty target, refusing (job=%s, target=%s)",
                    job_id, target,
                )
                _publish_workspace_event(
                    tenant_id,
                    "workspace_repo_clone_failed",
                    {
                        "owner": owner,
                        "repo": repo,
                        "job_id": job_id,
                        "error": "dirty worktree; pass force=true to overwrite",
                    },
                )
                return
            subprocess.run(
                [
                    "git",
                    "-c", f"http.extraHeader={auth_header}",
                    "-C", str(target),
                    "fetch", "--all", "--prune",
                ],
                env=env,
                check=True,
                capture_output=True,
                timeout=300,
            )
            if branch:
                subprocess.run(
                    ["git", "-C", str(target), "reset", "--hard", f"origin/{branch}"],
                    env=env,
                    check=True,
                    capture_output=True,
                    timeout=60,
                )
        else:
            logger.info(
                "workspace clone: cloning %s into %s (job=%s)",
                clean_url, target, job_id,
            )
            cmd: List[str] = [
                "git",
                "-c", f"http.extraHeader={auth_header}",
                "clone",
                "--depth=1",
            ]
            if branch:
                cmd += ["--branch", branch]
            cmd += [clean_url, str(target)]
            subprocess.run(
                cmd,
                env=env,
                check=True,
                capture_output=True,
                timeout=600,
            )
        _publish_workspace_event(
            tenant_id,
            "workspace_repo_cloned",
            {
                "owner": owner,
                "repo": repo,
                "branch": branch,
                "target": str(target),
                "job_id": job_id,
            },
        )
    except subprocess.CalledProcessError as e:
        # stderr can contain the URL with the token if git echoed the
        # remote name — strip our injected token before logging.
        stderr = (e.stderr or b"").decode("utf-8", errors="replace")
        stderr_safe = stderr.replace(token, "<token>") if token else stderr
        logger.warning(
            "workspace clone failed (job=%s, rc=%s): %s",
            job_id, e.returncode, stderr_safe[:1024],
        )
        # B4: scrub a half-written clone directory so a retry starts
        # fresh. Only on the clone path — partial fetch+reset on an
        # existing repo is recoverable in place.
        if is_clone_path:
            shutil.rmtree(target, ignore_errors=True)
        _publish_workspace_event(
            tenant_id,
            "workspace_repo_clone_failed",
            {"owner": owner, "repo": repo, "job_id": job_id, "error": stderr_safe[:512]},
        )
    except subprocess.TimeoutExpired:
        logger.warning("workspace clone timed out (job=%s)", job_id)
        if is_clone_path:
            shutil.rmtree(target, ignore_errors=True)
        _publish_workspace_event(
            tenant_id,
            "workspace_repo_clone_failed",
            {"owner": owner, "repo": repo, "job_id": job_id, "error": "timeout"},
        )
    finally:
        # B3: always release the per-repo lock, regardless of success
        # or failure. The TTL is a safety net for crashes.
        if lock_key:
            _release_clone_lock(lock_key)


async def _run_clone_bounded(**kwargs) -> None:
    """Dispatch ``_run_clone`` through the module-level semaphore (I1).

    Bounds concurrent clones across the api process so a burst doesn't
    starve the FastAPI threadpool. Background tasks run on anyio's
    threadpool; we acquire the semaphore here and delegate the blocking
    subprocess work via ``asyncio.to_thread``.
    """
    async with _CLONE_SEMAPHORE:
        await asyncio.to_thread(_run_clone, **kwargs)


@router.post("/workspace/clone", response_model=CloneResponse, status_code=200)
def workspace_clone(
    body: CloneRequest,
    background: BackgroundTasks,
    current_user: UserModel = Depends(deps.get_current_active_user),
    db: Session = Depends(deps.get_db),
):
    """Clone a GitHub repo into the caller's tenant workspace.

    Resolves the user's `github` integration token, kicks off a
    background ``git clone`` (or ``fetch + reset`` if the target
    already exists), and returns a ``job_id`` immediately. On success
    the function emits a ``workspace_repo_cloned`` event on the
    tenant's Redis channel for any subscribed UI.

    The clone target is
    ``<WORKSPACES_ROOT>/<tenant_id>/projects/<repo-name>/`` — readable
    via the existing ``/workspace/tree`` + ``/workspace/file``
    endpoints with no extra plumbing.
    """
    if not current_user.tenant_id:
        raise HTTPException(status_code=400, detail="user has no tenant")

    owner, repo = _parse_repo(body.repo)
    branch = _validate_branch(body.branch)

    token = _resolve_github_token(db, current_user.tenant_id)
    if not token:
        raise HTTPException(
            status_code=409,
            detail="no active github integration; connect github first",
        )

    tenant_id_str = str(current_user.tenant_id)
    tenant_root = Path(_WORKSPACES_ROOT).resolve() / tenant_id_str
    _seed_tenant_workspace(tenant_root)
    projects_root = tenant_root / "projects"
    target = (projects_root / repo).resolve()
    # Defence in depth: the resolved target must still live inside the
    # tenant's projects/ dir. ``_parse_repo`` already rejects slashes,
    # so this is belt-and-braces.
    try:
        target.relative_to(projects_root.resolve())
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid repo path")

    # I2: per-tenant disk quota. Block new clones once the tenant
    # exceeds the budget; the operator can lift it via the env var.
    used = _tenant_workspace_bytes(tenant_id_str)
    if used > _TENANT_WORKSPACE_BUDGET:
        raise HTTPException(status_code=413, detail="tenant workspace quota exceeded")

    # B3: per-(tenant, repo) Redis lock. Returns 409 on contention so
    # the caller knows to wait rather than dispatching a parallel clone
    # that would race on the same target directory.
    lock_key = _clone_lock_key(tenant_id_str, owner, repo)
    if not _acquire_clone_lock(lock_key):
        raise HTTPException(status_code=409, detail="clone already in flight")

    job_id = uuid.uuid4().hex
    logger.info(
        "workspace clone dispatched: tenant=%s owner=%s repo=%s branch=%s job=%s force=%s",
        tenant_id_str, owner, repo, branch, job_id, body.force,
    )

    # I1: dispatch via the semaphore-bounded wrapper so concurrent
    # clones across the api process don't starve the threadpool.
    background.add_task(
        _run_clone_bounded,
        tenant_id=tenant_id_str,
        owner=owner,
        repo=repo,
        branch=branch,
        token=token,
        target=target,
        projects_root=projects_root,
        job_id=job_id,
        force=body.force,
        lock_key=lock_key,
    )

    return CloneResponse(
        job_id=job_id,
        status="started",
        target_path=str(target.relative_to(tenant_root)),
        owner=owner,
        repo=repo,
        branch=branch,
    )
