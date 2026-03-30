"""Temporal activities for AremkoMonitorWorkflow.

Fetches live availability from aremko.cl/ventas/, compares snapshots to detect
changes, and creates notifications via the standard notifications table.
"""
import logging
import uuid
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

import httpx
from temporalio import activity

from app.db.session import SessionLocal
from app.models.notification import Notification

logger = logging.getLogger(__name__)

AREMKO_BASE_URL = "https://www.aremko.cl/ventas"

SERVICES = {
    "tinajas": {
        "Llaima": 1, "Hornopiren": 2, "Puntiagudo": 3, "Calbuco": 4,
        "Osorno": 5, "Tronador": 6, "Villarrica": 7, "Puyehue": 8,
    },
    "masajes": {
        "Relajacion": 11, "Deportivo": 12, "Piedras Calientes": 13,
        "Thai": 14, "Drenaje Linfatico": 15, "Reflexologia": 16,
    },
    "cabanas": {
        "Rio": 21, "Bosque": 22,
    },
}


def _get_dates(days_ahead: int) -> list[str]:
    today = date.today()
    return [(today + timedelta(days=i)).isoformat() for i in range(days_ahead)]


def _fetch_hours_sync(service_id: int, fecha: str) -> list[str]:
    """Synchronous HTTP call (run via activity thread)."""
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
        logger.warning("aremko fetch servicio_id=%d fecha=%s: %s", service_id, fecha, e)
    return []


@activity.defn(name="fetch_aremko_snapshot")
async def fetch_aremko_snapshot(tenant_id: str, days_ahead: int = 3) -> dict:
    """Fetch availability snapshot for all Aremko services across next N days.

    Returns a nested dict: snapshot[date][category][service_name] = [hours]
    """
    import asyncio
    dates = _get_dates(days_ahead)
    snapshot: Dict[str, Dict[str, Dict[str, list]]] = {}

    async def fetch_one(fecha: str, category: str, name: str, sid: int):
        return fecha, category, name, _fetch_hours_sync(sid, fecha)

    tasks = [
        fetch_one(fecha, category, name, sid)
        for fecha in dates
        for category, services in SERVICES.items()
        for name, sid in services.items()
    ]

    results = await asyncio.gather(*tasks, return_exceptions=True)

    for result in results:
        if isinstance(result, Exception):
            logger.warning("fetch_aremko_snapshot task error: %s", result)
            continue
        fecha, category, name, hours = result
        snapshot.setdefault(fecha, {}).setdefault(category, {})[name] = hours

    total_slots = sum(
        len(h)
        for day in snapshot.values()
        for cat in day.values()
        for h in cat.values()
    )

    logger.info(
        "fetch_aremko_snapshot: tenant=%s dates=%s total_slots=%d",
        tenant_id[:8], dates, total_slots,
    )
    return {"snapshot": snapshot, "dates": dates, "total_slots": total_slots}


@activity.defn(name="detect_aremko_changes")
async def detect_aremko_changes(
    tenant_id: str,
    old_snapshot: dict,
    new_snapshot: dict,
) -> dict:
    """Compare two availability snapshots and return meaningful changes.

    Detects:
    - Services that became fully booked (had slots → no slots)
    - Services that got new availability (no slots → has slots)
    - Large slot count changes (> 2 slots gained or lost)
    """
    changes = []

    all_dates = set(old_snapshot.keys()) | set(new_snapshot.keys())

    for fecha in all_dates:
        old_day = old_snapshot.get(fecha, {})
        new_day = new_snapshot.get(fecha, {})

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

                # Fully booked
                if old_hours and not new_hours:
                    changes.append({
                        "type": "fully_booked",
                        "date": fecha,
                        "category": category,
                        "service": service_name,
                        "message": f"{service_name} ({category}) se llenó para el {fecha}.",
                        "priority": "medium",
                    })
                # New availability
                elif not old_hours and new_hours:
                    changes.append({
                        "type": "now_available",
                        "date": fecha,
                        "category": category,
                        "service": service_name,
                        "new_slots": sorted(new_hours),
                        "message": (
                            f"{service_name} ({category}) tiene nueva disponibilidad el {fecha}: "
                            f"{', '.join(sorted(new_hours))}"
                        ),
                        "priority": "high",
                    })
                # Significant slot changes
                elif len(gained) > 2:
                    changes.append({
                        "type": "slots_gained",
                        "date": fecha,
                        "category": category,
                        "service": service_name,
                        "gained": sorted(gained),
                        "message": (
                            f"{service_name} ({category}) ganó {len(gained)} horarios el {fecha}: "
                            f"{', '.join(sorted(gained))}"
                        ),
                        "priority": "low",
                    })
                elif len(lost) > 2:
                    changes.append({
                        "type": "slots_lost",
                        "date": fecha,
                        "category": category,
                        "service": service_name,
                        "lost": sorted(lost),
                        "message": f"{service_name} ({category}) perdió {len(lost)} horarios el {fecha}.",
                        "priority": "low",
                    })

    logger.info(
        "detect_aremko_changes: tenant=%s changes=%d", tenant_id[:8], len(changes)
    )
    return {"changes": changes}


@activity.defn(name="create_aremko_notifications")
async def create_aremko_notifications(tenant_id: str, changes: list) -> dict:
    """Persist significant availability changes as notifications."""
    if not changes:
        return {"created": 0}

    priority_map = {"high": "high", "medium": "medium", "low": "low"}
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
                    priority=priority_map.get(change.get("priority", "low"), "low"),
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
                logger.warning("create_aremko_notifications: skipped — %s", e)
        try:
            db.commit()
        except Exception as e:
            db.rollback()
            logger.error("create_aremko_notifications commit failed: %s", e)
            return {"created": 0, "error": str(e)}

    logger.info("create_aremko_notifications: tenant=%s created=%d", tenant_id[:8], created)
    return {"created": created}
