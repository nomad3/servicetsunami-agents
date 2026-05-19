"""Unit tests for the `alpha review` cross-CLI consensus pipeline.

Covers:
  * the pure consensus aggregator (`aggregate_findings`) — no DB
  * the free-form text parser (`parse_findings_from_text`)
  * the round lifecycle (start → record per CLI → consensus →
    awaiting_response → reply → done) — driven via a mocked Session

The end-to-end Temporal dispatch path is exercised indirectly: the
router's start_review hook into `dispatch_review_workflow` is
monkeypatched, but the rest of the service runs as production code.

Dependency note: real CLI dispatch (the `alpha run` chain) is gated
on task #287; until that lands, leaf CLIs are simulated by directly
calling `record_cli_findings`.
"""
from __future__ import annotations

import os
os.environ["TESTING"] = "True"

import uuid
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

import pytest

from app.services.review_service import (
    _jaccard,
    _line_ranges_overlap,
    _strongest_severity,
    _tokenize,
    aggregate_findings,
    parse_findings_from_text,
)


# ── Pure-function unit tests ──────────────────────────────────────────


def test_tokenize_drops_stopwords_and_shorts():
    toks = _tokenize("The race condition is in the user login flow")
    assert "race" in toks
    assert "condition" in toks
    assert "the" not in toks
    assert "is" not in toks


def test_jaccard_identical_sets_is_one():
    a = {"race", "condition", "login"}
    assert _jaccard(a, a) == 1.0


def test_jaccard_disjoint_sets_is_zero():
    assert _jaccard({"a"}, {"b"}) == 0.0


def test_jaccard_empty_inputs_is_zero():
    assert _jaccard(set(), set()) == 0.0


def test_line_ranges_overlap_basic():
    assert _line_ranges_overlap("10-20", "15-25") is True
    # Default slack=5 — "10-20" and "30-40" are 10 apart, no overlap.
    assert _line_ranges_overlap("10-20", "30-40") is False
    assert _line_ranges_overlap("10", "10-20") is True


def test_line_ranges_overlap_slack_window():
    # Adjacent ranges within slack=5 cluster together so a finding at
    # line 11 (no range) clusters with a finding at lines 10-12.
    assert _line_ranges_overlap("10-20", "21-30") is True
    assert _line_ranges_overlap("10-20", "26-30") is False


def test_line_ranges_overlap_none_is_permissive():
    # None means "no specific range" — should not block matching.
    assert _line_ranges_overlap(None, "5-7") is True
    assert _line_ranges_overlap("5-7", None) is True


def test_strongest_severity_picks_blocker():
    assert _strongest_severity(["NIT", "BLOCKER", "IMPORTANT"]) == "BLOCKER"
    assert _strongest_severity(["NIT", "NIT"]) == "NIT"
    assert _strongest_severity(["IMPORTANT", "NIT"]) == "IMPORTANT"


# ── Text parser ───────────────────────────────────────────────────────


def test_parse_findings_extracts_blocker_with_file_range():
    text = (
        "Review notes:\n"
        "- BLOCKER apps/api/main.py:42-50 SQL injection in login query\n"
        "- IMPORTANT apps/api/utils.py:7 missing input validation\n"
        "- NIT apps/api/style.py:1 prefer f-string over %\n"
    )
    findings = parse_findings_from_text(text)
    assert len(findings) == 3
    by_sev = {f["severity"]: f for f in findings}
    assert by_sev["BLOCKER"]["file"] == "apps/api/main.py"
    assert by_sev["BLOCKER"]["line_range"] == "42-50"
    assert "sql injection" in by_sev["BLOCKER"]["description"].lower()
    assert by_sev["IMPORTANT"]["line_range"] == "7"


def test_parse_findings_ignores_lines_without_severity():
    text = "Some commentary.\n- Another note without severity."
    assert parse_findings_from_text(text) == []


