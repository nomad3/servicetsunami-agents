"""Product price scraper — works on any e-commerce / supermarket site via Playwright.

Two extraction modes:
  1. Preset sites (Lider, Jumbo, etc.) — use known CSS selectors for reliability.
  2. Generic mode — falls back to schema.org LD+JSON, itemprop attributes, and
     heuristic card detection. Works on most e-commerce search result pages without
     any per-site configuration.

Custom sites can be passed at call time as dicts:
  {"name": "MiTienda", "search_url": "https://...?q={query}"}
"""
import asyncio
import logging
import re
from typing import Optional, Union
from urllib.parse import quote_plus

from src.services.browser_service import get_browser_service

logger = logging.getLogger(__name__)

# Hard cap per scrape_site call to prevent runaway hangs
_SCRAPE_TIMEOUT_SECS = 45

# ---------------------------------------------------------------------------
# Preset site configs (use these for known sites with tricky selectors)
# ---------------------------------------------------------------------------

PRESETS: dict[str, dict] = {
    "lider": {
        "name": "Lider",
        "search_url": "https://www.lider.cl/supermercado/search?Ntt={query}",
        "product_selector": "[class*='product-card'], [class*='ProductCard'], [data-testid*='product']",
        "name_selector": "[class*='product-title'], [class*='ProductTitle'], h3",
        "price_selector": "[class*='price'], [class*='Price'], [class*='precio']",
        "wait_selector": "[class*='product-card'], [class*='ProductCard']",
    },
    "jumbo": {
        "name": "Jumbo",
        "search_url": "https://www.jumbo.cl/search?q={query}",
        "product_selector": "[class*='product-card'], [class*='ProductCard']",
        "name_selector": "[class*='product-name'], [class*='ProductName'], h3",
        "price_selector": "[class*='price'], [class*='Price']",
        "wait_selector": "[class*='product-card'], [class*='ProductCard']",
    },
    "santaisabel": {
        "name": "Santa Isabel",
        "search_url": "https://www.santaisabel.cl/search?q={query}",
        "product_selector": "[class*='product-card'], [class*='ProductCard']",
        "name_selector": "[class*='product-name'], [class*='ProductName'], h3",
        "price_selector": "[class*='price'], [class*='Price']",
        "wait_selector": "[class*='product-card'], [class*='ProductCard']",
    },
    "unimarc": {
        "name": "Unimarc",
        "search_url": "https://www.unimarc.cl/search?q={query}",
        "product_selector": "[class*='product-card'], [class*='ProductCard']",
        "name_selector": "[class*='product-name'], [class*='ProductName'], h3",
        "price_selector": "[class*='price'], [class*='Price']",
        "wait_selector": "[class*='product-card'], [class*='ProductCard']",
    },
}

# ---------------------------------------------------------------------------
# Generic extraction (schema.org + heuristics)
# ---------------------------------------------------------------------------

# JS that extracts products from any page using schema.org + heuristics.
# Matches prices with or without a currency symbol (handles sites that omit "$").
_GENERIC_EXTRACT_JS = """
() => {
    const results = [];

    // --- Strategy 1: LD+JSON schema.org Product markup ---
    const schemaScripts = document.querySelectorAll('script[type="application/ld+json"]');
    for (const script of schemaScripts) {
        try {
            const data = JSON.parse(script.textContent);
            const items = Array.isArray(data) ? data : [data];
            for (const item of items) {
                const entries = item['@type'] === 'ItemList'
                    ? (item.itemListElement || []).map(e => e.item || e)
                    : [item];
                for (const entry of entries) {
                    if (entry['@type'] !== 'Product') continue;
                    const offer = Array.isArray(entry.offers) ? entry.offers[0] : entry.offers;
                    const price = offer && (offer.price || offer.lowPrice);
                    if (entry.name && price) {
                        results.push({
                            name: entry.name,
                            price: String(price),
                            url: (offer && offer.url) || location.href,
                        });
                    }
                    if (results.length >= 20) break;
                }
            }
        } catch (_) {}
        if (results.length >= 20) break;
    }
    if (results.length > 0) return results;

    // --- Strategy 2: itemprop ---
    const propProducts = document.querySelectorAll('[itemtype*="schema.org/Product"]');
    for (const prod of propProducts) {
        const nameEl = prod.querySelector('[itemprop="name"]');
        const priceEl = prod.querySelector('[itemprop="price"]');
        if (!nameEl || !priceEl) continue;
        const name = nameEl.textContent.trim() || nameEl.getAttribute('content') || '';
        const price = priceEl.getAttribute('content') || priceEl.textContent.trim();
        if (name && price) results.push({ name, price, url: location.href });
        if (results.length >= 20) break;
    }
    if (results.length > 0) return results;

    // --- Strategy 3: heuristic card detection ---
    // Matches prices with currency symbol ($1.990) OR bare numbers (1.990, 1990).
    // The \\b word boundary avoids matching arbitrary numbers that aren't prices.
    const PRICE_RE = /(?:[$€£¥₹]\\s*)?\\b\\d[\\d.,]{1,9}\\b/;
    const candidates = document.querySelectorAll(
        'article, [class*="product"], [class*="item"], [class*="card"], li[class]'
    );
    for (const card of candidates) {
        const text = card.textContent;
        const priceMatch = text.match(PRICE_RE);
        if (!priceMatch) continue;
        const nameEl = card.querySelector(
            'h1,h2,h3,h4,[class*="name"],[class*="title"],[class*="description"]'
        );
        const name = nameEl ? nameEl.textContent.trim() : '';
        if (!name || name.length > 200) continue;
        results.push({ name, price: priceMatch[0], url: location.href });
        if (results.length >= 20) break;
    }
    return results;
}
"""


