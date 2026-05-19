"""Eval runner — Phase 2 of the skill-creator framework port.

For an iteration N the runner does, per eval:

1. Insert two ``skill_eval_runs`` rows in status ``queued`` — one
   ``with_skill=TRUE`` leg and one baseline ``with_skill=FALSE`` leg.
   Both rows share an ``iteration_run_id`` UUID so the caller of
   ``POST /skills/{id}/evals/run`` can poll the whole set as one job.
2. Spawn a background worker thread per leg that:
     a. Flips the row to ``running`` and pins ``started_at``.
     b. Dispatches a Temporal ``ChatCliWorkflow`` execution against
        ``apps/code-worker``. For the ``with_skill`` leg the eval prompt
        is prefixed with the skill's ``skill.md`` body so the CLI loads
        it as ``instruction_md_content``; the baseline leg sends the raw
        prompt only.
     c. On result, writes the on-disk artifacts (transcript.md,
        outputs/, metrics.json, timing.json) under
        ``<WORKSPACES_ROOT>/<tenant>/skills/<skill-slug>-workspace/
        iteration-<N>/eval-<eval_id>/<leg>/`` and persists the run row
        (transcript, outputs manifest, metrics, timing, model,
        token_usage, status, completed_at).

We deliberately do NOT use the ``chat_jobs`` async-chat pattern from
PR #570 here. ``chat_jobs.session_id`` is a NOT-NULL FK on
``chat_sessions(id)`` (mig 137) — eval runs have no chat session, only
a (skill_id, iteration) coordinate. The plan doc anticipates this with
the "otherwise the simpler ``skill_eval_runs.status`` machine"
fallback. Phase 4 (eval-viewer in the Den) will add an SSE feed
keyed by ``iteration_run_id``; that's the analog of the chat-job
event-log, not a wholesale reuse.

Dispatch model
--------------

``dispatch_iteration`` is an ``async`` coroutine that awaits
``Client.start_workflow`` directly from the FastAPI request handler,
one call per ``skill_eval_runs`` row. We do NOT spawn daemon threads
any more — that pattern (``threading.Thread(target=runner,
daemon=True).start()`` where ``runner`` calls ``asyncio.run(_go())``)
silently failed under gunicorn workers, leaving rows in ``queued``
with no Temporal workflow ever created (mirrors PR #574's bug).

Temporal continues each ``ChatCliWorkflow`` server-side regardless of
HTTP request lifecycle — that's the whole point of Temporal's
at-least-once delivery. The api process can die after
``start_workflow`` returns and the workflows keep running.

CHANGE FROM PHASE-2 BEHAVIOR: the API process no longer waits for
each ChatCliWorkflow to complete and no longer writes per-leg
artifacts to the workspaces volume or flips ``skill_eval_runs`` to a
terminal status from a worker thread. Those responsibilities move
into a Temporal parent-workflow design (see
``docs/plans/2026-05-19-skill-eval-temporal-parent-pattern-adr.md``).
Until that Phase-3 parent workflow lands, ``skill_eval_runs`` rows
will stay at ``queued`` after dispatch; the ChatCliWorkflow result is
recoverable from Temporal's history if needed. The ``_run_one``
helpers and ``compute_eval_dir`` layout helpers stay in this module
as the future activity bodies for the parent workflow.
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────
# Workspace root resolution
#
# Same env var as `apps/api/app/api/v1/workspace.py` so a tenant's eval
# artifacts live on the same volume as the rest of their workspace.
# Eval workspaces count against the same per-tenant HOME quota
# documented in the design doc's "Workspace volume bloat" risk; that
# cap is enforced by the workspace router on write, not here, so we
# DON'T re-check it from the runner.
# ──────────────────────────────────────────────────────────────────────────


_WORKSPACES_ROOT = os.environ.get(
    "WORKSPACES_ROOT",
    "/var/agentprovision/workspaces",
)


# ──────────────────────────────────────────────────────────────────────────
# Status taxonomy — see migration 140 header for the rationale on keeping
# this Python-side rather than a DB CHECK constraint.
# ──────────────────────────────────────────────────────────────────────────


_VALID_STATUSES = ("queued", "running", "ok", "error", "timeout")
TERMINAL_STATUSES = ("ok", "error", "timeout")


class TenantWorkspaceQuotaExceeded(Exception):
    """Raised by ``dispatch_iteration`` when the tenant is at or over the
    workspace quota enforced by ``apps/api/app/api/v1/workspace.py``.

    Carries the (used_bytes, budget_bytes) so the API layer can return a
    descriptive 413 without re-querying. Same cap as the clone endpoint
    (``_TENANT_WORKSPACE_BUDGET``, default 1 GiB, env-tunable via
    ``TENANT_WORKSPACE_BUDGET_BYTES``).
    """

    def __init__(self, used: int, budget: int) -> None:
        super().__init__(
            f"Tenant workspace quota exceeded: used={used} bytes, "
            f"budget={budget} bytes"
        )
        self.used = used
        self.budget = budget


# ──────────────────────────────────────────────────────────────────────────
# Workspace layout helpers
# ──────────────────────────────────────────────────────────────────────────


def compute_iteration_dir(
    *,
    tenant_id: uuid.UUID,
    skill_slug: str,
    iteration: int,
    workspaces_root: Optional[str] = None,
) -> Path:
    """Return the iteration directory for a given (tenant, skill, N).

    Layout mirrors Claude Code's skill-creator reference:
        <workspaces_root>/<tenant>/skills/<slug>-workspace/iteration-<N>/

    The eval-<id> subdir is appended by ``compute_eval_dir`` below;
    keeping the helpers split lets the Phase 3 aggregator iterate
    ``iteration-<N>/`` without needing an eval_id.
    """
    root = Path(workspaces_root or _WORKSPACES_ROOT).resolve()
    return (
        root
        / str(tenant_id)
        / "skills"
        / f"{skill_slug}-workspace"
        / f"iteration-{iteration}"
    )


def compute_eval_dir(
    *,
    tenant_id: uuid.UUID,
    skill_slug: str,
    iteration: int,
    eval_id: str,
    with_skill: bool,
    workspaces_root: Optional[str] = None,
    iteration_run_id: Optional[uuid.UUID] = None,
) -> Path:
    """Return ``<iteration_dir>/eval-<id>/<iteration_run_id>/<leg>/`` for a single run.

    ``leg`` is ``with-skill`` or ``baseline`` — matches the eval-viewer's
    expected directory split so Phase 4 doesn't have to re-decide.

    ``iteration_run_id`` is inserted between ``eval-<id>/`` and the leg
    so concurrent retries of the same iteration don't clobber each
    other's artifacts. Callers from Phase 2 always pass it (the runner
    mints it once per ``dispatch_iteration`` call); legacy callers that
    omit it fall back to the old flat layout, but that path is only
    reachable from a handful of unit-test fixtures.
    """
    leg = "with-skill" if with_skill else "baseline"
    base = (
        compute_iteration_dir(
            tenant_id=tenant_id,
            skill_slug=skill_slug,
            iteration=iteration,
            workspaces_root=workspaces_root,
        )
        / f"eval-{eval_id}"
    )
    if iteration_run_id is not None:
        return base / str(iteration_run_id) / leg
    return base / leg


# ──────────────────────────────────────────────────────────────────────────
# DB queries
# ──────────────────────────────────────────────────────────────────────────


def _load_skill_row(db: Session, *, skill_id: uuid.UUID) -> Optional[Dict[str, Any]]:
    """Return ``{name, tenant_id}`` for the skill, or None if missing."""
    row = db.execute(
        text("SELECT name, tenant_id FROM skills WHERE id = :id"),
        {"id": str(skill_id)},
    ).fetchone()
    if not row:
        return None
    return {"name": row[0], "tenant_id": str(row[1])}


def _list_evals_for_skill(
    db: Session, *, skill_id: uuid.UUID
) -> List[Dict[str, Any]]:
    """Return all ``skill_evals`` rows for this skill.

    Each row carries the prompt + expectations the Phase-2 runner needs
    to dispatch a paired run.
    """
    rows = db.execute(
        text(
            """
            SELECT id, prompt, expectations
              FROM skill_evals
             WHERE skill_id = :skill_id
             ORDER BY created_at ASC
            """
        ),
        {"skill_id": str(skill_id)},
    ).fetchall()
    return [
        {
            "id": str(r[0]),
            "prompt": r[1] or "",
            "expectations": r[2] or [],
        }
        for r in rows
    ]


def _insert_run_row(
    db: Session,
    *,
    run_id: uuid.UUID,
    eval_id: str,
    iteration: int,
    with_skill: bool,
    iteration_run_id: uuid.UUID,
) -> None:
    """Insert a queued ``skill_eval_runs`` row."""
    db.execute(
        text(
            """
            INSERT INTO skill_eval_runs (
                id, eval_id, iteration, with_skill, status, iteration_run_id
            ) VALUES (
                :id, :eval_id, :iteration, :with_skill, 'queued', :irid
            )
            """
        ),
        {
            "id": str(run_id),
            "eval_id": eval_id,
            "iteration": int(iteration),
            "with_skill": bool(with_skill),
            "irid": str(iteration_run_id),
        },
    )


def _flip_running(db: Session, *, run_id: uuid.UUID) -> None:
    """queued -> running. Pins started_at."""
    db.execute(
        text(
            """
            UPDATE skill_eval_runs
               SET status = 'running',
                   started_at = NOW()
             WHERE id = :id
               AND status = 'queued'
            """
        ),
        {"id": str(run_id)},
    )


def _persist_terminal(
    db: Session,
    *,
    run_id: uuid.UUID,
    status: str,
    transcript: Optional[str],
    outputs_manifest: Optional[Dict[str, Any]],
    metrics: Optional[Dict[str, Any]],
    timing_ms: Optional[int],
    model: Optional[str],
    token_usage: Optional[Dict[str, Any]],
    workspace_path: Optional[str],
    error: Optional[str],
) -> None:
    """Persist the terminal row state. Idempotent on terminal status."""
    if status not in _VALID_STATUSES:
        raise ValueError(f"invalid status {status!r}; allowed={_VALID_STATUSES}")
    db.execute(
        text(
            """
            UPDATE skill_eval_runs
               SET status         = :status,
                   transcript     = :transcript,
                   outputs        = CAST(:outputs AS JSONB),
                   metrics        = CAST(:metrics AS JSONB),
                   timing_ms      = :timing_ms,
                   model          = :model,
                   token_usage    = CAST(:token_usage AS JSONB),
                   workspace_path = :workspace_path,
                   error          = :error,
                   completed_at   = NOW()
             WHERE id = :id
            """
        ),
        {
            "id": str(run_id),
            "status": status,
            "transcript": transcript,
            "outputs": json.dumps(outputs_manifest) if outputs_manifest is not None else None,
            "metrics": json.dumps(metrics) if metrics is not None else None,
            "timing_ms": int(timing_ms) if timing_ms is not None else None,
            "model": model,
            "token_usage": json.dumps(token_usage) if token_usage is not None else None,
            "workspace_path": workspace_path,
            "error": error[:8192] if error else None,
        },
    )


# ──────────────────────────────────────────────────────────────────────────
# Skill body loader
#
# For the with_skill leg we need to inject the skill's `skill.md` body so
# the CLI behaves as if the skill were triggered. skill_manager owns the
# scan; we just look up the FileSkill by slug+tenant and read the file.
# Failure to load the body is non-fatal — we still dispatch the eval, but
# emit a warning so the eval-viewer can flag "ran without instructions".
# ──────────────────────────────────────────────────────────────────────────


def _load_skill_body(*, skill_slug: str, tenant_id: uuid.UUID) -> str:
    """Return the skill.md body for the given slug, or empty string on miss."""
    try:
        from app.services.skill_manager import skill_manager
    except Exception as exc:  # noqa: BLE001
        logger.warning("eval_runner: skill_manager import failed: %s", exc)
        return ""

    try:
        skill = skill_manager.get_skill_by_slug(skill_slug, str(tenant_id))
    except Exception as exc:  # noqa: BLE001
        logger.warning("eval_runner: get_skill_by_slug raised: %s", exc)
        return ""

    if not skill:
        return ""

    try:
        skill_md = Path(skill.skill_dir) / "skill.md"
        if skill_md.exists():
            return skill_md.read_text(encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        logger.warning("eval_runner: failed to read skill.md: %s", exc)
    return ""


# ──────────────────────────────────────────────────────────────────────────
# Temporal dispatch
#
# Identical shape to cli_session_manager._run_workflow but stripped of the
# chat-session bookkeeping (no chat_session_id, no streaming, no MCP config
# generation). Phase 3 will add agent-policy plumbing so the eval runs
# under the same RL-routed CLI the live tenant session uses.
# ──────────────────────────────────────────────────────────────────────────


def _dispatch_chat_cli_workflow(
    *,
    tenant_id: uuid.UUID,
    prompt: str,
    instruction_md_content: str,
    platform: str,
    model: str,
    workflow_id: str,
) -> Dict[str, Any]:
    """Run a ChatCliWorkflow synchronously, blocking until Temporal returns.

    Returns a dict ``{success, response_text, error, metadata}``. The
    25-minute execution timeout is generous — a single eval rarely needs
    more than 60 s, but headroom keeps a flaky CLI from prematurely
    tipping the row into ``error``.

    This helper is no longer on the dispatch hot path (``dispatch_iteration``
    now uses fire-and-forget ``start_workflow`` from the async request
    handler). It survives in this module as the prospective body of the
    per-leg Temporal activity in the Phase-3 parent-workflow design
    (see the eval-temporal-parent-pattern ADR).
    """
    import asyncio
    from dataclasses import dataclass as _dc
    from datetime import timedelta

    from temporalio.client import Client as TemporalClient

    temporal_address = os.environ.get("TEMPORAL_ADDRESS", "temporal:7233")

    @_dc
    class _ChatCliInput:
        platform: str
        message: str
        tenant_id: str
        instruction_md_content: str = ""
        mcp_config: str = ""
        image_b64: str = ""
        image_mime: str = ""
        session_id: str = ""
        model: str = ""
        allowed_tools: str = ""
        chat_session_id: str = ""
        attempt: int = 1

    task_input = _ChatCliInput(
        platform=platform,
        message=prompt,
        tenant_id=str(tenant_id),
        instruction_md_content=instruction_md_content,
        model=model,
    )

    async def _go():
        client = await TemporalClient.connect(temporal_address)
        return await client.execute_workflow(
            "ChatCliWorkflow",
            task_input,
            id=workflow_id,
            task_queue="agentprovision-code",
            execution_timeout=timedelta(minutes=25),
        )

    result = asyncio.run(_go())
    if isinstance(result, dict):
        return {
            "success": bool(result.get("success", False)),
            "response_text": result.get("response_text", "") or "",
            "error": result.get("error"),
            "metadata": result.get("metadata") or {},
        }
    return {
        "success": bool(getattr(result, "success", False)),
        "response_text": getattr(result, "response_text", "") or "",
        "error": getattr(result, "error", None),
        "metadata": getattr(result, "metadata", None) or {},
    }


# ──────────────────────────────────────────────────────────────────────────
# Worker — one per skill_eval_runs row
# ──────────────────────────────────────────────────────────────────────────


def _run_one(
    *,
    run_id: uuid.UUID,
    eval_id: str,
    eval_prompt: str,
    iteration: int,
    with_skill: bool,
    tenant_id: uuid.UUID,
    skill_id: uuid.UUID,
    skill_slug: str,
    skill_body: str,
    platform: str,
    model: str,
    workspaces_root: Optional[str] = None,
    iteration_run_id: Optional[uuid.UUID] = None,
) -> None:
    """Single-leg worker. Opens its own DB session, dispatches the
    workflow, writes artifacts to disk, persists terminal row.

    Errors here NEVER raise — we own a daemon thread; an unhandled
    exception just kills the thread without flipping the row out of
    ``running``. The except-blocks below explicitly mark the row
    ``error`` so the caller can observe the failure.
    """
    from app.db.session import SessionLocal

    # Lazy import — the executor lives in `apps/code-worker/` but exports a
    # pure-Python helper we reuse here for the disk-write shape. Falls back
    # gracefully if the import isn't available (the API and code-worker
    # share the same image in docker-compose but k8s splits them).
    try:
        from skill_eval_executor import write_run_artifacts  # type: ignore
    except Exception:  # noqa: BLE001
        from app.services.skill_creator._artifact_writer import write_run_artifacts  # type: ignore

    eval_dir = compute_eval_dir(
        tenant_id=tenant_id,
        skill_slug=skill_slug,
        iteration=iteration,
        eval_id=eval_id,
        with_skill=with_skill,
        workspaces_root=workspaces_root,
        iteration_run_id=iteration_run_id,
    )

    # B1 — defence-in-depth path containment check. derive_slug should
    # already guarantee this, but a slug bypass / symlink in the volume
    # would otherwise let a write escape the tenant root.
    try:
        _assert_path_under_tenant_root(
            path=eval_dir,
            tenant_id=tenant_id,
            workspaces_root=workspaces_root,
        )
    except ValueError as exc:
        logger.error(
            "eval_runner: refusing run %s — path escape: %s", run_id, exc
        )
        wdb = SessionLocal()
        try:
            _persist_terminal(
                wdb,
                run_id=run_id,
                status="error",
                transcript=None,
                outputs_manifest=None,
                metrics=None,
                timing_ms=None,
                model=model,
                token_usage=None,
                workspace_path=None,
                error=f"workspace path escapes tenant root: {exc}",
            )
            wdb.commit()
        finally:
            wdb.close()
        return

    wdb = SessionLocal()
    try:
        _flip_running(wdb, run_id=run_id)
        wdb.commit()

        instruction_md = skill_body if with_skill else ""
        t0 = time.perf_counter()
        started_at_iso = datetime.now(tz=timezone.utc).isoformat(
            timespec="seconds"
        ).replace("+00:00", "Z")

        try:
            workflow_id = f"skill-eval-{run_id}"
            wf_result = _dispatch_chat_cli_workflow(
                tenant_id=tenant_id,
                prompt=eval_prompt,
                instruction_md_content=instruction_md,
                platform=platform,
                model=model,
                workflow_id=workflow_id,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "eval_runner: workflow dispatch failed run_id=%s: %s",
                run_id, exc,
            )
            wf_result = {
                "success": False,
                "response_text": "",
                "error": f"dispatch failed: {exc}",
                "metadata": {},
            }

        timing_ms = int((time.perf_counter() - t0) * 1000)
        completed_at_iso = datetime.now(tz=timezone.utc).isoformat(
            timespec="seconds"
        ).replace("+00:00", "Z")

        meta = wf_result.get("metadata") or {}
        input_tokens = int(meta.get("input_tokens") or 0)
        output_tokens = int(meta.get("output_tokens") or 0)
        token_usage = {
            "input": input_tokens,
            "output": output_tokens,
            "total": input_tokens + output_tokens,
        }
        actual_model = meta.get("model") or model or ""

        # ── Determine status ─────────────────────────────────────────
        if wf_result.get("success"):
            status = "ok"
            error = None
        else:
            err_text = (wf_result.get("error") or "").lower()
            # We don't have a structured timeout signal from
            # ChatCliWorkflow — Temporal raises WorkflowFailureError
            # which we caught above and surfaced as a generic dispatch
            # error. Phase 3 can plumb a typed timeout class through;
            # for now the substring check is a best-effort.
            if "timeout" in err_text or "deadline" in err_text:
                status = "timeout"
            else:
                status = "error"
            error = wf_result.get("error") or "workflow returned no text"

        # ── Build artifacts ──────────────────────────────────────────
        eval_metadata = {
            "version": 1,
            "eval_id": eval_id,
            "iteration": iteration,
            "with_skill": with_skill,
            "skill_slug": skill_slug,
            "skill_version": "",  # populated from skill_md in Phase 3
            "model": actual_model,
            "cli_platform": platform,
            "started_at": started_at_iso,
            "completed_at": completed_at_iso,
            "timing_ms": timing_ms,
            "token_usage": token_usage,
            "status": status,
            "error": error,
        }
        timing_payload = {
            "version": 1,
            "started_at": started_at_iso,
            "completed_at": completed_at_iso,
            "timing_ms": timing_ms,
        }
        metrics_payload = {
            "version": 1,
            "tokens": token_usage,
            "cost": meta.get("cost") or meta.get("cost_usd"),
        }

        outputs_manifest: Dict[str, Any] = {}
        workspace_path_str: Optional[str] = None
        tenant_root_path = (
            Path(workspaces_root or _WORKSPACES_ROOT).resolve() / str(tenant_id)
        )
        try:
            outputs_manifest = write_run_artifacts(
                eval_dir=eval_dir,
                transcript=wf_result.get("response_text") or "",
                eval_metadata=eval_metadata,
                metrics=metrics_payload,
                timing=timing_payload,
                tenant_root=tenant_root_path,
            )
            workspace_path_str = str(eval_dir)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "eval_runner: artifact write failed run_id=%s: %s",
                run_id, exc,
            )
            # Don't downgrade status — the workflow itself may have
            # succeeded. We just lose the on-disk artifacts; the DB
            # row still carries transcript + metrics.

        _persist_terminal(
            wdb,
            run_id=run_id,
            status=status,
            transcript=wf_result.get("response_text") or "",
            outputs_manifest=outputs_manifest,
            metrics=metrics_payload,
            timing_ms=timing_ms,
            model=actual_model,
            token_usage=token_usage,
            workspace_path=workspace_path_str,
            error=error,
        )
        wdb.commit()
    except Exception as exc:  # noqa: BLE001
        logger.exception("eval_runner: unhandled error in run %s", run_id)
        try:
            wdb.rollback()
            _persist_terminal(
                wdb,
                run_id=run_id,
                status="error",
                transcript=None,
                outputs_manifest=None,
                metrics=None,
                timing_ms=None,
                model=model,
                token_usage=None,
                workspace_path=None,
                error=f"worker crashed: {exc}",
            )
            wdb.commit()
        except Exception:  # noqa: BLE001
            logger.exception(
                "eval_runner: failed to mark run %s as error", run_id,
            )
    finally:
        wdb.close()


# ──────────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────────


async def dispatch_iteration(
    db: Session,
    *,
    skill_id: uuid.UUID,
    iteration: int,
    platform: str = "claude_code",
    model: str = "",
    workspaces_root: Optional[str] = None,
) -> Dict[str, Any]:
    """Kick off all evals for a single iteration of a skill.

    Inserts the paired (with_skill + baseline) ``skill_eval_runs`` rows
    in ``queued`` status, then awaits one ``Client.start_workflow``
    RPC per row to launch a ``ChatCliWorkflow`` on Temporal. Returns:

        {
            "job_id": <iteration_run_id>,
            "run_ids": [<run_id>, ...],
            "iteration": <int>,
            "skill_id": <str>,
        }

    Args:
        db: Request-scoped SQLAlchemy session.
        skill_id: Skill the iteration belongs to.
        iteration: 1-indexed iteration number. The caller is responsible
            for picking N (typically max(iteration) + 1 from prior runs).
        platform: CLI platform to dispatch against (``claude_code`` by
            default). Matches ``_DEFAULT_PRIORITY`` in
            ``cli_platform_resolver.py``.
        model: Optional model slug override. Empty string defers to the
            code-worker's per-platform default.
        workspaces_root: Override of the workspaces root path. Retained
            in the signature so the Phase-3 parent-workflow refactor can
            thread it through to the artifact-write activity.

    Raises:
        ValueError: skill has no evals defined; iteration is < 1.
        LookupError: skill_id not found in the skills table.
        TenantWorkspaceQuotaExceeded: tenant is at/over the per-tenant
            workspace quota cap (I3).
        Exception: Temporal dispatch failure — propagated from
            ``Client.connect`` or ``start_workflow`` so the endpoint
            can turn it into a 503.
    """
    if iteration < 1:
        raise ValueError(f"iteration must be >= 1, got {iteration}")

    skill_row = _load_skill_row(db, skill_id=skill_id)
    if not skill_row:
        raise LookupError(f"skill {skill_id} not found")

    # Skill slug — for the workspace path. We don't yet have a `slug`
    # column on the `skills` table (the FileSkill carries it from the
    # disk-side scan). Falling back to the DB `name` value, lowercased
    # and dashed, mirrors the slug-derivation rule in skill_manager.
    skill_slug = _derive_slug(skill_row["name"])
    tenant_id = uuid.UUID(skill_row["tenant_id"])

    evals = _list_evals_for_skill(db, skill_id=skill_id)
    if not evals:
        raise ValueError(f"skill {skill_id} has no evals defined")

    skill_body = _load_skill_body(skill_slug=skill_slug, tenant_id=tenant_id)
    if not skill_body:
        logger.warning(
            "eval_runner: skill body empty for slug=%s tenant=%s — "
            "with_skill leg will run with no instructions injected",
            skill_slug, tenant_id,
        )

    # I3 — eval artifacts count against the per-tenant workspace quota.
    # Same 1 GiB cap and env knob as the git-clone endpoint; refuse
    # dispatch BEFORE inserting any queued rows so an over-quota tenant
    # doesn't accumulate orphan run rows. The check is best-effort: a
    # transient OSError during the walk under-counts, which is the
    # safer side to err on for an evaluator-internal feature.
    try:
        from app.api.v1.workspace import (
            _TENANT_WORKSPACE_BUDGET,
            _tenant_workspace_bytes,
        )
        used_bytes = _tenant_workspace_bytes(str(tenant_id))
        if used_bytes >= _TENANT_WORKSPACE_BUDGET:
            raise TenantWorkspaceQuotaExceeded(
                used=used_bytes, budget=_TENANT_WORKSPACE_BUDGET
            )
    except TenantWorkspaceQuotaExceeded:
        raise
    except Exception as exc:  # noqa: BLE001
        # Quota module unavailable (test harness, alternate runtime) —
        # log and continue. We'd rather over-allow than 503 every run.
        logger.warning(
            "eval_runner: workspace quota check skipped: %s", exc
        )

    iteration_run_id = uuid.uuid4()
    run_ids: List[str] = []
    plans: List[Tuple[uuid.UUID, str, str, bool]] = []  # (run_id, eval_id, prompt, with_skill)

    for ev in evals:
        for with_skill in (True, False):
            run_id = uuid.uuid4()
            _insert_run_row(
                db,
                run_id=run_id,
                eval_id=ev["id"],
                iteration=iteration,
                with_skill=with_skill,
                iteration_run_id=iteration_run_id,
            )
            run_ids.append(str(run_id))
            plans.append((run_id, ev["id"], ev["prompt"], with_skill))

    db.commit()

    # Temporal-native dispatch: await one start_workflow per leg.
    # Workflows continue server-side regardless of HTTP request
    # lifecycle. We DELIBERATELY do not await each workflow's
    # completion — that's deferred to the Phase-3 parent-workflow
    # design (see the eval-temporal-parent-pattern ADR), which will
    # also restore the artifact-writing + terminal-status flip that
    # the Phase-2 daemon-thread runner did.
    await _start_chat_cli_workflows(
        plans=plans,
        iteration=iteration,
        tenant_id=tenant_id,
        skill_slug=skill_slug,
        skill_body=skill_body,
        platform=platform,
        model=model,
    )

    return {
        "job_id": str(iteration_run_id),
        "run_ids": run_ids,
        "iteration": iteration,
        "skill_id": str(skill_id),
    }


async def _start_chat_cli_workflows(
    *,
    plans: List[Tuple[uuid.UUID, str, str, bool]],
    iteration: int,
    tenant_id: uuid.UUID,
    skill_slug: str,
    skill_body: str,
    platform: str,
    model: str,
) -> None:
    """Await one ``Client.start_workflow`` per planned leg.

    Replaces the Phase-2 ``_spawn_worker_thread`` daemon-thread loop.
    A single ``Client.connect`` is shared across all legs to keep the
    handler latency under the 100ms budget called out in the ADR.

    Test seam: tests can monkeypatch this function on the module to
    skip the real Temporal RPC.
    """
    from dataclasses import dataclass as _dc

    from temporalio.client import Client as TemporalClient

    temporal_address = os.environ.get("TEMPORAL_ADDRESS", "temporal:7233")

    @_dc
    class _ChatCliInput:
        platform: str
        message: str
        tenant_id: str
        instruction_md_content: str = ""
        mcp_config: str = ""
        image_b64: str = ""
        image_mime: str = ""
        session_id: str = ""
        model: str = ""
        allowed_tools: str = ""
        chat_session_id: str = ""
        attempt: int = 1

    client = await TemporalClient.connect(temporal_address)

    for run_id, _eval_id, prompt, with_skill in plans:
        instruction_md = skill_body if with_skill else ""
        task_input = _ChatCliInput(
            platform=platform,
            message=prompt,
            tenant_id=str(tenant_id),
            instruction_md_content=instruction_md,
            model=model,
        )
        await client.start_workflow(
            "ChatCliWorkflow",
            task_input,
            id=f"skill-eval-{run_id}",
            task_queue="agentprovision-code",
        )


def _derive_slug(name: str) -> str:
    """Canonical skill slug — delegates to ``skill_manager.derive_slug``.

    Keeping this thin wrapper avoids drift: a skill named
    ``../../etc`` flows through the same ``re.sub(r"[^a-z0-9]+", "_", …)``
    rule used by ``SkillManager.create_skill``, so the path segment is
    always safe AND it matches what ``get_skill_by_slug`` looks up.

    Raises ``ValueError`` when the input scrubs to empty — the runner
    can't build a workspace path without a slug.
    """
    from app.services.skill_manager import derive_slug as _canonical_derive_slug

    slug = _canonical_derive_slug(name)
    if not slug:
        raise ValueError(f"skill name {name!r} produced empty slug")
    return slug


def _assert_path_under_tenant_root(
    *,
    path: Path,
    tenant_id: uuid.UUID,
    workspaces_root: Optional[str] = None,
) -> None:
    """Defence-in-depth: raise ValueError if ``path`` escapes the tenant
    root after resolution.

    With ``derive_slug`` enforcing ``[a-z0-9_]`` segments this should be
    unreachable, but we keep the check so any future regression (a new
    code path that bypasses the helper, a symlink planted in the
    workspaces volume) trips loudly BEFORE we mkdir/write_text.
    """
    abs_path = path.resolve()
    abs_tenant_root = (
        Path(workspaces_root or _WORKSPACES_ROOT).resolve() / str(tenant_id)
    ).resolve()
    try:
        abs_path.relative_to(abs_tenant_root)
    except ValueError as exc:
        raise ValueError(
            f"workspace path escapes tenant root: "
            f"path={abs_path} tenant_root={abs_tenant_root}"
        ) from exc


def get_iteration_status(
    db: Session,
    *,
    iteration_run_id: uuid.UUID,
    tenant_id: uuid.UUID,
) -> Optional[Dict[str, Any]]:
    """Return the status snapshot for an iteration_run_id, scoped to tenant.

    Returns None if no rows exist for that id OR if the rows belong to
    a different tenant (404-not-403 pattern, same as
    ``chat_jobs.get_job``). Caller turns None into a 404.

    Shape::

        {
            "job_id": str,
            "skill_id": str,
            "iteration": int,
            "runs": [
                {"run_id": str, "eval_id": str, "with_skill": bool,
                 "status": str, "error": str|None,
                 "started_at": str|None, "completed_at": str|None},
                ...
            ],
            "terminal": bool,  # True iff every run is in TERMINAL_STATUSES
        }
    """
    rows = db.execute(
        text(
            """
            SELECT r.id, r.eval_id, r.with_skill, r.status, r.error,
                   r.started_at, r.completed_at, r.iteration,
                   e.skill_id, s.tenant_id
              FROM skill_eval_runs r
              JOIN skill_evals e ON e.id = r.eval_id
              JOIN skills s ON s.id = e.skill_id
             WHERE r.iteration_run_id = :irid
             ORDER BY r.created_at ASC
            """
        ),
        {"irid": str(iteration_run_id)},
    ).fetchall()

    if not rows:
        return None

    # Tenant-scoping: every row must belong to the calling tenant. If
    # ANY row's tenant differs we treat the whole id as not found.
    tenant_str = str(tenant_id)
    if any(str(r[9]) != tenant_str for r in rows):
        return None

    runs = []
    for r in rows:
        runs.append({
            "run_id": str(r[0]),
            "eval_id": str(r[1]),
            "with_skill": bool(r[2]),
            "status": r[3],
            "error": r[4],
            "started_at": r[5].isoformat() if r[5] else None,
            "completed_at": r[6].isoformat() if r[6] else None,
        })

    terminal = all(r["status"] in TERMINAL_STATUSES for r in runs)
    return {
        "job_id": str(iteration_run_id),
        "skill_id": str(rows[0][8]),
        "iteration": int(rows[0][7]),
        "runs": runs,
        "terminal": terminal,
    }
