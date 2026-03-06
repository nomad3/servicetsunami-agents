# Proactive Inbox Monitor Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Luna proactively monitors Gmail and Calendar via a Temporal workflow, triages items using existing memory context, creates in-app notifications, and extracts entities/memories/relations from important emails through the established knowledge extraction pipeline.

**Architecture:** A long-running `InboxMonitorWorkflow` (one per tenant, `continue_as_new` every 15 min) checks Gmail for new messages and Calendar for upcoming events. Triage uses `build_memory_context()` to enrich with known entities, then an LLM classifies importance. High/medium items become notifications; important emails are fed through `KnowledgeExtractionService.extract_from_content()` for entity/relation/memory extraction. Action triggers from emails (follow-ups, reminders) are dispatched via the same Temporal pattern as chat. Auto-starts when Google OAuth connects. Luna can control it via an ADK tool.

**Tech Stack:** FastAPI, Temporal Python SDK, SQLAlchemy/PostgreSQL, Google Gmail & Calendar REST APIs, `KnowledgeExtractionService`, `build_memory_context()`, `LLMService`, React + React Bootstrap

**Key Patterns Followed:**
- Workflow: `ChannelHealthMonitorWorkflow` (continue_as_new, per-tenant, long-running)
- Workflow start: Direct `client.start_workflow(Workflow.run, args, ...)` (see remedia.py, chat.py)
- Activities: `SessionLocal()` + lazy imports (see channel_health.py, follow_up.py)
- Extraction: `extract_from_content(content_type="plain_text")` with activity logging
- Recall: `build_memory_context()` for known-entity enrichment during triage
- Action dispatch: Same `_dispatch_action_triggers()` pattern as chat.py
- OAuth: Auto-start in callback (see oauth.py callback pattern)
- ADK tools: Async function in `apps/adk-server/tools/` (see google_tools.py)
- Frontend: `SkillsConfigPanel` for integration toggles (see skill_configs.py registry)

---

## Task 1: Notification Model + Migration

**Files:**
- Create: `apps/api/app/models/notification.py`
- Create: `apps/api/migrations/038_add_notifications.sql`
- Modify: `apps/api/app/models/__init__.py`

**Step 1: Create the Notification model**

Create `apps/api/app/models/notification.py`:

```python
"""Notification model for proactive alerts from Luna."""
import uuid
from datetime import datetime
from sqlalchemy import Boolean, Column, DateTime, ForeignKey, String, Text, JSON
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.db.base import Base


class Notification(Base):
    __tablename__ = "notifications"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)

    title = Column(String(255), nullable=False)
    body = Column(Text, nullable=True)
    source = Column(String(50), nullable=False)  # gmail, calendar, whatsapp, system
    priority = Column(String(20), nullable=False, default="medium")  # high, medium, low

    read = Column(Boolean, default=False, nullable=False)
    dismissed = Column(Boolean, default=False, nullable=False)

    reference_id = Column(String(255), nullable=True)  # email message_id, event_id, etc.
    reference_type = Column(String(50), nullable=True)  # email, event, reminder
    event_metadata = Column("metadata", JSON, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)

    tenant = relationship("Tenant")

    def __repr__(self):
        return f"<Notification {self.id} {self.priority}:{self.source}>"
```

**Step 2: Create the migration**

Create `apps/api/migrations/038_add_notifications.sql`:

```sql
-- Notifications table for proactive alerts from Luna
CREATE TABLE IF NOT EXISTS notifications (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    title VARCHAR(255) NOT NULL,
    body TEXT,
    source VARCHAR(50) NOT NULL,
    priority VARCHAR(20) NOT NULL DEFAULT 'medium',
    read BOOLEAN NOT NULL DEFAULT FALSE,
    dismissed BOOLEAN NOT NULL DEFAULT FALSE,
    reference_id VARCHAR(255),
    reference_type VARCHAR(50),
    metadata JSONB,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_notifications_tenant_id ON notifications(tenant_id);
CREATE INDEX IF NOT EXISTS ix_notifications_created_at ON notifications(created_at);
CREATE INDEX IF NOT EXISTS ix_notifications_tenant_unread ON notifications(tenant_id, read) WHERE read = FALSE;
```

**Step 3: Register model in `__init__.py`**

In `apps/api/app/models/__init__.py`, add import and `__all__` entry:

```python
from .notification import Notification
# Add "Notification" to __all__
```

**Step 4: Run migration on production**

```bash
kubectl exec -it deploy/servicetsunami-api -n prod -- psql "$DATABASE_URL" -f /app/migrations/038_add_notifications.sql
```

**Step 5: Commit**

```bash
git add apps/api/app/models/notification.py apps/api/migrations/038_add_notifications.sql apps/api/app/models/__init__.py
git commit -m "feat: add Notification model and migration for proactive alerts"
```

---

## Task 2: Notification Schema + API Endpoints

**Files:**
- Create: `apps/api/app/schemas/notification.py`
- Create: `apps/api/app/api/v1/notifications.py`
- Modify: `apps/api/app/api/v1/routes.py`

**Step 1: Create Pydantic schemas**

Create `apps/api/app/schemas/notification.py`:

```python
"""Pydantic schemas for Notification."""
from datetime import datetime
from typing import Optional, Dict, Any
from uuid import UUID
from pydantic import BaseModel


class NotificationInDB(BaseModel):
    id: UUID
    tenant_id: UUID
    title: str
    body: Optional[str] = None
    source: str
    priority: str
    read: bool
    dismissed: bool
    reference_id: Optional[str] = None
    reference_type: Optional[str] = None
    event_metadata: Optional[Dict[str, Any]] = None
    created_at: datetime

    class Config:
        from_attributes = True


class NotificationCount(BaseModel):
    unread: int
```

**Step 2: Create the notifications router**

Create `apps/api/app/api/v1/notifications.py`:

```python
"""Notification endpoints for proactive alerts."""
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.api import deps
from app.models.notification import Notification
from app.models.user import User
from app.schemas.notification import NotificationInDB, NotificationCount

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("", response_model=list[NotificationInDB])
def list_notifications(
    skip: int = 0,
    limit: int = 20,
    unread_only: bool = False,
    source: Optional[str] = None,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    """List notifications for the current tenant."""
    query = db.query(Notification).filter(
        Notification.tenant_id == current_user.tenant_id,
        Notification.dismissed == False,
    )
    if unread_only:
        query = query.filter(Notification.read == False)
    if source:
        query = query.filter(Notification.source == source)
    return query.order_by(Notification.created_at.desc()).offset(skip).limit(limit).all()


@router.get("/count", response_model=NotificationCount)
def notification_count(
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    """Get unread notification count."""
    count = db.query(func.count(Notification.id)).filter(
        Notification.tenant_id == current_user.tenant_id,
        Notification.read == False,
        Notification.dismissed == False,
    ).scalar() or 0
    return {"unread": count}


@router.patch("/{notification_id}/read")
def mark_read(
    notification_id: str,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    """Mark a notification as read."""
    notif = db.query(Notification).filter(
        Notification.id == notification_id,
        Notification.tenant_id == current_user.tenant_id,
    ).first()
    if not notif:
        raise HTTPException(status_code=404, detail="Notification not found")
    notif.read = True
    db.commit()
    return {"status": "ok"}


@router.patch("/read-all")
def mark_all_read(
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    """Mark all notifications as read."""
    db.query(Notification).filter(
        Notification.tenant_id == current_user.tenant_id,
        Notification.read == False,
    ).update({"read": True})
    db.commit()
    return {"status": "ok"}


@router.delete("/{notification_id}")
def dismiss_notification(
    notification_id: str,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    """Dismiss (soft-delete) a notification."""
    notif = db.query(Notification).filter(
        Notification.id == notification_id,
        Notification.tenant_id == current_user.tenant_id,
    ).first()
    if not notif:
        raise HTTPException(status_code=404, detail="Notification not found")
    notif.dismissed = True
    db.commit()
    return {"status": "ok"}
```