def test_parse_findings_handles_no_file_reference():
    text = "- BLOCKER This module has no tests"
    findings = parse_findings_from_text(text)
    assert len(findings) == 1
    assert findings[0]["file"] is None
    assert findings[0]["line_range"] is None


def test_parse_findings_empty_input_returns_empty():
    assert parse_findings_from_text("") == []
    assert parse_findings_from_text("   ") == []


# ── Consensus aggregator ──────────────────────────────────────────────


def _f(severity: str, file: Optional[str], line_range: Optional[str], desc: str) -> Dict:
    return {
        "severity": severity,
        "file": file,
        "line_range": line_range,
        "description": desc,
    }


def test_aggregate_findings_two_clis_agree_emits_one_cluster():
    per_cli = {
        "claude": [_f("BLOCKER", "main.py", "10-20", "race condition login flow")],
        "codex": [_f("IMPORTANT", "main.py", "12-18", "login flow race condition issue")],
    }
    agreed = aggregate_findings(per_cli)
    assert len(agreed) == 1
    c = agreed[0]
    assert c["severity"] == "BLOCKER"  # strongest wins
    assert set(c["cli_set"]) == {"claude", "codex"}
    assert c["file"] == "main.py"


def test_aggregate_findings_solo_cli_filtered_out():
    per_cli = {
        "claude": [_f("BLOCKER", "main.py", "1", "weird issue alone")],
        "codex": [_f("BLOCKER", "other.py", "1", "totally different thing")],
    }
    assert aggregate_findings(per_cli) == []


def test_aggregate_findings_different_files_not_clustered():
    per_cli = {
        "claude": [_f("BLOCKER", "a.py", "1", "race condition login")],
        "codex": [_f("BLOCKER", "b.py", "1", "race condition login")],
    }
    assert aggregate_findings(per_cli) == []


def test_aggregate_findings_three_clis_consensus():
    desc = "missing input validation on user payload"
    per_cli = {
        "claude": [_f("IMPORTANT", "api.py", "10", desc)],
        "codex": [_f("IMPORTANT", "api.py", "10-12", "user payload validation missing")],
        "gemini": [_f("BLOCKER", "api.py", "11", "input validation missing user payload")],
    }
    agreed = aggregate_findings(per_cli)
    assert len(agreed) == 1
    assert agreed[0]["severity"] == "BLOCKER"
    assert set(agreed[0]["cli_set"]) == {"claude", "codex", "gemini"}


def test_aggregate_findings_sorts_blocker_first():
    per_cli = {
        "claude": [
            _f("NIT", "x.py", "1", "naming convention typo wrong"),
            _f("BLOCKER", "y.py", "1", "data loss bug critical"),
        ],
        "codex": [
            _f("NIT", "x.py", "1", "typo in naming convention wrong"),
            _f("BLOCKER", "y.py", "1", "critical data loss bug"),
        ],
    }
    agreed = aggregate_findings(per_cli)
    assert len(agreed) == 2
    assert agreed[0]["severity"] == "BLOCKER"
    assert agreed[1]["severity"] == "NIT"


# ── Service-level lifecycle (mocked DB) ───────────────────────────────


class _FakeReview:
    """Stand-in for the ReviewCoalition ORM row inside service tests."""

    def __init__(self, **kw):
        self.id = kw.get("id", uuid.uuid4())
        self.tenant_id = kw.get("tenant_id", uuid.uuid4())
        self.blackboard_id = kw.get("blackboard_id")
        self.chat_session_id = kw.get("chat_session_id")
        self.ref = kw.get("ref", "#1")
        self.scope = kw.get("scope", "bugs+security")
        self.clis = kw.get("clis", [])
        self.rounds_completed = kw.get("rounds_completed", 0)
        self.max_rounds = kw.get("max_rounds", 3)
        self.status = kw.get("status", "running")
        self.findings = kw.get("findings", {"per_cli": {}, "last_round": 0})
        self.agreed_findings = kw.get("agreed_findings", [])
        self.last_reply_ref = kw.get("last_reply_ref")
        self.created_at = None
        self.updated_at = None


