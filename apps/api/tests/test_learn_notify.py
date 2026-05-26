"""Tests for T3.5 — ``act_notify_session``.

The activity writes a ``ChatMessage(role="agent", context.kind="learn_complete")``
row to Luna's chat session so the existing WhatsApp message-out plumbing
surfaces the final result to the user (spec §2 step 8, plan §52).

These tests stub the DB at the SessionLocal boundary so the chat-session
table machinery (FK chain to tenants, datasets, agents) does not need to
materialise. The contract we pin down:

  * Happy path → ChatMessage created with role="agent",
    context.kind="learn_complete", full result merged into context,
    rendered body string.
  * Session missing → envelope ``{ok: False, error.type: "SessionNotFound"}``.
  * Bad session_id → envelope ``{ok: False, error.type: "InvalidSessionId"}``.
  * DB write error → envelope ``{ok: False, error.type: "NotifyWriteFailed"}``,
    rollback called (so the activity doesn't poison a shared session per
    the PR #349 cascade lesson).
  * Failure-status result → body forwards spec §3 ``message`` verbatim.
"""
from __future__ import annotations

import uuid

import pytest

from app.workflows.activities.learn_from_media_activities import (
    _render_notify_body,
    act_notify_session,
)


# ── Body rendering (pure function) ──────────────────────────────────


def test_render_body_success():
    body = _render_notify_body(
        {
            "status": "success",
            "skill_name": "cardio-vet",
            "capabilities": ["cardio-report", "vet-clinical"],
            "source_url": "https://youtu.be/abc",
        }
    )
    assert "cardio-vet" in body
    assert "cardio-report" in body
    assert "vet-clinical" in body
    assert "https://youtu.be/abc" in body
    assert body.startswith("✓ learned")


def test_render_body_success_resumed_tag():
    body = _render_notify_body(
        {
            "status": "success",
            "skill_name": "x",
            "capabilities": [],
            "source_url": "u",
            "resumed": True,
        }
    )
    assert "(resumed)" in body


def test_render_body_success_diffuse_cached_tag():
    body = _render_notify_body(
        {
            "status": "success",
            "skill_name": "x",
            "capabilities": [],
            "source_url": "u",
            "diffuse_cached": True,
        }
    )
    assert "(diffuse pending)" in body


def test_render_body_failure_forwards_message_verbatim():
    """Failure envelopes already carry spec §3 user-facing copy in
    ``message`` — the activity must NOT re-wrap or paraphrase it."""
    spec_3_copy = (
        "this video requires sign-in or is restricted — Luna can't access it."
    )
    body = _render_notify_body({"status": "extract_failed", "message": spec_3_copy})
    assert body == spec_3_copy


def test_render_body_unknown_fallback():
    """Unknown shapes still produce a closing string — never empty."""
    body = _render_notify_body({"status": "weird"})
    assert "weird" in body
    body2 = _render_notify_body({})
    assert body2  # non-empty


# ── DB-write boundary (mocked SessionLocal) ─────────────────────────


class _FakeSession:
    """Stand-in for a real SQLAlchemy session.

    Records ``add`` / ``commit`` / ``rollback`` / ``close`` so the test
    can assert the activity reached the right rails. ``query(...).first()``
    returns whatever we seeded via ``set_lookup_result``. ``refresh``
    assigns a deterministic UUID so the envelope carries a stable id.
    """

    def __init__(self, lookup_result=None, write_error: Exception | None = None):
        self._lookup = lookup_result
        self._write_error = write_error
        self.added: list = []
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    def query(self, _model):
        self._query_model = _model
        return self

    def filter(self, *_args, **_kwargs):
        return self

    def first(self):
        return self._lookup

    def add(self, obj):
        if self._write_error is not None:
            raise self._write_error
        self.added.append(obj)

    def commit(self):
        if self._write_error is not None:
            raise self._write_error
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def refresh(self, obj):
        if getattr(obj, "id", None) is None:
            obj.id = uuid.UUID("11111111-1111-1111-1111-111111111111")

    def close(self):
        self.closed = True


def _patch_session(monkeypatch, fake: _FakeSession):
    """Wire ``app.db.session.SessionLocal`` to return ``fake``.

    The activity imports SessionLocal lazily inside the function body
    (sandbox-safe — see §0g). Patching the module attribute is therefore
    enough; no need to also patch the activities module.
    """
    monkeypatch.setattr(
        "app.db.session.SessionLocal", lambda: fake, raising=True
    )