**Step 3: Register router in routes.py**

In `apps/api/app/api/v1/routes.py`, add:

```python
from app.api.v1 import notifications
# ...
router.include_router(notifications.router, prefix="/notifications", tags=["notifications"])
```

**Step 4: Commit**

```bash
git add apps/api/app/schemas/notification.py apps/api/app/api/v1/notifications.py apps/api/app/api/v1/routes.py
git commit -m "feat: add notification API endpoints (list, count, read, dismiss)"
```

---

## Task 3: Add `source` Parameter to Knowledge Extraction

**Files:**
- Modify: `apps/api/app/services/knowledge_extraction.py`

The extraction service hardcodes `source="chat"` in all `log_activity()` calls. We need to parameterize this so inbox monitor emails log as `source="gmail"` and calendar items as `source="calendar"`.

**Step 1: Add `source` parameter to `extract_from_content`**

In `apps/api/app/services/knowledge_extraction.py`, modify the `extract_from_content` signature (line ~109):

```python
    def extract_from_content(
        self,
        db: Session,
        tenant_id: uuid.UUID,
        content: str,
        content_type: str = "plain_text",
        *,
        entity_schema: Optional[Dict[str, Any]] = None,
        source_url: Optional[str] = None,
        source_agent_id: Optional[uuid.UUID] = None,
        collection_task_id: Optional[uuid.UUID] = None,
        activity_source: str = "chat",  # NEW: "chat", "gmail", "calendar", "whatsapp"
    ) -> Dict[str, Any]:
```

Then replace all three hardcoded `source="chat"` calls in the method (~lines 212, 222, 232) with `source=activity_source`:

```python
            for entity in created:
                try:
                    log_activity(
                        db, tenant_id, "entity_created",
                        f'Extracted "{entity.name}" ({entity.entity_type})',
                        source=activity_source, entity_id=entity.id,
                    )
                except Exception:
                    logger.debug("Failed to log entity activity for %s", entity.name)

            if relations_created:
                try:
                    log_activity(
                        db, tenant_id, "relation_created",
                        f"Discovered {relations_created} relations",
                        source=activity_source,
                    )
                except Exception:
                    logger.debug("Failed to log relation activity")

            if memories_created:
                try:
                    log_activity(
                        db, tenant_id, "memory_created",
                        f"Learned {memories_created} new memories",
                        source=activity_source,
                    )
                except Exception:
                    logger.debug("Failed to log memory activity")
```

The `extract_from_session` wrapper (called from chat.py) doesn't need changes — it defaults to `activity_source="chat"`, preserving existing behavior.

**Step 2: Commit**

```bash
git add apps/api/app/services/knowledge_extraction.py
git commit -m "feat: parameterize activity source in knowledge extraction"
```

---

## Task 4: Inbox Monitor Activities (Memory-Aligned)

**Files:**
- Create: `apps/api/app/workflows/activities/inbox_monitor.py`

This is the core logic. Key integration points with memory system:
- **Triage enrichment**: Uses `build_memory_context()` to check if email senders are known entities (boosts priority for known contacts)
- **Entity extraction**: Feeds important emails through `KnowledgeExtractionService.extract_from_content(content_type="plain_text", activity_source="gmail")` — same pipeline as chat
- **Action triggers**: Extraction may return `action_triggers` (reminders, follow-ups) which are dispatched to Temporal via the same pattern as `chat.py`
- **Activity logging**: All events logged to `MemoryActivity` with proper source (`gmail`/`calendar`)

**Step 1: Create the activities file**

Create `apps/api/app/workflows/activities/inbox_monitor.py`:

