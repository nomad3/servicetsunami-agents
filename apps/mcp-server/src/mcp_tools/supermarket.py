"""Product price search MCP tool.

Uses Playwright to search live prices on any e-commerce / supermarket site.
Works with built-in presets (Lider, Jumbo, etc.) or any arbitrary site URL.
"""
import logging
from typing import Optional, Union

from mcp.server.fastmcp import Context

from src.mcp_app import mcp
from src.mcp_auth import resolve_tenant_id

logger = logging.getLogger(__name__)


@mcp.tool()
async def search_product_prices(
    products: list[str],
    sites: Optional[list[Union[str, dict]]] = None,
    max_results_per_product: int = 3,
    currency: str = "$",
    tenant_id: Optional[str] = None,
    ctx: Context = None,
) -> dict:
    """Search live prices for products on any e-commerce or supermarket site.

    Uses a real browser (Playwright) to bypass anti-bot protections and return
    current prices. Two modes:

    **Preset sites** (no config needed):
      "lider", "jumbo", "santaisabel", "unimarc"

    **Any custom site** — pass a dict with name + search URL:
      {"name": "Farmacia Cruz Verde", "search_url": "https://www.cruzverde.cl/buscar?q={query}"}
      The {query} placeholder is replaced with the product name.
      Works on most e-commerce sites via schema.org + heuristic extraction.

    Args:
        products: Product names to search (e.g. ["huevo", "leche sin lactosa"]).
        sites: Preset keys and/or custom site dicts. Defaults to ["lider", "jumbo"].
               Examples:
                 ["lider", "jumbo"]
                 [{"name": "Paris", "search_url": "https://www.paris.cl/search?text={query}"}]
                 ["lider", {"name": "Cruz Verde", "search_url": "https://www.cruzverde.cl/buscar?q={query}"}]
        max_results_per_product: Max results per product per site (default 3).
        currency: Currency symbol for formatted output (default "$").
        tenant_id: Resolved automatically.

    Returns:
        - results: {product: [{name, price, price_formatted, site, url}]}
        - summary: Human-readable price comparison
        - errors: Products with no results
    """
    resolved_tenant_id = resolve_tenant_id(tenant_id, ctx)
    logger.info("search_product_prices: tenant=%s products=%s sites=%s",
                str(resolved_tenant_id)[:8], products, sites)

    if sites is None:
        sites = ["lider", "jumbo"]

    from src.scrapers.supermarket import search_prices

    raw = await search_prices(
        products=products,
        sites=sites,
        max_results_per_product=max_results_per_product,
        currency=currency,
    )

    lines = []
    errors = []
    for product, items in raw.items():
        if not items:
            errors.append(product)
            lines.append(f"• {product}: sin resultados")
            continue
        items_sorted = sorted(items, key=lambda x: x["price"])
        best = items_sorted[0]
        line = f"• {product}: mejor precio {best['price_formatted']} en {best['site']}"
        if len(items_sorted) > 1:
            others = ", ".join(f"{i['site']} {i['price_formatted']}" for i in items_sorted[1:])
            line += f" (también: {others})"
        lines.append(line)

    return {
        "results": raw,
        "summary": "\n".join(lines) if lines else "No se encontraron resultados.",
        "errors": errors,
    }