def _parse_price(raw: str) -> Optional[int]:
    """Extract integer price from strings like '$1.990', '1990', '1,990.00'.

    Rules:
    - Both separators present: whichever comes first is thousands, last is decimal.
      "1.990,00" → 1990   |   "1,990.00" → 1990
    - Dot only, last segment has 3 digits → thousands separator (CLP format).
      "1.990" → 1990
    - Dot only, last segment != 3 digits → decimal.
      "19.90" → 19  (CLP cents dropped)
    - Comma only, last segment has 3 digits → thousands.
      "1,990" → 1990
    - Comma only, last segment != 3 digits → decimal, drop cents.
      "19,90" → 19
    """
    cleaned = re.sub(r"[^\d.,]", "", raw)
    if not cleaned:
        return None

    if "." in cleaned and "," in cleaned:
        if cleaned.index(".") < cleaned.index(","):
            # dot = thousands, comma = decimal  →  "1.990,50"
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            # comma = thousands, dot = decimal  →  "1,990.50"
            cleaned = cleaned.replace(",", "")
    elif "." in cleaned:
        parts = cleaned.split(".")
        if len(parts[-1]) == 3:
            cleaned = cleaned.replace(".", "")   # thousands: "1.990"
        # else decimal: keep as-is, int() will truncate cents
    elif "," in cleaned:
        parts = cleaned.split(",")
        if len(parts[-1]) == 3:
            cleaned = cleaned.replace(",", "")   # thousands: "1,990"
        else:
            cleaned = cleaned.split(",")[0]      # decimal: drop cents

    try:
        return int(float(cleaned))
    except (ValueError, OverflowError):
        return None


def _format_price(price: int, currency: str = "$") -> str:
    # Build thousands-separated string then swap , → . for CLP display
    return f"{currency}{price:,}".replace(",", ".")


# ---------------------------------------------------------------------------
# Core scraper
# ---------------------------------------------------------------------------