```python
"""Temporal activities for proactive inbox monitoring.

Integrates with the Luna memory system:
- Uses build_memory_context() for triage enrichment (known contacts boost priority)
- Uses KnowledgeExtractionService.extract_from_content() for entity/relation/memory extraction
- Dispatches action_triggers (reminders, follow-ups) via Temporal
- Logs all events to MemoryActivity with source="gmail"/"calendar"
"""
import base64
import json
import logging
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import httpx
from temporalio import activity

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.notification import Notification
from app.models.skill_config import SkillConfig
from app.services.orchestration.credential_vault import retrieve_credentials_for_skill
from app.services.memory_activity import log_activity

logger = logging.getLogger(__name__)


# ── Token helpers ──────────────────────────────────────────────────────

def _refresh_google_token(refresh_token: str) -> Optional[str]:
    """Use refresh token to get a fresh Google access token."""
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.post("https://oauth2.googleapis.com/token", data={
                "client_id": settings.GOOGLE_CLIENT_ID,
                "client_secret": settings.GOOGLE_CLIENT_SECRET,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            })
            resp.raise_for_status()
            return resp.json().get("access_token")
    except Exception as e:
        logger.warning("Google token refresh failed: %s", e)
        return None


def _get_google_token(db, tenant_id: str, skill_name: str) -> Optional[str]:
    """Retrieve and auto-refresh Google OAuth token from credential vault."""
    tid = uuid.UUID(tenant_id)
    skill_config = (
        db.query(SkillConfig)
        .filter(
            SkillConfig.tenant_id == tid,
            SkillConfig.skill_name == skill_name,
            SkillConfig.enabled.is_(True),
        )
        .first()
    )
    if not skill_config:
        return None

    creds = retrieve_credentials_for_skill(db, skill_config.id, tid)
    refresh_tok = creds.get("refresh_token")

    # Google access tokens expire after ~1h; always refresh
    if refresh_tok:
        new_token = _refresh_google_token(refresh_tok)
        if new_token:
            return new_token

    # Fall back to stored token (may be expired)
    return creds.get("oauth_token")


# ── Gmail helpers ──────────────────────────────────────────────────────

def _extract_body(payload: dict) -> str:
    """Recursively extract plain text body from Gmail message payload."""
    mime = payload.get("mimeType", "")
    if mime == "text/plain" and payload.get("body", {}).get("data"):
        return base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="replace")
    for part in payload.get("parts", []):
        text = _extract_body(part)
        if text:
            return text
    if mime == "text/html" and payload.get("body", {}).get("data"):
        return base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="replace")
    return ""


# ── Activities ─────────────────────────────────────────────────────────

@activity.defn
async def fetch_new_emails(tenant_id: str, last_history_id: Optional[str] = None) -> Dict[str, Any]:
    """Fetch new Gmail messages since last check.

    Uses Gmail history API for incremental sync when last_history_id is available.
    Falls back to messages.list with newer_than:1d on first run.
    """
    db = SessionLocal()
    try:
        token = _get_google_token(db, tenant_id, "gmail")
        if not token:
            return {"emails": [], "new_history_id": last_history_id, "count": 0, "error": "no_token"}

        auth = {"Authorization": f"Bearer {token}"}

        async with httpx.AsyncClient(timeout=30.0) as client:
            message_ids = []
            new_history_id = last_history_id

            if last_history_id:
                try:
                    resp = await client.get(
                        "https://gmail.googleapis.com/gmail/v1/users/me/history",
                        headers=auth,
                        params={
                            "startHistoryId": last_history_id,
                            "historyTypes": "messageAdded",
                            "labelId": "INBOX",
                        },
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        new_history_id = data.get("historyId", last_history_id)
                        for entry in data.get("history", []):
                            for msg in entry.get("messagesAdded", []):
                                message_ids.append(msg["message"]["id"])
                    elif resp.status_code == 404:
                        # historyId too old, fall back to messages.list
                        last_history_id = None
                    else:
                        logger.warning("Gmail history API returned %s", resp.status_code)
                        return {"emails": [], "new_history_id": last_history_id, "count": 0}
                except Exception as e:
                    logger.warning("Gmail history fetch failed: %s", e)
                    last_history_id = None

            # Fallback: list recent messages
            if not last_history_id and not message_ids:
                resp = await client.get(
                    "https://gmail.googleapis.com/gmail/v1/users/me/messages",
                    headers=auth,
                    params={"maxResults": 15, "q": "newer_than:1d"},
                )
                if resp.status_code != 200:
                    return {"emails": [], "new_history_id": None, "count": 0, "error": f"api_{resp.status_code}"}
                data = resp.json()
                message_ids = [m["id"] for m in data.get("messages", [])]

            # Get current historyId from profile for next run
            if not new_history_id or not last_history_id:
                profile_resp = await client.get(
                    "https://gmail.googleapis.com/gmail/v1/users/me/profile",
                    headers=auth,
                )
                if profile_resp.status_code == 200:
                    new_history_id = profile_resp.json().get("historyId")

            # Fetch message details
            emails = []
            for msg_id in message_ids[:15]:  # Cap at 15 per cycle
                detail = await client.get(
                    f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{msg_id}",
                    headers=auth,
                    params={"format": "full"},
                )
                if detail.status_code != 200:
                    continue
                md = detail.json()
                headers_list = md.get("payload", {}).get("headers", [])
                hdrs = {h["name"]: h["value"] for h in headers_list}
                body = _extract_body(md.get("payload", {}))
                emails.append({
                    "id": msg_id,
                    "subject": hdrs.get("Subject", "(no subject)"),
                    "from": hdrs.get("From", ""),
                    "date": hdrs.get("Date", ""),
                    "snippet": md.get("snippet", ""),
                    "body": body[:3000],
                    "labels": md.get("labelIds", []),
                })

            return {"emails": emails, "new_history_id": new_history_id, "count": len(emails)}

    except Exception as e:
        logger.exception("fetch_new_emails failed: %s", e)
        return {"emails": [], "new_history_id": last_history_id, "count": 0, "error": str(e)}
    finally:
        db.close()


@activity.defn
async def fetch_upcoming_events(tenant_id: str, hours_ahead: int = 24) -> Dict[str, Any]:
    """Fetch upcoming Google Calendar events within the next N hours."""
    db = SessionLocal()
    try:
        token = _get_google_token(db, tenant_id, "google_calendar")
        if not token:
            return {"events": [], "count": 0, "error": "no_token"}

        auth = {"Authorization": f"Bearer {token}"}
        now = datetime.now(timezone.utc)
        time_min = now.isoformat()
        time_max = (now + timedelta(hours=hours_ahead)).isoformat()

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                "https://www.googleapis.com/calendar/v3/calendars/primary/events",
                headers=auth,
                params={
                    "timeMin": time_min,
                    "timeMax": time_max,
                    "maxResults": 20,
                    "singleEvents": "true",
                    "orderBy": "startTime",
                },
            )
            if resp.status_code != 200:
                return {"events": [], "count": 0, "error": f"api_{resp.status_code}"}

            data = resp.json()
            events = []
            for item in data.get("items", []):
                start = item.get("start", {})
                end = item.get("end", {})
                events.append({
                    "id": item.get("id"),
                    "summary": item.get("summary", "(no title)"),
                    "start": start.get("dateTime", start.get("date", "")),
                    "end": end.get("dateTime", end.get("date", "")),
                    "location": item.get("location", ""),
                    "description": (item.get("description", "") or "")[:300],
                    "attendees": [a.get("email", "") for a in item.get("attendees", [])][:10],
                })

            return {"events": events, "count": len(events)}

    except Exception as e:
        logger.exception("fetch_upcoming_events failed: %s", e)
        return {"events": [], "count": 0, "error": str(e)}
    finally:
        db.close()


@activity.defn
async def triage_items(
    tenant_id: str, emails: List[Dict], events: List[Dict],
) -> List[Dict[str, Any]]:
    """Triage emails and events using LLM + memory context enrichment.

    Uses build_memory_context() to check if email senders are known entities.
    Known contacts get a priority boost in the triage prompt.

    Returns list of triaged items (high/medium priority only) with:
        source, priority, title, body, reference_id, reference_type
    """
    if not emails and not events:
        return []

    # ── Memory enrichment: check if senders are known entities ──
    db = SessionLocal()
    known_contacts = {}
    try:
        from app.services.memory_recall import build_memory_context

        # Build context from sender names
        sender_names = " ".join(
            e.get("from", "").split("<")[0].strip() for e in emails if e.get("from")
        )
        if sender_names:
            memory_ctx = build_memory_context(db, uuid.UUID(tenant_id), sender_names)
            for ent in memory_ctx.get("relevant_entities", []):
                known_contacts[ent["name"].lower()] = ent
    except Exception as e:
        logger.debug("Memory enrichment for triage failed (non-fatal): %s", e)
    finally:
        db.close()

    # ── Build LLM triage prompt ──
    items_text = ""
    if emails:
        items_text += "=== NEW EMAILS ===\n"
        for e in emails:
            sender = e.get("from", "unknown")
            # Mark known contacts
            sender_lower = sender.split("<")[0].strip().lower()
            known_tag = ""
            for name, ent in known_contacts.items():
                if name in sender_lower or sender_lower in name:
                    known_tag = f" [KNOWN CONTACT: {ent.get('category', 'contact')}, {ent.get('description', '')}]"
                    break
            items_text += (
                f"\nFrom: {sender}{known_tag}\n"
                f"Subject: {e['subject']}\n"
                f"Date: {e['date']}\n"
                f"Snippet: {e['snippet']}\n"
                f"Body preview: {e['body'][:500]}\n---\n"
            )

    if events:
        items_text += "\n=== UPCOMING EVENTS ===\n"
        for ev in events:
            items_text += (
                f"\nEvent: {ev['summary']}\n"
                f"Start: {ev['start']}\nEnd: {ev['end']}\n"
                f"Location: {ev.get('location', 'N/A')}\n"
                f"Attendees: {', '.join(ev.get('attendees', []))}\n---\n"
            )

    system_prompt = """You are Luna, an AI assistant that triages emails and calendar events.

Classify each item as:
- "high": Requires immediate attention. Examples: job offers, urgent deadlines within 24h, time-sensitive business decisions, security alerts, messages from known contacts or VIPs, financial transactions requiring action, important meeting changes.
- "medium": Important but not urgent. Examples: meetings in the next 24h, action items, project updates from team members, scheduled calls.
- "low": Informational only. Examples: newsletters, marketing emails, automated notifications, social media alerts, routine reminders.

Items tagged [KNOWN CONTACT] are from people the user has interacted with before — boost their priority.

For each item, produce a JSON object. Skip items classified as "low".

Respond ONLY with a JSON array (no markdown fences):
[
  {
    "source": "gmail" or "calendar",
    "priority": "high" or "medium",
    "title": "Brief summary (max 100 chars)",
    "body": "Why this matters and suggested action (1-2 sentences)",
    "reference_id": "the email id or event id",
    "reference_type": "email" or "event"
  }
]

If nothing is high or medium priority, respond with: []"""

    try:
        from app.services.llm.legacy_service import get_llm_service
        llm = get_llm_service()
        response = llm.generate_chat_response(
            user_message=items_text,
            conversation_history=[],
            system_prompt=system_prompt,
            max_tokens=2000,
            temperature=0.3,
        )

        text = response.get("text", "[]").strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

        triaged = json.loads(text)
        if not isinstance(triaged, list):
            return []

        return triaged

    except Exception as e:
        logger.exception("triage_items LLM call failed: %s", e)
        # Fallback: treat known-contact emails as medium
        fallback = []
        for email in emails:
            sender_lower = email.get("from", "").split("<")[0].strip().lower()
            is_known = any(name in sender_lower or sender_lower in name for name in known_contacts)
            if is_known:
                fallback.append({
                    "source": "gmail",
                    "priority": "medium",
                    "title": email.get("subject", "New email"),
                    "body": f"From known contact: {email.get('from', 'unknown')}",
                    "reference_id": email.get("id", ""),
                    "reference_type": "email",
                })
        return fallback


@activity.defn
async def create_notifications(tenant_id: str, triaged_items: List[Dict]) -> Dict[str, Any]:
    """Create Notification rows for triaged items.

    Deduplicates by reference_id (works for both emails and calendar events).
    """
    if not triaged_items:
        return {"created": 0, "skipped": 0}

    db = SessionLocal()
    try:
        tid = uuid.UUID(tenant_id)
        created = 0
        skipped = 0

        for item in triaged_items:
            ref_id = item.get("reference_id")

            # Deduplicate: skip if already notified about this email or event
            if ref_id:
                existing = db.query(Notification.id).filter(
                    Notification.tenant_id == tid,
                    Notification.reference_id == ref_id,
                ).first()
                if existing:
                    skipped += 1
                    continue

            notif = Notification(
                tenant_id=tid,
                title=item.get("title", "New notification")[:255],
                body=item.get("body"),
                source=item.get("source", "system"),
                priority=item.get("priority", "medium"),
                reference_id=ref_id,
                reference_type=item.get("reference_type"),
            )
            db.add(notif)
            created += 1

        db.commit()

        if created > 0:
            log_activity(
                db,
                tenant_id=tid,
                event_type="notification_created",
                description=f"Proactive monitor created {created} notifications",
                source="inbox_monitor",
                event_metadata={"created": created, "skipped": skipped},
            )

        return {"created": created, "skipped": skipped}

    except Exception as e:
        logger.exception("create_notifications failed: %s", e)
        db.rollback()
        return {"created": 0, "skipped": 0, "error": str(e)}
    finally:
        db.close()


@activity.defn
async def extract_from_emails(tenant_id: str, emails: List[Dict], triaged_items: List[Dict]) -> Dict[str, Any]:
    """Extract entities, relations, memories, and action triggers from important emails.

    Uses the SAME extraction pipeline as chat messages:
    KnowledgeExtractionService.extract_from_content(content_type="plain_text", activity_source="gmail")

    Only processes emails that were triaged as high/medium priority.
    Action triggers (reminders, follow-ups) are dispatched to Temporal.
    """
    if not triaged_items:
        return {"entities": 0, "relations": 0, "memories": 0, "triggers": 0}

    # Build set of important email IDs from triage
    important_ids = {
        item["reference_id"] for item in triaged_items
        if item.get("reference_type") == "email" and item.get("reference_id")
    }

    # Filter emails to only important ones
    important_emails = [e for e in emails if e.get("id") in important_ids]
    if not important_emails:
        return {"entities": 0, "relations": 0, "memories": 0, "triggers": 0}

    db = SessionLocal()
    try:
        from app.services.knowledge_extraction import KnowledgeExtractionService

        tid = uuid.UUID(tenant_id)
        extraction_service = KnowledgeExtractionService()

        total_entities = 0
        total_relations = 0
        total_memories = 0
        total_triggers = 0

        for email in important_emails:
            # Build content string from email
            content = (
                f"Email from: {email.get('from', 'unknown')}\n"
                f"Subject: {email.get('subject', '')}\n"
                f"Date: {email.get('date', '')}\n\n"
                f"{email.get('body', '')}"
            )

            result = extraction_service.extract_from_content(
                db=db,
                tenant_id=tid,
                content=content,
                content_type="plain_text",
                activity_source="gmail",  # Logs as source="gmail" in MemoryActivity
            )

            total_entities += len(result.get("entities", []))
            total_relations += len(result.get("relations", []))
            total_memories += len(result.get("memories", []))

            # Dispatch action triggers (same pattern as chat.py _dispatch_action_triggers)
            triggers = result.get("action_triggers", [])
            if triggers:
                total_triggers += len(triggers)
                _dispatch_email_action_triggers(db, tid, triggers, tenant_id)

        return {
            "entities": total_entities,
            "relations": total_relations,
            "memories": total_memories,
            "triggers": total_triggers,
        }

    except Exception as e:
        logger.exception("extract_from_emails failed: %s", e)
        return {"entities": 0, "relations": 0, "memories": 0, "triggers": 0, "error": str(e)}
    finally:
        db.close()


def _dispatch_email_action_triggers(db, tid: uuid.UUID, triggers: List[Dict], tenant_id_str: str):
    """Dispatch action triggers from email extraction to Temporal.

    Same pattern as chat.py _dispatch_action_triggers, adapted for email context.
    """
    import asyncio

    for trigger in triggers:
        trigger_type = trigger.get("type", "")
        if trigger_type not in ("reminder", "follow_up", "research"):
            continue

        try:
            description = trigger.get("description", "")
            delay_hours = trigger.get("delay_hours", 24)
            entity_name = trigger.get("entity_name", "")

            async def _start():
                from temporalio.client import Client as TemporalClient
                from app.workflows.follow_up import FollowUpInput, FollowUpWorkflow

                client = await TemporalClient.connect(settings.TEMPORAL_ADDRESS)
                await client.start_workflow(
                    FollowUpWorkflow.run,
                    FollowUpInput(
                        entity_id=entity_name,
                        tenant_id=tenant_id_str,
                        action=trigger_type,
                        delay_hours=delay_hours,
                        message=description,
                    ),
                    id=f"email-trigger-{tenant_id_str[:8]}-{entity_name[:20]}-{int(time.time())}",
                    task_queue="servicetsunami-orchestration",
                )

            asyncio.get_event_loop().run_until_complete(_start())

            log_activity(
                db, tid, "action_triggered",
                f"Email trigger: {description}",
                source="gmail",
                event_metadata={
                    "trigger_type": trigger_type,
                    "delay_hours": delay_hours,
                    "entity_name": entity_name,
                },
            )
        except Exception as e:
            logger.warning("Failed to dispatch email action trigger: %s", e)


@activity.defn
async def log_monitor_cycle(
    tenant_id: str,
    workflow_run_id: str,
    email_count: int,
    event_count: int,
    notifications_created: int,
    entities_extracted: int,
) -> Dict[str, Any]:
    """Log a monitor scan cycle to MemoryActivity for audit trail."""
    db = SessionLocal()
    try:
        tid = uuid.UUID(tenant_id)
        log_activity(
            db,
            tenant_id=tid,
            event_type="monitor_scan",
            description=(
                f"Inbox scan: {email_count} emails, {event_count} events, "
                f"{notifications_created} alerts, {entities_extracted} entities extracted"
            ),
            source="inbox_monitor",
            event_metadata={
                "email_count": email_count,
                "event_count": event_count,
                "notifications_created": notifications_created,
                "entities_extracted": entities_extracted,
            },
            workflow_run_id=workflow_run_id,
        )
        return {"logged": True}
    except Exception as e:
        logger.exception("log_monitor_cycle failed: %s", e)
        return {"logged": False, "error": str(e)}
    finally:
        db.close()
```

