"""Learning experiment and policy candidate API endpoints."""

import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.models.user import User
from app.schemas.learning_experiment import (
    LearningExperimentCreate,
    LearningExperimentInDB,
    PolicyCandidateCreate,
    PolicyCandidateInDB,
)
from app.services import learning_experiment_service

router = APIRouter()


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


@router.post("/experiments/{experiment_id}/run-offline", response_model=dict)
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
