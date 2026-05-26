"""LearningAudioCleanupWorkflow — daily sweep of stale learning audio (T6.4).

Spec §1.12 + spec §3 (orphan-row recovery): the Luna Learn pipeline
writes extracted audio files into ``/var/agentprovision/workspaces/_learning``
during the transcription phase. The workflow normally deletes its own
file in the finally-block; mid-flight crashes (worker OOM, container
SIGKILL, ffmpeg segfault) can leave orphan files behind. Storage
pressure compounds quickly because audio extracts are typically
10–50MB each and the workflow runs ad-hoc per user request.

This workflow is registered as a Temporal Schedule firing at 04:00 UTC
daily (see ``app.workers.scheduler_worker``). It sweeps any file in
the learning audio directory whose mtime is older than 24h — a
conservative window that's much longer than the workflow's 60–90s
typical runtime, so we never delete an in-flight extract.

Sync ``_sweep_old_files`` is exported separately so the unit tests
(``tests/test_learning_audio_cleanup.py``) can exercise the deletion
logic without spinning up a Temporal worker. The async
``act_sweep_learning_audio`` activity is the thin Temporal-side
wrapper; the workflow body is a single ``execute_activity`` call so
the workflow itself has no per-tenant state and is safe to register
once globally.
"""
from __future__ import annotations

import logging
import time
from datetime import timedelta
from pathlib import Path

from temporalio import activity, workflow

logger = logging.getLogger(__name__)


# The on-disk root where the extract_media activity drops audio files.
# Centralised here (rather than re-derived from settings) because the
# cleanup workflow runs out of band from the extract path and must
# pick the same dir regardless of which container scheduled it.
LEARNING_AUDIO_DIR = Path("/var/agentprovision/workspaces/_learning")


def _sweep_old_files(directory: Path, max_age_s: int = 24 * 3600) -> int:
    """Delete files older than ``max_age_s`` in ``directory``.

    Returns the count of deleted files. Missing directory → 0 (the
    sweep is idempotent — a fresh deploy with no audio yet must not
    crash the scheduled run). Subdirectories are skipped: the spec
    says audio files are flat under the root, and recursing into
    unknown trees risks deleting something unrelated.

    Per-file errors (e.g. file disappeared between iterdir() and
    unlink() because another worker is cleaning up too) are logged
    and skipped — we never let one bad file abort the whole sweep.
    """
    if not directory.exists():
        return 0
    cutoff = time.time() - max_age_s
    deleted = 0
    for f in directory.iterdir():
        try:
            if f.is_file() and f.stat().st_mtime < cutoff:
                f.unlink()
                deleted += 1
        except FileNotFoundError:
            # Race with another cleanup pass — fine, treat as already-handled.
            continue
        except OSError as exc:
            # Permissions / IO error: log and continue rather than aborting.
            logger.warning("learning audio cleanup: skipping %s: %s", f, exc)
            continue
    return deleted


@activity.defn
async def act_sweep_learning_audio() -> int:
    """Activity wrapper — delete stale audio under LEARNING_AUDIO_DIR."""
    deleted = _sweep_old_files(LEARNING_AUDIO_DIR)
    if deleted:
        logger.info("learning audio cleanup: deleted %d stale file(s)", deleted)
    return deleted


@workflow.defn(name="LearningAudioCleanupWorkflow")
class LearningAudioCleanupWorkflow:
    """Single-activity workflow — invoked by the daily Temporal schedule."""

    @workflow.run
    async def run(self) -> int:
        return await workflow.execute_activity(
            act_sweep_learning_audio,
            start_to_close_timeout=timedelta(minutes=5),
        )