**Step 2: Commit**

```bash
git add apps/api/app/workflows/activities/inbox_monitor.py
git commit -m "feat: add inbox monitor activities with memory extraction pipeline"
```

---

## Task 5: Inbox Monitor Workflow

**Files:**
- Create: `apps/api/app/workflows/inbox_monitor.py`

Uses plain args for `continue_as_new` (matching `ChannelHealthMonitorWorkflow` pattern).

**Step 1: Create the workflow**

Create `apps/api/app/workflows/inbox_monitor.py`:

```python
"""Temporal workflow for proactive Gmail + Calendar monitoring.

Long-running workflow (one per tenant) that periodically checks for new
emails and upcoming events, triages them with an LLM enriched by memory
context, creates notifications, and extracts entities/memories from
important emails through the standard knowledge extraction pipeline.

Uses continue_as_new to prevent history growth (same as ChannelHealthMonitorWorkflow).
"""
from temporalio import workflow
from datetime import timedelta
from typing import Optional


@workflow.defn(sandboxed=False)
class InboxMonitorWorkflow:
    """Periodic inbox monitor for Gmail and Calendar.

    Runs every N seconds (default 15 min):
    fetch emails → fetch events → triage (with memory enrichment) →
    create notifications → extract entities from important emails → log → continue_as_new

    One workflow instance per tenant. Workflow ID: inbox-monitor-{tenant_id}
    """

    @workflow.run
    async def run(
        self,
        tenant_id: str,
        check_interval_seconds: int = 900,
        last_gmail_history_id: Optional[str] = None,
        calendar_hours_ahead: int = 24,
    ) -> dict:
        retry_policy = workflow.RetryPolicy(
            maximum_attempts=3,
            initial_interval=timedelta(seconds=15),
            backoff_coefficient=2.0,
        )
        activity_timeout = timedelta(minutes=2)

        workflow.logger.info(f"Inbox monitor cycle for tenant {tenant_id[:8]}")

        # Step 1: Fetch new emails
        email_result = await workflow.execute_activity(
            "fetch_new_emails",
            args=[tenant_id, last_gmail_history_id],
            start_to_close_timeout=activity_timeout,
            retry_policy=retry_policy,
        )
        emails = email_result.get("emails", [])
        new_history_id = email_result.get("new_history_id", last_gmail_history_id)

        # Step 2: Fetch upcoming calendar events
        event_result = await workflow.execute_activity(
            "fetch_upcoming_events",
            args=[tenant_id, calendar_hours_ahead],
            start_to_close_timeout=activity_timeout,
            retry_policy=retry_policy,
        )
        events = event_result.get("events", [])

        # Step 3: Triage items with LLM + memory context enrichment
        triaged_items = []
        if emails or events:
            triaged_items = await workflow.execute_activity(
                "triage_items",
                args=[tenant_id, emails, events],
                start_to_close_timeout=timedelta(minutes=3),
                retry_policy=retry_policy,
            )

        # Step 4: Create notifications (deduplicates by reference_id)
        notif_result = await workflow.execute_activity(
            "create_notifications",
            args=[tenant_id, triaged_items],
            start_to_close_timeout=activity_timeout,
            retry_policy=retry_policy,
        )
        notifications_created = notif_result.get("created", 0)

        # Step 5: Extract entities/relations/memories from important emails
        extraction_result = {"entities": 0}
        if emails and triaged_items:
            extraction_result = await workflow.execute_activity(
                "extract_from_emails",
                args=[tenant_id, emails, triaged_items],
                start_to_close_timeout=timedelta(minutes=5),
                retry_policy=retry_policy,
            )

        # Step 6: Log the scan cycle
        wf_info = workflow.info()
        await workflow.execute_activity(
            "log_monitor_cycle",
            args=[
                tenant_id,
                wf_info.run_id,
                len(emails),
                len(events),
                notifications_created,
                extraction_result.get("entities", 0),
            ],
            start_to_close_timeout=activity_timeout,
            retry_policy=retry_policy,
        )

        # Sleep then continue as new (plain args, matching ChannelHealthMonitor pattern)
        await workflow.sleep(timedelta(seconds=check_interval_seconds))

        workflow.continue_as_new(args=[
            tenant_id,
            check_interval_seconds,
            new_history_id,
            calendar_hours_ahead,
        ])
```

