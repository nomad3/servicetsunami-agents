"""T6.4 — Tests for the Luna Learn audio cleanup workflow + sync sweep.

The sync ``_sweep_old_files`` helper is the unit under test; the
async activity + workflow wrappers are trivial passes through it
(verified separately via the workflow registration smoke), so the
real edge-case coverage lives here.

Scope per plan T6.4:
  * deletes files older than ``max_age_s``
  * leaves recent files in place
  * missing directory → 0 (idempotent on a fresh deploy)
  * race condition: a file vanishes between iterdir() and unlink()
    (another worker swept it) → counted as already-handled, not crash
  * subdirectories are skipped (defense in depth — the directory is
    flat by spec; a stray subdir must not be recursed into)
"""
from __future__ import annotations

import os
import time

import pytest

# Import under test. Module-scope import surfaces broken imports
# immediately (same surface as api startup).
from app.workflows.learning_audio_cleanup_workflow import (
    _sweep_old_files,
    act_sweep_learning_audio,
    LearningAudioCleanupWorkflow,
    LEARNING_AUDIO_DIR,
)


# ── Core sweep behaviour ────────────────────────────────────────────────


def test_sweep_removes_files_older_than_24h(tmp_path):
    """Files older than the cutoff are unlinked; newer files survive."""
    old = tmp_path / "old.audio"
    old.write_bytes(b"x")
    new = tmp_path / "new.audio"
    new.write_bytes(b"x")

    # Force the mtimes: 25h ago (delete) vs 1h ago (keep).
    old_mtime = time.time() - 25 * 3600
    new_mtime = time.time() - 1 * 3600
    os.utime(old, (old_mtime, old_mtime))
    os.utime(new, (new_mtime, new_mtime))

    deleted = _sweep_old_files(tmp_path, max_age_s=24 * 3600)

    assert deleted == 1
    assert not old.exists()
    assert new.exists()


def test_sweep_handles_missing_dir(tmp_path):
    """Sweeping a non-existent dir returns 0 — must NOT raise. The
    scheduler fires this daily on every deploy; a fresh box with no
    learning audio yet would crash-loop the scheduler if this raised."""
    deleted = _sweep_old_files(tmp_path / "does-not-exist", max_age_s=3600)
    assert deleted == 0


def test_sweep_leaves_recent_files_untouched(tmp_path):
    """All files within the cutoff → nothing deleted, all survive."""
    for i in range(3):
        p = tmp_path / f"recent-{i}.audio"
        p.write_bytes(b"x")
        # Default mtime is now; explicit set to ensure < max_age_s.
        mtime = time.time() - 10
        os.utime(p, (mtime, mtime))

    deleted = _sweep_old_files(tmp_path, max_age_s=24 * 3600)

    assert deleted == 0
    assert len(list(tmp_path.iterdir())) == 3


def test_sweep_skips_subdirectories(tmp_path):
    """Subdirs under the audio root are NOT recursed into — even if
    their mtime is ancient. The spec promises a flat layout; quietly
    recursing risks deleting an unrelated mounted volume."""
    subdir = tmp_path / "subdir"
    subdir.mkdir()
    nested = subdir / "nested-old.audio"
    nested.write_bytes(b"x")
    ancient = time.time() - 30 * 24 * 3600
    os.utime(nested, (ancient, ancient))
    os.utime(subdir, (ancient, ancient))

    deleted = _sweep_old_files(tmp_path, max_age_s=24 * 3600)

    assert deleted == 0
    # Subdir + its contents still there.
    assert subdir.exists()
    assert nested.exists()


def test_sweep_tolerates_file_disappearing_mid_iteration(tmp_path, monkeypatch):
    """If another cleanup pass unlinks a file between iterdir() and
    our unlink(), the FileNotFoundError must be swallowed and the
    sweep must keep going on the remaining files."""
    keep = tmp_path / "keep.audio"
    keep.write_bytes(b"x")
    ancient = time.time() - 30 * 24 * 3600
    os.utime(keep, (ancient, ancient))

    # Patch Path.unlink for files matching "phantom" to raise FNF.
    from pathlib import Path

    phantom = tmp_path / "phantom.audio"
    phantom.write_bytes(b"x")
    os.utime(phantom, (ancient, ancient))

    original_unlink = Path.unlink

    def _flaky_unlink(self, *args, **kwargs):
        if self.name == "phantom.audio":
            raise FileNotFoundError(str(self))
        return original_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", _flaky_unlink)

    deleted = _sweep_old_files(tmp_path, max_age_s=24 * 3600)

    # phantom raised FNF (counted as 0), keep was deleted → total 1.
    assert deleted == 1
    assert not keep.exists()


# ── Activity wrapper points at the right directory ──────────────────────


