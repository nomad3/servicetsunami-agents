<<<<<<< feature/aremko-availability-filters
"""Aremko availability MCP tool."""
from __future__ import annotations

from typing import Optional

from mcp.server.fastmcp import Context

from src.mcp_app import mcp
from src.mcp_auth import resolve_tenant_id
from src.tools.aremko_availability import check_aremko_availability_data
=======
"""Aremko Spa availability MCP tools.

Real-time availability queries against the Aremko booking system API
(https://www.aremko.cl/ventas/). No authentication required.

Services:
  - Tinajas (hot tubs): IDs 1-8
  - Masajes (massages): IDs 11-16
  - Cabañas (cabins): IDs 21-22
"""
import asyncio
import logging
from datetime import date, datetime, timedelta
from typing import Optional

import httpx
from mcp.server.fastmcp import Context

from src.mcp_app import mcp

logger = logging.getLogger(__name__)

AREMKO_BASE_URL = "https://www.aremko.cl/ventas"

TINAJAS = {
    "Llaima": 1,
    "Hornopiren": 2,
    "Puntiagudo": 3,
    "Calbuco": 4,
    "Osorno": 5,
    "Tronador": 6,
    "Villarrica": 7,
    "Puyehue": 8,
}

MASAJES = {
    "Relajacion": 11,
    "Deportivo": 12,
    "Piedras Calientes": 13,
    "Thai": 14,
    "Drenaje Linfatico": 15,
    "Reflexologia": 16,
}

CABANAS = {
    "Rio": 21,
    "Bosque": 22,
}

SERVICE_INFO = {
    "tinajas": {
        "ids": TINAJAS,
        "duration_min": 120,
        "price_range": "$25.000–$30.000 CLP/persona",
        "hours": "09:00–21:00",
    },
    "masajes": {
        "ids": MASAJES,
        "duration_min": 50,
        "price_range": "$40.000–$45.000 CLP/persona",
        "hours": "09:00–21:00",
    },
    "cabanas": {
        "ids": CABANAS,
        "duration_min": None,
        "price_range": "$90.000–$100.000 CLP/noche",
        "hours": "09:00–21:00",
    },
}


async def _fetch_hours(client: httpx.AsyncClient, service_id: int, fecha: str) -> list[str]:
    """Fetch available hours for a single service on a given date."""
    try:
        resp = await client.get(
            f"{AREMKO_BASE_URL}/get-available-hours/",
            params={"servicio_id": service_id, "fecha": fecha},
            timeout=10.0,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("success"):
            return data.get("horas_disponibles", [])
    except Exception as e:
        logger.warning("aremko _fetch_hours servicio_id=%d fecha=%s error: %s", service_id, fecha, e)
    return []


async def _fetch_all_in_category(category_ids: dict, fecha: str) -> dict:
    """Fetch available hours for all services in a category concurrently."""
    async with httpx.AsyncClient() as client:
        tasks = {
            name: _fetch_hours(client, sid, fecha)
            for name, sid in category_ids.items()
        }
        results = await asyncio.gather(*tasks.values(), return_exceptions=True)
    return {
        name: (r if isinstance(r, list) else [])
        for name, r in zip(tasks.keys(), results)
    }


def _resolve_date(fecha: Optional[str]) -> str:
    """Resolve fecha string: today if None, tomorrow if 'manana'/'mañana'."""
    if not fecha or fecha.lower() in ("hoy", "today"):
        return date.today().isoformat()
    if fecha.lower() in ("manana", "mañana", "tomorrow"):
        return (date.today() + timedelta(days=1)).isoformat()
    return fecha


# ---------------------------------------------------------------------------
# MCP Tools
# ---------------------------------------------------------------------------
>>>>>>> main


@mcp.tool()
async def check_aremko_availability(
    service_type: str,
<<<<<<< feature/aremko-availability-filters
    fecha: str = "mañana",
    tenant_id: Optional[str] = None,
    ctx: Context = None,
) -> dict:
    """Consultar disponibilidad curada en Aremko para tinajas, cabañas o masajes.

    Args:
        service_type: "tinajas", "cabanas" o "masajes".
        fecha: "hoy", "mañana" o fecha absoluta (YYYY-MM-DD, DD-MM-YYYY, DD/MM/YYYY).
        tenant_id: Tenant UUID (resolved automatically).
        ctx: MCP request context.

    Returns:
        Dict con fecha resuelta, catálogo curado y horarios disponibles por servicio.
    """
    _resolved_tenant_id = resolve_tenant_id(ctx) or tenant_id
    return await check_aremko_availability_data(service_type=service_type, fecha=fecha)
=======
    fecha: Optional[str] = None,
    service_name: Optional[str] = None,
    tenant_id: Optional[str] = None,
    ctx: Context = None,
) -> dict:
    """Check real-time availability at Aremko Spa for a specific service type and date.

    Queries the live Aremko booking system for available time slots.

    Args:
        service_type: Category to check — "tinajas", "masajes", or "cabanas". Required.
        fecha: Date in YYYY-MM-DD format, or "hoy"/"mañana". Defaults to today.
        service_name: Optional specific service name (e.g. "Osorno", "Thai", "Rio").
                      If omitted, checks ALL services in the category.
        tenant_id: Resolved automatically.
        ctx: MCP request context (injected automatically).

    Returns:
        Dict with date, service_type, available slots per service, info, and a
        human-readable summary suitable for responding to customers.
    """
    service_type = service_type.lower().strip()
    if service_type not in SERVICE_INFO:
        return {
            "error": f"service_type '{service_type}' inválido. Usa: tinajas, masajes, cabanas."
        }

    fecha = _resolve_date(fecha)
    info = SERVICE_INFO[service_type]
    all_ids = info["ids"]

    if service_name:
        # Find best match (case-insensitive)
        matched = {
            name: sid
            for name, sid in all_ids.items()
            if service_name.lower() in name.lower()
        }
        if not matched:
            return {
                "error": f"Servicio '{service_name}' no encontrado en {service_type}. "
                         f"Opciones: {', '.join(all_ids.keys())}"
            }
        ids_to_check = matched
    else:
        ids_to_check = all_ids

    logger.info("check_aremko_availability: type=%s date=%s services=%s", service_type, fecha, list(ids_to_check.keys()))

    slots = await _fetch_all_in_category(ids_to_check, fecha)

    available = {name: hours for name, hours in slots.items() if hours}
    unavailable = [name for name, hours in slots.items() if not hours]
    total_slots = sum(len(h) for h in available.values())

    # Build human-readable summary
    if not available:
        summary = f"No hay disponibilidad en {service_type} para el {fecha}."
    else:
        lines = [f"Disponibilidad en {service_type} para el {fecha}:"]
        for name, hours in available.items():
            lines.append(f"  • {name}: {', '.join(hours)}")
        lines.append(f"\nPrecio: {info['price_range']}")
        if info["duration_min"]:
            lines.append(f"Duración: {info['duration_min']} minutos")
        lines.append("Reservas: WhatsApp +56 9 5790-2525 o aremko.cl")
        summary = "\n".join(lines)

    return {
        "date": fecha,
        "service_type": service_type,
        "available": available,
        "unavailable": unavailable,
        "total_slots": total_slots,
        "price_range": info["price_range"],
        "duration_minutes": info["duration_min"],
        "summary": summary,
    }