**Step 2: Commit**

```bash
git add apps/api/app/workflows/inbox_monitor.py
git commit -m "feat: add InboxMonitorWorkflow with continue_as_new and extraction pipeline"
```

---

## Task 6: Register Workflow in Orchestration Worker

**Files:**
- Modify: `apps/api/app/workers/orchestration_worker.py`

**Step 1: Add imports and register**

Add after existing imports:

```python
from app.workflows.inbox_monitor import InboxMonitorWorkflow
from app.workflows.activities.inbox_monitor import (
    fetch_new_emails,
    fetch_upcoming_events,
    triage_items,
    create_notifications,
    extract_from_emails,
    log_monitor_cycle,
)
```

Add `InboxMonitorWorkflow` to the `workflows` list.

Add the six activities to the `activities` list:
- `fetch_new_emails`
- `fetch_upcoming_events`
- `triage_items`
- `create_notifications`
- `extract_from_emails`
- `log_monitor_cycle`

**Step 2: Commit**

```bash
git add apps/api/app/workers/orchestration_worker.py
git commit -m "feat: register InboxMonitorWorkflow in orchestration worker"
```

---

## Task 7: Start/Stop/Status API Endpoints

**Files:**
- Modify: `apps/api/app/api/v1/workflows.py`

Uses direct `client.start_workflow(Workflow.run, ...)` pattern — NOT the `services/workflows.py` helper — matching how dataclass/plain-arg workflows are started in `remedia.py` and `chat.py`.

**Step 1: Add start/stop/status endpoints**

Add to `apps/api/app/api/v1/workflows.py`:

```python
@router.post("/inbox-monitor/start")
async def start_inbox_monitor(
    check_interval_minutes: int = 15,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    """Start the proactive inbox monitor for the current tenant."""
    from temporalio.client import Client
    from app.workflows.inbox_monitor import InboxMonitorWorkflow

    tenant_id = str(current_user.tenant_id)
    workflow_id = f"inbox-monitor-{tenant_id}"
    interval = max(5, min(check_interval_minutes, 60)) * 60  # Clamp 5-60 min → seconds

    try:
        client = await Client.connect(settings.TEMPORAL_ADDRESS)
        handle = await client.start_workflow(
            InboxMonitorWorkflow.run,
            args=[tenant_id, interval],
            id=workflow_id,
            task_queue="servicetsunami-orchestration",
        )
        return {
            "status": "started",
            "workflow_id": workflow_id,
            "run_id": handle.result_run_id,
            "interval_minutes": check_interval_minutes,
        }
    except Exception as e:
        if "already started" in str(e).lower() or "already running" in str(e).lower():
            return {"status": "already_running", "workflow_id": workflow_id}
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/inbox-monitor/stop")
async def stop_inbox_monitor(
    current_user: User = Depends(deps.get_current_active_user),
):
    """Stop the proactive inbox monitor for the current tenant."""
    from temporalio.client import Client

    tenant_id = str(current_user.tenant_id)
    workflow_id = f"inbox-monitor-{tenant_id}"

    try:
        client = await Client.connect(settings.TEMPORAL_ADDRESS)
        handle = client.get_workflow_handle(workflow_id)
        await handle.cancel()
        return {"status": "stopped", "workflow_id": workflow_id}
    except Exception as e:
        if "not found" in str(e).lower():
            return {"status": "not_running", "workflow_id": workflow_id}
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/inbox-monitor/status")
async def inbox_monitor_status(
    current_user: User = Depends(deps.get_current_active_user),
):
    """Check if the inbox monitor is running for the current tenant."""
    from app.services import workflows

    tenant_id = str(current_user.tenant_id)
    workflow_id = f"inbox-monitor-{tenant_id}"

    try:
        desc = await workflows.describe_workflow(workflow_id=workflow_id)
        return {
            "running": desc.get("status") == "WORKFLOW_EXECUTION_STATUS_RUNNING",
            "workflow_id": workflow_id,
            "status": desc.get("status"),
            "start_time": desc.get("start_time"),
        }
    except Exception:
        return {"running": False, "workflow_id": workflow_id, "status": None}
```

