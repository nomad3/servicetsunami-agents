"""Code-worker side of the skill-creator Phase 2 eval runner.

This module exists so the artifact-writer contract is reachable from
both the API process (``apps/api/app/services/skill_creator/eval_runner.py``)
AND the code-worker process. The two processes ship in separate
container images with separate ``sys.path`` roots; a single ``import``
won't cross the boundary. The runner's lazy import does::

    try:
        from skill_eval_executor import write_run_artifacts
    except Exception:
        from app.services.skill_creator._artifact_writer import write_run_artifacts

That fallback means anywhere the code-worker root is on sys.path
(local dev, docker-compose where both processes share the same repo
checkout) the worker-side module is used; the API container falls back
to the in-package twin. The two files MUST stay in sync byte-for-byte
on the public surface (``write_run_artifacts`` signature + return shape).

Phase 7 (packaging) will collapse the duplication into a shared
``skill_creator_shared`` distributable. For now both files are short
and self-contained.

Beyond ``write_run_artifacts``, this module exposes ``run_eval_subprocess``
which is the actual entrypoint for a future code-worker-native eval
task type. Phase 2 of the plan reuses ``ChatCliWorkflow`` so this
function is currently UNUSED by the eval_runner; it ships now so the
contract is in place for Phase 3 when we add parallel fanout + the
code-worker-native skill_eval activity. Keep it thin — anything that
grows beyond "wrap the chat-CLI invocation, persist to disk" belongs
in a separate Phase-3 module.
"""

from __future__ import annotations

import json
import logging
import mimetypes
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────
# write_run_artifacts — kept in lock-step with apps/api/app/services/
# skill_creator/_artifact_writer.py. See module docstring for the
# duplication rationale.
# ──────────────────────────────────────────────────────────────────────────


def write_run_artifacts(
    *,
    eval_dir: Path,
    transcript: str,
    eval_metadata: Dict[str, Any],
    metrics: Dict[str, Any],
    timing: Dict[str, Any],
) -> Dict[str, Dict[str, Any]]:
    """Write transcript.md / eval_metadata.json / metrics.json / timing.json
    and return the outputs manifest.

    See ``apps/api/app/services/skill_creator/_artifact_writer.py`` for the
    documented contract. This is the byte-for-byte twin so callers from
    the code-worker import root resolve here.
    """
    eval_dir = Path(eval_dir)
    eval_dir.mkdir(parents=True, exist_ok=True)
    (eval_dir / "outputs").mkdir(exist_ok=True)

    (eval_dir / "transcript.md").write_text(transcript or "", encoding="utf-8")
    (eval_dir / "eval_metadata.json").write_text(
        json.dumps(eval_metadata, indent=2, sort_keys=False, default=str),
        encoding="utf-8",
    )
    (eval_dir / "metrics.json").write_text(
        json.dumps(metrics, indent=2, sort_keys=False, default=str),
        encoding="utf-8",
    )
    (eval_dir / "timing.json").write_text(
        json.dumps(timing, indent=2, sort_keys=False, default=str),
        encoding="utf-8",
    )

    return _build_manifest(eval_dir)


def _build_manifest(eval_dir: Path) -> Dict[str, Dict[str, Any]]:
    """path -> {size_bytes, mime} for every non-hidden file under eval_dir.

    Mirrors the twin in ``_artifact_writer.py``. Paths are POSIX,
    relative to ``eval_dir``.
    """
    manifest: Dict[str, Dict[str, Any]] = {}
    for entry in sorted(eval_dir.rglob("*")):
        if not entry.is_file():
            continue
        if entry.name.startswith("."):
            continue
        rel = entry.relative_to(eval_dir).as_posix()
        try:
            size = entry.stat().st_size
        except OSError:
            size = -1
        mime, _ = mimetypes.guess_type(entry.name)
        manifest[rel] = {
            "size_bytes": int(size),
            "mime": mime or "application/octet-stream",
        }
    return manifest


# ──────────────────────────────────────────────────────────────────────────
# run_eval_subprocess — unused by Phase 2 (the API-side runner dispatches
# ChatCliWorkflow directly), but the contract is published now so the
# Phase-3 code-worker-native activity has a stable surface to call.
# ──────────────────────────────────────────────────────────────────────────


