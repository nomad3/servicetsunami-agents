"""T4.4b — server-side enforcement for `/api/v1/learning/upload-attachment`.

Spec §1.8 invariants pinned here:
  * audio/* or video/* MIME only (415 otherwise)
  * 50MB max size (413)
  * 900s ffprobe duration cap (413)
  * source_url recorded as `attachment://<basename>` (never full path)
  * internal-key gated (401 without header)

Uses minimal FastAPI app with just the learning router (avoids full
app spin-up which requires Postgres for UUID columns).
"""
from __future__ import annotations

import os
os.environ["TESTING"] = "True"

import pytest


def test_router_imports_clean():
    """Locked per feedback_test_router_startup: the v1 routes graph
    must import without error."""
    from app.api.v1 import routes  # noqa: F401
    from app.api.v1 import learning  # noqa: F401

    paths = {r.path for r in learning.router.routes}
    assert "/upload-attachment" in paths


def _build_client(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.core.config import settings
    from app.api.v1.learning import router

    monkeypatch.setattr(settings, "API_INTERNAL_KEY", "test-key", raising=False)

    app = FastAPI()
    app.include_router(router, prefix="/api/v1/learning")
    return TestClient(app)


def _post(client, file_bytes: bytes, filename: str, content_type: str, key: str = "test-key"):
    headers = {}
    if key is not None:
        headers["X-Internal-Key"] = key
    return client.post(
        "/api/v1/learning/upload-attachment",
        files={"file": (filename, file_bytes, content_type)},
        headers=headers,
    )


def test_attachment_requires_internal_key(monkeypatch):
    client = _build_client(monkeypatch)
    r = _post(client, b"OggS" + b"\x00" * 100, "v.ogg", "audio/ogg", key="wrong-key")
    assert r.status_code == 401


def test_attachment_bad_mime_rejected(monkeypatch):
    client = _build_client(monkeypatch)
    r = _post(client, b"hello", "doc.pdf", "application/pdf")
    assert r.status_code == 415
    detail = r.json()["detail"].lower()
    assert "mime" in detail or "type" in detail


def test_attachment_oversize_rejected(monkeypatch):
    client = _build_client(monkeypatch)
    # 51MB - one byte over the cap
    r = _post(client, b"\x00" * (51 * 1024 * 1024), "big.mp4", "video/mp4")
    assert r.status_code == 413
    assert "50" in r.json()["detail"]


def test_attachment_audio_ok(monkeypatch):
    monkeypatch.setattr("app.api.v1.learning._ffprobe_duration", lambda p: 120)
    client = _build_client(monkeypatch)
    payload = b"OggS" + b"\x00" * 100
    r = _post(client, payload, "voice.ogg", "audio/ogg")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["source_url"] == "attachment://voice.ogg"
    assert body["duration_s"] == 120
    assert body["size_bytes"] == len(payload)
    assert "attachment_path" in body
    assert "voice.ogg" in body["attachment_path"]


def test_attachment_too_long_rejected(monkeypatch):
    monkeypatch.setattr("app.api.v1.learning._ffprobe_duration", lambda p: 1200)
    client = _build_client(monkeypatch)
    r = _post(client, b"\x00" * 200, "long.mp4", "video/mp4")
    assert r.status_code == 413
    detail = r.json()["detail"].lower()
    assert "900" in detail or "duration" in detail


def test_attachment_ffprobe_failure_returns_422(monkeypatch):
    def boom(_p):
        raise RuntimeError("ffprobe missing")
    monkeypatch.setattr("app.api.v1.learning._ffprobe_duration", boom)
    client = _build_client(monkeypatch)
    r = _post(client, b"\x00" * 100, "weird.mp4", "video/mp4")
    assert r.status_code == 422
    assert "duration" in r.json()["detail"].lower() or "probe" in r.json()["detail"].lower()
