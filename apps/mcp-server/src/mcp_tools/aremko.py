"""Aremko availability MCP tool."""
from __future__ import annotations

from typing import Optional

from mcp.server.fastmcp import Context

from src.mcp_app import mcp
from src.mcp_auth import resolve_tenant_id
from src.tools.aremko_availability import check_aremko_availability_data


@mcp.tool()
async def check_aremko_availability(
    service_type: str,
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