def _fake_session_row(session_id: uuid.UUID):
    """Return an object that looks enough like ChatSession for the activity."""
    obj = type("FakeChatSession", (), {})()
    obj.id = session_id
    return obj


@pytest.mark.asyncio
async def test_notify_session_writes_chat_message(monkeypatch):
    """Happy path: ChatMessage created with right role, content, context."""
    sess_id = uuid.UUID("22222222-2222-2222-2222-222222222222")
    fake = _FakeSession(lookup_result=_fake_session_row(sess_id))
    _patch_session(monkeypatch, fake)

    result = {
        "status": "success",
        "skill_id": "abc",
        "skill_path": "/skills/x",
        "skill_name": "cardio-vet",
        "capabilities": ["cardio-report"],
        "source_url": "https://youtu.be/xyz",
    }
    envelope = await act_notify_session(str(sess_id), result)

    assert envelope["ok"] is True
    assert envelope["error"] is None
    assert "message_id" in envelope["data"]
    assert "cardio-vet" in envelope["data"]["content"]

    assert len(fake.added) == 1
    msg = fake.added[0]
    assert msg.role == "agent"
    assert msg.session_id == sess_id
    assert "cardio-vet" in msg.content
    assert msg.context["kind"] == "learn_complete"
    # Full result is merged into context so the WhatsApp side has the
    # skill id / source url / capabilities without re-querying.
    assert msg.context["skill_id"] == "abc"
    assert msg.context["source_url"] == "https://youtu.be/xyz"

    assert fake.commits == 1
    assert fake.rollbacks == 0
    assert fake.closed is True


@pytest.mark.asyncio
async def test_notify_session_failure_envelope_forwards_message(monkeypatch):
    """Failure result → ChatMessage body is the spec §3 copy verbatim."""
    sess_id = uuid.UUID("33333333-3333-3333-3333-333333333333")
    fake = _FakeSession(lookup_result=_fake_session_row(sess_id))
    _patch_session(monkeypatch, fake)

    spec3_copy = "this video doesn't exist or has been removed."
    envelope = await act_notify_session(
        str(sess_id), {"status": "extract_failed", "message": spec3_copy}
    )

    assert envelope["ok"] is True
    msg = fake.added[0]
    assert msg.content == spec3_copy
    assert msg.context["kind"] == "learn_complete"
    assert msg.context["status"] == "extract_failed"


@pytest.mark.asyncio
async def test_notify_session_handles_missing_session(monkeypatch):
    """No row matching session_id → SessionNotFound envelope, no write."""
    fake = _FakeSession(lookup_result=None)
    _patch_session(monkeypatch, fake)

    envelope = await act_notify_session(
        "44444444-4444-4444-4444-444444444444",
        {"status": "success", "skill_name": "x", "capabilities": [], "source_url": "u"},
    )

    assert envelope["ok"] is False
    assert envelope["error"]["type"] == "SessionNotFound"
    assert fake.added == []
    assert fake.commits == 0
    assert fake.closed is True


@pytest.mark.asyncio
async def test_notify_session_handles_invalid_session_id(monkeypatch):
    """Non-UUID session_id → InvalidSessionId envelope (defensive — the
    workflow should never reach this path, but the activity is the trust
    boundary for the dispatch contract)."""
    fake = _FakeSession()
    _patch_session(monkeypatch, fake)

    envelope = await act_notify_session("not-a-uuid", {"status": "success"})

    assert envelope["ok"] is False
    assert envelope["error"]["type"] == "InvalidSessionId"
    assert fake.added == []
    assert fake.closed is True


@pytest.mark.asyncio
async def test_notify_session_rolls_back_on_write_error(monkeypatch):
    """A DB write error must roll back (PR #349 cascade lesson) and
    return a NotifyWriteFailed envelope rather than raising."""
    sess_id = uuid.UUID("55555555-5555-5555-5555-555555555555")
    fake = _FakeSession(
        lookup_result=_fake_session_row(sess_id),
        write_error=RuntimeError("connection refused"),
    )
    _patch_session(monkeypatch, fake)

    envelope = await act_notify_session(
        str(sess_id),
        {"status": "success", "skill_name": "x", "capabilities": [], "source_url": "u"},
    )

    assert envelope["ok"] is False
    assert envelope["error"]["type"] == "NotifyWriteFailed"
    assert "connection refused" in envelope["error"]["message"]
    assert fake.rollbacks == 1
    assert fake.closed is True
