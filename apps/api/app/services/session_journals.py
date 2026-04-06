"""Session Journal service — manage continuity briefing context for Gap 1."""
import uuid
from datetime import datetime, date, timedelta
from typing import List, Optional
from sqlalchemy.orm import Session

from app.models import SessionJournal

try:
    from app.services.embedding_service import embed_and_store
except ImportError:
    embed_and_store = None  # type: ignore

try:
    from app.services.local_inference import summarize_conversation_sync
except ImportError:
    summarize_conversation_sync = None  # type: ignore


class SessionJournalService:
    """Service for managing session journals (episodic memory synthesis)."""

    def create_journal_entry(
        self,
        db: Session,
        tenant_id: uuid.UUID,
        summary: str,
        period_start: date,
        period_end: date,
        period_type: str = "week",
        key_themes: list = None,
        key_accomplishments: list = None,
        key_challenges: list = None,
        mentioned_people: list = None,
        mentioned_projects: list = None,
        episode_count: int = 0,
        message_count: int = 0,
        activity_score: int = 0,
    ) -> SessionJournal:
        """Create a new journal entry for a period."""
        journal = SessionJournal(
            tenant_id=tenant_id,
            summary=summary,
            period_start=period_start,
            period_end=period_end,
            period_type=period_type,
            key_themes=key_themes or [],
            key_accomplishments=key_accomplishments or [],
            key_challenges=key_challenges or [],
            mentioned_people=mentioned_people or [],
            mentioned_projects=mentioned_projects or [],
            episode_count=episode_count,
            message_count=message_count,
            activity_score=activity_score,
        )

        db.add(journal)
        db.flush()

        # Embed the summary for semantic search (non-blocking failure)
        try:
            embedding = embed_and_store(
                content=summary,
                content_type="session_journal",
                content_id=str(journal.id),
                tenant_id=tenant_id,
                db=db,
            )
            if embedding is not None:
                journal.embedding = embedding
        except Exception as e:
            # Log but don't fail — embeddings are optional for basic journal functionality
            import logging
            logging.warning(f"Failed to embed journal {journal.id}: {e}")

        db.commit()
        return journal

    def get_latest_journal(self, db: Session, tenant_id: uuid.UUID) -> Optional[SessionJournal]:
        """Get the most recent journal entry for a tenant."""
        return db.query(SessionJournal).filter(
            SessionJournal.tenant_id == tenant_id
        ).order_by(SessionJournal.period_end.desc()).first()

    def get_journals_in_range(
        self,
        db: Session,
        tenant_id: uuid.UUID,
        start_date: date,
        end_date: date,
    ) -> List[SessionJournal]:
        """Get journal entries within a date range."""
        return db.query(SessionJournal).filter(
            SessionJournal.tenant_id == tenant_id,
            SessionJournal.period_start >= start_date,
            SessionJournal.period_end <= end_date,
        ).order_by(SessionJournal.period_end.desc()).all()

    def get_weekly_journals(
        self,
        db: Session,
        tenant_id: uuid.UUID,
        weeks_back: int = 4,
    ) -> List[SessionJournal]:
        """Get journal entries for the last N weeks."""
        start_date = date.today() - timedelta(weeks=weeks_back)
        return self.get_journals_in_range(db, tenant_id, start_date, date.today())

    def synthesize_morning_context(
        self,
        db: Session,
        tenant_id: uuid.UUID,
        days_lookback: int = 7,
    ) -> str:
        """Synthesize continuity context from recent journals for morning briefing."""
        # Get journals from the last N days
        start_date = date.today() - timedelta(days=days_lookback)
        journals = self.get_journals_in_range(db, tenant_id, start_date, date.today())

        if not journals:
            return ""

        # Build context from recent journals
        context_parts = []
        for journal in journals:
            context_parts.append(f"• **{journal.period_start} to {journal.period_end}**: {journal.summary}")

            if journal.key_accomplishments:
                context_parts.append(f"  - Accomplished: {', '.join(journal.key_accomplishments[:3])}")

            if journal.key_challenges:
                context_parts.append(f"  - Faced: {', '.join(journal.key_challenges[:2])}")

        # Synthesize into a cohesive narrative using local Qwen model (with fallback)
        combined_context = "\n".join(context_parts)
        if len(context_parts) > 1:
            try:
                prompt_text = (
                    f"Synthesize this activity journal into a brief warm narrative about the user's "
                    f"last few days. Be personal and grounded, like a chief of staff briefing.\n\n"
                    f"{combined_context}"
                )
                synthesis = summarize_conversation_sync(prompt_text)
                if synthesis and len(synthesis) > 10:
                    return synthesis
            except Exception:
                pass  # Fall through to simple join

        # Fallback: return the concatenated context as-is
        return combined_context if combined_context else (journals[0].summary if journals else "")


session_journal_service = SessionJournalService()