def _stub_db_for_review(review: _FakeReview) -> MagicMock:
    """Build a minimal DB session mock that returns `review` from
    `get_review` and is a no-op for add/commit/refresh."""
    db = MagicMock()
    db.add = MagicMock()
    db.commit = MagicMock()
    db.refresh = MagicMock()
    db.rollback = MagicMock()
    return db


def test_record_cli_findings_partial_round_stays_running(monkeypatch):
    """First CLI reports — review must stay 'running' until the rest."""
    from app.services import review_service

    review = _FakeReview(
        clis=[{"name": "claude", "agent_slug": "claude"},
              {"name": "codex", "agent_slug": "codex"}],
    )
    db = _stub_db_for_review(review)

    monkeypatch.setattr(review_service, "get_review", lambda *_a, **_k: review)
    # blackboard write is best-effort — short-circuit it.
    monkeypatch.setattr(
        review_service.blackboard_service,
        "add_entry",
        lambda *_a, **_k: None,
    )

    out = review_service.record_cli_findings(
        db,
        review.tenant_id,
        review.id,
        cli="claude",
        raw_text="- BLOCKER main.py:1 oops",
    )
    assert out is not None
    assert out.status == "running"
    assert out.rounds_completed == 0
    assert "claude" in out.findings["per_cli"]
    assert "codex" not in out.findings["per_cli"]


def test_record_cli_findings_consensus_completes_round(monkeypatch):
    """Both CLIs report the same issue → agreed_findings populated +
    status 'awaiting_response' (still more rounds available)."""
    from app.services import review_service

    review = _FakeReview(
        clis=[{"name": "claude", "agent_slug": "claude"},
              {"name": "codex", "agent_slug": "codex"}],
        max_rounds=3,
    )
    db = _stub_db_for_review(review)
    monkeypatch.setattr(review_service, "get_review", lambda *_a, **_k: review)
    monkeypatch.setattr(
        review_service.blackboard_service, "add_entry", lambda *_a, **_k: None,
    )

    review_service.record_cli_findings(
        db, review.tenant_id, review.id,
        cli="claude",
        raw_text="- BLOCKER main.py:10-20 race condition login flow",
    )
    out = review_service.record_cli_findings(
        db, review.tenant_id, review.id,
        cli="codex",
        raw_text="- BLOCKER main.py:12-18 login flow race condition issue",
    )
    assert out.rounds_completed == 1
    assert out.status == "awaiting_response"
    assert len(out.agreed_findings) == 1
    assert set(out.agreed_findings[0]["cli_set"]) == {"claude", "codex"}


def test_record_cli_findings_consensus_zero_marks_done(monkeypatch):
    """Both CLIs return but disagree on everything → status 'done'."""
    from app.services import review_service

    review = _FakeReview(
        clis=[{"name": "claude", "agent_slug": "claude"},
              {"name": "codex", "agent_slug": "codex"}],
    )
    db = _stub_db_for_review(review)
    monkeypatch.setattr(review_service, "get_review", lambda *_a, **_k: review)
    monkeypatch.setattr(
        review_service.blackboard_service, "add_entry", lambda *_a, **_k: None,
    )

    review_service.record_cli_findings(
        db, review.tenant_id, review.id,
        cli="claude",
        raw_text="- BLOCKER alpha.py:1 something completely different here",
    )
    out = review_service.record_cli_findings(
        db, review.tenant_id, review.id,
        cli="codex",
        raw_text="- BLOCKER beta.py:99 totally unrelated other issue",
    )
    assert out.status == "done"
    assert out.agreed_findings == []


