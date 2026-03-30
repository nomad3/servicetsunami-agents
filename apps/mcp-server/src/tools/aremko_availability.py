"""Helpers for querying curated availability from aremko.cl."""
from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Iterable
from urllib.parse import urlencode

import httpx


AREMKO_BASE_URL = "https://www.aremko.cl"

BUTTON_PATTERN = re.compile(
    r'<button[^>]*class="[^"]*js-open-booking-modal[^"]*"'
    r'[^>]*data-servicio-id="(?P<id>[^"]+)"'
    r'[^>]*data-servicio-nombre="(?P<nombre>[^"]+)"'
    r'[^>]*data-servicio-precio="(?P<precio>[^"]+)"'
    r'[^>]*data-servicio-tipo="(?P<tipo>[^"]+)"'
    r'(?P<rest>[^>]*)>',
    re.S,
)

CATALOG_URLS = {
    "masajes": f"{AREMKO_BASE_URL}/masajes/",
    "cabanas": f"{AREMKO_BASE_URL}/alojamientos/",
    "tinajas": f"{AREMKO_BASE_URL}/",
}

CURATED_SERVICE_NAMES = {
    "cabanas": {
        "Cabaña Arrayan",
        "Cabaña Laurel",
        "Cabaña Tepa",
        "Cabaña Torre",
        "Cabaña Acantilado",
    },
    "tinajas": {
        "Tina Hornopiren",
        "Tina Calbuco",
        "Tina Osorno",
        "Tina Tronador",
        "Tina Hidromasaje Llaima",
        "Tina Hidromasaje Puntiagudo",
        "Tina Hidromasaje Villarrica",
        "Tina Hidromasaje Puyehue",
    },
}

CURATED_MASSAGE_KEYWORDS = {
    "relajacion",
    "descontracturante",
}

FALLBACK_SERVICES = {
    "cabanas": [
        {"id": "9", "nombre": "Cabaña Arrayan", "precio": 90000, "tipo": "cabana"},
        {"id": "8", "nombre": "Cabaña Laurel", "precio": 90000, "tipo": "cabana"},
        {"id": "7", "nombre": "Cabaña Tepa", "precio": 90000, "tipo": "cabana"},
        {"id": "3", "nombre": "Cabaña Torre", "precio": 100000, "tipo": "cabana"},
        {"id": "6", "nombre": "Cabaña Acantilado", "precio": 90000, "tipo": "cabana"},
    ],
    "masajes": [
        {
            "id": "53",
            "nombre": "Masaje Relajación o Descontracturante",
            "precio": 40000,
            "tipo": "masaje",
        }
    ],
    "tinajas": [
        {"id": "1", "nombre": "Tina Hornopiren", "precio": 25000, "tipo": "tina"},
        {"id": "12", "nombre": "Tina Calbuco", "precio": 25000, "tipo": "tina"},
        {"id": "11", "nombre": "Tina Osorno", "precio": 25000, "tipo": "tina"},
        {"id": "10", "nombre": "Tina Tronador", "precio": 25000, "tipo": "tina"},
        {"id": "14", "nombre": "Tina Hidromasaje Llaima", "precio": 30000, "tipo": "tina"},
        {"id": "13", "nombre": "Tina Hidromasaje Puntiagudo", "precio": 30000, "tipo": "tina"},
        {"id": "15", "nombre": "Tina Hidromasaje Villarrica", "precio": 30000, "tipo": "tina"},
        {"id": "16", "nombre": "Tina Hidromasaje Puyehue", "precio": 30000, "tipo": "tina"},
    ],
}

CATEGORY_ALIASES = {
    "cabana": "cabanas",
    "cabanas": "cabanas",
    "cabañas": "cabanas",
    "alojamiento": "cabanas",
    "alojamientos": "cabanas",
    "masaje": "masajes",
    "masajes": "masajes",
    "tina": "tinajas",
    "tinas": "tinajas",
    "tinaja": "tinajas",
    "tinajas": "tinajas",
}


@dataclass
class CuratedService:
    id: str
    nombre: str
    precio: int
    tipo: str
    horas_disponibles: list[str]

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "nombre": self.nombre,
            "precio": self.precio,
            "tipo": self.tipo,
            "horas_disponibles": self.horas_disponibles,
        }