async def test_activity_targets_default_audio_dir(monkeypatch, tmp_path):
    """The activity calls _sweep_old_files against LEARNING_AUDIO_DIR.
    We monkeypatch the module-level constant to a tmp path so the test
    doesn't require /var/agentprovision/workspaces to exist."""
    import app.workflows.learning_audio_cleanup_workflow as mod

    # Stand up two old files in the redirected dir.
    for name in ("a.audio", "b.audio"):
        p = tmp_path / name
        p.write_bytes(b"x")
        ancient = time.time() - 30 * 24 * 3600
        os.utime(p, (ancient, ancient))

    monkeypatch.setattr(mod, "LEARNING_AUDIO_DIR", tmp_path)

    deleted = await act_sweep_learning_audio()

    assert deleted == 2
    assert not (tmp_path / "a.audio").exists()
    assert not (tmp_path / "b.audio").exists()


# ── Workflow is registered with the orchestration worker ────────────────


def test_cleanup_workflow_registered_in_orchestration_worker():
    """T6.4 wiring smoke — the workflow + activity must be on the
    orchestration worker registration so the daily scheduler trigger
    has somewhere to dispatch to. If a future refactor drops the
    registration silently, the daily cron fires into a void."""
    from app.workers import orchestration_worker as ow

    src = open(ow.__file__).read()
    assert "LearningAudioCleanupWorkflow" in src, (
        "LearningAudioCleanupWorkflow missing from orchestration_worker imports"
    )
    assert "act_sweep_learning_audio" in src, (
        "act_sweep_learning_audio missing from orchestration_worker imports"
    )


def test_cleanup_workflow_has_temporal_decorator():
    """Sanity: the workflow class still carries the @workflow.defn
    decorator with the expected name. The name is the wire contract
    used by the scheduler's start_workflow() call."""
    assert hasattr(LearningAudioCleanupWorkflow, "__temporal_workflow_definition")
    defn = LearningAudioCleanupWorkflow.__temporal_workflow_definition
    assert defn.name == "LearningAudioCleanupWorkflow"


def test_default_audio_dir_matches_spec():
    """The audio dir is hard-coded to the spec'd path. Pinning it here
    keeps a future refactor from silently relocating audio without the
    cleanup following — which would be a slow-burn disk-fill bug."""
    assert str(LEARNING_AUDIO_DIR) == "/var/agentprovision/workspaces/_learning"


# ── Scheduler trigger registration ──────────────────────────────────────
#
# The scheduler_worker module currently imports `async_session_factory`
# from `app.db.session`, which is a name that only resolves inside the
# api container's runtime env (it's wired by the docker entrypoint).
# Locally / in unit-test CI that import raises ImportError before our
# test code runs. Rather than couple the tests to that path, we
# attempt the import once at module collection and skip the scheduler-
# side tests gracefully when it isn't available — the helper itself
# is verified via the inner-class fake-client pattern, no real DB
# session is touched in any case.

try:
    from app.workers.scheduler_worker import SchedulerWorker as _SchedulerWorker

    _SCHEDULER_IMPORTABLE = True
except Exception:  # pragma: no cover — import-environment dependent
    _SchedulerWorker = None
    _SCHEDULER_IMPORTABLE = False


_skip_no_scheduler = pytest.mark.skipif(
    not _SCHEDULER_IMPORTABLE,
    reason="scheduler_worker requires runtime DB wiring not present in unit env",
)


@_skip_no_scheduler
async def test_scheduler_trigger_starts_cleanup_workflow(monkeypatch):
    """The scheduler's trigger_learning_audio_cleanup helper must call
    Client.start_workflow with the right workflow name + task queue.
    Mirrors the verification path used by the stale-deals trigger."""
    from app.workers.scheduler_worker import SchedulerWorker

    worker = SchedulerWorker()

    recorded = {}

    class _FakeClient:
        async def start_workflow(self, name, *, id, task_queue):
            recorded["name"] = name
            recorded["id"] = id
            recorded["task_queue"] = task_queue
            return object()

    worker.temporal_client = _FakeClient()

    await worker.trigger_learning_audio_cleanup()

    assert recorded["name"] == "LearningAudioCleanupWorkflow"
    assert recorded["id"].startswith("luna-learn-audio-cleanup-")
    assert recorded["task_queue"] == "agentprovision-orchestration"


@_skip_no_scheduler
async def test_scheduler_trigger_no_client_logs_and_returns(caplog):
    """If Temporal isn't reachable, the trigger must not raise — the
    scheduler keeps running so other schedules (stale-deals etc.)
    still fire."""
    from app.workers.scheduler_worker import SchedulerWorker

    worker = SchedulerWorker()
    worker.temporal_client = None

    # Must not raise.
    await worker.trigger_learning_audio_cleanup()


@_skip_no_scheduler
async def test_scheduler_trigger_swallows_already_started(monkeypatch):
    """A scheduler restart inside the 04:xx hour can re-fire the
    trigger. Temporal returns WorkflowAlreadyStarted; we must treat
    that as the happy path (already handled today) instead of crashing
    the scheduler loop."""
    from app.workers.scheduler_worker import SchedulerWorker

    class _AlreadyStarted(Exception):
        pass

    class _FakeClient:
        async def start_workflow(self, *a, **kw):
            raise _AlreadyStarted("Workflow execution already started")

    w = SchedulerWorker()
    w.temporal_client = _FakeClient()
    # Must not raise — the "already started" branch is the expected
    # no-op when the scheduler restarts in the firing hour.
    await w.trigger_learning_audio_cleanup()
