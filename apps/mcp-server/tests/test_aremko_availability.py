"""Tests for Aremko availability helpers."""
from datetime import date

import pytest

from src.tools.aremko_availability import (
    _curate_services,
    canonical_service_type,
    resolve_fecha,
)


def test_canonical_service_type_accepts_aliases():
    assert canonical_service_type("cabañas") == "cabanas"
    assert canonical_service_type("masaje") == "masajes"
    assert canonical_service_type("tinas") == "tinajas"


def test_resolve_fecha_supports_relative_and_absolute_values():
    base = date(2026, 3, 30)
    assert resolve_fecha("mañana", today=base).isoformat() == "2026-03-31"
    assert resolve_fecha("01/04/2026", today=base).isoformat() == "2026-04-01"
    assert resolve_fecha("2026-04-02", today=base).isoformat() == "2026-04-02"


def test_curate_services_keeps_only_supported_massages_and_cabins():
    services = [
        {"id": "53", "nombre": "Masaje Relajación o Descontracturante", "precio": 40000, "tipo": "masaje"},
        {"id": "56", "nombre": "Masaje Deportivo", "precio": 45000, "tipo": "masaje"},
        {"id": "9", "nombre": "Cabaña Arrayan", "precio": 90000, "tipo": "cabana"},
        {"id": "26", "nombre": "Desayuno", "precio": 20000, "tipo": "otro"},
    ]

    assert _curate_services("masajes", services) == [
        {"id": "53", "nombre": "Masaje Relajación o Descontracturante", "precio": 40000, "tipo": "masaje"},
    ]
    assert _curate_services("cabanas", services) == [
        {"id": "9", "nombre": "Cabaña Arrayan", "precio": 90000, "tipo": "cabana"},
    ]


def test_invalid_date_raises_clear_error():
    with pytest.raises(ValueError):
        resolve_fecha("proxima semana")
