import logging
import os
import tempfile
import time
import uuid as uuidlib

from fastapi import APIRouter, Depends, File, Header, HTTPException, Request, UploadFile, status

from app.api import deps
from app.core.config import settings
from app.models.user import User
from app.services import media_utils
from app.services.transcription_client import (
    TranscriptionUnavailable,
    transcribe_async,
    transcription_status,
)


# ── Upload streaming ───────────────────────────────────────────────────────
#
# BLOCKER2 fix: stream the upload to a tempfile, checking cumulative size
# after each chunk, instead of slurping the whole 200MB into RAM. N
# concurrent uploaders calling `await file.read()` on a 200MB cap = N×200MB
# resident in the api process and we OOM'd. We also do a best-effort
# Content-Length pre-check so a client that *announces* an over-cap upload
# is rejected before we even open a tempfile.
_STREAM_CHUNK_BYTES = 1 * 1024 * 1024  # 1 MiB per read — bounded RAM, low syscall count


def _too_large_response() -> HTTPException:
    cap_mb = media_utils.MAX_AUDIO_SIZE // (1024 * 1024)
    return HTTPException(
        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
        detail=f"Audio too large (max {cap_mb}MB)",
    )


def _reject_oversize_content_length(request: Request) -> None:
    """Cheap pre-check before reading the body. Best-effort — a client
    can lie about Content-Length, the streaming loop below is the real
    enforcement. We still do this so a well-behaved-but-oversize client
    burns zero RAM."""
    cl = request.headers.get("content-length")
    if not cl:
        return
    try:
        declared = int(cl)
    except ValueError:
        return
    if declared > media_utils.MAX_AUDIO_SIZE:
        raise _too_large_response()


async def _stream_upload_to_tempfile(file: UploadFile) -> tuple[str, int]:
    """Drain ``file`` into a tempfile, aborting with 413 once the cap is
    crossed. Returns ``(path, size_bytes)``. Caller MUST unlink the
    returned path when done.

    The tempfile is opened with ``delete=False`` so we control the unlink
    in a try/finally (the transcribe_async helper takes bytes today; we
    read once at the end into a single buffer). When transcribe_async is
    refactored to take a path this function won't need the read step.
    """
    fd, path = tempfile.mkstemp(prefix="upload_", suffix=".bin")
    written = 0
    try:
        with os.fdopen(fd, "wb") as out:
            while True:
                chunk = await file.read(_STREAM_CHUNK_BYTES)
                if not chunk:
                    break
                written += len(chunk)
                if written > media_utils.MAX_AUDIO_SIZE:
                    # Stop reading immediately — don't drain the remaining
                    # bytes into memory or disk just to reject them.
                    raise _too_large_response()
                out.write(chunk)
        return path, written
    except Exception:
        # Drop the tempfile on any failure (including the 413 above).
        try:
            os.unlink(path)
        except OSError:
            pass
        raise

router = APIRouter()
logger = logging.getLogger(__name__)


# ── Tenant-scoped job-id ledger (Redis) ────────────────────────────────────
#
# The POST endpoint may return a workflow id (Temporal job id) that the
# client polls via GET /transcription/{job_id}. Without a tenant binding
# any authenticated user from any tenant could replay that URL and read
# another tenant's transcript. We bind each emitted job_id to the issuing
# tenant in Redis with a 1h TTL so the GET handler can verify ownership.
#
# Best-effort design: a Redis outage degrades to "every authenticated
# user can poll any in-flight job", which is the prior (broken) state but
# never WORSE. We log loudly so the breakage is visible. Same circuit-
# breaker pattern as auth.py:_get_redis_client.

_TRANSCRIBE_JOB_TTL_SECONDS = 60 * 60  # 1h — generous; whisper jobs finish in seconds
_TRANSCRIBE_JOB_KEY_PREFIX = "transcribe:job:"
_REDIS_CIRCUIT_BREAKER_SECONDS = 60

_redis_client = None
_redis_disabled_until: float = 0.0


def _get_redis_client():
    """Return a cached Redis client. None when the breaker is open or
    the client can't be constructed at all. Mirrors auth.py's pattern."""
    global _redis_client, _redis_disabled_until
    if _redis_client is not None:
        return _redis_client
    if _redis_disabled_until and time.monotonic() < _redis_disabled_until:
        return None
    try:
        import redis as _redis
        _redis_client = _redis.from_url(
            settings.REDIS_URL, decode_responses=True, socket_timeout=2
        )
    except Exception as exc:
        logger.warning(
            "transcribe: redis client unavailable, tenant-scoped job ledger disabled: %s",
            exc,
        )
        _redis_client = None
        _redis_disabled_until = time.monotonic() + _REDIS_CIRCUIT_BREAKER_SECONDS
    return _redis_client


