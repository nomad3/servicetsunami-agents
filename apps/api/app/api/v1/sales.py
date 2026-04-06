"""Sales module: inbound lead capture, pipeline management, outreach."""
import uuid
from typing import Optional, List
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status, Body
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session

from app.api import deps
from app.models import KnowledgeEntity, User
from app.services.knowledge import knowledge_service

router = APIRouter(prefix="/sales", tags=["sales"])


# ── Request/Response Models ──

class InboundLeadCreate(BaseModel):
    """Web form or webhook inbound lead submission."""
    name: str
    email: Optional[EmailStr] = None
    company: Optional[str] = None
    message: Optional[str] = None
    source: Optional[str] = "web_form"  # web_form, email, whatsapp, workshop


class LeadResponse(BaseModel):
    """Minimal lead response."""
    id: uuid.UUID
    name: str
    company: Optional[str]
    email: Optional[str]
    pipeline_stage: Optional[str]
    score: Optional[int]
    created_at: datetime


# ── Inbound Lead Capture ──

def _extract_company_from_email(email: str) -> Optional[str]:
    """Extract domain/company hint from email address."""
    if "@" not in email:
        return None
    domain = email.split("@")[-1].split(".")[0]
    return domain.title() if domain else None


def _classify_lead_source(source: Optional[str], email: Optional[str]) -> str:
    """Classify and normalize source."""
    if source in ("email", "inbound_email", "email_to_lead"):
        return "inbound_email"
    if source in ("whatsapp", "whatsapp_to_lead"):
        return "inbound_whatsapp"
    if source in ("workshop", "workshop_2026_03_29"):
        return "workshop"
    return "web_form"


@router.post("/inbound", response_model=LeadResponse, status_code=status.HTTP_201_CREATED)
def capture_inbound_lead(
    req: InboundLeadCreate,
    db: Session = Depends(deps.get_db),
    current_user: Optional[User] = Depends(deps.get_current_user),
):
    """
    Capture an inbound lead from web form, email, WhatsApp, or workshop.

    No auth required for web form submissions (rate-limited by slowapi IP throttling).
    """
    # Default tenant to current user if authenticated, else use a request header or deny
    if current_user:
        tenant_id = current_user.tenant_id
    else:
        # For public submissions, require tenant context from header or reject
        raise HTTPException(
            status_code=403,
            detail="Unauthenticated inbound capture not yet supported. Contact admin.",
        )

    # Infer company from email if not provided
    company = req.company
    if not company and req.email:
        company = _extract_company_from_email(req.email)

    # Normalize source
    source = _classify_lead_source(req.source, req.email)

    # Create lead entity in knowledge graph
    try:
        lead_entity = knowledge_service.create_entity(
            db=db,
            tenant_id=tenant_id,
            name=req.name,
            entity_type="person",
            category="lead",
            description=f"Inbound lead from {source}. Company: {company}. Message: {req.message or '(no message)'}",
            properties={
                "email": req.email,
                "company": company,
                "source": source,
                "pipeline_stage": "prospect",
                "inbound_message": req.message,
            },
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create lead: {str(e)}")

    return LeadResponse(
        id=lead_entity.id,
        name=lead_entity.name,
        company=company,
        email=req.email,
        pipeline_stage="prospect",
        score=None,
        created_at=lead_entity.created_at,
    )


@router.get("/pipeline")
def get_pipeline_summary(
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
):
    """
    Get sales pipeline summary by stage with counts and values.
    Uses SQL aggregation for accuracy (not fuzzy KG search).
    """
    from sqlalchemy import func, text

    # Query: aggregate leads by pipeline_stage
    result = db.execute(
        text(
            """
            SELECT
                properties->>'pipeline_stage' as stage,
                COUNT(*) as count,
                COALESCE(SUM((properties->>'deal_value')::numeric), 0) as total_value
            FROM knowledge_entities
            WHERE tenant_id = :tenant_id
              AND category = 'lead'
              AND properties->>'pipeline_stage' IS NOT NULL
            GROUP BY properties->>'pipeline_stage'
            ORDER BY CASE
                WHEN properties->>'pipeline_stage' = 'prospect' THEN 1
                WHEN properties->>'pipeline_stage' = 'qualified' THEN 2
                WHEN properties->>'pipeline_stage' = 'proposal' THEN 3
                WHEN properties->>'pipeline_stage' = 'negotiation' THEN 4
                WHEN properties->>'pipeline_stage' = 'closed_won' THEN 5
                ELSE 99
            END
            """
        ),
        {"tenant_id": str(current_user.tenant_id)},
    )

    stages = {}
    total_value = 0
    total_count = 0

    for row in result:
        stage, count, value = row
        if stage:
            stages[stage] = {"count": count, "value": float(value or 0)}
            total_count += count
            total_value += float(value or 0)

    return {
        "stages": stages,
        "total_count": total_count,
        "total_value": total_value,
        "pipeline_health": "good" if total_count >= 10 else "warning" if total_count >= 5 else "alert",
    }


@router.get("/leads")
def list_leads(
    stage: Optional[str] = None,
    limit: int = 20,
    offset: int = 0,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
):
    """List leads for the current tenant, optionally filtered by stage."""
    query = db.query(KnowledgeEntity).filter(
        KnowledgeEntity.tenant_id == current_user.tenant_id,
        KnowledgeEntity.category == "lead",
    )

    if stage:
        query = query.filter(
            KnowledgeEntity.properties["pipeline_stage"].astext == stage
        )

    leads = query.order_by(KnowledgeEntity.created_at.desc()).offset(offset).limit(limit).all()

    return [
        {
            "id": lead.id,
            "name": lead.name,
            "email": lead.properties.get("email"),
            "company": lead.properties.get("company"),
            "stage": lead.properties.get("pipeline_stage", "prospect"),
            "score": lead.properties.get("score"),
            "source": lead.properties.get("source"),
            "created_at": lead.created_at,
        }
        for lead in leads
    ]


@router.get("/leads/{lead_id}")
def get_lead_detail(
    lead_id: uuid.UUID,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
):
    """Get full lead details with activity timeline."""
    lead = db.query(KnowledgeEntity).filter(
        KnowledgeEntity.id == lead_id,
        KnowledgeEntity.tenant_id == current_user.tenant_id,
        KnowledgeEntity.category == "lead",
    ).first()

    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")

    return {
        "id": lead.id,
        "name": lead.name,
        "description": lead.description,
        "properties": lead.properties,
        "created_at": lead.created_at,
        "updated_at": lead.updated_at,
    }


@router.patch("/leads/{lead_id}/stage")
def update_lead_stage(
    lead_id: uuid.UUID,
    new_stage: str = Body(..., embed=True),
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
):
    """Move a lead to a new pipeline stage."""
    lead = db.query(KnowledgeEntity).filter(
        KnowledgeEntity.id == lead_id,
        KnowledgeEntity.tenant_id == current_user.tenant_id,
    ).first()

    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")

    # Update stage in properties
    if lead.properties is None:
        lead.properties = {}
    lead.properties["pipeline_stage"] = new_stage
    lead.updated_at = datetime.utcnow()

    db.commit()

    return {"id": lead.id, "stage": new_stage}
