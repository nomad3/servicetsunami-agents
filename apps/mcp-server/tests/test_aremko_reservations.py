"""Tests for Aremko reservation MCP tools."""
from types import SimpleNamespace

import pytest

from src.mcp_tools import aremko


class _DummyResponse:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class _DummyClient:
    def __init__(self, recorder, payload):
        self._recorder = recorder
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url, headers=None, json=None):
        self._recorder.setdefault("calls", []).append(
            {
                "url": url,
                "headers": headers,
                "json": json,
            }
        )
        if callable(self._payload):
            payload = self._payload(url, json)
        else:
            payload = self._payload
        return _DummyResponse(payload)


@pytest.mark.asyncio
async def test_create_aremko_reservation_defaults_location(monkeypatch):
    recorder = {}

    def _payload(url, json_payload):
        if url.endswith("/reservas/validar/"):
            return {
                "success": True,
                "disponibilidad": [
                    {
                        "servicio_id": 12,
                        "disponible": True,
                    }
                ],
            }
        return {"success": True, "reservation_id": "RES-1234"}

    def _client_factory(*args, **kwargs):
        return _DummyClient(recorder, _payload)

    monkeypatch.setattr(aremko.httpx, "AsyncClient", _client_factory)

    result = await aremko.create_aremko_reservation(
        nombre="Jorge Aguilera González",
        email="ecolonco@gmail.com",
        telefono="+56958655810",
        servicios=[{
            "servicio_id": 12,
            "fecha": "2026-04-02",
            "hora": "14:30",
            "cantidad_personas": 4,
        }],
        documento_identidad="7.604.892-4",
        tenant_id="test-tenant",
        ctx=SimpleNamespace(),
    )

    create_call = recorder["calls"][1]
    assert create_call["json"]["cliente"]["region_id"] == aremko.DEFAULT_REGION_ID
    assert create_call["json"]["cliente"]["comuna_id"] == aremko.DEFAULT_COMUNA_ID
    assert result["success"] is True
    assert result["location"]["used_default_location"] is True
    assert result["location"]["region_id"] == aremko.DEFAULT_REGION_ID
    assert result["location"]["comuna_id"] == aremko.DEFAULT_COMUNA_ID


@pytest.mark.asyncio
async def test_create_aremko_reservation_stops_when_validation_fails(monkeypatch):
    async def _validation_failure(*args, **kwargs):
        return {
            "success": False,
            "disponibilidad": [
                {
                    "servicio_id": 12,
                    "disponible": False,
                    "motivo": "Horario no disponible",
                }
            ],
        }

    def _client_factory(*args, **kwargs):
        raise AssertionError("create endpoint should not be called when validation fails")

    monkeypatch.setattr(aremko, "validate_aremko_reservation", _validation_failure)
    monkeypatch.setattr(aremko.httpx, "AsyncClient", _client_factory)

    result = await aremko.create_aremko_reservation(
        nombre="Luna Test",
        email="test@example.com",
        telefono="+56900000000",
        servicios=[{
            "servicio_id": 12,
            "fecha": "2026-04-02",
            "hora": "13:00",
            "cantidad_personas": 4,
        }],
        tenant_id="test-tenant",
        ctx=SimpleNamespace(),
    )

    assert result["success"] is False
    assert "disponibilidad no fue confirmada" in result["error"]
    assert result["validation"]["disponibilidad"][0]["disponible"] is False
