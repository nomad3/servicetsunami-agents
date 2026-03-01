"""Veterinary cardiology tools.

ECG image analysis via Claude vision and breed reference range lookups
from the knowledge graph.
"""
import json
import logging
import re
from typing import Optional

import httpx

from config.settings import settings
from services.knowledge_graph import get_knowledge_service

logger = logging.getLogger(__name__)

_UUID_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I
)
_cached_default_tenant_id = None


def _parse_json(val, default=None):
    if val is None:
        return default
    if isinstance(val, (dict, list)):
        return val
    try:
        return json.loads(val)
    except (json.JSONDecodeError, TypeError):
        return default


def _resolve_tenant_id(tenant_id: str) -> str:
    global _cached_default_tenant_id
    if _UUID_PATTERN.match(tenant_id):
        return tenant_id
    if _cached_default_tenant_id:
        return _cached_default_tenant_id
    try:
        from sqlalchemy import create_engine, text
        engine = create_engine(settings.database_url)
        with engine.connect() as conn:
            result = conn.execute(text("SELECT id FROM tenants LIMIT 1")).fetchone()
            if result:
                _cached_default_tenant_id = str(result[0])
                return _cached_default_tenant_id
    except Exception:
        pass
    return tenant_id


async def analyze_ecg_image(
    image_urls: str,
    species: str,
    breed: str,
    age_years: float,
    weight_kg: float,
    medications: str = "[]",
    tenant_id: str = "auto",
) -> dict:
    """Analyze ECG image(s) using Claude vision model.

    Sends ECG images to Claude with veterinary cardiology context
    and returns structured findings including rhythm classification,
    heart rate, intervals, axis, and abnormalities.

    Args:
        image_urls: JSON array of image URLs (S3 or public URLs)
        species: Animal species — "canine", "feline", or "equine"
        breed: Breed name for reference range comparison
        age_years: Patient age in years
        weight_kg: Patient weight in kilograms
        medications: JSON array of current medications (strings)
        tenant_id: Tenant context

    Returns:
        Structured findings dict with rhythm, heart_rate_bpm, intervals,
        axis_degrees, abnormalities, overall_confidence, breed_comparison,
        and raw_interpretation.
    """
    tenant_id = _resolve_tenant_id(tenant_id)
    urls = _parse_json(image_urls, [])
    meds = _parse_json(medications, [])

    if not urls:
        return {"status": "error", "error": "No image URLs provided"}

    # Fetch breed reference ranges for comparison
    ref_ranges = await get_breed_reference_ranges(species, breed, tenant_id)

    ref_context = ""
    if ref_ranges.get("status") != "error":
        props = ref_ranges.get("properties", {})
        ref_context = f"""
Normal reference ranges for {breed} ({species}):
- Heart rate: {props.get('heart_rate_range', 'unknown')} bpm
- PR interval: {props.get('pr_interval_ms', 'unknown')} ms
- QRS duration: {props.get('qrs_duration_ms', 'unknown')} ms
- QT interval: {props.get('qt_interval_ms', 'unknown')} ms
- Breed predispositions: {', '.join(props.get('common_conditions', []))}
- Notes: {props.get('breed_predisposition_notes', '')}
"""

    prompt = f"""You are an expert veterinary cardiologist analyzing an ECG recording.

Patient: {species}, {breed}, {age_years} years old, {weight_kg} kg
Current medications: {', '.join(meds) if meds else 'None'}
{ref_context}

Analyze the ECG image(s) and provide a structured interpretation. Return your findings as a JSON object with this exact structure:
{{
    "rhythm": "<classification: normal_sinus, sinus_arrhythmia, atrial_fibrillation, ventricular_tachycardia, etc>",
    "heart_rate_bpm": <integer>,
    "intervals": {{
        "pr_ms": <integer or null>,
        "qrs_ms": <integer or null>,
        "qt_ms": <integer or null>,
        "qt_corrected_ms": <integer or null>
    }},
    "axis_degrees": <integer or null>,
    "abnormalities": [
        {{
            "finding": "<abnormality name>",
            "severity": "mild | moderate | severe",
            "confidence": <0.0-1.0>,
            "evidence": "<what you see in the ECG>"
        }}
    ],
    "overall_confidence": <0.0-1.0>,
    "breed_comparison": {{
        "hr_normal_range": "<range> bpm",
        "within_normal": <true/false>
    }},
    "raw_interpretation": "<free-text narrative of your full interpretation>"
}}

Be precise. If you cannot measure an interval, set it to null. Flag any findings that warrant urgent attention."""

    # Build Claude API message with image content blocks
    # Fetch images and send as base64 (supports internal cluster URLs and public URLs)
    content_blocks = []
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as img_client:
        for url in urls:
            try:
                img_resp = await img_client.get(url)
                img_resp.raise_for_status()
                import base64
                import mimetypes
                mime_type = img_resp.headers.get("content-type", "").split(";")[0].strip()
                if not mime_type or not mime_type.startswith("image/"):
                    mime_type = mimetypes.guess_type(url)[0] or "image/jpeg"
                b64_data = base64.b64encode(img_resp.content).decode()
                content_blocks.append({
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": mime_type,
                        "data": b64_data,
                    },
                })
            except Exception as e:
                logger.warning("Failed to fetch image %s: %s", url, e)
    content_blocks.append({"type": "text", "text": prompt})

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            # Call Claude API directly for vision (ADK model may not support vision)
            response = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": settings.anthropic_api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": "claude-sonnet-4-20250514",
                    "max_tokens": 4096,
                    "messages": [{"role": "user", "content": content_blocks}],
                },
            )
            response.raise_for_status()
            data = response.json()

        # Extract the text response and parse JSON
        text_content = data["content"][0]["text"]

        # Try to extract JSON from the response
        json_match = re.search(r"\{[\s\S]*\}", text_content)
        if json_match:
            findings = json.loads(json_match.group())
            findings["status"] = "success"
            findings["images_analyzed"] = len(urls)
            return findings
        else:
            return {
                "status": "success",
                "raw_interpretation": text_content,
                "images_analyzed": len(urls),
                "note": "Could not parse structured findings; raw interpretation provided",
            }

    except httpx.HTTPStatusError as e:
        logger.exception(f"Claude API error: {e}")
        return {"status": "error", "error": f"Claude API error: {e.response.status_code}"}
    except Exception as e:
        logger.exception(f"ECG analysis failed: {e}")
        return {"status": "error", "error": str(e)}


