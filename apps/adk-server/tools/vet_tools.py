"""Veterinary cardiology tools.

Cardiac diagnostic image analysis (echocardiograms + ECGs) via Gemini vision
and breed reference range lookups from the knowledge graph.
"""
import base64
import json
import logging
import mimetypes
import re
from typing import Optional

import httpx
from google import genai

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


async def analyze_cardiac_images(
    image_urls: str,
    species: str,
    breed: str,
    age_years: float,
    weight_kg: float,
    medications: str = "[]",
    tenant_id: str = "auto",
) -> dict:
    """Analyze cardiac diagnostic images (echocardiograms and ECGs) using Gemini vision.

    Sends echocardiogram and/or ECG images to Gemini with veterinary cardiology
    context and returns structured findings including image classification,
    echo measurements, narrative summary, ACVIM/HCM staging, and abnormalities.

    Args:
        image_urls: JSON array of image URLs (S3 or public URLs)
        species: Animal species — "canine", "feline", or "equine"
        breed: Breed name for reference range comparison
        age_years: Patient age in years
        weight_kg: Patient weight in kilograms
        medications: JSON array of current medications (strings)
        tenant_id: Tenant context

    Returns:
        Structured findings dict with image_classifications, echo_measurements,
        echo_summary, suggested_staging, abnormalities, and raw_interpretation.
    """
    tenant_id = _resolve_tenant_id(tenant_id)
    urls = _parse_json(image_urls, [])
    meds = _parse_json(medications, [])

    if not urls:
        return {"status": "error", "error": "No image URLs provided"}

    # Normalize URLs: prepend internal base URL for relative paths
    base = settings.healthpets_api_url.rstrip("/")
    urls = [u if u.startswith(("http://", "https://")) else f"{base}{u}" for u in urls]

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
- LVIDd normal range: {props.get('lvidd_range', 'unknown')} cm
- LA/Ao normal range: {props.get('la_ao_range', 'unknown')}
- Breed predispositions: {', '.join(props.get('common_conditions', []))}
- Notes: {props.get('breed_predisposition_notes', '')}
"""

    staging_system = "HCM staging (normal / equivocal / mild / moderate / severe)" if species.lower() == "feline" else "ACVIM staging (A / B1 / B2 / C / D)"

    prompt = f"""You are an expert veterinary cardiologist analyzing cardiac diagnostic images (echocardiograms and/or ECG recordings).

Patient: {species}, {breed}, {age_years} years old, {weight_kg} kg
Current medications: {', '.join(meds) if meds else 'None'}
{ref_context}

For each image provided, perform the following steps:

1. **Image Classification**: Classify each image as one of: 2d_echo, mmode, doppler, color_flow, measurement_screen, ecg_strip. Also identify the view (e.g., right_parasternal_long_axis, right_parasternal_short_axis, left_apical_4_chamber, subcostal, etc.).

2. **Measurement Extraction**: For any measurement screens, extract all visible numeric measurements and organize them into structured sections:
   - "2d": 2D echocardiographic measurements (e.g., LVIDd, LVIDs, LVPWd, LVPWs, IVSd, IVSs, LA, Ao, LA_Ao, FS, EF, EPSS, etc.)
   - "mmode": M-mode measurements (e.g., IVSd_MM, LVIDd_MM, LVPWd_MM, IVSs_MM, LVIDs_MM, LVPWs_MM, FS_MM, etc.)
   - "doppler": Doppler measurements (e.g., LVOT_Vmax, LVOT_VTI, AV_Vmax, MV_E, MV_A, E_A_ratio, IVRT, PA_Vmax, TR_Vmax, etc.)
   Include units where visible. Use numeric values (cm, cm/s, m/s, mmHg, ms, %).

3. **Echo Narrative Summary**: Generate a concise echocardiographic narrative describing all findings across the images — chamber sizes, wall thickness, valve morphology, systolic/diastolic function, flow patterns, and any abnormalities.

4. **Staging Suggestion**: Based on all findings, suggest a {staging_system} with confidence and reasoning.

5. **Abnormalities**: List all abnormalities found with severity and evidence.

Return your findings as a JSON object with this exact structure:
{{
    "image_classifications": [
        {{"url": "<image url>", "type": "<2d_echo|mmode|doppler|color_flow|measurement_screen|ecg_strip>", "view": "<view name>"}}
    ],
    "echo_measurements": {{
        "2d": {{"LVIDd": null, "LVIDs": null, "LVPWd": null, "LVPWs": null, "IVSd": null, "IVSs": null, "LA": null, "Ao": null, "LA_Ao": null, "FS": null, "EF": null, "EPSS": null}},
        "mmode": {{"IVSd_MM": null, "LVIDd_MM": null, "LVPWd_MM": null, "IVSs_MM": null, "LVIDs_MM": null, "LVPWs_MM": null, "FS_MM": null}},
        "doppler": {{"LVOT_Vmax": null, "LVOT_VTI": null, "AV_Vmax": null, "MV_E": null, "MV_A": null, "E_A_ratio": null, "IVRT": null, "PA_Vmax": null, "TR_Vmax": null}}
    }},
    "echo_summary": "<concise echocardiographic narrative>",
    "suggested_staging": {{
        "system": "<acvim|hcm>",
        "stage": "<stage>",
        "confidence": <0.0-1.0>,
        "reasoning": "<explanation>"
    }},
    "abnormalities": [
        {{
            "finding": "<abnormality name>",
            "severity": "mild | moderate | severe",
            "confidence": <0.0-1.0>,
            "evidence": "<what you see in the images>"
        }}
    ],
    "raw_interpretation": "<full narrative text of your complete interpretation>"
}}

Only populate measurement fields you can actually read from the images. Set unreadable fields to null. Flag any findings that warrant urgent attention."""

    # Fetch images and build Gemini content parts
    parts = []
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as img_client:
        for url in urls:
            try:
                img_resp = await img_client.get(url)
                img_resp.raise_for_status()
                mime_type = img_resp.headers.get("content-type", "").split(";")[0].strip()
                if not mime_type or not mime_type.startswith("image/"):
                    mime_type = mimetypes.guess_type(url)[0] or "image/jpeg"
                parts.append(genai.types.Part.from_bytes(
                    data=img_resp.content,
                    mime_type=mime_type,
                ))
            except Exception as e:
                logger.warning("Failed to fetch image %s: %s", url, e)
    parts.append(prompt)

    try:
        client = genai.Client()
        response = await client.aio.models.generate_content(
            model="gemini-3.1-pro-preview",
            contents=parts,
        )
        text_content = response.text

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

    except Exception as e:
        logger.exception("Cardiac image analysis failed: %s", e)
        return {"status": "error", "error": str(e)}


async def get_breed_reference_ranges(
    species: str,
    breed: str,
    tenant_id: str = "auto",
) -> dict:
    """Look up normal cardiac reference ranges for a specific breed.

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
