"""Activities for Gap 1 - Session Journal morning briefing synthesis."""
import uuid
import logging
from datetime import date, timedelta
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.services.session_journals import session_journal_service
from app.services.embedding_service import embed_text
from temporalio import activity

logger = logging.getLogger(__name__)


@activity.defn
async def synthesize_morning_briefing(tenant_id: str) -> str:
    """
    Synthesize a morning briefing from recent session journals.

    This activity:
    1. Retrieves recent session journals (last 7 days)
    2. Synthesizes them into a warm, engaging narrative
    3. Returns the briefing text for inclusion in system context

    Used by Gap 1 (Continuity) to provide Luna with narrative context
    of the user's recent activity.
    """
    db: Session = SessionLocal()
    try:
        tenant_uuid = uuid.UUID(tenant_id)

        # Synthesize morning context from recent journals
        briefing = session_journal_service.synthesize_morning_context(
            db=db,
            tenant_id=tenant_uuid,
            days_lookback=7,
        )

        logger.info(f"Synthesized morning briefing for tenant {tenant_id}")
        return briefing

    except Exception as e:
        logger.error(f"Failed to synthesize morning briefing: {e}")
        return ""
    finally:
        db.close()


@activity.defn
async def create_daily_journal_entry(
    tenant_id: str,
    summary: str,
    key_accomplishments: list = None,
    key_challenges: list = None,
    mentioned_people: list = None,
    mentioned_projects: list = None,
) -> str:
    """
    Create a new daily journal entry for a tenant.

    This activity is called after analyzing a day's activity to
    store a summary in the session journal for future context.
    """
    db: Session = SessionLocal()
    try:
        tenant_uuid = uuid.UUID(tenant_id)
        today = date.today()

        journal = session_journal_service.create_journal_entry(
            db=db,
            tenant_id=tenant_uuid,
            summary=summary,
            period_start=today,
            period_end=today,
            period_type="day",
            key_accomplishments=key_accomplishments,
            key_challenges=key_challenges,
            mentioned_people=mentioned_people,
            mentioned_projects=mentioned_projects,
        )

        logger.info(f"Created daily journal entry {journal.id} for tenant {tenant_id}")
        return str(journal.id)

    except Exception as e:
        logger.error(f"Failed to create journal entry: {e}")
        return ""
    finally:
        db.close()


@activity.defn
async def create_weekly_journal_summary(
    tenant_id: str,
    summary: str,
    key_themes: list = None,
    key_accomplishments: list = None,
    key_challenges: list = None,
    mentioned_people: list = None,
    mentioned_projects: list = None,
) -> str:
    """
    Create a weekly summary journal entry.

    Called by an autonomous learning or manual process to synthesize
    a full week's activity into narrative form.
    """
    db: Session = SessionLocal()
    try:
        tenant_uuid = uuid.UUID(tenant_id)
        today = date.today()
        week_start = today - timedelta(days=today.weekday())  # Monday
        week_end = week_start + timedelta(days=6)  # Sunday

        journal = session_journal_service.create_journal_entry(
            db=db,
            tenant_id=tenant_uuid,
            summary=summary,
            period_start=week_start,
            period_end=week_end,
            period_type="week",
            key_themes=key_themes,
            key_accomplishments=key_accomplishments,
            key_challenges=key_challenges,
            mentioned_people=mentioned_people,
            mentioned_projects=mentioned_projects,
        )

        logger.info(f"Created weekly journal summary {journal.id} for tenant {tenant_id}")
        return str(journal.id)

    except Exception as e:
        logger.error(f"Failed to create weekly journal: {e}")
        return ""
    finally:
        db.close()