async def get_breed_reference_ranges(
    species: str,
    breed: str,
    tenant_id: str = "auto",
) -> dict:
    """Look up normal ECG reference ranges for a specific breed.

    Queries knowledge entities with entity_type='reference' and
    category='breed_reference' matching the species and breed.

    Args:
        species: Animal species — "canine", "feline", or "equine"
        breed: Breed name to look up
        tenant_id: Tenant context

    Returns:
        Dict with breed reference properties (heart_rate_range, pr_interval_ms, etc.)
        or error if not found.
    """
    tenant_id = _resolve_tenant_id(tenant_id)
    kg = get_knowledge_service()

    # Search for breed reference entity via find_entities (text/vector search)
    results = await kg.find_entities(
        query=f"{breed} {species} ECG reference",
        tenant_id=tenant_id,
        entity_types=["reference"],
    )

    if results:
        # Retrieve full entity with properties for each match
        for entity_summary in results:
            entity = await kg.get_entity(entity_id=str(entity_summary["id"]))
            if entity.get("error"):
                continue
            props = entity.get("properties", {})
            if isinstance(props, str):
                props = _parse_json(props, {})
            if (
                props.get("species", "").lower() == species.lower()
                and props.get("breed", "").lower() == breed.lower()
            ):
                return {
                    "status": "found",
                    "name": entity.get("name"),
                    "properties": props,
                }

        # Return first result as partial match — fetch its full properties too
        first_entity = await kg.get_entity(entity_id=str(results[0]["id"]))
        first_props = first_entity.get("properties", {})
        if isinstance(first_props, str):
            first_props = _parse_json(first_props, {})
        return {
            "status": "partial_match",
            "name": first_entity.get("name", results[0].get("name")),
            "properties": first_props,
            "note": f"Exact match for {breed} not found; closest match returned",
        }

    return {
        "status": "error",
        "error": f"No reference ranges found for {species}/{breed}",
        "note": "Using general species defaults recommended",
    }
