"""Temporal activities for AremkoMonitorWorkflow.

Fetches live availability from aremko.cl using the curated service catalog
(5 cabañas, 8 tinajas, masaje relajación/descontracturante only).
Compares snapshots to detect changes and creates notifications.
CLOSED TUESDAYS — skips Tuesday dates automatically.
"""
import logging
import uuid
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional

import httpx
from temporalio import activity

from app.db.session import SessionLocal
from app.models.notification import Notification

logger = logging.getLogger(__name__)

AREMKO_BASE_URL = "https://www.aremko.cl/ventas"

# Curated catalog — matches src/tools/aremko_availability.py in mcp-server
TINAJAS = {
    "Tina Hornopiren":           1,
    "Tina Tronador":             10,
    "Tina Osorno":               11,
    "Tina Calbuco":              12,
    "Tina Hidromasaje Puntiagudo": 13,
    "Tina Hidromasaje Llaima":   14,
    "Tina Hidromasaje Villarrica": 15,
    "Tina Hidromasaje Puyehue":  16,
}

MASAJES = {
    "Masaje Relajación o Descontracturante": 53,
}

CABANAS = {
    "Cabaña Arrayan":    9,
    "Cabaña Laurel":     8,
    "Cabaña Tepa":       7,
    "Cabaña Torre":      3,
    "Cabaña Acantilado": 6,
}

SERVICES = {
    "tinajas": TINAJAS,
    "masajes": MASAJES,
    "cabanas": CABANAS,
}


def _is_tuesday(d: date) -> bool:
    return d.weekday() == 1


def _get_dates(days_ahead: int) -> list[date]:
    today = date.today()
    return [today + timedelta(days=i) for i in range(days_ahead)]