def test_record_cli_findings_max_rounds_caps(monkeypatch):
    from app.services import review_service

    review = _FakeReview(
        clis=[{"name": "claude", "agent_slug": "claude"},
              {"name": "codex", "agent_slug": "codex"}],
        max_rounds=1,
    )
    db = _stub_db_for_review(review)
    monkeypatch.setattr(review_service, "get_review", lambda *_a, **_k: review)
    monkeypatch.setattr(
        review_service.blackboard_service, "add_entry", lambda *_a, **_k: None,
    )

    review_service.record_cli_findings(
        db, review.tenant_id, review.id,
        cli="claude",
        raw_text="- BLOCKER main.py:10 race condition login flow",
    )
    out = review_service.record_cli_findings(
        db, review.tenant_id, review.id,
        cli="codex",
        raw_text="- BLOCKER main.py:10 race condition login flow",
    )
    # Still agreed findings, but max_rounds == 1 means we stop.
    assert out.rounds_completed == 1
    assert out.status == "done"
    assert len(out.agreed_findings) == 1


def test_apply_reply_resets_round_and_advances_ref(monkeypatch):
    from app.services import review_service

    review = _FakeReview(
        clis=[{"name": "claude", "agent_slug": "claude"},
              {"name": "codex", "agent_slug": "codex"}],
        rounds_completed=1,
        max_rounds=3,
        status="awaiting_response",
        findings={"per_cli": {"claude": {"findings": [], "raw_text": "..."}},
                  "last_round": 1},
        agreed_findings=[{"severity": "BLOCKER", "file": "a.py",
                          "line_range": "1", "descriptions": ["x"],
                          "cli_set": ["claude", "codex"]}],
    )
    db = _stub_db_for_review(review)
    monkeypatch.setattr(review_service, "get_review", lambda *_a, **_k: review)

    out = review_service.apply_reply(
        db, review.tenant_id, review.id, updated_ref="#570-rev2",
    )
    assert out.status == "running"
    assert out.ref == "#570-rev2"
    assert out.last_reply_ref == "#570-rev2"
    assert out.agreed_findings == []
    assert out.findings["per_cli"] == {}


def test_apply_reply_on_done_is_idempotent(monkeypatch):
    from app.services import review_service

    review = _FakeReview(status="done", rounds_completed=3, max_rounds=3)
    db = _stub_db_for_review(review)
    monkeypatch.setattr(review_service, "get_review", lambda *_a, **_k: review)

    out = review_service.apply_reply(
        db, review.tenant_id, review.id, updated_ref="ignored",
    )
    assert out.status == "done"
    # Did not advance the ref
    assert out.last_reply_ref is None


def test_record_after_done_is_noop(monkeypatch):
    from app.services import review_service

    review = _FakeReview(status="done")
    db = _stub_db_for_review(review)
    monkeypatch.setattr(review_service, "get_review", lambda *_a, **_k: review)

    out = review_service.record_cli_findings(
        db, review.tenant_id, review.id,
        cli="claude",
        raw_text="- BLOCKER x.py:1 late finding",
    )
    # Same row, untouched
    assert out is review
    assert out.findings == {"per_cli": {}, "last_round": 0}


# ── Schema sanity ─────────────────────────────────────────────────────


def test_review_start_request_dedupes_clis():
    from app.schemas.review import ReviewStartRequest

    req = ReviewStartRequest(
        ref="#123",
        clis=["Claude", "claude", "codex", "  ", "codex"],
    )
    assert req.clis == ["claude", "codex"]


def test_review_start_request_rejects_empty_clis_after_strip():
    from app.schemas.review import ReviewStartRequest

    with pytest.raises(ValueError):
        ReviewStartRequest(ref="#1", clis=["", "   "])


def test_review_start_request_default_max_rounds():
    from app.schemas.review import ReviewStartRequest

    req = ReviewStartRequest(ref="#1")
    assert req.max_rounds == 3
    assert req.scope == "bugs+security"


def test_review_start_request_caps_max_rounds():
    from app.schemas.review import ReviewStartRequest

    with pytest.raises(ValueError):
        ReviewStartRequest(ref="#1", max_rounds=99)