Ensure `settings` and `HTTPException` imports are available at the top of the file.

**Step 2: Commit**

```bash
git add apps/api/app/api/v1/workflows.py
git commit -m "feat: add inbox monitor start/stop/status endpoints (direct client pattern)"
```

---

## Task 8: Auto-Start Monitor on Google OAuth Connect

**Files:**
- Modify: `apps/api/app/api/v1/oauth.py`

When a user connects Google OAuth, automatically start the inbox monitor workflow if it's not already running.

**Step 1: Add auto-start after successful callback**

In `apps/api/app/api/v1/oauth.py`, after the token storage loop (around line 462, after `logger.info("OAuth %s connected...")`), add:

```python
    # Auto-start inbox monitor when Google connects
    if provider == "google":
        try:
            import asyncio
            from temporalio.client import Client as TemporalClient
            from app.workflows.inbox_monitor import InboxMonitorWorkflow

            async def _start_monitor():
                tc = await TemporalClient.connect(settings.TEMPORAL_ADDRESS)
                wf_id = f"inbox-monitor-{tenant_id}"
                await tc.start_workflow(
                    InboxMonitorWorkflow.run,
                    args=[str(tenant_id), 900],  # 15 min interval
                    id=wf_id,
                    task_queue="servicetsunami-orchestration",
                )
                logger.info("Auto-started inbox monitor for tenant=%s", tenant_id)

            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    asyncio.ensure_future(_start_monitor())
                else:
                    loop.run_until_complete(_start_monitor())
            except RuntimeError:
                asyncio.run(_start_monitor())
        except Exception as e:
            # Don't fail OAuth if monitor start fails
            logger.warning("Auto-start inbox monitor failed (non-fatal): %s", e)
```

**Step 2: Commit**

```bash
git add apps/api/app/api/v1/oauth.py
git commit -m "feat: auto-start inbox monitor when Google OAuth connects"
```

---

## Task 9: ADK Tool for Luna to Control Monitor

**Files:**
- Create: `apps/adk-server/tools/monitor_tools.py`
- Modify: `apps/adk-server/servicetsunami_supervisor/personal_assistant.py`

Luna should be able to say "I'll start monitoring your inbox" or respond to "stop monitoring my email".

**Step 1: Create monitor tools**

Create `apps/adk-server/tools/monitor_tools.py`:

```python
"""Inbox monitor control tools for Luna.

Allows Luna to start, stop, and check the status of the proactive
inbox monitor via the API's Temporal workflow endpoints.
"""
import logging
from typing import Optional

import httpx

from config.settings import settings
from tools.knowledge_tools import _resolve_tenant_id

logger = logging.getLogger(__name__)

_api_client: Optional[httpx.AsyncClient] = None


def _get_api_client() -> httpx.AsyncClient:
    global _api_client
    if _api_client is None:
        _api_client = httpx.AsyncClient(
            base_url=settings.api_base_url,
            timeout=30.0,
        )
    return _api_client


async def start_inbox_monitor(
    tenant_id: str = "auto",
    interval_minutes: int = 15,
) -> dict:
    """Start proactive monitoring of the user's Gmail and Calendar.

    Luna will check for new emails and upcoming events every N minutes,
    create notifications for important items, and extract entities from
    significant emails.

    Args:
        tenant_id: Tenant context. Use "auto" if unknown.
        interval_minutes: How often to check (5-60 minutes, default 15).

    Returns:
        Dict with monitoring status.
    """
    tenant_id = _resolve_tenant_id(tenant_id)
    client = _get_api_client()

    try:
        resp = await client.post(
            f"/api/v1/workflows/inbox-monitor/start",
            headers={"X-Internal-Key": settings.mcp_api_key},
            params={
                "tenant_id": tenant_id,
                "check_interval_minutes": interval_minutes,
            },
        )
        if resp.status_code == 200:
            data = resp.json()
            if data.get("status") == "already_running":
                return {"status": "already_active", "message": "Inbox monitoring is already active."}
            return {
                "status": "started",
                "message": f"I'll now monitor your inbox every {interval_minutes} minutes and notify you of important items.",
                "interval_minutes": interval_minutes,
            }
        return {"error": f"Failed to start monitor: {resp.status_code}"}
    except Exception as e:
        logger.exception("start_inbox_monitor failed")
        return {"error": f"Failed to start monitoring: {str(e)}"}


async def stop_inbox_monitor(
    tenant_id: str = "auto",
) -> dict:
    """Stop proactive monitoring of the user's Gmail and Calendar.

    Args:
        tenant_id: Tenant context. Use "auto" if unknown.

    Returns:
        Dict with status.
    """
    tenant_id = _resolve_tenant_id(tenant_id)
    client = _get_api_client()

    try:
        resp = await client.post(
            f"/api/v1/workflows/inbox-monitor/stop",
            headers={"X-Internal-Key": settings.mcp_api_key},
            params={"tenant_id": tenant_id},
        )
        if resp.status_code == 200:
            data = resp.json()
            if data.get("status") == "not_running":
                return {"status": "not_running", "message": "Inbox monitoring was not active."}
            return {"status": "stopped", "message": "Inbox monitoring has been stopped."}
        return {"error": f"Failed to stop monitor: {resp.status_code}"}
    except Exception as e:
        logger.exception("stop_inbox_monitor failed")
        return {"error": f"Failed to stop monitoring: {str(e)}"}


async def check_inbox_monitor_status(
    tenant_id: str = "auto",
) -> dict:
    """Check if proactive inbox monitoring is currently active.

    Args:
        tenant_id: Tenant context. Use "auto" if unknown.

    Returns:
        Dict with monitoring status and details.
    """
    tenant_id = _resolve_tenant_id(tenant_id)
    client = _get_api_client()

    try:
        resp = await client.get(
            f"/api/v1/workflows/inbox-monitor/status",
            headers={"X-Internal-Key": settings.mcp_api_key},
            params={"tenant_id": tenant_id},
        )
        if resp.status_code == 200:
            data = resp.json()
            if data.get("running"):
                return {
                    "status": "active",
                    "message": "Inbox monitoring is active. I'm checking your email and calendar periodically.",
                    "since": data.get("start_time"),
                }
            return {"status": "inactive", "message": "Inbox monitoring is not active."}
        return {"error": f"Status check failed: {resp.status_code}"}
    except Exception as e:
        logger.exception("check_inbox_monitor_status failed")
        return {"error": f"Failed to check status: {str(e)}"}
```

**Step 2: Register tools in personal_assistant.py**

In `apps/adk-server/servicetsunami_supervisor/personal_assistant.py`, add the tools to Luna's tool list:

```python
from tools.monitor_tools import (
    start_inbox_monitor,
    stop_inbox_monitor,
    check_inbox_monitor_status,
)
```

Add to the `tools` list in the Luna agent definition:
```python
tools=[
    # ... existing tools ...
    start_inbox_monitor,
    stop_inbox_monitor,
    check_inbox_monitor_status,
]
```

