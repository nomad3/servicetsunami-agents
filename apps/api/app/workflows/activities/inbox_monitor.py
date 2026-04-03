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
from app.models.integration_config import IntegrationConfig
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


def _get_google_token(db, tenant_id: str, integration_name: str) -> Optional[str]:
    """Retrieve and auto-refresh Google OAuth token from credential vault."""
    tid = uuid.UUID(tenant_id)
    config = (
        db.query(IntegrationConfig)
        .filter(
            IntegrationConfig.tenant_id == tid,
            IntegrationConfig.integration_name == integration_name,
            IntegrationConfig.enabled.is_(True),
        )
        .first()
    )
    if not config:
        return None

    creds = retrieve_credentials_for_skill(db, config.id, tid)
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

            # Fallback: list recent messages (all, not just unread — user reads on multiple devices)
            if not last_history_id and not message_ids:
                resp = await client.get(
                    "https://gmail.googleapis.com/gmail/v1/users/me/messages",
                    headers=auth,
                    params={"maxResults": 30, "q": "newer_than:2d"},
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
            for msg_id in message_ids[:30]:  # Cap at 30 per cycle
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
                labels = md.get("labelIds", [])
                emails.append({
                    "id": msg_id,
                    "subject": hdrs.get("Subject", "(no subject)"),
                    "from": hdrs.get("From", ""),
                    "date": hdrs.get("Date", ""),
                    "snippet": md.get("snippet", ""),
                    "body": body[:3000],
                    "labels": labels,
                    "is_read": "UNREAD" not in labels,
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
    """
    if not emails and not events:
        return []

    # ── Memory enrichment: check if senders are known entities ──
    db = SessionLocal()
    known_contacts = {}
    try:
        from app.services.memory_recall import build_memory_context

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
        items_text += "=== EMAILS ===\n"
        for e in emails:
            sender = e.get("from", "unknown")
            sender_lower = sender.split("<")[0].strip().lower()
            known_tag = ""
            for name, ent in known_contacts.items():
                if name in sender_lower or sender_lower in name:
                    known_tag = f" [KNOWN CONTACT: {ent.get('category', 'contact')}, {ent.get('description', '')}]"
                    break
            read_tag = " [ALREADY READ]" if e.get("is_read") else " [UNREAD]"
            items_text += (
                f"\nFrom: {sender}{known_tag}{read_tag}\n"
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

IMPORTANT: The user reads emails on multiple devices (phone, laptop). Items tagged [ALREADY READ] have been opened by the user elsewhere but may STILL need action or a notification. Do NOT skip an email just because it was read. Judge priority by CONTENT, not read status.

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

    # ── Try local Qwen model first (zero token cost) ──
    try:
        from app.services.local_inference import triage_inbox_items as _qwen_triage
        qwen_result = await _qwen_triage(items_text)
        if qwen_result is not None:
            logger.info("triage_items: used local Qwen model (saved Anthropic tokens)")
            return qwen_result
        logger.debug("Qwen triage returned None — falling back to Anthropic")
    except Exception as e:
        logger.debug("Qwen triage failed (%s) — falling back to Anthropic", e)

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
    """Create Notification rows for triaged items. Deduplicates by reference_id."""
    if not triaged_items:
        return {"created": 0, "skipped": 0}

    db = SessionLocal()
    try:
        tid = uuid.UUID(tenant_id)
        created = 0
        skipped = 0

        for item in triaged_items:
            ref_id = item.get("reference_id")

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
            # High-priority notification triggers alert presence state
            if item.get("priority") == "high":
                try:
                    from app.services import luna_presence_service
                    luna_presence_service.update_state(tid, state="alert")
                except Exception:
                    pass

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
    """
    if not triaged_items:
        return {"entities": 0, "relations": 0, "memories": 0, "triggers": 0}

    important_ids = {
        item["reference_id"] for item in triaged_items
        if item.get("reference_type") == "email" and item.get("reference_id")
    }

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
                activity_source="gmail",
            )

            total_entities += len(result.get("entities", []))
            total_relations += len(result.get("relations", []))
            total_memories += len(result.get("memories", []))

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
    """Dispatch action triggers from email extraction via dynamic workflow."""
    import asyncio

    for trigger in triggers:
        trigger_type = trigger.get("type", "")
        if trigger_type not in ("reminder", "follow_up", "research"):
            continue

        try:
            description = trigger.get("description", "")
            delay_hours = trigger.get("delay_hours", 24)
            entity_name = trigger.get("entity_name", "")

            follow_up_input = {
                "entity_id": entity_name,
                "action": trigger_type,
                "delay_hours": delay_hours,
                "message": description,
            }

            async def _start():
                from app.services.dynamic_workflow_launcher import start_dynamic_workflow_by_name
                await start_dynamic_workflow_by_name(
                    "Sales Follow-Up", tenant_id_str, follow_up_input
                )

            try:
                running_loop = asyncio.get_running_loop()
            except RuntimeError:
                running_loop = None

            if running_loop is not None and running_loop.is_running():
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    pool.submit(lambda: asyncio.run(_start())).result(timeout=30)
            else:
                loop = asyncio.new_event_loop()
                try:
                    loop.run_until_complete(_start())
                finally:
                    loop.close()

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
async def check_proactive_triggers(
    tenant_id: str, events: List[Dict],
) -> Dict[str, Any]:
    """Check proactive memory triggers: meeting context and stale leads.

    For each upcoming event with attendees: find matching entities and create
    notifications with relevant context. Also find stale leads and notify.
    """
    db = SessionLocal()
    try:
        from app.services.memory_recall import find_meeting_context, find_stale_leads

        tid = uuid.UUID(tenant_id)
        meeting_notifications = 0
        lead_notifications = 0

        # --- Meeting context triggers ---
        for event in events:
            attendees = event.get("attendees", [])
            if not attendees:
                continue

            context_results = find_meeting_context(db, tid, attendees)
            if not context_results:
                continue

            # Build a notification body with entity context
            entity_names = [r["name"] for r in context_results]
            obs_summary_parts = []
            for r in context_results:
                for obs in r.get("observations", [])[:2]:
                    obs_summary_parts.append(f"- {obs['text'][:100]}")

            body = f"Known contacts in this meeting: {', '.join(entity_names)}"
            if obs_summary_parts:
                body += "\nRecent context:\n" + "\n".join(obs_summary_parts[:4])

            # Deduplicate by reference_id
            ref_id = f"meeting-ctx-{event.get('id', '')}"
            existing = db.query(Notification.id).filter(
                Notification.tenant_id == tid,
                Notification.reference_id == ref_id,
            ).first()
            if existing:
                continue

            notif = Notification(
                tenant_id=tid,
                title=f"Meeting prep: {event.get('summary', 'Upcoming meeting')}"[:255],
                body=body[:1000],
                source="calendar",
                priority="medium",
                reference_id=ref_id,
                reference_type="meeting_context",
            )
            db.add(notif)
            meeting_notifications += 1

        # --- Stale lead triggers ---
        stale_leads = find_stale_leads(db, tid, stale_days=7)
        for lead in stale_leads[:3]:
            ref_id = f"stale-lead-{lead['entity_id'][:8]}"
            existing = db.query(Notification.id).filter(
                Notification.tenant_id == tid,
                Notification.reference_id == ref_id,
            ).first()
            if existing:
                continue

            notif = Notification(
                tenant_id=tid,
                title=f"Stale lead: {lead['name']} ({lead['days_stale']}d inactive)"[:255],
                body=f"Lead '{lead['name']}' has had no activity for {lead['days_stale']} days. "
                     f"Consider following up. Score: {lead.get('score') or 'unscored'}.",
                source="system",
                priority="medium",
                reference_id=ref_id,
                reference_type="stale_lead",
            )
            db.add(notif)
            lead_notifications += 1

        db.commit()

        if meeting_notifications or lead_notifications:
            log_activity(
                db, tid, "proactive_trigger",
                f"Proactive triggers: {meeting_notifications} meeting preps, {lead_notifications} stale leads",
                source="inbox_monitor",
                event_metadata={
                    "meeting_notifications": meeting_notifications,
                    "lead_notifications": lead_notifications,
                },
            )

        return {
            "meeting_notifications": meeting_notifications,
            "lead_notifications": lead_notifications,
        }

    except Exception as e:
        logger.exception("check_proactive_triggers failed: %s", e)
        return {"meeting_notifications": 0, "lead_notifications": 0, "error": str(e)}
    finally:
        db.close()


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