def _fetch_hours_sync(service_id: int, fecha: str) -> list[str]:
    """Synchronous HTTP call — runs in activity thread."""
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(
                f"{AREMKO_BASE_URL}/get-available-hours/",
                params={"servicio_id": service_id, "fecha": fecha},
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("success"):
                return data.get("horas_disponibles", [])
    except Exception as e:
        logger.warning("aremko fetch id=%d fecha=%s: %s", service_id, fecha, e)
    return []


@activity.defn(name="fetch_aremko_snapshot")
async def fetch_aremko_snapshot(tenant_id: str, days_ahead: int = 3) -> dict:
    """Fetch availability snapshot for all curated Aremko services across next N days.

    Skips Tuesdays (Aremko is closed). Returns nested dict:
    snapshot[date_str][category][service_name] = [hours]
    """
    import asyncio

    dates = _get_dates(days_ahead)
    snapshot: Dict[str, Dict[str, Dict[str, list]]] = {}

    async def fetch_one(fecha: str, category: str, name: str, sid: int):
        return fecha, category, name, _fetch_hours_sync(sid, fecha)

    tasks = [
        fetch_one(d.isoformat(), category, name, sid)
        for d in dates
        if not _is_tuesday(d)
        for category, services in SERVICES.items()
        for name, sid in services.items()
    ]

    # Mark closed Tuesdays in snapshot
    for d in dates:
        if _is_tuesday(d):
            snapshot[d.isoformat()] = {"closed": True}

    results = await asyncio.gather(*tasks, return_exceptions=True)
    for result in results:
        if isinstance(result, Exception):
            logger.warning("fetch_aremko_snapshot error: %s", result)
            continue
        fecha, category, name, hours = result
        snapshot.setdefault(fecha, {}).setdefault(category, {})[name] = hours

    total_slots = sum(
        len(h)
        for day in snapshot.values()
        if not day.get("closed")
        for cat in day.values()
        if isinstance(cat, dict)
        for h in cat.values()
        if isinstance(h, list)
    )

    logger.info("fetch_aremko_snapshot: tenant=%s days=%d total_slots=%d", tenant_id[:8], days_ahead, total_slots)
    return {"snapshot": snapshot, "dates": [d.isoformat() for d in dates], "total_slots": total_slots}


@activity.defn(name="detect_aremko_changes")
async def detect_aremko_changes(
    tenant_id: str,
    old_snapshot: dict,
    new_snapshot: dict,
) -> dict:
    """Compare two snapshots and return meaningful changes.

    Detects:
    - fully_booked: had slots → no slots
    - now_available: no slots → has slots
    - slots_gained: > 2 new slots appeared
    - slots_lost: > 2 slots disappeared
    """
    changes = []
    all_dates = set(old_snapshot.keys()) | set(new_snapshot.keys())

    for fecha in all_dates:
        old_day = old_snapshot.get(fecha, {})
        new_day = new_snapshot.get(fecha, {})

        if old_day.get("closed") or new_day.get("closed"):
            continue

        for category in SERVICES:
            old_cat = old_day.get(category, {})
            new_cat = new_day.get(category, {})
            all_services = set(old_cat.keys()) | set(new_cat.keys())

            for service_name in all_services:
                old_hours = set(old_cat.get(service_name, []))
                new_hours = set(new_cat.get(service_name, []))

                if old_hours == new_hours:
                    continue

                gained = new_hours - old_hours
                lost = old_hours - new_hours

                if old_hours and not new_hours:
                    changes.append({
                        "type": "fully_booked",
                        "date": fecha,
                        "category": category,
                        "service": service_name,
                        "message": f"{service_name} se llenó para el {fecha}.",
                        "priority": "medium",
                    })
                elif not old_hours and new_hours:
                    changes.append({
                        "type": "now_available",
                        "date": fecha,
                        "category": category,
                        "service": service_name,
                        "new_slots": sorted(new_hours),
                        "message": (
                            f"{service_name} tiene nueva disponibilidad el {fecha}: "
                            f"{', '.join(sorted(new_hours))}"
                        ),
                        "priority": "high",
                    })
                elif len(gained) > 2:
                    changes.append({
                        "type": "slots_gained",
                        "date": fecha,
                        "category": category,
                        "service": service_name,
                        "gained": sorted(gained),
                        "message": f"{service_name} ganó {len(gained)} horarios el {fecha}: {', '.join(sorted(gained))}",
                        "priority": "low",
                    })
                elif len(lost) > 2:
                    changes.append({
                        "type": "slots_lost",
                        "date": fecha,
                        "category": category,
                        "service": service_name,
                        "lost": sorted(lost),
                        "message": f"{service_name} perdió {len(lost)} horarios el {fecha}.",
                        "priority": "low",
                    })

    logger.info("detect_aremko_changes: tenant=%s changes=%d", tenant_id[:8], len(changes))
    return {"changes": changes}


@activity.defn(name="create_aremko_notifications")
async def create_aremko_notifications(tenant_id: str, changes: list) -> dict:
    """Persist significant availability changes as notifications."""
    if not changes:
        return {"created": 0}

    created = 0
    with SessionLocal() as db:
        tid = uuid.UUID(tenant_id)
        for change in changes:
            try:
                notif = Notification(
                    tenant_id=tid,
                    source="system",
                    title=f"Aremko: {change['service']} — {change['type'].replace('_', ' ')}",
                    body=change["message"],
                    priority=change.get("priority", "low"),
                    reference_id=f"aremko-{change['category']}-{change['service']}-{change['date']}-{change['type']}",
                    metadata={
                        "category": change["category"],
                        "service": change["service"],
                        "date": change["date"],
                        "change_type": change["type"],
                    },
                )
                db.add(notif)
                created += 1
            except Exception as e:
                logger.warning("create_aremko_notifications skip: %s", e)
        try:
            db.commit()
        except Exception as e:
            db.rollback()
            logger.error("create_aremko_notifications commit failed: %s", e)
            return {"created": 0, "error": str(e)}

    logger.info("create_aremko_notifications: tenant=%s created=%d", tenant_id[:8], created)
    return {"created": created}