Add to Luna's instructions:
```
- Inbox Monitoring: You can proactively monitor the user's Gmail and Calendar. Use start_inbox_monitor to begin, stop_inbox_monitor to stop, check_inbox_monitor_status to check. When the user mentions wanting to stay on top of their inbox or asks about email monitoring, offer to start it. If they ask "what's going on with my emails?", check the monitor status first, then search_emails.
```

**Step 3: Commit**

```bash
git add apps/adk-server/tools/monitor_tools.py apps/adk-server/servicetsunami_supervisor/personal_assistant.py
git commit -m "feat: add ADK monitor tools so Luna can control inbox monitoring"
```

---

## Task 10: Frontend Notification Service

**Files:**
- Create: `apps/web/src/services/notifications.js`

**Step 1: Create the notification service**

Create `apps/web/src/services/notifications.js`:

```javascript
import api from './api';

export const notificationService = {
  async getNotifications({ unreadOnly = false, skip = 0, limit = 20 } = {}) {
    const params = new URLSearchParams();
    if (unreadOnly) params.append('unread_only', 'true');
    params.append('skip', skip);
    params.append('limit', limit);
    const response = await api.get(`/notifications?${params.toString()}`);
    return response.data;
  },

  async getUnreadCount() {
    const response = await api.get('/notifications/count');
    return response.data.unread;
  },

  async markRead(id) {
    await api.patch(`/notifications/${id}/read`);
  },

  async markAllRead() {
    await api.patch('/notifications/read-all');
  },

  async dismiss(id) {
    await api.delete(`/notifications/${id}`);
  },

  // Inbox monitor controls
  async startInboxMonitor(intervalMinutes = 15) {
    const response = await api.post(`/workflows/inbox-monitor/start?check_interval_minutes=${intervalMinutes}`);
    return response.data;
  },

  async stopInboxMonitor() {
    const response = await api.post('/workflows/inbox-monitor/stop');
    return response.data;
  },

  async getInboxMonitorStatus() {
    const response = await api.get('/workflows/inbox-monitor/status');
    return response.data;
  },
};
```

**Step 2: Commit**

```bash
git add apps/web/src/services/notifications.js
git commit -m "feat: add frontend notification service"
```

---

## Task 11: Notification Bell Component

**Files:**
- Create: `apps/web/src/components/NotificationBell.js`

**Step 1: Create the notification bell**

Create `apps/web/src/components/NotificationBell.js`:

```jsx
import { useState, useEffect, useCallback, useRef } from 'react';
import { Badge, Dropdown } from 'react-bootstrap';
import {
  FaBell,
  FaEnvelope,
  FaCalendarAlt,
  FaExclamationTriangle,
  FaCheck,
  FaCheckDouble,
} from 'react-icons/fa';
import { notificationService } from '../services/notifications';

const SOURCE_ICONS = {
  gmail: FaEnvelope,
  calendar: FaCalendarAlt,
  system: FaBell,
};

const PRIORITY_COLORS = {
  high: '#ff4757',
  medium: '#ffa502',
  low: '#747d8c',
};

const NotificationBell = () => {
  const [notifications, setNotifications] = useState([]);
  const [unreadCount, setUnreadCount] = useState(0);
  const [loading, setLoading] = useState(false);
  const intervalRef = useRef(null);

  const fetchCount = useCallback(async () => {
    try {
      const count = await notificationService.getUnreadCount();
      setUnreadCount(count);
    } catch {
      // Silent fail
    }
  }, []);

  const fetchNotifications = useCallback(async () => {
    setLoading(true);
    try {
      const data = await notificationService.getNotifications({ limit: 10 });
      setNotifications(data);
    } catch {
      // Silent fail
    } finally {
      setLoading(false);
    }
  }, []);

  // Poll unread count every 60s
  useEffect(() => {
    fetchCount();
    intervalRef.current = setInterval(fetchCount, 60000);
    return () => clearInterval(intervalRef.current);
  }, [fetchCount]);

  const handleToggle = (isOpen) => {
    if (isOpen) fetchNotifications();
  };

  const handleMarkRead = async (id, e) => {
    e.stopPropagation();
    await notificationService.markRead(id);
    setNotifications(prev => prev.map(n => n.id === id ? { ...n, read: true } : n));
    setUnreadCount(prev => Math.max(0, prev - 1));
  };

  const handleMarkAllRead = async (e) => {
    e.stopPropagation();
    await notificationService.markAllRead();
    setNotifications(prev => prev.map(n => ({ ...n, read: true })));
    setUnreadCount(0);
  };

  const handleDismiss = async (id, e) => {
    e.stopPropagation();
    await notificationService.dismiss(id);
    setNotifications(prev => prev.filter(n => n.id !== id));
    setUnreadCount(prev => Math.max(0, prev - 1));
  };

  const formatTime = (dateStr) => {
    const d = new Date(dateStr);
    const now = new Date();
    const diff = (now - d) / 1000 / 60;
    if (diff < 60) return `${Math.round(diff)}m ago`;
    if (diff < 1440) return `${Math.round(diff / 60)}h ago`;
    return d.toLocaleDateString();
  };

  return (
    <Dropdown align="end" onToggle={handleToggle}>
      <Dropdown.Toggle
        variant="link"
        className="notification-bell-toggle"
        style={{
          position: 'relative',
          color: 'var(--text-secondary)',
          padding: '4px 8px',
          border: 'none',
          background: 'none',
        }}
      >
        <FaBell size={18} />
        {unreadCount > 0 && (
          <Badge
            bg="danger"
            pill
            style={{
              position: 'absolute',
              top: 0,
              right: 0,
              fontSize: '0.65rem',
              minWidth: '16px',
            }}
          >
            {unreadCount > 99 ? '99+' : unreadCount}
          </Badge>
        )}
      </Dropdown.Toggle>

      <Dropdown.Menu
        style={{
          width: '380px',
          maxHeight: '480px',
          overflowY: 'auto',
          background: 'var(--bg-card)',
          border: '1px solid var(--border-color)',
          boxShadow: '0 8px 32px rgba(0,0,0,0.3)',
        }}
      >
        <div style={{
          display: 'flex', justifyContent: 'space-between', alignItems: 'center',
          padding: '8px 16px', borderBottom: '1px solid var(--border-color)',
        }}>
          <strong style={{ color: 'var(--text-primary)' }}>Notifications</strong>
          {unreadCount > 0 && (
            <button onClick={handleMarkAllRead} style={{
              background: 'none', border: 'none', color: 'var(--bs-primary)',
              fontSize: '0.8rem', cursor: 'pointer',
            }}>
              <FaCheckDouble size={12} className="me-1" /> Mark all read
            </button>
          )}
        </div>

        {loading && notifications.length === 0 && (
          <div style={{ padding: '20px', textAlign: 'center', color: 'var(--text-muted)' }}>Loading...</div>
        )}

        {!loading && notifications.length === 0 && (
          <div style={{ padding: '20px', textAlign: 'center', color: 'var(--text-muted)' }}>No notifications yet</div>
        )}

        {notifications.map((n) => {
          const Icon = SOURCE_ICONS[n.source] || FaBell;
          return (
            <Dropdown.Item key={n.id} as="div" style={{
              padding: '10px 16px', borderBottom: '1px solid var(--border-color)',
              background: n.read ? 'transparent' : 'rgba(var(--bs-primary-rgb), 0.05)', cursor: 'default',
            }}>
              <div style={{ display: 'flex', gap: '10px', alignItems: 'flex-start' }}>
                <Icon size={16} style={{ color: PRIORITY_COLORS[n.priority] || '#ffa502', marginTop: '3px' }} />
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{
                    fontWeight: n.read ? 400 : 600, fontSize: '0.85rem', color: 'var(--text-primary)',
                    whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
                  }}>
                    {n.priority === 'high' && <FaExclamationTriangle size={10} className="me-1" style={{ color: '#ff4757' }} />}
                    {n.title}
                  </div>
                  {n.body && (
                    <div style={{ fontSize: '0.78rem', color: 'var(--text-muted)', marginTop: '2px', lineHeight: 1.3 }}>
                      {n.body.length > 120 ? n.body.slice(0, 120) + '...' : n.body}
                    </div>
                  )}
                  <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', marginTop: '4px', display: 'flex', gap: '8px' }}>
                    <span>{formatTime(n.created_at)}</span>
                    {!n.read && (
                      <button onClick={(e) => handleMarkRead(n.id, e)} style={{
                        background: 'none', border: 'none', color: 'var(--bs-primary)', padding: 0, cursor: 'pointer', fontSize: '0.7rem',
                      }}><FaCheck size={10} /> Read</button>
                    )}
                    <button onClick={(e) => handleDismiss(n.id, e)} style={{
                      background: 'none', border: 'none', color: 'var(--text-muted)', padding: 0, cursor: 'pointer', fontSize: '0.7rem',
                    }}>Dismiss</button>
                  </div>
                </div>
              </div>
            </Dropdown.Item>
          );
        })}
      </Dropdown.Menu>
    </Dropdown>
  );
};

export default NotificationBell;
```