@mcp.tool()
async def get_aremko_full_availability(
    fecha: Optional[str] = None,
    days_ahead: int = 1,
    tenant_id: Optional[str] = None,
    ctx: Context = None,
) -> dict:
    """Get a complete availability summary across ALL Aremko services for one or more dates.

    Queries all 18 services (8 tinajas + 6 masajes + 2 cabañas) concurrently
    for each requested date. Ideal for daily briefings or scheduling overviews.

    Args:
        fecha: Start date in YYYY-MM-DD format, or "hoy"/"mañana". Defaults to today.
        days_ahead: Number of days to check from fecha (1 = just that day, up to 7). Default 1.
        tenant_id: Resolved automatically.
        ctx: MCP request context (injected automatically).

    Returns:
        Dict with per-date availability breakdown and a human-readable summary.
    """
    start = _resolve_date(fecha)
    days_ahead = max(1, min(days_ahead, 7))

    dates = [
        (datetime.fromisoformat(start) + timedelta(days=i)).date().isoformat()
        for i in range(days_ahead)
    ]

    logger.info("get_aremko_full_availability: dates=%s", dates)

    by_date = {}
    for d in dates:
        tinajas_slots, masajes_slots, cabanas_slots = await asyncio.gather(
            _fetch_all_in_category(TINAJAS, d),
            _fetch_all_in_category(MASAJES, d),
            _fetch_all_in_category(CABANAS, d),
        )
        by_date[d] = {
            "tinajas": {k: v for k, v in tinajas_slots.items() if v},
            "masajes": {k: v for k, v in masajes_slots.items() if v},
            "cabanas": {k: v for k, v in cabanas_slots.items() if v},
        }

    # Build summary
    lines = []
    for d, cats in by_date.items():
        lines.append(f"\n📅 {d}")
        t_count = sum(len(h) for h in cats["tinajas"].values())
        m_count = sum(len(h) for h in cats["masajes"].values())
        c_count = sum(len(h) for h in cats["cabanas"].values())
        lines.append(f"  Tinajas: {len(cats['tinajas'])} disponibles ({t_count} slots)")
        lines.append(f"  Masajes: {len(cats['masajes'])} disponibles ({m_count} slots)")
        lines.append(f"  Cabañas: {len(cats['cabanas'])} disponibles ({c_count} slots)")
        if cats["tinajas"]:
            best = max(cats["tinajas"].items(), key=lambda x: len(x[1]))
            lines.append(f"  → Mejor tinaja: {best[0]} ({len(best[1])} horarios)")

    return {
        "dates_checked": dates,
        "by_date": by_date,
        "summary": "\n".join(lines),
    }
>>>>>>> main