def _record_job_tenant(job_id: str, tenant_id: str) -> None:
    """Bind ``job_id`` to ``tenant_id`` in Redis with TTL.

    Best-effort: a Redis miss leaves the binding absent, which the GET
    handler treats as 404 (safer to fail closed for cross-tenant safety
    than fail open). We log a warning so an op can spot it.
    """
    client = _get_redis_client()
    if client is None:
        logger.warning(
            "transcribe: no Redis client; cannot bind job %s to tenant %s",
            job_id, tenant_id,
        )
        return
    try:
        client.set(
            f"{_TRANSCRIBE_JOB_KEY_PREFIX}{job_id}",
            tenant_id,
            ex=_TRANSCRIBE_JOB_TTL_SECONDS,
        )
    except Exception:
        logger.warning("transcribe: failed to record job %s for tenant %s", job_id, tenant_id, exc_info=True)


def _verify_job_tenant(job_id: str, tenant_id: str) -> bool:
    """Return True iff ``job_id`` is bound to ``tenant_id``.

    Unknown / missing job (Redis says nothing OR Redis is down OR the
    key expired) → False. The caller MUST turn that into a 404 so we
    never confirm the existence of another tenant's job id.
    """
    client = _get_redis_client()
    if client is None:
        return False
    try:
        owner = client.get(f"{_TRANSCRIBE_JOB_KEY_PREFIX}{job_id}")
    except Exception:
        logger.warning("transcribe: redis lookup failed for job %s", job_id, exc_info=True)
        return False
    return owner is not None and str(owner) == str(tenant_id)


# Sync-wait window for the user-facing endpoint. Keep modest so we never
# tie up the request budget on long clips — clients are expected to fall
# back to polling ``GET /media/transcription/{job_id}`` if we return
# ``status=pending``.
_SYNC_WINDOW_SECONDS = 10.0


@router.post("/transcribe")
async def transcribe_audio(
    request: Request,
    file: UploadFile = File(...),
    current_user: User = Depends(deps.get_current_active_user),
):
    """Transcribe an uploaded audio file via the code-worker whisper workflow.

    The file is streamed to a disk-backed temp file (so we never hold the
    whole payload in RAM — BLOCKER2 fix), then handed off to the
    ``TranscribeAudioWorkflow`` running on the ``agentprovision-code``
    Temporal queue (see ``apps/code-worker/transcription.py``).

    We wait up to ``_SYNC_WINDOW_SECONDS`` for the result so short clips
    return inline like the pre-migration endpoint did. Longer clips
    return a 202 with ``{"status": "pending", "job_id": ...}`` — clients
    poll ``GET /media/transcription/{job_id}`` for the final transcript.
    """
    if not file.content_type.startswith("audio/") and file.content_type not in media_utils.AUDIO_MIMES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported media type: {file.content_type}. Must be audio.",
        )

    # BLOCKER2 — best-effort upfront rejection on declared Content-Length
    # so a well-behaved-but-oversize client never even opens a tempfile.
    _reject_oversize_content_length(request)

    temp_path: str | None = None
    try:
        # BLOCKER2 — drain to disk in 1MiB chunks, aborting at the cap
        # rather than buffering the full 200MB in process memory.
        temp_path, _size = await _stream_upload_to_tempfile(file)
        with open(temp_path, "rb") as fh:
            audio_bytes = fh.read()

        try:
            result = await transcribe_async(
                audio_bytes, sync_timeout=_SYNC_WINDOW_SECONDS
            )
        except TranscriptionUnavailable as exc:
            logger.warning("Transcription service unavailable: %s", exc)
            return {
                "transcript": None,
                "engine": "unavailable",
                "reason": "transcription_service_unavailable",
                "duration_ms": 0,
            }

        # Always bind the workflow id to the requesting tenant so the
        # poll endpoint can verify ownership. We do this for completed
        # jobs too — the client may still hit the GET to re-fetch, and
        # the binding is cheap.
        if result.job_id:
            _record_job_tenant(result.job_id, str(current_user.tenant_id))

        if result.status == "pending":
            # 202 + job_id; web client should poll the status endpoint.
            return {
                "status": "pending",
                "job_id": result.job_id,
                "transcript": None,
                "engine": "pending",
                "duration_ms": 0,
                "poll_url": f"/api/v1/media/transcription/{result.job_id}",
            }

        return {
            "transcript": result.transcript,
            "engine": result.engine,
            "duration_ms": result.duration_ms,
            "job_id": result.job_id,
            "status": "completed",
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Transcription endpoint failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e),
        )
    finally:
        if temp_path:
            try:
                os.unlink(temp_path)
            except OSError:
                pass


