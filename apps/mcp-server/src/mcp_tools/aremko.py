"""Aremko Spa availability and reservation MCP tools.

Two layers:
  1. Availability — real-time slot queries via /ventas/get-available-hours/
  2. Reservations — full booking flow via /ventas/api/luna/ (X-Luna-API-Key auth)

Services:
  Tinajas : Hornopiren(1), Tronador(10), Osorno(11), Calbuco(12),
            Hidromasaje Puntiagudo(13), Llaima(14), Villarrica(15), Puyehue(16)
  Masajes : Relajación o Descontracturante (ID 53)
  Cabañas : Arrayan(9), Laurel(8), Tepa(7), Torre(3), Acantilado(6)

Closed Tuesdays.

Contact: +56 9 5336 1647 | reservas@aremko.cl | www.aremko.cl
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from datetime import date, timedelta
from typing import Optional

import httpx
from mcp.server.fastmcp import Context

from src.mcp_app import mcp
from src.mcp_auth import resolve_tenant_id
from src.tools.aremko_availability import (
    check_aremko_availability_data,
    resolve_fecha,
)

logger = logging.getLogger(__name__)

AREMKO_BASE_URL = "https://www.aremko.cl/ventas"
LUNA_API_BASE = "https://www.aremko.cl/ventas/api/luna"
LUNA_API_KEY = "wmRL0kJ52oq15VfTW8db0bZuYYHLoKKq3mXzwGXXnms"
LUNA_HEADERS = {
    "X-Luna-API-Key": LUNA_API_KEY,
    "Content-Type": "application/json",
}
CONTACT_WHATSAPP = "+56 9 5336 1647"
CONTACT_EMAIL = "reservas@aremko.cl"
CONTACT_WEB = "www.aremko.cl"
DEFAULT_REGION_ID = 14  # Los Lagos
DEFAULT_COMUNA_ID = 25  # Puerto Varas


def _is_tuesday(d: date) -> bool:
    return d.weekday() == 1


# ---------------------------------------------------------------------------
# 1. Availability tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def check_aremko_availability(
    service_type: str,
    fecha: str = "mañana",
    tenant_id: Optional[str] = None,
    ctx: Context = None,
) -> dict:
    """Check real-time availability at Aremko Spa for tinajas, masajes, or cabañas.

    Curated catalog: 8 tinajas, 1 masaje (relajación/descontracturante), 5 cabañas.
    CLOSED TUESDAYS — returns closed notice automatically.

    Args:
        service_type: "tinajas", "cabanas" or "masajes". Required.
        fecha: "hoy", "mañana", or YYYY-MM-DD / DD-MM-YYYY / DD/MM/YYYY. Default "mañana".
        tenant_id: Resolved automatically.
        ctx: MCP context (injected automatically).

    Returns:
        Dict with fecha, services list with horas_disponibles, and summary in Spanish.
    """
    resolve_tenant_id(ctx) or tenant_id

    try:
        target_date = resolve_fecha(fecha)
    except ValueError as e:
        return {"error": str(e)}

    if _is_tuesday(target_date):
        next_day = target_date + timedelta(days=1)
        return {
            "service_type": service_type,
            "fecha": target_date.isoformat(),
            "closed": True,
            "summary": (
                f"Aremko está cerrado los días martes. "
                f"¿Te gustaría revisar disponibilidad para el miércoles {next_day.isoformat()}?"
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
    """Get availability snapshot across ALL Aremko services for one or more dates.

    Queries tinajas + masajes + cabañas concurrently. Skips Tuesdays automatically.

    Args:
        fecha: Start date — "hoy", "mañana", or YYYY-MM-DD. Default "mañana".
        days_ahead: Days to check from fecha (max 7). Default 1.
        tenant_id: Resolved automatically.
        ctx: MCP context (injected automatically).

    Returns:
        Dict with per-date breakdown and human-readable summary.
    """
    resolve_tenant_id(ctx) or tenant_id

    try:
        start = resolve_fecha(fecha)
    except ValueError as e:
        return {"error": str(e)}

    days_ahead = max(1, min(days_ahead, 7))
    dates = [start + timedelta(days=i) for i in range(days_ahead)]

    by_date = {}
    lines = []

    for d in dates:
        d_str = d.isoformat()
        if _is_tuesday(d):
            by_date[d_str] = {"closed": True}
            lines.append(f"\n📅 {d_str} — CERRADO (martes)")
            continue

        tinajas_r, masajes_r, cabanas_r = await asyncio.gather(
            check_aremko_availability_data("tinajas", d_str),
            check_aremko_availability_data("masajes", d_str),
            check_aremko_availability_data("cabanas", d_str),
        )

        t_avail = [s for s in tinajas_r.get("services", []) if s["horas_disponibles"]]
        m_avail = [s for s in masajes_r.get("services", []) if s["horas_disponibles"]]
        c_avail = [s for s in cabanas_r.get("services", []) if s["horas_disponibles"]]

        by_date[d_str] = {
            "tinajas": tinajas_r.get("services", []),
            "masajes": masajes_r.get("services", []),
            "cabanas": cabanas_r.get("services", []),
        }

        lines.append(f"\n📅 {d_str}")
        lines.append(f"  Tinajas: {len(t_avail)}/8 disponibles ({sum(len(s['horas_disponibles']) for s in t_avail)} slots)")
        lines.append(f"  Masajes: {'disponible' if m_avail else 'sin disponibilidad'}")
        lines.append(f"  Cabañas: {len(c_avail)}/5 disponibles")
        if t_avail:
            best = max(t_avail, key=lambda s: len(s["horas_disponibles"]))
            lines.append(f"  → Mejor tinaja: {best['nombre']} ({', '.join(best['horas_disponibles'])})")

    return {"dates_checked": [d.isoformat() for d in dates], "by_date": by_date, "summary": "\n".join(lines)}


# ---------------------------------------------------------------------------
# 2. Reservation tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def get_aremko_regions(
    tenant_id: Optional[str] = None,
    ctx: Context = None,
) -> dict:
    """Get all Chilean regions and their comunas from the Aremko system.

    Use this to resolve region_id and comuna_id when a customer provides their
    location during the booking flow.

    Key IDs: Los Lagos = 14, Puerto Varas = 25, Puerto Montt = 24.

    Args:
        tenant_id: Resolved automatically.
        ctx: MCP context (injected automatically).

    Returns:
        Dict with success, regiones list (each with id, nombre, comunas[]).
    """
    resolve_tenant_id(ctx) or tenant_id

    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
        try:
            resp = await client.get(f"{LUNA_API_BASE}/regiones/", headers=LUNA_HEADERS)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error("get_aremko_regions error: %s", e)
            return {
                "success": False,
                "error": str(e),
                "note": "IDs comunes: Los Lagos=14, Puerto Varas=25, Puerto Montt=24",
            }


@mcp.tool()
async def validate_aremko_reservation(
    servicios: list,
    tenant_id: Optional[str] = None,
    ctx: Context = None,
) -> dict:
    """Validate availability before creating a reservation at Aremko Spa.

    ALWAYS call this before create_aremko_reservation to confirm the slot is
    available, get the exact price, and see any pack discounts.

    Args:
        servicios: List of service dicts, each with:
            - servicio_id (int): Service ID
            - fecha (str): Date in YYYY-MM-DD format
            - hora (str): Time in HH:MM format (24h)
            - cantidad_personas (int): Number of people
          Example: [{"servicio_id": 12, "fecha": "2026-04-15", "hora": "14:30", "cantidad_personas": 4}]
        tenant_id: Resolved automatically.
        ctx: MCP context (injected automatically).

    Returns:
        Dict with success, availability per service, prices, pack discounts, and total.
        If not available, includes suggested alternative times.
    """
    resolve_tenant_id(ctx) or tenant_id

    if not servicios:
        return {"success": False, "error": "servicios list is required"}

    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
        try:
            resp = await client.post(
                f"{LUNA_API_BASE}/reservas/validar/",
                headers=LUNA_HEADERS,
                json={"servicios": servicios},
            )
            data = resp.json()
            return data
        except Exception as e:
            logger.error("validate_aremko_reservation error: %s", e)
            return {
                "success": False,
                "error": str(e),
                "fallback": f"Contactar directamente: WhatsApp {CONTACT_WHATSAPP}",
            }


@mcp.tool()
async def create_aremko_reservation(
    nombre: str,
    email: str,
    telefono: str,
    servicios: list,
    region_id: Optional[int] = None,
    comuna_id: Optional[int] = None,
    documento_identidad: str = "",
    notas: str = "Reserva creada por Luna via WhatsApp",
    tenant_id: Optional[str] = None,
    ctx: Context = None,
) -> dict:
    """Create a complete reservation at Aremko Spa via the Luna API.

    IMPORTANT: Always call validate_aremko_reservation first.
    Uses an auto-generated idempotency_key to prevent duplicates.

    Args:
        nombre: Customer full name. Required.
        email: Customer email. Required.
        telefono: Customer phone with country code (e.g. "+56912345678"). Required.
        servicios: List of service dicts with servicio_id, fecha, hora, cantidad_personas. Required.
        region_id: Region ID from get_aremko_regions (e.g. Los Lagos = 14). Optional.
        comuna_id: Comuna ID from get_aremko_regions (e.g. Puerto Varas = 25). Optional.
        documento_identidad: RUT/DNI (optional).
        notas: Internal notes for the reservation. Default: "Reserva creada por Luna via WhatsApp".
        tenant_id: Resolved automatically.
        ctx: MCP context (injected automatically).

    Returns:
        Dict with success, reservation ID (RES-XXXX), customer details, services,
        total price, and discounts applied.
    """
    resolve_tenant_id(ctx) or tenant_id

    validation = await validate_aremko_reservation(
        servicios=servicios,
        tenant_id=tenant_id,
        ctx=ctx,
    )
    availability = validation.get("disponibilidad") or []
    has_unavailable_service = any(not item.get("disponible", False) for item in availability)
    if not validation.get("success") or has_unavailable_service:
        return {
            "success": False,
            "error": "La reserva no pudo crearse porque la disponibilidad no fue confirmada.",
            "validation": validation,
            "fallback": f"Contactar directamente: WhatsApp {CONTACT_WHATSAPP}",
        }

    resolved_region_id = region_id or DEFAULT_REGION_ID
    resolved_comuna_id = comuna_id or DEFAULT_COMUNA_ID
    used_default_location = region_id is None or comuna_id is None

    idempotency_key = f"luna-{uuid.uuid4().hex[:12]}-{int(time.time())}"

    payload = {
        "idempotency_key": idempotency_key,
        "cliente": {
            "nombre": nombre,
            "email": email,
            "telefono": telefono,
            "region_id": resolved_region_id,
            "comuna_id": resolved_comuna_id,
        },
        "servicios": servicios,
        "metodo_pago": "pendiente",
        "notas": notas,
    }

    if documento_identidad:
        payload["cliente"]["documento_identidad"] = documento_identidad

    logger.info("create_aremko_reservation: cliente=%s servicios=%d key=%s", nombre, len(servicios), idempotency_key)

    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        try:
            resp = await client.post(
                f"{LUNA_API_BASE}/reservas/create/",
                headers=LUNA_HEADERS,
                json=payload,
            )
            data = resp.json()

            # Enrich successful response with contact/arrival info
            if data.get("success"):
                data["location"] = {
                    "region_id": resolved_region_id,
                    "comuna_id": resolved_comuna_id,
                    "used_default_location": used_default_location,
                }
                if used_default_location:
                    data["location_note"] = (
                        "Se usó ubicación por defecto Los Lagos / Puerto Varas para completar la reserva."
                    )
                data["instrucciones_llegada"] = (
                    "• Llega 10 minutos antes\n"
                    "• Trae toallas y traje de baño\n"
                    "• Pago al llegar (efectivo/tarjeta/transferencia)"
                )
                data["contacto"] = {
                    "whatsapp": CONTACT_WHATSAPP,
                    "email": CONTACT_EMAIL,
                    "direccion": "Camino Volcán Calbuco Km 4, Sector Río Pescado, Puerto Varas",
                }

            return data

        except Exception as e:
            logger.error("create_aremko_reservation error: %s", e)
            return {
                "success": False,
                "error": str(e),
                "fallback": f"Contactar directamente: WhatsApp {CONTACT_WHATSAPP}",
            }