def _utc_iso() -> str:
    """RFC 3339 UTC with Z suffix — matches docs/skill-creator/schemas.md."""
    return (
        datetime.now(tz=timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def run_eval_subprocess(
    *,
    eval_dir: Path,
    eval_id: str,
    iteration: int,
    with_skill: bool,
    skill_slug: str,
    prompt: str,
    instruction_md_content: str,
    cli_platform: str,
    model: str,
    cli_invoker: Optional[Callable[..., Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Run a single eval and write its artifacts.

    Phase 2 reuses ``ChatCliWorkflow`` from the API side; this helper is
    a code-worker-native counterpart that the Phase-3 fan-out activity
    will register as a Temporal activity. The default ``cli_invoker``
    raises NotImplementedError so misuse fails loudly; tests inject a
    stub that returns the expected shape::

        {
            "success": bool,
            "response_text": str,
            "error": str | None,
            "metadata": {
                "input_tokens": int,
                "output_tokens": int,
                "model": str,
                "cost": float | None,
            }
        }

    The helper:
      1. Stamps started_at
      2. Calls ``cli_invoker(prompt, instruction_md_content, ...)``
      3. Stamps completed_at + computes timing_ms
      4. Calls ``write_run_artifacts`` with the four canonical payloads
      5. Returns ``{"status", "transcript", "metrics", "timing",
                     "outputs": manifest}`` for the caller to persist.

    Errors from ``cli_invoker`` are caught and surfaced as
    ``status="error"`` with the artifact files still written — that way
    a failed run still has a viewer-renderable directory.
    """
    if cli_invoker is None:
        raise NotImplementedError(
            "run_eval_subprocess called without a cli_invoker — "
            "Phase 2 dispatches ChatCliWorkflow from the API side. "
            "Tests inject a stub; Phase 3 wires the real activity."
        )

    started_at = _utc_iso()
    t0 = time.perf_counter()

    try:
        result = cli_invoker(
            prompt=prompt,
            instruction_md_content=instruction_md_content,
            cli_platform=cli_platform,
            model=model,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "run_eval_subprocess: cli_invoker raised eval_id=%s: %s",
            eval_id, exc,
        )
        result = {
            "success": False,
            "response_text": "",
            "error": f"cli_invoker raised: {exc}",
            "metadata": {},
        }

    timing_ms = int((time.perf_counter() - t0) * 1000)
    completed_at = _utc_iso()

    meta = result.get("metadata") or {}
    input_tokens = int(meta.get("input_tokens") or 0)
    output_tokens = int(meta.get("output_tokens") or 0)
    token_usage = {
        "input": input_tokens,
        "output": output_tokens,
        "total": input_tokens + output_tokens,
    }
    actual_model = str(meta.get("model") or model or "")

    if result.get("success"):
        status = "ok"
        error: Optional[str] = None
    else:
        err = (result.get("error") or "").lower()
        status = "timeout" if ("timeout" in err or "deadline" in err) else "error"
        error = result.get("error") or "cli returned no text"

    eval_metadata = {
        "version": 1,
        "eval_id": eval_id,
        "iteration": iteration,
        "with_skill": with_skill,
        "skill_slug": skill_slug,
        "skill_version": "",
        "model": actual_model,
        "cli_platform": cli_platform,
        "started_at": started_at,
        "completed_at": completed_at,
        "timing_ms": timing_ms,
        "token_usage": token_usage,
        "status": status,
        "error": error,
    }
    metrics_payload = {
        "version": 1,
        "tokens": token_usage,
        "cost": meta.get("cost") or meta.get("cost_usd"),
    }
    timing_payload = {
        "version": 1,
        "started_at": started_at,
        "completed_at": completed_at,
        "timing_ms": timing_ms,
    }

    manifest = write_run_artifacts(
        eval_dir=Path(eval_dir),
        transcript=result.get("response_text") or "",
        eval_metadata=eval_metadata,
        metrics=metrics_payload,
        timing=timing_payload,
    )

    return {
        "status": status,
        "transcript": result.get("response_text") or "",
        "error": error,
        "model": actual_model,
        "token_usage": token_usage,
        "timing_ms": timing_ms,
        "metrics": metrics_payload,
        "timing": timing_payload,
        "outputs": manifest,
        "eval_metadata": eval_metadata,
    }
