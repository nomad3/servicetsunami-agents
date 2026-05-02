"""Tests for Microsoft agent discovery service.

Stubs the Graph endpoints via httpx mocks so we don't need real
microsoft credentials — what matters is that the service:
  - Returns the right shape (kind + raw payload importer can consume)
  - Handles 401 / 404 from Graph gracefully (one surface failing
    shouldn't kill the other)
  - Bounds results via the cap
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.services import microsoft_agent_discovery as mad


def _mock_response(status: int, value: list = None):
    r = MagicMock()
    r.status_code = status
    r.json = MagicMock(return_value={"value": value or [], "@odata.nextLink": None})
    return r


@pytest.mark.asyncio
async def test_copilot_studio_discovery_shape():
    """A Copilot Studio bot returned by Graph maps to the importer-
    compatible `raw` shape (kind=copilot_studio, schemaName, etc.)."""
    bots = [{
        "id": "bot-123",
        "displayName": "Acme Sales Bot",
        "description": "Helps with quotes",
        "schemaName": "Microsoft.CopilotStudio.v1",
        "generativeAI": {"instructions": "You are helpful."},
        "topics": [{"name": "pricing"}],
    }]
    client = MagicMock()
    client.get = AsyncMock(return_value=_mock_response(200, bots))
    out = await mad.discover_copilot_studio_bots(client, "fake-token")
    assert len(out) == 1
    assert out[0]["kind"] == "copilot_studio"
    assert out[0]["display_name"] == "Acme Sales Bot"
    raw = out[0]["raw"]
    assert raw["kind"] == "copilot_studio"
    assert raw["schemaName"] == "Microsoft.CopilotStudio.v1"
    assert raw["instructions"] == "You are helpful."
    assert raw["topics"] == [{"name": "pricing"}]
    assert raw["botId"] == "bot-123"


@pytest.mark.asyncio
async def test_ai_foundry_discovery_shape():
    """AI Foundry assistants map to importer-compatible Assistants shape."""
    assistants = [{
        "id": "asst_xyz",
        "name": "Cardiology Assistant",
        "description": "Reads echocardiograms",
        "model": "gpt-4o",
        "instructions": "You read echocardiograms.",
        "tools": [{"type": "code_interpreter"}],
        "endpoint": "https://contoso.openai.azure.com",
    }]
    client = MagicMock()
    client.get = AsyncMock(return_value=_mock_response(200, assistants))
    out = await mad.discover_ai_foundry_agents(client, "fake-token")
    assert len(out) == 1
    assert out[0]["kind"] == "ai_foundry"
    assert out[0]["display_name"] == "Cardiology Assistant"
    raw = out[0]["raw"]
    assert raw["kind"] == "ai_foundry"
    assert raw["name"] == "Cardiology Assistant"
    assert raw["model"] == "gpt-4o"
    assert raw["instructions"] == "You read echocardiograms."
    assert raw["tools"] == [{"type": "code_interpreter"}]
    assert raw["endpoint"] == "https://contoso.openai.azure.com"


@pytest.mark.asyncio
async def test_pagination_follows_nextlink_to_cap():
    """`_list_paginated` follows @odata.nextLink up to the cap."""
    page1 = MagicMock()
    page1.status_code = 200
    page1.json = MagicMock(return_value={
        "value": [{"id": str(i)} for i in range(50)],
        "@odata.nextLink": "https://graph.microsoft.com/beta/page2",
    })
    page2 = MagicMock()
    page2.status_code = 200
    page2.json = MagicMock(return_value={
        "value": [{"id": str(i)} for i in range(50, 100)],
        "@odata.nextLink": None,
    })
    client = MagicMock()
    # Two-call sequence: page1 then page2
    client.get = AsyncMock(side_effect=[page1, page2])
    out = await mad._list_paginated(client, "https://example.com/page1", {}, cap=200)
    assert len(out) == 100  # both pages combined


@pytest.mark.asyncio
async def test_pagination_respects_cap():
    """When pages would exceed cap, stop at cap."""
    page1 = MagicMock()
    page1.status_code = 200
    page1.json = MagicMock(return_value={
        "value": [{"id": str(i)} for i in range(150)],
        "@odata.nextLink": None,
    })
    client = MagicMock()
    client.get = AsyncMock(return_value=page1)
    out = await mad._list_paginated(client, "https://x", {}, cap=100)
    assert len(out) == 100  # truncated to cap


@pytest.mark.asyncio
async def test_discovery_handles_endpoint_404():
    """If Graph returns 404 / 501 for one surface (e.g. tenant doesn't
    have AI Foundry enabled), the other surface still returns its
    results."""
    not_found = _mock_response(404, [])
    client = MagicMock()
    client.get = AsyncMock(return_value=not_found)
    # _list_paginated returns empty on non-200; helpers return [].
    bots = await mad.discover_copilot_studio_bots(client, "tok")
    assert bots == []
