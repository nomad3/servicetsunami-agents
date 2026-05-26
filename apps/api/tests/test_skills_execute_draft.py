"""T4.4d — internal `POST /api/v1/skills/execute-draft` endpoint.

Used by mcp-server's ``run_synthetic_test`` (T2.5) to exercise an
unsaved skill_md against a reviewer-provided test input. The endpoint
parses the draft frontmatter, writes it to a transient location, runs
it through the existing skill-execution path, and returns the raw
output dict WITHOUT touching the persistent skills library.

Pins:
  * internal-key gated (401)
  * payload shape: {skill_md: str, inputs: dict}
  * malformed frontmatter / unsupported engine → 422
  * happy path returns the same envelope shape as execute_file_skill
"""
from __future__ import annotations

import os
os.environ["TESTING"] = "True"

from unittest.mock import patch

import pytest


def test_execute_draft_router_imports_clean():
    from app.api.v1 import routes  # noqa: F401
    from app.api.v1 import skills_new

    paths = {r.path for r in skills_new.router.routes}
    assert "/execute-draft" in paths


def _build_client(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.core.config import settings
    from app.api.v1.skills_new import router

    monkeypatch.setattr(settings, "API_INTERNAL_KEY", "test-key", raising=False)

    app = FastAPI()
    app.include_router(router, prefix="/api/v1/skills")
    return TestClient(app)


_VALID_MD = """---
name: probe-printer
engine: markdown
script_path: prompt.md
---

## Description
Probe a printer for {{model}} errors and return a fix plan.
"""

_VALID_PY = """---
name: doubler
engine: python
script_path: script.py
---

## Description
Doubles a number.
"""

_PY_SCRIPT = "def execute(inputs):\n    return {'doubled': inputs['n'] * 2}\n"


def test_execute_draft_requires_internal_key(monkeypatch):
    client = _build_client(monkeypatch)
    r = client.post(
        "/api/v1/skills/execute-draft",
        json={"skill_md": _VALID_MD, "inputs": {"model": "HP-2030"}},
    )
    assert r.status_code == 401


def test_execute_draft_rejects_malformed_frontmatter(monkeypatch):
    client = _build_client(monkeypatch)
    r = client.post(
        "/api/v1/skills/execute-draft",
        json={"skill_md": "no frontmatter at all", "inputs": {}},
        headers={"X-Internal-Key": "test-key"},
    )
    assert r.status_code == 422
    detail = r.json()["detail"].lower()
    assert "parse" in detail or "frontmatter" in detail or "malformed" in detail


def test_execute_draft_markdown_returns_substituted_prompt(monkeypatch):
    client = _build_client(monkeypatch)
    r = client.post(
        "/api/v1/skills/execute-draft",
        json={"skill_md": _VALID_MD, "inputs": {"model": "HP-2030"}},
        headers={"X-Internal-Key": "test-key"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["success"] is True
    assert "HP-2030" in body["result"]["prompt"]
    # The {{model}} placeholder must have been substituted away.
    assert "{{model}}" not in body["result"]["prompt"]


def test_execute_draft_python_executes(monkeypatch):
    client = _build_client(monkeypatch)
    r = client.post(
        "/api/v1/skills/execute-draft",
        json={
            "skill_md": _VALID_PY,
            "script": _PY_SCRIPT,
            "inputs": {"n": 21},
        },
        headers={"X-Internal-Key": "test-key"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["success"] is True
    assert body["result"] == {"doubled": 42}
