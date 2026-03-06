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
