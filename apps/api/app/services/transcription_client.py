"""API-side client for the code-worker ``TranscribeAudioWorkflow``.

Phase A of the docker-shrink plan moved whisper + torch into the
code-worker image. This module is the api's shim: it accepts audio bytes
or paths, dispatches the workflow on the ``agentprovision-code`` queue,
and either awaits the transcript inline (sync style, for short clips) or
returns the workflow id so the caller can poll.

Audio bytes ship via the shared ``workspaces`` named volume rather than
the Temporal payload. Both api and code-worker mount it at
``/var/agentprovision/workspaces``; the api writes
``_transcribe/<uuid>.bin`` and passes the path through the workflow
input.

Callers:
  - ``apps/api/app/api/v1/media.py`` — the user-facing browser endpoint.
    Tries a 10 s sync wait, falls back to 202 + job_id if whisper is slow.
  - ``apps/api/app/api/v1/robot.py`` — robot device frames. Sync, short
    timeout.
  - ``apps/api/app/services/whatsapp_service.py`` — inbound voice notes.
    Sync, slightly longer timeout since the bot reply already takes
    several seconds.

Failure modes are surfaced as plain ``None`` (matches the pre-migration
behaviour of ``media_utils.transcribe_audio_bytes``) so the call sites
stay structurally identical. The endpoint surface in ``media.py`` is the
only place that knows about the async-job fallback.
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from dataclasses import dataclass
from typing import Optional

from app.core.config import settings

logger = logging.getLogger(__name__)


# ── Constants ──────────────────────────────────────────────────────────────

# Shared volume between api + code-worker (see docker-compose.yml's
# ``workspaces`` named volume). Code-worker mounts the same path.
_TRANSCRIBE_DIR = "/var/agentprovision/workspaces/_transcribe"

# Temporal queue that the code-worker listens on. Must match
# ``apps/code-worker/worker.py``'s ``TASK_QUEUE``.
_TASK_QUEUE = "agentprovision-code"

# Default sync wait for HTTP callers. 10 s covers the p95 whisper latency
# for the ``base`` model on a typical voice note (≤30 s of audio). The web
# endpoint upgrades to a 202 response above this threshold.
_DEFAULT_SYNC_TIMEOUT_SECONDS = 10.0

# Workflow timeout — bounds the whole job lifetime. 5 minutes matches the
# activity timeout in ``transcription.py``.
_WORKFLOW_EXECUTION_TIMEOUT_SECONDS = 300.0


# ── Errors ─────────────────────────────────────────────────────────────────


class TranscriptionUnavailable(Exception):
    """Raised when the code-worker / Temporal stack is unreachable.

    Distinct from a successful-but-empty transcript (which returns ``None``);
    use this exception to differentiate "the file had no speech" from "we
    couldn't even talk to the worker".
    """


# ── Public API ─────────────────────────────────────────────────────────────


@dataclass
class TranscriptionResult:
    """Shape returned by ``transcribe_async``.

    Mirrors ``TranscribeAudioResult`` in apps/code-worker/transcription.py
    plus the ``job_id`` field for clients that want to poll.
    """

    transcript: Optional[str]
    engine: str
    duration_ms: int
    job_id: str
    status: str  # "completed" | "pending" | "failed"


def _ensure_transcribe_dir() -> str:
    """Create the shared scratch directory on first use."""
    os.makedirs(_TRANSCRIBE_DIR, exist_ok=True)
    return _TRANSCRIBE_DIR


def _write_audio_to_shared_volume(audio_bytes: bytes) -> str:
    """Persist ``audio_bytes`` to the shared volume and return the absolute path.

    The code-worker activity unlinks the file after reading. If the caller's
    request fails before the workflow starts, the file leaks until manually
    cleaned — acceptable given the 25 MB cap on individual uploads.
    """
    _ensure_transcribe_dir()
    path = os.path.join(_TRANSCRIBE_DIR, f"{uuid.uuid4().hex}.bin")
    with open(path, "wb") as fh:
        fh.write(audio_bytes)
    return path


# Singleton Temporal client. Lazy, async-safe (asyncio.Lock guards the
# double-check). The prior try/except NameError variant was dead code
# because _client was bound to None at module scope — every call returned
# None and the downstream `client.start_workflow(...)` exploded with
# AttributeError on NoneType.
_client = None  # type: Optional["temporalio.client.Client"]
_client_lock = asyncio.Lock()


async def _get_client():
    """Connect to Temporal. Cached on first successful call.

    Double-checked locking: the fast path returns the cached client
    without acquiring the lock; concurrent first-callers serialise on
    the lock and only one actually runs ``Client.connect``.
    """
    global _client
    if _client is not None:
        return _client
    async with _client_lock:
        if _client is None:
            from temporalio.client import Client

            _client = await Client.connect(
                settings.TEMPORAL_ADDRESS,  # type: ignore[arg-type]
            )
    return _client


async def _start_workflow(audio_path: str) -> str:
    """Kick off ``TranscribeAudioWorkflow`` and return the workflow id."""
    # Import the dataclass directly from the code-worker module path — both
    # services share the repo root on sys.path inside their respective
    # containers (code-worker via /app, api via the local checkout). We
    # round-trip through a dict so the api doesn't have to depend on the
    # code-worker's runtime layout.
    from temporalio.common import RetryPolicy
    from datetime import timedelta

    client = await _get_client()
    workflow_id = f"transcribe-{uuid.uuid4().hex}"

    # Use string workflow name + plain-dict input so the api doesn't need to
    # import the dataclass definition from apps/code-worker. audio_b64 was
    # removed from the input dataclass — see TranscribeAudioInput docstring.
    payload = {
        "audio_path": audio_path,
        "delete_after": True,
    }

    await client.start_workflow(
        "TranscribeAudioWorkflow",
        payload,
        id=workflow_id,
        task_queue=_TASK_QUEUE,
        execution_timeout=timedelta(seconds=_WORKFLOW_EXECUTION_TIMEOUT_SECONDS),
        retry_policy=RetryPolicy(maximum_attempts=1),
    )
    return workflow_id


async def _await_workflow_result(workflow_id: str, timeout: float) -> Optional[dict]:
    """Poll the workflow handle's ``result()`` up to ``timeout`` seconds.

    Returns the result dict on completion, or ``None`` if the timeout
    elapses with the workflow still running. Re-raises connection errors
    as ``TranscriptionUnavailable``.
    """
    try:
        client = await _get_client()
    except Exception as exc:
        raise TranscriptionUnavailable(f"Temporal unreachable: {exc}") from exc

    handle = client.get_workflow_handle(workflow_id)
    try:
        result = await asyncio.wait_for(handle.result(), timeout=timeout)
        # temporalio returns a dataclass when the type is registered on the
        # api side, or a plain dict otherwise — normalise.
        if isinstance(result, dict):
            return result
        return {
            "transcript": getattr(result, "transcript", None),
            "engine": getattr(result, "engine", "unavailable"),
            "duration_ms": getattr(result, "duration_ms", 0),
        }
    except asyncio.TimeoutError:
        return None


async def transcribe_async(
    audio_bytes: bytes,
    *,
    sync_timeout: float = _DEFAULT_SYNC_TIMEOUT_SECONDS,
) -> TranscriptionResult:
    """Start a transcription job and wait up to ``sync_timeout`` seconds.

    If the job finishes inside the window, returns the transcript with
    ``status="completed"``. Otherwise returns ``status="pending"`` with the
    ``job_id`` so the caller can fall back to a 202 response and let the
    client poll via ``transcription_status``.
    """
    audio_path = _write_audio_to_shared_volume(audio_bytes)
    try:
        workflow_id = await _start_workflow(audio_path)
    except Exception as exc:
        # If we couldn't even hand off, clean up the temp file ourselves.
        from pathlib import Path
        Path(audio_path).unlink(missing_ok=True)
        raise TranscriptionUnavailable(f"Failed to start workflow: {exc}") from exc

    # Wait for the result. Cleanup is idempotent with the activity-side
    # unlink (see transcribe_audio_activity's finally block): both layers
    # use Path.unlink(missing_ok=True) so it doesn't matter which one
    # wins. The api-side unlink here is the safety net for cases where
    # the workflow times out / fails BEFORE the activity runs (e.g. the
    # code-worker is down, the activity hits its start-to-close timeout,
    # or the workflow is cancelled). Without it those files would leak
    # on the shared volume indefinitely.
    try:
        result = await _await_workflow_result(workflow_id, timeout=sync_timeout)
        if result is None:
            # Workflow is still running — DO NOT delete the temp file yet;
            # the activity hasn't read it. Let the activity own cleanup.
            return TranscriptionResult(
                transcript=None,
                engine="pending",
                duration_ms=0,
                job_id=workflow_id,
                status="pending",
            )
        # Workflow completed inside our window — safe to belt-and-braces
        # unlink. The activity already deleted (delete_after=True), so
        # the missing_ok flag is doing the work.
        from pathlib import Path
        Path(audio_path).unlink(missing_ok=True)
        return TranscriptionResult(
            transcript=result.get("transcript"),
            engine=result.get("engine", "unavailable"),
            duration_ms=int(result.get("duration_ms", 0)),
            job_id=workflow_id,
            status="completed",
        )
    except Exception:
        # Workflow raised (cancelled, RpcError, etc) — the activity may
        # never have run. Best-effort unlink so the shared volume doesn't
        # collect orphans on every failure.
        from pathlib import Path
        Path(audio_path).unlink(missing_ok=True)
        raise


async def transcription_status(job_id: str) -> TranscriptionResult:
    """Poll an in-flight transcription job by workflow id.

    Mirrors ``transcribe_async`` but with no fresh upload — used by the
    ``GET /api/v1/media/transcription/{job_id}`` endpoint.
    """
    # Short polling window so the http call doesn't hang; clients should
    # back off and retry if status is still "pending".
    result = await _await_workflow_result(job_id, timeout=2.0)
    if result is None:
        return TranscriptionResult(
            transcript=None,
            engine="pending",
            duration_ms=0,
            job_id=job_id,
            status="pending",
        )
    return TranscriptionResult(
        transcript=result.get("transcript"),
        engine=result.get("engine", "unavailable"),
        duration_ms=int(result.get("duration_ms", 0)),
        job_id=job_id,
        status="completed",
    )


# ── Sync helpers for internal hot paths ────────────────────────────────────
#
# robot.py and whatsapp_service.py call the old ``transcribe_audio_bytes``
# function synchronously from within sync request handlers. We provide
# thin sync wrappers that bridge to the async client via a per-call event
# loop. The contract matches the pre-migration helper: returns ``str`` on
# success, ``None`` on empty / failed transcription.


def transcribe_bytes_sync(
    audio_bytes: bytes,
    *,
    timeout: float = _DEFAULT_SYNC_TIMEOUT_SECONDS,
) -> Optional[str]:
    """Sync wrapper around ``transcribe_async``.

    INTENDED for sync FastAPI handlers ONLY — e.g. ``apps/api/app/api/v1/
    robot.py`` whose ``def`` (not ``async def``) handlers run on the
    FastAPI threadpool and therefore have no running event loop on the
    calling thread.

    DO NOT call from ``async def`` code. Use ``await transcribe_async(...)``
    there — the loop-detected ``ThreadPoolExecutor`` + ``.result(timeout)``
    fallback below blocks the calling event loop for the duration of the
    workflow. Callers in async contexts that need a transcript-or-None
    shape should resolve the transcript themselves and pass it through
    ``build_media_parts(precomputed_transcript=...)``.

    Swallows ``TranscriptionUnavailable`` and returns ``None`` to preserve
    the pre-migration error-tolerant contract — those callers already log
    on None and route to fallback prompts.
    """
    try:
        # Detect a running event loop. If one is running, the caller is
        # async and is misusing this helper — fall back to a worker-thread
        # bridge but log loudly so we catch it in code review.
        try:
            asyncio.get_running_loop()
            in_loop = True
        except RuntimeError:
            in_loop = False

        if in_loop:
            logger.error(
                "transcribe_bytes_sync called from a running event loop — "
                "this blocks the loop. Switch the caller to "
                "`await transcribe_async(...)` instead."
            )
            # Schedule onto a new loop in a worker thread. Kept as a soft
            # fallback so a stray caller doesn't crash, but the log line
            # above is the contract violation.
            import concurrent.futures

            def _run() -> Optional[str]:
                return asyncio.run(_transcribe_bytes_inner(audio_bytes, timeout))

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                return pool.submit(_run).result(timeout=timeout + 5)

        return asyncio.run(_transcribe_bytes_inner(audio_bytes, timeout))
    except TranscriptionUnavailable:
        logger.warning("Transcription unavailable — returning None")
        return None
    except Exception:
        logger.exception("transcribe_bytes_sync failed unexpectedly")
        return None


async def _transcribe_bytes_inner(audio_bytes: bytes, timeout: float) -> Optional[str]:
    result = await transcribe_async(audio_bytes, sync_timeout=timeout)
    return result.transcript
