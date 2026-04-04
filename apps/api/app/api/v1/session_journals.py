"""API routes for Gap 1: Session Journal continuity features."""
import uuid
from typing import List, Optional
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, status, Body
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api import deps
from app.models import SessionJournal, User
from app.services.session_journals import session_journal_service

router = APIRouter(prefix="/session-journals", tags=["session-journals"])


class SessionJournalCreate(BaseModel):
    """Request body for creating a session journal entry."""
    summary: str
    period_start: date
    period_end: date
    period_type: str = "week"
    key_themes: Optional[List[str]] = None
    key_accomplishments: Optional[List[str]] = None
    key_challenges: Optional[List[str]] = None
    mentioned_people: Optional[List[str]] = None
    mentioned_projects: Optional[List[str]] = None


@router.post("", status_code=status.HTTP_201_CREATED)
def create_journal(
    req: SessionJournalCreate,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
):
    """Create a new session journal entry (Gap 1: Continuity)."""
    journal = session_journal_service.create_journal_entry(
        db=db,
        tenant_id=current_user.tenant_id,
        summary=req.summary,
        period_start=req.period_start,
        period_end=req.period_end,
        period_type=req.period_type,
        key_themes=req.key_themes,
        key_accomplishments=req.key_accomplishments,
        key_challenges=req.key_challenges,
        mentioned_people=req.mentioned_people,
        mentioned_projects=req.mentioned_projects,
    )

    return {
        "id": journal.id,
        "summary": journal.summary,
        "period_start": journal.period_start,
        "period_end": journal.period_end,
        "period_type": journal.period_type,
        "created_at": journal.created_at,
    }


@router.get("/latest")
def get_latest_journal(
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
):
    """Get the most recent session journal entry."""
    journal = session_journal_service.get_latest_journal(db, current_user.tenant_id)
    if not journal:
        raise HTTPException(status_code=404, detail="No journal entries found")

    return {
        "id": journal.id,
        "summary": journal.summary,
        "period_start": journal.period_start,
        "period_end": journal.period_end,
        "key_themes": journal.key_themes,
        "key_accomplishments": journal.key_accomplishments,
        "key_challenges": journal.key_challenges,
        "created_at": journal.created_at,
    }


@router.get("/morning-briefing")
def get_morning_briefing(
    days_lookback: int = 7,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
):
    """Get synthesized morning briefing context (Gap 1: Continuity)."""
    briefing = session_journal_service.synthesize_morning_context(
        db=db,
        tenant_id=current_user.tenant_id,
        days_lookback=days_lookback,
    )

    return {
        "briefing": briefing,
        "days_lookback": days_lookback,
    }


@router.get("")
def list_journals(
    limit: int = 10,
    offset: int = 0,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
):
    """List session journal entries for the current user."""
    journals = db.query(SessionJournal).filter(
        SessionJournal.tenant_id == current_user.tenant_id
    ).order_by(SessionJournal.period_end.desc()).offset(offset).limit(limit).all()

    return [
        {
            "id": j.id,
            "summary": j.summary,
            "period_start": j.period_start,
            "period_end": j.period_end,
            "period_type": j.period_type,
            "key_themes": j.key_themes,
            "key_accomplishments": j.key_accomplishments,
            "key_challenges": j.key_challenges,
            "created_at": j.created_at,
        }
        for j in journals
    ]
