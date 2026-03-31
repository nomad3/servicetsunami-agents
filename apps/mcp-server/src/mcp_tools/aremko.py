"""Aremko Spa availability MCP tools.

Real-time availability queries against the Aremko booking system.
Delegates to src.tools.aremko_availability for curated catalog logic.

Services covered:
  - Tinajas (8 tubs): IDs 1, 10, 11, 12, 13, 14, 15, 16
  - Masajes (relajación/descontracturante only): ID 53
  - Cabañas (5 cabins): IDs 3, 6, 7, 8, 9
  - CLOSED TUESDAYS
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date, timedelta
from typing import Optional

from mcp.server.fastmcp import Context

from src.mcp_app import mcp
from src.mcp_auth import resolve_tenant_id
from src.tools.aremko_availability import (
    check_aremko_availability_data,
    canonical_service_type,
    resolve_fecha,
    fetch_catalog,
    fetch_available_hours,
    FALLBACK_SERVICES,
)

logger = logging.getLogger(__name__)


def _is_tuesday(d: date) -> bool:
    return d.weekday() == 1  # 0=Mon, 1=Tue


# ---------------------------------------------------------------------------
# MCP Tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def check_aremko_availability(
    service_type: str,
    fecha: str = "mañana",
    tenant_id: Optional[str] = None,
    ctx: Context = None,
) -> dict:
    """Check real-time availability at Aremko Spa for tinajas, masajes, or cabañas.

    Uses the live Aremko booking system with a curated service catalog:
      - Tinajas: 8 tubs (Hornopiren, Calbuco, Osorno, Tronador, and 4 Hidromasaje)
      - Masajes: Masaje Relajación o Descontracturante only
      - Cabañas: 5 cabins (Arrayan, Laurel, Tepa, Torre, Acantilado)
    IMPORTANT: Aremko is CLOSED on Tuesdays.

    Args:
        service_type: "tinajas", "cabanas" or "masajes". Required.
        fecha: "hoy", "mañana", or absolute date (YYYY-MM-DD, DD-MM-YYYY, DD/MM/YYYY).
               Defaults to "mañana".
        tenant_id: Resolved automatically.
        ctx: MCP request context (injected automatically).

    Returns:
        Dict with fecha, service_type, services list (with horas_disponibles per service),
        catalog_source, and a human-readable summary in Spanish.
    """
    _resolved_tenant_id = resolve_tenant_id(ctx) or tenant_id

    try:
        target_date = resolve_fecha(fecha)
    except ValueError as e:
        return {"error": str(e)}

    if _is_tuesday(target_date):
        return {
            "service_type": service_type,
            "fecha": target_date.isoformat(),
            "closed": True,
            "summary": (
                f"Aremko está cerrado los días martes. "
                f"El {target_date.isoformat()} es martes. "
                f"¿Te gustaría revisar disponibilidad para el miércoles "
                f"{(target_date + timedelta(days=1)).isoformat()}?"
            ),
        }

    return await check_aremko_availability_data(service_type=service_type, fecha=fecha)


@mcp.tool()
async def get_aremko_full_availability(
    fecha: str = "mañana",
    days_ahead: int = 1,
    tenant_id: Optional[str] = None,
    ctx: Context = None,
) -> dict:
    """Get a complete availability snapshot across ALL Aremko services for one or more dates.

    Queries all services (tinajas + masajes + cabañas) concurrently for each date.
    Skips Tuesdays automatically (Aremko is closed). Ideal for daily briefings.

    Args:
        fecha: Start date — "hoy", "mañana", or YYYY-MM-DD. Defaults to "mañana".
        days_ahead: Number of days to check starting from fecha (max 7). Default 1.
        tenant_id: Resolved automatically.
        ctx: MCP request context (injected automatically).

    Returns:
        Dict with per-date breakdown and a human-readable summary.
    """
    _resolved_tenant_id = resolve_tenant_id(ctx) or tenant_id

    try:
        start = resolve_fecha(fecha)
    except ValueError as e:
        return {"error": str(e)}

    days_ahead = max(1, min(days_ahead, 7))
    dates = [start + timedelta(days=i) for i in range(days_ahead)]

    logger.info("get_aremko_full_availability: start=%s days=%d", start, days_ahead)

    by_date = {}
    lines = []

    for d in dates:
        d_str = d.isoformat()

        if _is_tuesday(d):
            by_date[d_str] = {"closed": True, "reason": "Cerrado los martes"}
            lines.append(f"\n📅 {d_str} — CERRADO (martes)")
            continue

        tinajas_result, masajes_result, cabanas_result = await asyncio.gather(
            check_aremko_availability_data("tinajas", d_str),
            check_aremko_availability_data("masajes", d_str),
            check_aremko_availability_data("cabanas", d_str),
        )

        by_date[d_str] = {
            "tinajas": tinajas_result.get("services", []),
            "masajes": masajes_result.get("services", []),
            "cabanas": cabanas_result.get("services", []),
        }

        t_avail = [s for s in by_date[d_str]["tinajas"] if s["horas_disponibles"]]
        m_avail = [s for s in by_date[d_str]["masajes"] if s["horas_disponibles"]]
        c_avail = [s for s in by_date[d_str]["cabanas"] if s["horas_disponibles"]]
        t_slots = sum(len(s["horas_disponibles"]) for s in t_avail)

        lines.append(f"\n📅 {d_str}")
        lines.append(f"  Tinajas: {len(t_avail)}/8 disponibles ({t_slots} horarios)")
        lines.append(f"  Masajes: {'disponible' if m_avail else 'sin disponibilidad'}")
        lines.append(f"  Cabañas: {len(c_avail)}/5 disponibles")
        if t_avail:
            best = max(t_avail, key=lambda s: len(s["horas_disponibles"]))
            lines.append(f"  → Mejor tinaja: {best['nombre']} ({', '.join(best['horas_disponibles'])})")

    return {
        "dates_checked": [d.isoformat() for d in dates],
        "by_date": by_date,
        "summary": "\n".join(lines),
    }
