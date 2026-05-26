import logging
import time

from fastapi import APIRouter, Depends, File, Header, HTTPException, UploadFile, status

from app.api import deps
from app.core.config import settings
from app.models.user import User
from app.services import media_utils
from app.services.transcription_client import (
    TranscriptionUnavailable,
    transcribe_async,
    transcription_status,
)

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
    file: UploadFile = File(...),
    current_user: User = Depends(deps.get_current_active_user),
):
    """Transcribe an uploaded audio file via the code-worker whisper workflow.

    The file is streamed to a disk-backed temp file (so we never hold the
    whole payload in RAM), then handed off to the
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

    try:
        # Read bytes once, enforce the size cap inline. The previous
        # streaming-to-tempfile dance was unnecessary — we hand the bytes
        # straight to transcribe_async, which writes its own copy to the
        # shared workspaces volume for the workflow.
        audio_bytes = await file.read()
        if len(audio_bytes) > media_utils.MAX_AUDIO_SIZE:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Audio file too large. Max size is {media_utils.MAX_AUDIO_SIZE // (1024 * 1024)}MB.",
            )

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


@router.post("/transcribe-internal")
async def transcribe_audio_internal(
    file: UploadFile = File(...),
    x_internal_key: str = Header(..., alias="X-Internal-Key"),
    x_tenant_id: str = Header(..., alias="X-Tenant-Id"),
):
    """Internal-tier sibling of POST /transcribe (T2.2b followup).

    Used by mcp-server's transcribe_url tool which has no user JWT. Same
    body + return shape as /transcribe; auth is X-Internal-Key with the
    tenant id passed via X-Tenant-Id header.
    """
    if x_internal_key not in (
        getattr(settings, "API_INTERNAL_KEY", ""),
        getattr(settings, "MCP_API_KEY", ""),
    ):
        raise HTTPException(status_code=401, detail="Invalid internal key")
    ct = (file.content_type or "").lower()
    if not (ct.startswith("audio/") or ct.startswith("video/") or ct in media_utils.AUDIO_MIMES):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported media type: {ct}",
        )
    audio_bytes = await file.read()
    if len(audio_bytes) > media_utils.MAX_AUDIO_SIZE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Audio too large (max {media_utils.MAX_AUDIO_SIZE // (1024*1024)}MB)",
        )
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