def _configured_internal_keys() -> list[str]:
    """Return non-empty configured internal keys (IMPORTANT2 fix).

    Empty-string fallback was the bug: ``getattr(settings, X, "")`` returns
    "" when unset, then ``x_internal_key not in ("", "")`` is False for
    every request whose header happened to also be empty — auth-bypass on
    misconfig. We instead surface a 503 when NEITHER key is set so the
    operator notices, and 401 when the header doesn't match a configured
    non-empty key.
    """
    keys = [
        getattr(settings, "API_INTERNAL_KEY", "") or "",
        getattr(settings, "MCP_API_KEY", "") or "",
    ]
    return [k for k in keys if k]


@router.post("/transcribe-internal")
async def transcribe_audio_internal(
    request: Request,
    file: UploadFile = File(...),
    x_internal_key: str = Header(..., alias="X-Internal-Key"),
    x_tenant_id: str = Header(..., alias="X-Tenant-Id"),
):
    """Internal-tier sibling of POST /transcribe (T2.2b followup).

    Used by mcp-server's transcribe_url tool which has no user JWT. Same
    body + return shape as /transcribe; auth is X-Internal-Key with the
    tenant id passed via X-Tenant-Id header (UUID-validated per
    IMPORTANT1).
    """
    # IMPORTANT2 — reject misconfig (no keys set anywhere) with 503 so the
    # api isn't silently open. Then require the header to match a
    # configured non-empty key.
    configured = _configured_internal_keys()
    if not configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Internal API key not configured",
        )
    if not x_internal_key or x_internal_key not in configured:
        raise HTTPException(status_code=401, detail="Invalid internal key")

    # IMPORTANT1 — UUID-validate the tenant header so the Redis ledger
    # doesn't get poisoned by free-form strings.
    try:
        uuidlib.UUID(x_tenant_id)
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="X-Tenant-Id must be a valid UUID",
        )

    ct = (file.content_type or "").lower()
    if not (ct.startswith("audio/") or ct.startswith("video/") or ct in media_utils.AUDIO_MIMES):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported media type: {ct}",
        )

    # BLOCKER2 — same streaming/cap pattern as /transcribe so a single
    # malicious internal client can't OOM the api by claiming 200MB.
    _reject_oversize_content_length(request)
    temp_path: str | None = None
    try:
        temp_path, _size = await _stream_upload_to_tempfile(file)
        with open(temp_path, "rb") as fh:
            audio_bytes = fh.read()
        try:
            result = await transcribe_async(audio_bytes, sync_timeout=_SYNC_WINDOW_SECONDS)
        except TranscriptionUnavailable as exc:
            logger.warning("Transcription service unavailable: %s", exc)
            return {"transcript": None, "engine": "unavailable", "duration_ms": 0, "reason": str(exc)}
        if result.job_id:
            _record_job_tenant(result.job_id, x_tenant_id)
        if result.status == "pending":
            return {
                "status": "pending",
                "job_id": result.job_id,
                "transcript": None,
                "engine": "pending",
                "duration_ms": 0,
                "poll_url": f"/api/v1/media/transcription/{result.job_id}",
            }
        return {
            "transcript": result.transcript,
            "engine": result.engine,
            "duration_ms": result.duration_ms,
            "job_id": result.job_id,
            "status": "completed",
        }
    finally:
        if temp_path:
            try:
                os.unlink(temp_path)
            except OSError:
                pass


@router.get("/transcription/{job_id}")
async def get_transcription_status(
    job_id: str,
    current_user: User = Depends(deps.get_current_active_user),
):
    """Poll an in-flight transcription job.

    Tenant-scoped: the job_id must have been issued to the caller's
    tenant by ``POST /transcribe``. We use 404 (not 403) for foreign or
    unknown ids to avoid confirming that a given workflow id exists —
    same pattern as ``apps/api/app/api/v1/skill_evals.py``.

    Returns the same shape as ``POST /transcribe`` once the workflow
    finishes (``status=completed``). While running, returns
    ``status=pending`` and the client should retry after a short delay.
    """
    # Tenant-binding check FIRST — never confirm a foreign job exists.
    if not _verify_job_tenant(job_id, str(current_user.tenant_id)):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Transcription job not found",
        )

    try:
        result = await transcription_status(job_id)
    except TranscriptionUnavailable as exc:
        logger.warning("Transcription status check failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Transcription service unavailable",
        )
    except HTTPException:
        raise
    except Exception as exc:
        # An unknown / completed-and-evicted job_id raises Temporal's
        # RpcError here (caught as a bare Exception). Surface that as a
        # 404 rather than a 500 so the client can't distinguish "never
        # existed" from "expired" — and so an authenticated tenant
        # member can't probe other tenants' workflow ids by polling for
        # a 500-vs-404 oracle.
        logger.warning("Transcription status check returned no workflow: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Transcription job not found",
        )

    return {
        "status": result.status,
        "job_id": result.job_id,
        "transcript": result.transcript,
        "engine": result.engine,
        "duration_ms": result.duration_ms,
    }
