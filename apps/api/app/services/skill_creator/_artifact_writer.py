"""Artifact writer shared by the API-side eval_runner and the
code-worker's ``skill_eval_executor`` module.

Phase 2 of the skill-creator framework port writes four files per run:

    <eval_dir>/transcript.md           # full assistant transcript
    <eval_dir>/eval_metadata.json      # eval_metadata.json shape (schemas.md)
    <eval_dir>/metrics.json            # tokens + cost
    <eval_dir>/timing.json             # started/completed/timing_ms
    <eval_dir>/outputs/                # placeholder dir for skill-generated outputs

The shape mirrors Claude Code's skill-creator reference so the
eval-viewer in Phase 4 can be lifted nearly verbatim. Phase 1 already
froze the JSON schemas in ``docs/skill-creator/schemas.md``.

Returns an "outputs manifest" mapping:

    {<rel_path>: {"size_bytes": int, "mime": str}}

which the caller persists into ``skill_eval_runs.outputs``. The manifest
keys are relative to ``eval_dir``; the eval-viewer resolves them through
``GET /api/v1/workspace/file?path=...`` so file bodies live on disk and
the DB row stays light.

This file lives under ``apps/api/`` and the code-worker imports a
mirror copy at ``apps/code-worker/skill_eval_executor.py``. They
share a contract (same return shape, same files written) but stay
on their own import roots — the API and code-worker have separate
``sys.path`` roots in production (different container images) so a
shared package would force a packaging change. Phase 7 (packaging)
is where we'd unify them; for now the duplication is small enough.
"""

from __future__ import annotations

import json
import mimetypes
import os
from pathlib import Path
from typing import Any, Dict


def write_run_artifacts(
    *,
    eval_dir: Path,
    transcript: str,
    eval_metadata: Dict[str, Any],
    metrics: Dict[str, Any],
    timing: Dict[str, Any],
    tenant_root: "Path | None" = None,
) -> Dict[str, Dict[str, Any]]:
    """Write the four canonical artifacts and return the outputs manifest.

    Idempotent: if ``eval_dir`` already exists the files are overwritten
    in place. The ``outputs/`` subdir is created empty (Phase 2 doesn't
    capture skill-generated artifacts beyond the transcript; that lands
    in Phase 3 when the runner parses tool-call file writes out of the
    CLI event stream).

    ``tenant_root``: if provided, ``eval_dir`` is resolved and verified
    to live under it BEFORE any mkdir/write — defence-in-depth against
    a slug or symlink that escapes the tenant volume (B1). The runner
    always passes its tenant root here; legacy callers that omit it
    skip the check and rely on the runner-side ``_assert_path_under_tenant_root``.

    Raises:
        OSError: when the directory can't be created. The caller catches
            this and downgrades the run row's ``workspace_path`` to NULL
            without flipping the status — the transcript still lives in
            ``skill_eval_runs.transcript``.
        ValueError: when ``tenant_root`` is provided and ``eval_dir``
            resolves outside it.
    """
    eval_dir = Path(eval_dir)
    if tenant_root is not None:
        abs_eval = eval_dir.resolve()
        abs_tenant_root = Path(tenant_root).resolve()
        try:
            abs_eval.relative_to(abs_tenant_root)
        except ValueError as exc:
            raise ValueError(
                f"workspace path escapes tenant root: "
                f"path={abs_eval} tenant_root={abs_tenant_root}"
            ) from exc
    eval_dir.mkdir(parents=True, exist_ok=True)
    outputs_subdir = eval_dir / "outputs"
    outputs_subdir.mkdir(exist_ok=True)

    # ── transcript.md ────────────────────────────────────────────────
    transcript_path = eval_dir / "transcript.md"
    transcript_path.write_text(transcript or "", encoding="utf-8")

    # ── eval_metadata.json ───────────────────────────────────────────
    metadata_path = eval_dir / "eval_metadata.json"
    metadata_path.write_text(
        json.dumps(eval_metadata, indent=2, sort_keys=False, default=str),
        encoding="utf-8",
    )

    # ── metrics.json ─────────────────────────────────────────────────
    metrics_path = eval_dir / "metrics.json"
    metrics_path.write_text(
        json.dumps(metrics, indent=2, sort_keys=False, default=str),
        encoding="utf-8",
    )

    # ── timing.json ──────────────────────────────────────────────────
    timing_path = eval_dir / "timing.json"
    timing_path.write_text(
        json.dumps(timing, indent=2, sort_keys=False, default=str),
        encoding="utf-8",
    )

    return _build_manifest(eval_dir)


def _build_manifest(eval_dir: Path) -> Dict[str, Dict[str, Any]]:
    """Walk ``eval_dir`` and return a path -> {size, mime} manifest.

    Paths are POSIX, relative to ``eval_dir`` (matches schemas.md). Hidden
    files (``.`` prefix) are skipped — no metadata sidecars belong in
    the manifest the eval-viewer renders.
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
