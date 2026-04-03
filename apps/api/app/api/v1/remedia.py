"""Internal endpoints for Remedia PharmApp integration.

Token storage for WhatsApp users and order workflow trigger.
All endpoints require X-Internal-Key header (internal service calls).
"""
import uuid
import logging
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, status, Body
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.api import deps
from app.core.config import settings
from app.models.chat import ChatSession

logger = logging.getLogger(__name__)
router = APIRouter()


def _verify_internal_key(x_internal_key: Optional[str] = Header(None, alias="X-Internal-Key")):
    if x_internal_key not in (settings.API_INTERNAL_KEY, settings.MCP_API_KEY):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid internal key")


@router.post("/token")
def store_remedia_token(
    *,
    db: Session = Depends(deps.get_db),
    phone: str = Body(..., embed=True),
    token: str = Body(..., embed=True),
    tenant_id: str = Body(..., embed=True),
    _auth=Depends(_verify_internal_key),
):
    """Store a Remedia auth token for a WhatsApp phone number.

    Stores in the matching WhatsApp chat session's memory_context.
    """
    session_key = f"whatsapp:{phone}"
    session = db.query(ChatSession).filter(
        ChatSession.external_id == session_key,
        ChatSession.source == "whatsapp",
    ).first()

    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No WhatsApp session found for {phone}",
        )

    mem = dict(session.memory_context or {})
    mem["remedia_token"] = token
    session.memory_context = mem
    flag_modified(session, "memory_context")
    db.commit()

    logger.info(f"Stored Remedia token for phone={phone} session={session.id}")
    return {"status": "ok"}


@router.get("/token/{phone}")
def get_remedia_token(
    phone: str,
    db: Session = Depends(deps.get_db),
    _auth=Depends(_verify_internal_key),
):
    """Retrieve stored Remedia auth token for a WhatsApp phone number."""
    session_key = f"whatsapp:{phone}"
    session = db.query(ChatSession).filter(
        ChatSession.external_id == session_key,
        ChatSession.source == "whatsapp",
    ).first()

    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No WhatsApp session found for {phone}",
        )

    mem = session.memory_context or {}
    token = mem.get("remedia_token")

    if not token:
        return {"token": None}
    return {"token": token}


@router.post("/orders")
async def create_remedia_order(
    *,
    db: Session = Depends(deps.get_db),
    phone_number: str = Body(..., embed=True),
    tenant_id: str = Body(..., embed=True),
    token: str = Body(..., embed=True),
    pharmacy_id: str = Body(..., embed=True),
    items: list = Body(..., embed=True),
    payment_provider: str = Body(..., embed=True),
    _auth=Depends(_verify_internal_key),
):
    """Start a Remedia Order workflow via dynamic workflow launcher.

    Returns immediately with workflow_id. The workflow handles:
    order creation → confirmation → payment monitoring → delivery tracking.
    """
    # Find the chat session for linking
    session_key = f"whatsapp:{phone_number}"
    session = db.query(ChatSession).filter(
        ChatSession.external_id == session_key,
        ChatSession.source == "whatsapp",
    ).first()

    chat_session_id = str(session.id) if session else None

    order_data = {
        "phone_number": phone_number,
        "token": token,
        "pharmacy_id": pharmacy_id,
        "items": items,
        "payment_provider": payment_provider,
        "chat_session_id": chat_session_id,
    }

    try:
        from app.services.dynamic_workflow_launcher import start_dynamic_workflow
        temporal_wf_id = await start_dynamic_workflow(
            db, "Remedia Order", uuid.UUID(tenant_id),
            input_data=order_data,
        )

        logger.info(
            f"Started Remedia Order workflow {temporal_wf_id} for phone={phone_number} "
            f"pharmacy={pharmacy_id} payment={payment_provider}"
        )

        return {
            "success": True,
            "workflow_id": temporal_wf_id,
            "message": "Pedido en proceso. Recibirás confirmación por WhatsApp.",
            "pharmacy_id": pharmacy_id,
            "items": items,
            "payment_provider": payment_provider,
        }

    except Exception as e:
        logger.exception("Failed to start Remedia Order workflow")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to start order workflow: {str(e)}",
        )