**Step 2: Commit**

```bash
git add apps/web/src/components/NotificationBell.js
git commit -m "feat: add NotificationBell component with dropdown"
```

---

## Task 12: Add Notification Bell to Layout

**Files:**
- Modify: `apps/web/src/components/Layout.js`

**Step 1: Add import**

```javascript
import NotificationBell from './NotificationBell';
```

**Step 2: Insert bell in sidebar header**

In the `sidebar-header` div (line ~87), wrap the theme toggle in a flex container and add the bell:

Replace:
```jsx
<button className="theme-toggle" ...>
```

With:
```jsx
<div className="d-flex align-items-center gap-1">
  <NotificationBell />
  <button className="theme-toggle" ...>
```

And close the new div after the button.

**Step 3: Commit**

```bash
git add apps/web/src/components/Layout.js
git commit -m "feat: add notification bell to sidebar header"
```

---

## Task 13: Monitor Toggle in SkillsConfigPanel

**Files:**
- Modify: `apps/web/src/components/SkillsConfigPanel.js`

The Google OAuth connection is managed here via `SKILL_CREDENTIAL_SCHEMAS` registry. Add a "Proactive Monitoring" toggle that appears when Google is connected.

**Step 1: Add import and state**

Add import:
```javascript
import { notificationService } from '../services/notifications';
```

Add state near other useState hooks:
```javascript
const [monitorRunning, setMonitorRunning] = useState(false);
```

**Step 2: Fetch monitor status when Google is connected**

In the existing `fetchData` callback, after OAuth statuses are loaded, add:

```javascript
// Check inbox monitor status if Google is connected
if (statuses.google?.connected) {
  try {
    const monitorStatus = await notificationService.getInboxMonitorStatus();
    setMonitorRunning(monitorStatus.running || false);
  } catch {
    // Ignore
  }
}
```

**Step 3: Add toggle handler**

```javascript
const handleToggleMonitor = async () => {
  try {
    if (monitorRunning) {
      await notificationService.stopInboxMonitor();
      setMonitorRunning(false);
    } else {
      await notificationService.startInboxMonitor(15);
      setMonitorRunning(true);
    }
  } catch (err) {
    console.error('Failed to toggle monitor:', err);
  }
};
```

**Step 4: Render toggle in Google card**

Find where the Google OAuth connected accounts are rendered (inside the skill card expansion). After the connected accounts list, add:

```jsx
{/* Show monitor toggle when Google is connected */}
{skill.oauth_provider === 'google' && oauthStatuses.google?.connected && (
  <div className="d-flex align-items-center justify-content-between mt-3 pt-3"
    style={{ borderTop: '1px solid var(--border-color)' }}>
    <div>
      <strong style={{ fontSize: '0.85rem' }}>Proactive Monitoring</strong>
      <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>
        Luna monitors your inbox & calendar every 15 min
      </div>
    </div>
    <Form.Check
      type="switch"
      checked={monitorRunning}
      onChange={handleToggleMonitor}
    />
  </div>
)}
```

**Step 5: Commit**

```bash
git add apps/web/src/components/SkillsConfigPanel.js
git commit -m "feat: add proactive monitoring toggle to Google skill card"
```

---

## Task 14: Add `notification_created` and `monitor_scan` to Memory Activity Constants

**Files:**
- Modify: `apps/web/src/components/memory/constants.js`

Add the new event types so they display properly in the Activity Feed.

**Step 1: Add to ACTIVITY_EVENT_CONFIG**

```javascript
// In ACTIVITY_EVENT_CONFIG, add:
notification_created: { icon: FaBell, color: '#ffa502', label: 'Notification Created' },
monitor_scan: { icon: FaSearch, color: '#747d8c', label: 'Inbox Scan' },
```

Add to ALL_ACTIVITY_SOURCES if not already present:
```javascript
'inbox_monitor'
```

**Step 2: Commit**

```bash
git add apps/web/src/components/memory/constants.js
git commit -m "feat: add inbox monitor event types to memory activity constants"
```

---

## Task 15: Deploy and Verify

**Step 1: Push to main**

```bash
git push origin main
```

**Step 2: Wait for CI/CD**

```bash
gh run list --limit 5
```

Wait for api, web, worker, and ADK workflows.

**Step 3: Run migration**

```bash
kubectl exec -it deploy/servicetsunami-api -n prod -- psql "$DATABASE_URL" -f /app/migrations/038_add_notifications.sql
```

**Step 4: Verify API**

```bash
TOKEN=$(curl -s -X POST https://servicetsunami.com/api/v1/auth/login \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "username=saguilera1608@gmail.com&password=@SebaSofi.2k25!!" | jq -r '.access_token')

# Notification count
curl -s https://servicetsunami.com/api/v1/notifications/count \
  -H "Authorization: Bearer $TOKEN" | jq .

# Start monitor
curl -s -X POST "https://servicetsunami.com/api/v1/workflows/inbox-monitor/start?check_interval_minutes=15" \
  -H "Authorization: Bearer $TOKEN" | jq .

# Status
curl -s https://servicetsunami.com/api/v1/workflows/inbox-monitor/status \
  -H "Authorization: Bearer $TOKEN" | jq .
```

**Step 5: Wait 15 min, verify notifications + extraction**

```bash
# Check notifications
curl -s https://servicetsunami.com/api/v1/notifications \
  -H "Authorization: Bearer $TOKEN" | jq .

# Check activity feed for gmail source
curl -s "https://servicetsunami.com/api/v1/memories/activity?source=gmail" \
  -H "Authorization: Bearer $TOKEN" | jq .

# Check activity feed for inbox_monitor source
curl -s "https://servicetsunami.com/api/v1/memories/activity?source=inbox_monitor" \
  -H "Authorization: Bearer $TOKEN" | jq .
```

**Step 6: Verify in browser**

1. Log in → check notification bell in sidebar header
2. Go to Integrations → Connected Apps → Gmail card → verify "Proactive Monitoring" toggle
3. Wait for first scan → notifications appear in bell dropdown
4. Go to Memory → Activity tab → verify "Inbox Scan" and gmail-source entity_created events
5. Go to Memory → Entities tab → verify entities extracted from emails have proper categories
6. Chat with Luna: "Are you monitoring my inbox?" → Luna should use check_inbox_monitor_status tool
