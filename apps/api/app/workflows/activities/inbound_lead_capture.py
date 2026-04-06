"""Inbound lead capture activities for Module 5 (Sales Phase 2)."""
import uuid
import logging
from typing import Optional
from datetime import datetime
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.db.session import SessionLocal
from app.models import KnowledgeEntity
from app.services.knowledge import knowledge_service
from temporalio import activity

logger = logging.getLogger(__name__)

# Keywords that indicate sales intent in email content
_SALES_INTENT_KEYWORDS = {
    "pricing", "demo", "trial", "interested", "how much", "cost",
    "free trial", "sign up", "contact us", "more information", "learn more",
    "quote", "proposal", "rate", "fee", "plan", "subscription",
}


@activity.defn
async def classify_email_as_lead(
    tenant_id: str,
    sender_email: str,
    sender_name: Optional[str],
    subject: str,
    body: str,
    message_id: str,
) -> Optional[str]:
    """
    Classify inbound email and auto-create lead if it shows sales intent.

    Returns the created lead entity ID if a lead was created, None otherwise.
    """
    db: Session = SessionLocal()
    try:
        tenant_uuid = uuid.UUID(tenant_id)

        # Check if sender is already known (existing entity with this email)
        existing = db.query(KnowledgeEntity).filter(
            KnowledgeEntity.properties["email"].astext == sender_email,
            KnowledgeEntity.tenant_id == tenant_uuid,
        ).first()
        if existing:
            logger.debug(f"Email from {sender_email} is known lead, skipping")
            return None

        # Classify: does this email show sales intent?
        has_sales_intent = _detect_sales_intent(subject, body)
        if not has_sales_intent:
            logger.debug(f"Email from {sender_email} does not show sales intent")
            return None

        # Extract company from email domain
        domain = sender_email.split("@")[-1].split(".")[0] if "@" in sender_email else ""
        company = domain.title() if domain else None

        # Create lead entity
        lead = knowledge_service.create_entity(
            db=db,
            tenant_id=tenant_uuid,
            name=sender_name or sender_email.split("@")[0],
            entity_type="person",
            category="lead",
            description=f"Inbound lead from email. Subject: {subject[:200]}",
            properties={
                "email": sender_email,
                "company": company,
                "source": "inbound_email",
                "pipeline_stage": "prospect",
                "inbound_subject": subject[:200],
                "inbound_message_id": message_id,
            },
        )

        logger.info(f"Created lead {lead.id} from inbound email {sender_email}")
        return str(lead.id)

    except Exception as e:
        logger.error(f"Failed to classify email as lead: {e}", exc_info=True)
        return None
    finally:
        db.close()


@activity.defn
async def classify_whatsapp_as_lead(
    tenant_id: str,
    sender_phone: str,
    sender_name: Optional[str],
    message: str,
) -> Optional[str]:
    """
    Classify inbound WhatsApp message and auto-create lead if it shows sales intent.

    Returns the created lead entity ID if a lead was created, None otherwise.
    """
    db: Session = SessionLocal()
    try:
        tenant_uuid = uuid.UUID(tenant_id)

        # Check if sender is already known
        existing = db.query(KnowledgeEntity).filter(
            KnowledgeEntity.properties["phone"].astext == sender_phone,
            KnowledgeEntity.tenant_id == tenant_uuid,
        ).first()
        if existing:
            logger.debug(f"WhatsApp from {sender_phone} is known lead, skipping")
            return None

        # Classify: does this message show sales intent?
        has_sales_intent = _detect_sales_intent("", message)
        if not has_sales_intent:
            logger.debug(f"WhatsApp from {sender_phone} does not show sales intent")
            return None

        # Create lead entity
        lead = knowledge_service.create_entity(
            db=db,
            tenant_id=tenant_uuid,
            name=sender_name or f"WhatsApp {sender_phone[-4:]}",
            entity_type="person",
            category="lead",
            description=f"Inbound lead from WhatsApp. Message: {message[:200]}",
            properties={
                "phone": sender_phone,
                "source": "inbound_whatsapp",
                "pipeline_stage": "prospect",
                "inbound_message": message[:500],
            },
        )

        logger.info(f"Created lead {lead.id} from inbound WhatsApp {sender_phone}")
        return str(lead.id)

    except Exception as e:
        logger.error(f"Failed to classify WhatsApp as lead: {e}", exc_info=True)
        return None
    finally:
        db.close()


def _detect_sales_intent(subject: str, body: str) -> bool:
    """Heuristic: does this look like sales/demo inquiry?"""
    combined = f"{subject} {body}".lower()
    keyword_count = sum(1 for kw in _SALES_INTENT_KEYWORDS if kw in combined)

    # Simple heuristic: if 2+ sales keywords detected, assume intent
    return keyword_count >= 2
