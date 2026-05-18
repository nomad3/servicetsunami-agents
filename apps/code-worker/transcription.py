"""Audio transcription — moved here from apps/api per the api-image-diet plan.

The api container used to bundle ``openai-whisper`` + ``torch`` (~2 GB of
CUDA wheels) just to power three caller sites:

  - ``POST /api/v1/media/transcribe`` (user-facing browser uploads)
  - ``apps/api/app/api/v1/robot.py`` (robot device audio frames)
  - ``apps/api/app/services/whatsapp_service.py`` (inbound voice notes)

This module now owns the heavy ML dependency. The api enqueues a
``TranscribeAudioWorkflow`` on the existing ``agentprovision-code``
Temporal queue and either awaits the result synchronously (short clips,
under the api's request budget) or returns a job id for the client to
poll.

Audio bytes are shipped via the shared ``workspaces`` volume rather than
through the Temporal payload (default 2 MB limit, max audio is 25 MB).
The api writes a temp file under ``/var/agentprovision/workspaces/_transcribe/``
and passes the path; the workflow reads, transcribes, deletes.
"""

from __future__ import annotations

import functools
import io
import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import Optional

from temporalio import activity, workflow

logger = logging.getLogger(__name__)


# ── Whisper model cache ────────────────────────────────────────────────────


@functools.lru_cache(maxsize=1)
def _get_whisper_model(model_name: str = "base"):
    """Load and cache the whisper model. First call downloads weights (~140 MB)."""
    import whisper

    return whisper.load_model(model_name)


# ── Core transcription ─────────────────────────────────────────────────────


def _transcribe_from_source(source) -> Optional[str]:
    """Internal helper: soundfile reads `source` (a path or file-like), whisper transcribes.

    `source` must be something soundfile.read accepts (str path, pathlib.Path,
    or file-like).
    """
    try:
        import soundfile as sf

        data, sr = sf.read(source, dtype="float32")
        if len(data.shape) > 1:
            data = data.mean(axis=1)  # stereo → mono

        # Whisper expects 16 kHz — resample if needed
        if sr != 16000:
            try:
                import librosa

                data = librosa.resample(data, orig_sr=sr, target_sr=16000)
            except Exception:
                pass  # proceed anyway; whisper handles other rates reasonably

        model = _get_whisper_model("base")
        result = model.transcribe(data)
        text = (result.get("text") or "").strip()
        return text if text else None
    except Exception:
        logger.exception("Local Whisper transcription failed")
        return None


def transcribe_audio_bytes(audio_bytes: bytes) -> Optional[str]:
    """Transcribe raw audio bytes via Whisper."""
    return _transcribe_from_source(io.BytesIO(audio_bytes))


def transcribe_audio_path(path: str) -> Optional[str]:
    """Transcribe an audio file on disk via Whisper without loading it into memory first.

    Preferred for large uploads — soundfile mmaps the file itself.
    """
    return _transcribe_from_source(path)


# ── Temporal workflow / activity ───────────────────────────────────────────


@dataclass
class TranscribeAudioInput:
    """Input for ``TranscribeAudioWorkflow``.

    ``audio_path`` is the ONLY transport. It points at a file on the
    shared ``workspaces`` volume that both api and code-worker mount at
    ``/var/agentprovision/workspaces``. The api writes the upload there
    (``apps/api/app/services/transcription_client.py``) and the activity
    reads + unlinks it after transcription.

    Inline-bytes transport (the previous ``audio_b64`` field) was
    intentionally removed: Temporal's default payload limit is 2 MB and a
    25 MB audio clip base64-encodes to ~33 MB, which Temporal rejects.
    Keeping the field dormant was a footgun — no caller actually set it,
    and shipping it would have failed at the worker boundary instead of
    at the api request boundary.
    """

    audio_path: str = ""
    # Caller cleanup contract: when True the activity unlinks ``audio_path``
    # after transcription succeeds or fails. Default True because the
    # canonical caller (apps/api/app/services/transcription_client.py) owns
    # the temp file via its own ``with NamedTemporaryFile`` block — the
    # workflow taking ownership avoids leaking files when the api request
    # times out before reading the workflow result.
    delete_after: bool = True


@dataclass
class TranscribeAudioResult:
    """Result for ``TranscribeAudioWorkflow``.

    ``transcript`` is ``None`` when whisper produced an empty/failed result
    (e.g. silent input). ``engine`` is always either "whisper-local" or
    "unavailable" so callers can branch on the latter without re-checking
    imports.
    """

    transcript: Optional[str]
    engine: str  # "whisper-local" | "unavailable"
    duration_ms: int


@activity.defn(name="transcribe_audio")
def transcribe_audio_activity(input: TranscribeAudioInput) -> TranscribeAudioResult:
    """Run whisper on the supplied audio source.

    Sync activity (CPU-bound, ~1–5 s per clip). Temporal runs it in the
    code-worker's thread-pool executor (see ``worker.py``).
    """
    import time

    start = time.time()
    try:
        try:
            import whisper  # noqa: F401

            engine = "whisper-local"
        except ImportError:
            duration_ms = int((time.time() - start) * 1000)
            return TranscribeAudioResult(
                transcript=None, engine="unavailable", duration_ms=duration_ms
            )

        transcript: Optional[str] = None
        if input.audio_path:
            transcript = transcribe_audio_path(input.audio_path)
        else:
            logger.warning("transcribe_audio_activity called with empty input")

        duration_ms = int((time.time() - start) * 1000)
        return TranscribeAudioResult(
            transcript=transcript, engine=engine, duration_ms=duration_ms
        )
    finally:
        # Best-effort cleanup. The api-side ``transcribe_async`` ALSO
        # unlinks on completion + failure for the case where this
        # activity never ran (worker down, start-to-close timeout, etc).
        # Both layers use ``Path.unlink(missing_ok=True)`` so whichever
        # wins, the other is a no-op.
        if input.delete_after and input.audio_path:
            from pathlib import Path
            Path(input.audio_path).unlink(missing_ok=True)


@workflow.defn
class TranscribeAudioWorkflow:
    """Thin workflow wrapper around the activity.

    Exists so the api can address transcription jobs by Temporal workflow
    id (≡ external job id surfaced to web clients) and re-fetch the result
    via ``client.get_workflow_handle(id).result()``. Single activity call;
    no retry loop here because whisper failures are deterministic — they
    won't pass on retry.
    """

    @workflow.run
    async def run(self, input: TranscribeAudioInput) -> TranscribeAudioResult:
        return await workflow.execute_activity(
            transcribe_audio_activity,
            input,
            start_to_close_timeout=timedelta(minutes=5),
            # No RetryPolicy: a failing whisper run is content-deterministic.
        )
