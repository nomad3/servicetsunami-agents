import uuid
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api import deps
from app.models.agent_test_suite import AgentTestCase, AgentTestRun
from app.models.user import User
from app.services import agent_test_runner

router = APIRouter()


class TestCaseIn(BaseModel):
    name: str
    input: str
    expected_output_contains: List[str] = Field(default_factory=list)
    expected_output_excludes: List[str] = Field(default_factory=list)
    min_quality_score: float = 0.6
    max_latency_ms: int = 10000
    tags: List[str] = Field(default_factory=list)
    enabled: bool = True


class TestCaseOut(TestCaseIn):
    id: uuid.UUID
    agent_id: uuid.UUID

    class Config:
        from_attributes = True


class TestRunOut(BaseModel):
    id: uuid.UUID
    agent_id: uuid.UUID
    agent_version: Optional[int] = None
    run_type: str
    status: str
    total_cases: int
    passed_count: int
    failed_count: int
    results: list = Field(default_factory=list)
    created_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None

    class Config:
        from_attributes = True


def _enforce_agent_access(db: Session, agent_id: uuid.UUID, tenant_id: uuid.UUID):
    from app.models.agent import Agent

    agent = db.query(Agent).filter(Agent.id == agent_id, Agent.tenant_id == tenant_id).first()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    return agent


@router.get("/{agent_id}/test-cases", response_model=List[TestCaseOut])
def list_test_cases(
    agent_id: uuid.UUID,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    _enforce_agent_access(db, agent_id, current_user.tenant_id)
    return (
        db.query(AgentTestCase)
        .filter(AgentTestCase.agent_id == agent_id, AgentTestCase.tenant_id == current_user.tenant_id)
        .order_by(AgentTestCase.created_at.asc())
        .all()
    )


@router.post("/{agent_id}/test-cases", response_model=TestCaseOut, status_code=status.HTTP_201_CREATED)
def create_test_case(
    agent_id: uuid.UUID,
    body: TestCaseIn,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    _enforce_agent_access(db, agent_id, current_user.tenant_id)
    case = AgentTestCase(
        agent_id=agent_id,
        tenant_id=current_user.tenant_id,
        name=body.name,
        input=body.input,
        expected_output_contains=body.expected_output_contains,
        expected_output_excludes=body.expected_output_excludes,
        min_quality_score=body.min_quality_score,
        max_latency_ms=body.max_latency_ms,
        tags=body.tags,
        enabled=body.enabled,
    )
    db.add(case)
    db.commit()
    db.refresh(case)
    return case


@router.delete("/{agent_id}/test-cases/{case_id}")
def delete_test_case(
    agent_id: uuid.UUID,
    case_id: uuid.UUID,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    case = (
        db.query(AgentTestCase)
        .filter(
            AgentTestCase.id == case_id,
            AgentTestCase.agent_id == agent_id,
            AgentTestCase.tenant_id == current_user.tenant_id,
        )
        .first()
    )
    if not case:
        raise HTTPException(status_code=404, detail="Test case not found")
    db.delete(case)
    db.commit()
    return {"deleted": True}


@router.post("/{agent_id}/test", response_model=TestRunOut)
def run_tests(
    agent_id: uuid.UUID,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    """Run all enabled test cases for the agent and return the TestRun record."""
    _enforce_agent_access(db, agent_id, current_user.tenant_id)
    try:
        run = agent_test_runner.run_test_suite(
            db,
            agent_id=agent_id,
            tenant_id=current_user.tenant_id,
            triggered_by_user_id=current_user.id,
            run_type="manual",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return run


@router.get("/{agent_id}/test-runs", response_model=List[TestRunOut])
def list_test_runs(
    agent_id: uuid.UUID,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
    limit: int = 20,
):
    _enforce_agent_access(db, agent_id, current_user.tenant_id)
    return (
        db.query(AgentTestRun)
        .filter(AgentTestRun.agent_id == agent_id, AgentTestRun.tenant_id == current_user.tenant_id)
        .order_by(AgentTestRun.created_at.desc())
        .limit(max(1, min(limit, 100)))
        .all()
    )
