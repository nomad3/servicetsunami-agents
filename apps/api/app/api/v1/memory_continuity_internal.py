"""Internal endpoints for memory continuity workflows (Gap 1 + Gap 2).

Called by Temporal dynamic workflows via MCP tools.
All endpoints require X-Internal-Key + X-Tenant-Id headers.
"""
import uuid
import logging
from datetime import date, timedelta
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, status
from sqlalchemy.orm import Session

from app.api import deps
from app.core.config import settings
from app.db.session import SessionLocal
from fastapi import Depends

logger = logging.getLogger(__name__)
router = APIRouter()


def _verify(
    x_internal_key: Optional[str] = Header(None, alias="X-Internal-Key"),
    x_tenant_id: Optional[str] = Header(None, alias="X-Tenant-Id"),
):
    if x_internal_key not in (settings.API_INTERNAL_KEY, settings.MCP_API_KEY):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid internal key")
    if not x_tenant_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="X-Tenant-Id required")
    try:
        return uuid.UUID(x_tenant_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid tenant UUID")


# ---------------------------------------------------------------------------
# Gap 1: Session Journal synthesis
# ---------------------------------------------------------------------------

@router.post("/session-journals/synthesize-daily")
def synthesize_daily_journal(
    tenant_id: uuid.UUID = Depends(_verify),
    db: Session = Depends(deps.get_db),
):
    """
    Synthesize today's conversation episodes into a daily journal entry.

    Called nightly by the Daily Journal Synthesis workflow (cron: 55 23 * * *).
    Reads today's ConversationEpisode records, extracts key themes, and stores
    a SessionJournal entry with embedding.
    """
    from app.models.conversation_episode import ConversationEpisode
    from app.services.session_journals import session_journal_service
    from app.services.local_inference import summarize_conversation_sync

    today = date.today()

    # Fetch today's episodes
    episodes = db.query(ConversationEpisode).filter(
        ConversationEpisode.tenant_id == tenant_id,
        ConversationEpisode.created_at >= today,
    ).order_by(ConversationEpisode.created_at.desc()).limit(50).all()

    if not episodes:
        return {"status": "skipped", "reason": "no episodes today", "date": str(today)}

    # Build combined text for synthesis
    episode_texts = []
    all_entities = []
    for ep in episodes:
        episode_texts.append(ep.summary)
        if ep.key_entities:
            all_entities.extend(ep.key_entities)

    combined = "\n".join(f"- {t}" for t in episode_texts)

    # Generate narrative summary via local LLM
    summary = summarize_conversation_sync(
        messages=[{"role": "user", "content": f"Summarize this user's activity from today in 2-3 warm sentences:\n\n{combined}"}],
        system="You are Luna. Write a warm, personal narrative about the user's day in first person (e.g. 'Today you worked on...'). Be specific but concise.",
        max_tokens=200,
    )

    if not summary or len(summary) < 10:
        summary = f"You had {len(episodes)} conversations today covering various topics."

    # Deduplicate entities, extract top people/projects
    entity_counts: dict = {}
    for e in all_entities:
        entity_counts[e] = entity_counts.get(e, 0) + 1
    top_entities = [e for e, _ in sorted(entity_counts.items(), key=lambda x: -x[1])[:10]]

    journal = session_journal_service.create_journal_entry(
        db=db,
        tenant_id=tenant_id,
        summary=summary,
        period_start=today,
        period_end=today,
        period_type="day",
        episode_count=len(episodes),
        mentioned_people=top_entities[:5],
        mentioned_projects=top_entities[5:],
    )

    return {
        "status": "created",
        "journal_id": str(journal.id),
        "date": str(today),
        "episodes_processed": len(episodes),
        "summary_preview": summary[:100],
    }


@router.post("/session-journals/synthesize-weekly")
def synthesize_weekly_journal(
    tenant_id: uuid.UUID = Depends(_verify),
    db: Session = Depends(deps.get_db),
):
    """
    Aggregate this week's daily journals into a weekly summary.

    Called Sundays at 23:00 by the Weekly Journal workflow.
    Reads daily journals from Mon-Sun and synthesizes a weekly narrative.
    """
    from app.services.session_journals import session_journal_service
    from app.services.local_inference import summarize_conversation_sync

    today = date.today()
    week_start = today - timedelta(days=today.weekday())
    week_end = today

    daily_journals = session_journal_service.get_journals_in_range(
        db, tenant_id, week_start, week_end
    )
    daily_journals = [j for j in daily_journals if j.period_type == "day"]

    if not daily_journals:
        return {"status": "skipped", "reason": "no daily journals this week"}

    combined = "\n".join(f"- {j.summary}" for j in daily_journals)

    summary = summarize_conversation_sync(
        messages=[{"role": "user", "content": f"Summarize this user's week in 3-4 warm sentences:\n\n{combined}"}],
        system="You are Luna. Write a warm weekly narrative (e.g. 'This week you...'). Highlight key accomplishments and recurring themes.",
        max_tokens=300,
    )

    if not summary or len(summary) < 10:
        summary = f"You had {len(daily_journals)} active days this week."

    all_people = []
    all_projects = []
    for j in daily_journals:
        all_people.extend(j.mentioned_people or [])
        all_projects.extend(j.mentioned_projects or [])

    journal = session_journal_service.create_journal_entry(
        db=db,
        tenant_id=tenant_id,
        summary=summary,
        period_start=week_start,
        period_end=week_end,
        period_type="week",
        episode_count=sum(j.episode_count or 0 for j in daily_journals),
        mentioned_people=list(set(all_people))[:5],
        mentioned_projects=list(set(all_projects))[:5],
    )

    return {
        "status": "created",
        "journal_id": str(journal.id),
        "week_start": str(week_start),
        "week_end": str(week_end),
        "daily_journals_merged": len(daily_journals),
    }


# ---------------------------------------------------------------------------
# Gap 2: Behavioral signal maintenance
# ---------------------------------------------------------------------------

@router.post("/behavioral-signals/expire")
def expire_behavioral_signals(
    tenant_id: uuid.UUID = Depends(_verify),
    db: Session = Depends(deps.get_db),
):
    """
    Mark stale pending behavioral signals as ignored (acted_on=False).

    Called nightly at 00:30 by the Signal Maintenance workflow.
    Any suggestion older than its expires_after_hours with no action gets
    marked as ignored so learning stats stay accurate.
    """
    from app.services.behavioral_signals import expire_stale_signals
    count = expire_stale_signals(db, tenant_id)
    return {"status": "ok", "expired_count": count}


@router.get("/behavioral-signals/stats")
def get_behavioral_signal_stats(
    days: int = 14,
    tenant_id: uuid.UUID = Depends(_verify),
    db: Session = Depends(deps.get_db),
):
    """Return suggestion performance stats for a tenant."""
    from app.services.behavioral_signals import get_suggestion_stats
    stats = get_suggestion_stats(db, tenant_id, days=days)
    return {"stats": stats, "days": days}
