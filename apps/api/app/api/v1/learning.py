"""Learning experiment and policy candidate API endpoints.

Also hosts the Luna Learn from Media internal endpoints (T4.4b, T4.4c):

  * ``POST /upload-attachment`` — multipart upload + ffprobe duration cap
    + MIME / size enforcement (spec §1.8). Internal-key gated.
  * ``POST /dispatch`` — HTTP wrapper around
    ``LearningService.dispatch()`` (T4.1a). Returns ``{workflow_id}``.
"""

import logging
import os
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.api.v1.skills_new import _verify_internal_key
from app.models.user import User
from app.schemas.learning import LearningIntent
from app.schemas.learning_experiment import (
    LearningExperimentCreate,
    LearningExperimentInDB,
    PolicyCandidateCreate,
    PolicyCandidateInDB,
)
from app.services import learning_experiment_service

logger = logging.getLogger(__name__)

router = APIRouter()


# ─────────────────────────────────────────────────────────────────────
# Luna Learn — internal upload + dispatch surfaces (T4.4b / T4.4c)
# ─────────────────────────────────────────────────────────────────────

_MAX_SIZE_BYTES = 50 * 1024 * 1024  # spec §1.8
_MAX_DURATION_S = 900               # spec §1.8


def _attach_dir() -> Path:
    """Return the on-disk dir for learning attachments.

    Honours ``LUNA_LEARN_ATTACH_DIR`` for tests / dev; falls back to
    ``/var/agentprovision/workspaces/_learning`` in production. If the
    production path isn't writable (e.g. unit-test environment without
    that mount), we fall back to ``$TMPDIR/luna-learn-attachments`` so
    the endpoint stays exercisable in CI.
    """
    env_dir = os.environ.get("LUNA_LEARN_ATTACH_DIR")
    if env_dir:
        return Path(env_dir)
    default = Path("/var/agentprovision/workspaces/_learning")
    try:
        default.mkdir(parents=True, exist_ok=True)
        return default
    except OSError:
        return Path(tempfile.gettempdir()) / "luna-learn-attachments"


def _ffprobe_duration(path: Path) -> int:
    """Return media duration in seconds (rounded). Raises on probe failure.

    Module-level for monkeypatching in tests (ffprobe is not guaranteed in
    CI / dev environments).
    """
    out = subprocess.check_output(
        [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(path),
        ]
    )
    return int(float(out.decode().strip()))


@router.post("/upload-attachment")
async def upload_attachment(
    file: UploadFile = File(...),
    _auth: None = Depends(_verify_internal_key),
):
    """Upload an audio/video attachment for Luna Learn (T4.4b, spec §1.8).

    Enforces ALL spec §1.8 constraints server-side — CLI checks are
    best-effort UX only; the server is the trust boundary:

      * MIME: ``audio/*`` or ``video/*`` only (415 otherwise)
      * Size: <= 50MB (413)
      * Duration: <= 900s via ffprobe (413)

    Returns a path on local disk for the workflow to consume plus a
    sanitised ``source_url`` of the form ``attachment://<basename>``
    (the full local path is never leaked into provenance).
    """
    ct = (file.content_type or "").lower()
    if not (ct.startswith("audio/") or ct.startswith("video/")):
        raise HTTPException(415, f"unsupported MIME type {ct!r}; only audio/* or video/* allowed")

    body = await file.read()
    if len(body) > _MAX_SIZE_BYTES:
        raise HTTPException(413, f"file size {len(body)} exceeds 50MB cap")

    attach_dir = _attach_dir()
    attach_dir.mkdir(parents=True, exist_ok=True)
    safe_basename = Path(file.filename or "upload").name
    dest = attach_dir / f"{uuid.uuid4().hex}-{safe_basename}"
    dest.write_bytes(body)

    try:
        dur = _ffprobe_duration(dest)
    except Exception as e:
        dest.unlink(missing_ok=True)
        raise HTTPException(422, f"could not probe duration: {e}")

    if dur > _MAX_DURATION_S:
        dest.unlink(missing_ok=True)
        raise HTTPException(413, f"duration {dur}s exceeds 900s cap")

    return {
        "attachment_path": str(dest),
        "source_url": f"attachment://{safe_basename}",
        "duration_s": dur,
        "size_bytes": len(body),
    }


@router.post("/dispatch")
async def dispatch_learning(
    intent: LearningIntent,
    _auth: None = Depends(_verify_internal_key),
):
    """Dispatch a ``LearnFromMediaWorkflow`` via ``LearningService`` (T4.4c).

    Thin HTTP wrapper around the service-layer helper from T4.1a so the
    ``alpha learn`` CLI and any future external caller share the same
    dispatch path. Fire-and-forget — returns ``{workflow_id}`` once the
    workflow is queued.
    """
    from app.services.learning_service import LearningService

    try:
        workflow_id = await LearningService.dispatch(intent)
    except Exception as e:
        logger.exception("learning dispatch failed: %s", e)
        raise HTTPException(500, f"dispatch failed: {e}")
    return {"workflow_id": workflow_id}