def _normalize(text: str) -> str:
    stripped = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in stripped if not unicodedata.combining(ch)).lower().strip()


def canonical_service_type(service_type: str) -> str:
    key = _normalize(service_type)
    if key not in CATEGORY_ALIASES:
        raise ValueError(
            "service_type debe ser 'tinajas', 'cabanas' o 'masajes'"
        )
    return CATEGORY_ALIASES[key]


def resolve_fecha(fecha: str | None, today: date | None = None) -> date:
    base = today or date.today()
    if not fecha:
        return base

    normalized = _normalize(fecha)
    if normalized in {"hoy", "today"}:
        return base
    if normalized in {"manana", "mañana", "tomorrow"}:
        return base + timedelta(days=1)
    if normalized in {"pasado manana", "pasado mañana"}:
        return base + timedelta(days=2)

    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(fecha, fmt).date()
        except ValueError:
            continue

    raise ValueError(
        "fecha debe ser 'hoy', 'mañana' o una fecha en formato YYYY-MM-DD, DD-MM-YYYY o DD/MM/YYYY"
    )


def _decode_nombre(nombre: str) -> str:
    return json.loads(f'"{nombre}"')


def _parse_buttons(html: str) -> list[dict]:
    services = []
    for match in BUTTON_PATTERN.finditer(html):
        services.append(
            {
                "id": match.group("id"),
                "nombre": _decode_nombre(match.group("nombre")),
                "precio": int(match.group("precio")),
                "tipo": match.group("tipo"),
            }
        )
    return services


def _curate_services(category: str, services: Iterable[dict]) -> list[dict]:
    if category == "masajes":
        selected = []
        for svc in services:
            normalized_name = _normalize(svc["nombre"])
            if any(keyword in normalized_name for keyword in CURATED_MASSAGE_KEYWORDS):
                selected.append(svc)
    else:
        allowed = CURATED_SERVICE_NAMES[category]
        selected = [svc for svc in services if svc["nombre"] in allowed]
    selected.sort(key=lambda svc: svc["nombre"])
    return selected


async def fetch_catalog(category: str) -> tuple[list[dict], str]:
    url = CATALOG_URLS[category]
    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
        response = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
        response.raise_for_status()
    parsed = _curate_services(category, _parse_buttons(response.text))
    if parsed:
        return parsed, "live_catalog"
    return FALLBACK_SERVICES[category], "fallback_catalog"


async def fetch_available_hours(service_id: str, target_date: date) -> list[str]:
    params = urlencode({"servicio_id": service_id, "fecha": target_date.isoformat()})
    url = f"{AREMKO_BASE_URL}/ventas/get-available-hours/?{params}"
    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
        response = await client.get(
            url,
            headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
        )
        response.raise_for_status()
    payload = response.json()
    if not payload.get("success"):
        return []
    return [str(hour) for hour in payload.get("horas_disponibles", [])]


def _build_summary(category: str, target_date: date, services: list[CuratedService]) -> str:
    labels = {
        "tinajas": "tinajas",
        "cabanas": "cabañas",
        "masajes": "masajes",
    }
    lines = [f"Disponibilidad de {labels[category]} para {target_date.isoformat()}:"]
    for service in services:
        if service.horas_disponibles:
            lines.append(f"- {service.nombre}: {', '.join(service.horas_disponibles)}")
        else:
            lines.append(f"- {service.nombre}: sin disponibilidad")
    return "\n".join(lines)


async def check_aremko_availability_data(
    service_type: str,
    fecha: str | None = None,
) -> dict:
    category = canonical_service_type(service_type)
    target_date = resolve_fecha(fecha)
    catalog, source = await fetch_catalog(category)

    services = []
    for service in catalog:
        available_hours = await fetch_available_hours(service["id"], target_date)
        services.append(
            CuratedService(
                id=service["id"],
                nombre=service["nombre"],
                precio=service["precio"],
                tipo=service["tipo"],
                horas_disponibles=available_hours,
            )
        )

    return {
        "service_type": category,
        "fecha": target_date.isoformat(),
        "catalog_source": source,
        "services": [service.to_dict() for service in services],
        "summary": _build_summary(category, target_date, services),
    }
