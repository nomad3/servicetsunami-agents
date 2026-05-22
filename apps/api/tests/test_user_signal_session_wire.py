"""Tests for emotion_engine_io.record_session_user_signal (task #336).

PR #653 shipped the ``appraise_and_record_user_signal`` contract but
nothing in production code called it. Luna's 2026-05-22 audit P0 #1
flagged this as the largest unfinished bit of the value-layer Phase 1
work. The session-level wrapper added here is the missing caller; this
test file locks the contract:

  - empty session_id / empty user_text → no-op (defensive)
  - no episode yet → graceful no-op (mirrors tool_failure pattern for
    fresh sessions before PostChatMemoryWorkflow has run)
  - classifier raises → swallowed, returns None (fail-open)
  - appraisal raises → swallowed, returns None (fail-open)
  - happy path → classifier output forwarded to
    ``appraise_and_record_user_signal`` with the original user_text
  - agent_id=None → random UUID fallback fires (logged loudly)
"""
from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(name="frozen_classifier")
def frozen_classifier_fixture():
    """Patch the classifier so the helper takes a deterministic path
    without hitting Ollama or the heuristic backend at module-import
    time."""
    from app.services.user_signal_classifier import PADClassifierResult

    payload = PADClassifierResult(pleasure=0.4, arousal=-0.1, dominance=0.0)
    with patch(
        "app.services.user_signal_classifier.classify_user_signal",
        return_value=payload,
    ) as mock_classify:
        yield mock_classify, payload


def _mk_db_with_episode(episode):
    """MagicMock db whose ``query(ConversationEpisode)`` returns a
    chain that yields ``episode`` on ``.first()``."""
    db = MagicMock()

    def _query(model):
        chained = MagicMock()
        chained.filter.return_value.order_by.return_value.first.return_value = episode
        return chained

    db.query.side_effect = _query
    return db


def test_session_id_none_returns_none(frozen_classifier):
    """Defensive: missing session_id short-circuits without hitting
    the DB or classifier."""
    from app.services import emotion_engine_io

    mock_classify, _ = frozen_classifier
    db = MagicMock()

    result = emotion_engine_io.record_session_user_signal(
        db,
        session_id=None,
        tenant_id=uuid.uuid4(),
        user_text="hello",
    )
    assert result is None
    db.query.assert_not_called()
    mock_classify.assert_not_called()


def test_empty_user_text_returns_none(frozen_classifier):
    """Empty / whitespace-only text → no appraisal (nothing to
    classify; PAD would be neutral and the row would be noise)."""
    from app.services import emotion_engine_io

    mock_classify, _ = frozen_classifier
    db = MagicMock()

    result = emotion_engine_io.record_session_user_signal(
        db,
        session_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        user_text="   \n  ",
    )
    assert result is None
    mock_classify.assert_not_called()


def test_no_episode_for_session_returns_none_graceful(frozen_classifier):
    """First turns of a fresh session — PostChatMemoryWorkflow hasn't
    run yet, no episode exists. Helper bails gracefully (mirrors
    record_session_tool_failure)."""
    from app.services import emotion_engine_io

    mock_classify, _ = frozen_classifier
    db = _mk_db_with_episode(episode=None)

    result = emotion_engine_io.record_session_user_signal(
        db,
        session_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        user_text="hello",
    )
    assert result is None
    mock_classify.assert_not_called()


def test_classifier_raises_fail_open():
    """A crash in ``classify_user_signal`` (e.g. Ollama 5xx) MUST NOT
    propagate — the chat hot path is in the call stack. Return None."""
    from app.services import emotion_engine_io

    episode = MagicMock()
    episode.id = uuid.uuid4()
    db = _mk_db_with_episode(episode=episode)

    with patch(
        "app.services.user_signal_classifier.classify_user_signal",
        side_effect=RuntimeError("simulated classifier crash"),
    ):
        result = emotion_engine_io.record_session_user_signal(
            db,
            session_id=uuid.uuid4(),
            tenant_id=uuid.uuid4(),
            user_text="hello",
            backend="heuristic",
        )
    assert result is None


def test_appraisal_raises_fail_open(frozen_classifier):
    """A crash in ``appraise_and_record_user_signal`` (e.g. DB write
    conflict) MUST also be swallowed."""
    from app.services import emotion_engine_io

    episode = MagicMock()
    episode.id = uuid.uuid4()
    db = _mk_db_with_episode(episode=episode)

    with patch(
        "app.services.emotion_engine_io.appraise_and_record_user_signal",
        side_effect=RuntimeError("simulated appraisal crash"),
    ):
        result = emotion_engine_io.record_session_user_signal(
            db,
            session_id=uuid.uuid4(),
            tenant_id=uuid.uuid4(),
            agent_id=uuid.uuid4(),
            user_text="hello",
            backend="heuristic",
        )
    assert result is None


def test_happy_path_forwards_classifier_output_and_text(frozen_classifier):
    """Locked: when the episode exists + the classifier succeeds, the
    helper calls ``appraise_and_record_user_signal`` with the
    classifier's PAD payload AND the original user_text. The
    user_text is NOT the classifier output — they're separate args
    because the value-layer consult downstream needs the original
    text to slug-match against pursue items."""
    from app.services import emotion_engine_io

    mock_classify, payload = frozen_classifier
    episode = MagicMock()
    episode.id = uuid.uuid4()
    db = _mk_db_with_episode(episode=episode)
    tenant_id = uuid.uuid4()
    agent_id = uuid.uuid4()
    session_id = uuid.uuid4()

    captured = {}

    def _appraise(db_arg, **kw):
        captured.update(kw)
        return "stub-pad-vector"

    with patch(
        "app.services.emotion_engine_io.appraise_and_record_user_signal",
        side_effect=_appraise,
    ):
        result = emotion_engine_io.record_session_user_signal(
            db,
            session_id=session_id,
            tenant_id=tenant_id,
            agent_id=agent_id,
            user_text="this is working great, thanks!",
            backend="heuristic",
        )

    assert result == "stub-pad-vector"
    assert captured["episode_id"] == episode.id
    assert captured["tenant_id"] == tenant_id
    assert captured["agent_id"] == agent_id
    assert captured["user_text"] == "this is working great, thanks!"
    # Classifier output forwarded as the bounded `payload` (NOT raw text)
    assert captured["payload"] == payload.to_dict()


def test_agent_id_none_uses_random_uuid_fallback(frozen_classifier):
    """When the chat session has no agent_id resolved, the helper
    falls back to a random UUID (mirrors record_session_tool_failure
    pattern). The appraisal lands on a neutral baseline; logged
    loudly so ops can spot the attribution gap."""
    from app.services import emotion_engine_io

    episode = MagicMock()
    episode.id = uuid.uuid4()
    db = _mk_db_with_episode(episode=episode)

    captured = {}

    def _appraise(db_arg, **kw):
        captured.update(kw)
        return "stub"

    with patch(
        "app.services.emotion_engine_io.appraise_and_record_user_signal",
        side_effect=_appraise,
    ):
        emotion_engine_io.record_session_user_signal(
            db,
            session_id=uuid.uuid4(),
            tenant_id=uuid.uuid4(),
            agent_id=None,
            user_text="hi",
            backend="heuristic",
        )

    # A UUID was synthesized
    assert isinstance(captured["agent_id"], uuid.UUID)