async def _wait_past_queue_it(page, timeout: int = 30000) -> bool:
    """Return False if stuck in queue-it after timeout."""
    if "queue-it" not in page.url and "queueit" not in page.url:
        return True
    logger.info("Queue-it detected — waiting up to %ds", timeout // 1000)
    try:
        await page.wait_for_url(
            lambda u: "queue-it" not in u and "queueit" not in u,
            timeout=timeout,
        )
        return True
    except Exception:
        logger.warning("Queue-it timeout")
        return False


async def _scrape_site_inner(site: dict, query: str, max_results: int, currency: str) -> list[dict]:
    """Inner scrape logic — called inside a timeout wrapper."""
    browser_service = get_browser_service()
    encoded_query = quote_plus(query)
    url = site["search_url"].format(query=encoded_query)
    site_name = site.get("name", url)
    results = []

    async with browser_service.new_page(timeout=30000) as page:
        logger.info("Scraping '%s' on %s", query, site_name)
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)

        if not await _wait_past_queue_it(page):
            return []

        has_preset_selectors = "product_selector" in site and "price_selector" in site

        if has_preset_selectors:
            # --- Preset mode: known CSS selectors ---
            try:
                await page.wait_for_selector(
                    site.get("wait_selector", site["product_selector"]),
                    timeout=15000,
                )
            except Exception:
                logger.warning("No products found for '%s' on %s (selector timeout)", query, site_name)
                return []

            cards = await page.query_selector_all(site["product_selector"])
            for card in cards[:max_results]:
                try:
                    name_el = await card.query_selector(site["name_selector"])
                    price_el = await card.query_selector(site["price_selector"])
                    name = (await name_el.inner_text()).strip() if name_el else ""
                    price_raw = (await price_el.inner_text()).strip() if price_el else ""
                    price = _parse_price(price_raw)
                    if name and price:
                        results.append({
                            "name": name,
                            "price": price,
                            "price_formatted": _format_price(price, currency),
                            "site": site_name,
                            "url": url,
                        })
                except Exception as e:
                    logger.debug("Card parse error: %s", e)

        else:
            # --- Generic mode: schema.org + heuristic JS ---
            try:
                await page.wait_for_load_state("networkidle", timeout=10000)
            except Exception:
                pass  # proceed with whatever loaded

            raw_items: list[dict] = await page.evaluate(_GENERIC_EXTRACT_JS)
            for item in raw_items[:max_results]:
                price = _parse_price(item.get("price", ""))
                name = (item.get("name") or "").strip()
                if name and price:
                    results.append({
                        "name": name,
                        "price": price,
                        "price_formatted": _format_price(price, currency),
                        "site": site_name,
                        "url": item.get("url", url),
                    })

    return results


async def scrape_site(
    site: dict,
    query: str,
    max_results: int = 5,
    currency: str = "$",
) -> list[dict]:
    """Scrape a single site for a product query with a hard timeout.

    site dict:
      Required: name (str), search_url (str with {query} placeholder)
      Optional: product_selector, name_selector, price_selector, wait_selector
                If selectors omitted → falls back to generic JS extraction.
    """
    try:
        return await asyncio.wait_for(
            _scrape_site_inner(site, query, max_results, currency),
            timeout=_SCRAPE_TIMEOUT_SECS,
        )
    except asyncio.TimeoutError:
        logger.warning("scrape_site hard timeout (%ds) for %s '%s'",
                       _SCRAPE_TIMEOUT_SECS, site.get("name", "?"), query)
        return []
    except Exception as e:
        logger.warning("Scrape failed for %s '%s': %s", site.get("name", "?"), query, e)
        return []


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def search_prices(
    products: list[str],
    sites: Optional[list[Union[str, dict]]] = None,
    max_results_per_product: int = 3,
    currency: str = "$",
) -> dict[str, list[dict]]:
    """Search prices for multiple products across one or more sites in parallel.

    Args:
        products: Product names to search.
        sites: Mix of preset keys ("lider", "jumbo") and/or custom site dicts
               {"name": "MyStore", "search_url": "https://...?q={query}"}.
               Defaults to ["lider", "jumbo"].
        max_results_per_product: Max results per product per site.
        currency: Currency symbol for formatted prices.

    Returns:
        Dict mapping product name → list of {name, price, price_formatted, site, url}.
    """
    if sites is None:
        sites = ["lider", "jumbo"]

    resolved_sites: list[dict] = []
    for s in sites:
        if isinstance(s, str):
            preset = PRESETS.get(s.lower())
            if preset:
                resolved_sites.append(preset)
            else:
                logger.warning("Unknown preset '%s' — skipping. Known: %s", s, list(PRESETS.keys()))
        elif isinstance(s, dict) and "search_url" in s:
            resolved_sites.append(s)
        else:
            logger.warning("Invalid site config: %s", s)

    if not resolved_sites:
        return {product: [] for product in products}

    # Build all (product, site) tasks and gather them all in parallel
    combos = [(product, site) for product in products for site in resolved_sites]
    tasks = [scrape_site(site, product, max_results_per_product, currency) for product, site in combos]
    all_batches = await asyncio.gather(*tasks, return_exceptions=True)

    # Reassemble into {product: [results]}
    all_results: dict[str, list[dict]] = {product: [] for product in products}
    for (product, _site), batch in zip(combos, all_batches):
        if isinstance(batch, list):
            all_results[product].extend(batch)

    return all_results