# --- Policy Candidates ---

@router.get("/candidates", response_model=List[PolicyCandidateInDB])
def list_candidates(
    candidate_status: Optional[str] = Query(default=None, alias="status"),
    policy_type: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List policy candidates."""
    return learning_experiment_service.list_candidates(
        db, current_user.tenant_id,
        status=candidate_status, policy_type=policy_type, limit=limit,
    )


@router.post("/candidates", response_model=PolicyCandidateInDB, status_code=status.HTTP_201_CREATED)
def create_candidate(
    candidate_in: PolicyCandidateCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create a policy candidate manually."""
    return learning_experiment_service.create_candidate(db, current_user.tenant_id, candidate_in)


@router.post("/candidates/generate-routing", response_model=List[PolicyCandidateInDB])
def generate_routing_candidates(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Auto-generate routing policy candidates from RL experience analysis."""
    return learning_experiment_service.generate_routing_candidates(db, current_user.tenant_id)


@router.get("/candidates/{candidate_id}", response_model=PolicyCandidateInDB)
def get_candidate(
    candidate_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get a policy candidate."""
    candidate = learning_experiment_service.get_candidate(db, current_user.tenant_id, candidate_id)
    if not candidate:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Candidate not found")
    return candidate


@router.post("/candidates/{candidate_id}/promote", response_model=PolicyCandidateInDB)
def promote_candidate(
    candidate_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Promote a policy candidate. Requires a completed experiment with significant improvement."""
    try:
        candidate = learning_experiment_service.promote_candidate(db, current_user.tenant_id, candidate_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    if not candidate:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot promote: not in promotable state")
    return candidate


@router.post("/candidates/{candidate_id}/reject", response_model=PolicyCandidateInDB)
def reject_candidate(
    candidate_id: uuid.UUID,
    reason: str = Query(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Reject a policy candidate."""
    candidate = learning_experiment_service.reject_candidate(db, current_user.tenant_id, candidate_id, reason)
    if not candidate:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot reject: not in rejectable state")
    return candidate


# --- Experiments ---

@router.get("/experiments", response_model=List[LearningExperimentInDB])
def list_experiments(
    experiment_status: Optional[str] = Query(default=None, alias="status"),
    candidate_id: Optional[uuid.UUID] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List learning experiments."""
    return learning_experiment_service.list_experiments(
        db, current_user.tenant_id,
        status=experiment_status, candidate_id=candidate_id, limit=limit,
    )


@router.post("/experiments", response_model=LearningExperimentInDB, status_code=status.HTTP_201_CREATED)
def create_experiment(
    experiment_in: LearningExperimentCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create and configure a learning experiment for a policy candidate."""
    try:
        return learning_experiment_service.create_experiment(db, current_user.tenant_id, experiment_in)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post("/experiments/{experiment_id}/run-offline", response_model=LearningExperimentInDB)
def run_offline_evaluation(
    experiment_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Run an offline evaluation against historical RL data."""
    result = learning_experiment_service.run_offline_evaluation(db, current_user.tenant_id, experiment_id)
    if not result:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot run: experiment not in runnable state")
    return result


@router.get("/experiments/{experiment_id}", response_model=LearningExperimentInDB)
def get_experiment(
    experiment_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get a learning experiment."""
    experiment = learning_experiment_service.get_experiment(db, current_user.tenant_id, experiment_id)
    if not experiment:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Experiment not found")
    return experiment


# --- Rollouts (Phase 2) ---

@router.post("/rollouts/start", response_model=LearningExperimentInDB)
def start_rollout(
    candidate_id: uuid.UUID = Query(...),
    rollout_pct: float = Query(default=0.1, ge=0.01, le=1.0),
    experiment_type: str = Query(default="split", regex="^(split)$"),
    min_sample_size: int = Query(default=30, ge=5),
    max_duration_hours: int = Query(default=168, ge=1),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Start a controlled split rollout for a policy candidate."""
    from app.services import policy_rollout_service
    try:
        return policy_rollout_service.start_rollout(
            db, current_user.tenant_id, candidate_id,
            rollout_pct=rollout_pct,
            experiment_type=experiment_type,
            min_sample_size=min_sample_size,
            max_duration_hours=max_duration_hours,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post("/rollouts/{experiment_id}/stop", response_model=LearningExperimentInDB)
def stop_rollout(
    experiment_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Manually stop a running rollout."""
    from app.services import policy_rollout_service
    experiment = policy_rollout_service.stop_rollout(db, current_user.tenant_id, experiment_id)
    if not experiment:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No running rollout found")
    return experiment


@router.get("/rollouts/active", response_model=dict)
def get_active_rollout(
    decision_point: str = Query(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Check if there's an active rollout for a decision point."""
    from app.services import policy_rollout_service
    rollout = policy_rollout_service.get_active_rollout(db, current_user.tenant_id, decision_point)
    return rollout or {"active": False}
