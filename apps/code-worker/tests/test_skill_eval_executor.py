"""Tests for ``apps/code-worker/skill_eval_executor.py``.

Phase 2 of the skill-creator framework port. The executor's public
surface is two functions:

  * ``write_run_artifacts`` — writes transcript / metadata / metrics /
    timing into ``eval_dir`` and returns the outputs manifest.
  * ``run_eval_subprocess`` — wraps a CLI invocation, captures timing
    + token usage, calls ``write_run_artifacts``.

We do NOT call the real CLI here; the tests inject a stub
``cli_invoker`` so they're deterministic and don't require a Temporal
worker or LLM credentials.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from skill_eval_executor import (
    run_eval_subprocess,
    write_run_artifacts,
)


# ──────────────────────────────────────────────────────────────────────────
# write_run_artifacts
# ──────────────────────────────────────────────────────────────────────────


def test_write_run_artifacts_writes_four_files_and_outputs_dir(tmp_path: Path):
    """The four canonical artifacts must land on disk + outputs/ subdir."""
    eval_dir = tmp_path / "eval-001" / "with-skill"

    manifest = write_run_artifacts(
        eval_dir=eval_dir,
        transcript="# Hello\n\nThe model said hi.\n",
        eval_metadata={
            "version": 1, "eval_id": "eval-001", "iteration": 1,
            "with_skill": True, "skill_slug": "expense-classifier",
            "model": "claude-haiku-4-5", "cli_platform": "claude_code",
            "started_at": "2026-05-19T12:00:00Z",
            "completed_at": "2026-05-19T12:00:08Z",
            "timing_ms": 8000,
            "token_usage": {"input": 100, "output": 50, "total": 150},
            "status": "ok", "error": None,
        },
        metrics={"version": 1, "tokens": {"input": 100, "output": 50, "total": 150}, "cost": 0.001},
        timing={"version": 1, "started_at": "2026-05-19T12:00:00Z",
                "completed_at": "2026-05-19T12:00:08Z", "timing_ms": 8000},
    )

    assert (eval_dir / "transcript.md").exists()
    assert (eval_dir / "eval_metadata.json").exists()
    assert (eval_dir / "metrics.json").exists()
    assert (eval_dir / "timing.json").exists()
    assert (eval_dir / "outputs").is_dir()

    # transcript content preserved verbatim
    assert (eval_dir / "transcript.md").read_text() == "# Hello\n\nThe model said hi.\n"

    # metadata round-trips as JSON
    meta = json.loads((eval_dir / "eval_metadata.json").read_text())
    assert meta["eval_id"] == "eval-001"
    assert meta["status"] == "ok"
    assert meta["token_usage"]["total"] == 150

    # Manifest covers exactly the four files we wrote (outputs/ is an
    # empty dir → no entries from it).
    assert sorted(manifest.keys()) == [
        "eval_metadata.json",
        "metrics.json",
        "timing.json",
        "transcript.md",
    ]
    for entry in manifest.values():
        assert entry["size_bytes"] > 0
        assert entry["mime"]  # mimetypes always returns *something*


def test_write_run_artifacts_is_idempotent(tmp_path: Path):
    """A second call overwrites in place — no FileExistsError."""
    eval_dir = tmp_path / "eval-002" / "baseline"

    common = dict(
        eval_dir=eval_dir,
        eval_metadata={"version": 1, "eval_id": "eval-002", "iteration": 1,
                       "with_skill": False, "skill_slug": "x", "model": "m",
                       "cli_platform": "claude_code",
                       "started_at": "z", "completed_at": "z", "timing_ms": 1,
                       "token_usage": {"input": 0, "output": 0, "total": 0},
                       "status": "ok", "error": None},
        metrics={"version": 1, "tokens": {"input": 0, "output": 0, "total": 0}, "cost": None},
        timing={"version": 1, "started_at": "z", "completed_at": "z", "timing_ms": 1},
    )

    write_run_artifacts(transcript="first", **common)
    write_run_artifacts(transcript="second", **common)

    assert (eval_dir / "transcript.md").read_text() == "second"


def test_write_run_artifacts_manifest_uses_posix_paths(tmp_path: Path):
    """Manifest keys are POSIX (forward-slash) regardless of host OS."""
    eval_dir = tmp_path / "eval-003" / "with-skill"
    write_run_artifacts(
        eval_dir=eval_dir,
        transcript="t",
        eval_metadata={"version": 1, "eval_id": "e", "iteration": 1,
                       "with_skill": True, "skill_slug": "s", "model": "m",
                       "cli_platform": "c", "started_at": "z",
                       "completed_at": "z", "timing_ms": 1,
                       "token_usage": {"input": 0, "output": 0, "total": 0},
                       "status": "ok", "error": None},
        metrics={"version": 1, "tokens": {"input": 0, "output": 0, "total": 0}},
        timing={"version": 1, "started_at": "z", "completed_at": "z", "timing_ms": 1},
    )
    # Drop a nested file under outputs/ to verify recursive walk uses POSIX.
    nested = eval_dir / "outputs" / "subdir" / "result.json"
    nested.parent.mkdir(parents=True, exist_ok=True)
    nested.write_text('{"ok": true}')

    # Recompute by walking; we don't expose the helper, so re-call
    # write_run_artifacts with the same kwargs and assert the nested
    # file shows up.
    manifest = write_run_artifacts(
        eval_dir=eval_dir,
        transcript="t",
        eval_metadata={"version": 1, "eval_id": "e", "iteration": 1,
                       "with_skill": True, "skill_slug": "s", "model": "m",
                       "cli_platform": "c", "started_at": "z",
                       "completed_at": "z", "timing_ms": 1,
                       "token_usage": {"input": 0, "output": 0, "total": 0},
                       "status": "ok", "error": None},
        metrics={"version": 1, "tokens": {"input": 0, "output": 0, "total": 0}},
        timing={"version": 1, "started_at": "z", "completed_at": "z", "timing_ms": 1},
    )
    assert "outputs/subdir/result.json" in manifest


# ──────────────────────────────────────────────────────────────────────────
# run_eval_subprocess
# ──────────────────────────────────────────────────────────────────────────


def _ok_invoker(**kwargs):
    return {
        "success": True,
        "response_text": "Model said: " + kwargs["prompt"],
        "error": None,
        "metadata": {
            "input_tokens": 42,
            "output_tokens": 17,
            "model": "claude-haiku-4-5",
            "cost": 0.0001,
        },
    }


def _failing_invoker(**kwargs):
    return {
        "success": False,
        "response_text": "",
        "error": "anthropic 529: credit balance too low",
        "metadata": {},
    }


def _timeout_invoker(**kwargs):
    return {
        "success": False,
        "response_text": "",
        "error": "deadline exceeded after 25m",
        "metadata": {},
    }


def _raising_invoker(**kwargs):
    raise RuntimeError("boom")


def test_run_eval_subprocess_ok_writes_full_artifact_set(tmp_path: Path):
    eval_dir = tmp_path / "eval-001" / "with-skill"
    result = run_eval_subprocess(
        eval_dir=eval_dir,
        eval_id="eval-001",
        iteration=1,
        with_skill=True,
        skill_slug="expense-classifier",
        prompt="Classify this expense: dinner $40",
        instruction_md_content="# Expense classifier\n...",
        cli_platform="claude_code",
        model="claude-haiku-4-5",
        cli_invoker=_ok_invoker,
    )

    assert result["status"] == "ok"
    assert result["model"] == "claude-haiku-4-5"
    assert result["token_usage"]["total"] == 59
    assert result["timing_ms"] >= 0
    assert "transcript.md" in result["outputs"]

    # Files on disk match the contract
    assert (eval_dir / "transcript.md").exists()
    meta = json.loads((eval_dir / "eval_metadata.json").read_text())
    assert meta["with_skill"] is True
    assert meta["iteration"] == 1
    assert meta["eval_id"] == "eval-001"
    assert meta["skill_slug"] == "expense-classifier"
    assert meta["status"] == "ok"
    assert meta["token_usage"] == {"input": 42, "output": 17, "total": 59}


def test_run_eval_subprocess_error_still_writes_artifacts(tmp_path: Path):
    """A CLI failure must still leave a viewer-renderable directory."""
    eval_dir = tmp_path / "eval-001" / "baseline"
    result = run_eval_subprocess(
        eval_dir=eval_dir,
        eval_id="eval-001",
        iteration=2,
        with_skill=False,
        skill_slug="expense-classifier",
        prompt="x",
        instruction_md_content="",
        cli_platform="claude_code",
        model="claude-haiku-4-5",
        cli_invoker=_failing_invoker,
    )

    assert result["status"] == "error"
    assert "credit balance" in (result["error"] or "")
    # Artifacts still written for the viewer to render.
    assert (eval_dir / "transcript.md").exists()
    assert (eval_dir / "eval_metadata.json").exists()
    meta = json.loads((eval_dir / "eval_metadata.json").read_text())
    assert meta["status"] == "error"


def test_run_eval_subprocess_timeout_status(tmp_path: Path):
    """Substring match on 'deadline'/'timeout' tips the status to timeout."""
    eval_dir = tmp_path / "eval-001" / "baseline"
    result = run_eval_subprocess(
        eval_dir=eval_dir,
        eval_id="eval-001",
        iteration=1,
        with_skill=False,
        skill_slug="s",
        prompt="x",
        instruction_md_content="",
        cli_platform="claude_code",
        model="m",
        cli_invoker=_timeout_invoker,
    )
    assert result["status"] == "timeout"
    meta = json.loads((eval_dir / "eval_metadata.json").read_text())
    assert meta["status"] == "timeout"


def test_run_eval_subprocess_raising_invoker_is_caught(tmp_path: Path):
    """An invoker that raises is surfaced as status='error', not bubbled."""
    eval_dir = tmp_path / "eval-001" / "with-skill"
    result = run_eval_subprocess(
        eval_dir=eval_dir,
        eval_id="eval-001",
        iteration=1,
        with_skill=True,
        skill_slug="s",
        prompt="x",
        instruction_md_content="",
        cli_platform="claude_code",
        model="m",
        cli_invoker=_raising_invoker,
    )
    assert result["status"] == "error"
    assert "boom" in (result["error"] or "")
    assert (eval_dir / "transcript.md").exists()


def test_run_eval_subprocess_without_invoker_raises():
    """Phase 2 doesn't run the real CLI here; misuse should fail loudly."""
    with pytest.raises(NotImplementedError):
        run_eval_subprocess(
            eval_dir=Path("/tmp/x"),
            eval_id="e",
            iteration=1,
            with_skill=True,
            skill_slug="s",
            prompt="p",
            instruction_md_content="",
            cli_platform="claude_code",
            model="m",
            cli_invoker=None,
        )
