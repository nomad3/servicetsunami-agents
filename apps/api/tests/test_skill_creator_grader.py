"""Unit tests for the skill-creator grader.

Covers:
  * Happy path with a mocked LLM response producing valid JSON verdicts.
  * Malformed input expectations get dropped rather than 500'ing.
  * Multi-expectation aggregation: partial passes produce the right
    fractional score; missing verdicts default to ``passed=False``.
  * Wholesale grader outage raises ``GraderError`` (caller turns it into
    a 503 — not a misleading 0% pass).
  * ``_resolve_grader_model`` honors the tenant's ``default_cli_platform``
    and falls back to opencode otherwise.
"""

import os
os.environ["TESTING"] = "True"

import json
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest

from app.services.skill_creator import grader
from app.services.skill_creator.grader import (
    Expectation,
    GraderError,
    GradingResult,
    _extract_json,
    _resolve_grader_model,
    _summarize_outputs,
    _validate_expectations,
    grade,
)


# ──────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────


def _verdicts_json(*entries):
    return json.dumps({"expectations": list(entries)})


# ──────────────────────────────────────────────────────────────────────────
# _extract_json
# ──────────────────────────────────────────────────────────────────────────


def test_extract_json_raw():
    assert _extract_json('{"a": 1}') == {"a": 1}


def test_extract_json_fenced():
    text = "Here you go:\n```json\n{\"a\": 1}\n```\nthanks"
    assert _extract_json(text) == {"a": 1}


def test_extract_json_with_preamble():
    text = "Sure! {\"a\": 1, \"b\": [2, 3]} done."
    assert _extract_json(text) == {"a": 1, "b": [2, 3]}


def test_extract_json_returns_none_for_garbage():
    assert _extract_json("nothing here") is None
    assert _extract_json("") is None
    assert _extract_json(None) is None


# ──────────────────────────────────────────────────────────────────────────
# _validate_expectations — malformed handling
# ──────────────────────────────────────────────────────────────────────────


def test_validate_expectations_drops_non_dict():
    raw = ["not a dict", 42, {"id": "e1", "description": "ok"}]
    cleaned = _validate_expectations(raw)
    assert len(cleaned) == 1
    assert cleaned[0].id == "e1"


def test_validate_expectations_drops_missing_required_fields():
    raw = [
        {"id": "e1"},  # missing description
        {"description": "no id"},  # missing id
        {"id": "e2", "description": "valid"},
    ]
    cleaned = _validate_expectations(raw)
    assert [e.id for e in cleaned] == ["e2"]


def test_validate_expectations_accepts_already_typed():
    e = Expectation(id="e1", description="ok")
    cleaned = _validate_expectations([e])
    assert cleaned == [e]


def test_validate_expectations_defaults_kind_to_assertion():
    cleaned = _validate_expectations([{"id": "e1", "description": "ok"}])
    assert cleaned[0].kind == "assertion"


# ──────────────────────────────────────────────────────────────────────────
# _summarize_outputs
# ──────────────────────────────────────────────────────────────────────────


def test_summarize_outputs_none():
    assert "no outputs" in _summarize_outputs(None).lower()


def test_summarize_outputs_missing(tmp_path):
    assert "not found" in _summarize_outputs(tmp_path / "nope").lower()


def test_summarize_outputs_empty(tmp_path):
    assert "empty" in _summarize_outputs(tmp_path).lower()


def test_summarize_outputs_lists_files(tmp_path):
    (tmp_path / "a.json").write_text('{"k": 1}')
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "b.txt").write_text("hello")
    out = _summarize_outputs(tmp_path)
    assert "a.json" in out
    assert "sub/b.txt" in out
    assert "bytes" in out


# ──────────────────────────────────────────────────────────────────────────
# _resolve_grader_model
# ──────────────────────────────────────────────────────────────────────────


class _FakeFeatures:
    def __init__(self, platform):
        self.default_cli_platform = platform


class _FakeDB:
    """Minimal stand-in for sqlalchemy.Session for the tenant_features query."""

    def __init__(self, platform):
        self._platform = platform

    def query(self, _model):
        return self

    def filter(self, *_args, **_kw):
        return self

    def first(self):
        if self._platform is None:
            return None
        return _FakeFeatures(self._platform)


def test_resolve_model_uses_tenant_default():
    db = _FakeDB("claude_code")
    assert _resolve_grader_model(db, uuid.uuid4()) == "claude-3-5-sonnet-latest"


def test_resolve_model_falls_back_to_opencode():
    db = _FakeDB(None)
    assert _resolve_grader_model(db, uuid.uuid4()) == "gemma3:4b"


def test_resolve_model_unknown_platform_falls_back():
    db = _FakeDB("imaginary_cli")
    assert _resolve_grader_model(db, uuid.uuid4()) == "gemma3:4b"


def test_resolve_model_tolerates_db_error():
    class _BoomDB:
        def query(self, _model):
            raise RuntimeError("db down")

    # Falls back to opencode rather than raising — chat must keep working.
    assert _resolve_grader_model(_BoomDB(), uuid.uuid4()) == "gemma3:4b"


# ──────────────────────────────────────────────────────────────────────────
# grade() — happy path + aggregation
# ──────────────────────────────────────────────────────────────────────────


def test_grade_happy_path_all_pass():
    expectations = [
        {"id": "e1", "description": "Has greeting"},
        {"id": "e2", "description": "Mentions weather"},
    ]
    fake = _verdicts_json(
        {"id": "e1", "passed": True, "reasoning": "Says hello."},
        {"id": "e2", "passed": True, "reasoning": "Says rainy."},
    )

    with patch.object(grader, "local_inference", create=True) as li:
        # local_inference is imported inside grade() — patch the module
        # attribute directly via the imported name.
        pass

    with patch("app.services.local_inference.generate_sync", return_value=fake):
        result = grade(
            transcript="Hello! It is rainy today.",
            outputs_dir=None,
            expectations=expectations,
            tenant_id=uuid.uuid4(),
            session_id=uuid.uuid4(),
            eval_id="ev-001",
            run_id=str(uuid.uuid4()),
        )

    assert isinstance(result, GradingResult)
    assert result.passed is True
    assert result.score == 1.0
    assert len(result.expectations) == 2
    assert all(e.passed for e in result.expectations)
    assert result.eval_id == "ev-001"


def test_grade_partial_pass_produces_fractional_score():
    expectations = [
        {"id": "e1", "description": "Has greeting"},
        {"id": "e2", "description": "Mentions weather"},
        {"id": "e3", "description": "Includes a question"},
    ]
    fake = _verdicts_json(
        {"id": "e1", "passed": True, "reasoning": "Says hello."},
        {"id": "e2", "passed": False, "reasoning": "No weather reference."},
        {"id": "e3", "passed": True, "reasoning": "Asks a follow-up."},
    )

    with patch("app.services.local_inference.generate_sync", return_value=fake):
        result = grade(
            transcript="Hello! How are you?",
            outputs_dir=None,
            expectations=expectations,
            tenant_id=uuid.uuid4(),
            session_id=uuid.uuid4(),
        )

    assert result.passed is False
    assert result.score == round(2 / 3, 4)
    passed_ids = {e.id for e in result.expectations if e.passed}
    assert passed_ids == {"e1", "e3"}


def test_grade_missing_verdict_marks_failed():
    expectations = [
        {"id": "e1", "description": "Has greeting"},
        {"id": "e2", "description": "Mentions weather"},  # no verdict returned
    ]
    fake = _verdicts_json(
        {"id": "e1", "passed": True, "reasoning": "Says hello."},
    )

    with patch("app.services.local_inference.generate_sync", return_value=fake):
        result = grade(
            transcript="Hello!",
            outputs_dir=None,
            expectations=expectations,
            tenant_id=uuid.uuid4(),
            session_id=uuid.uuid4(),
        )

    by_id = {e.id: e for e in result.expectations}
    assert by_id["e1"].passed is True
    assert by_id["e2"].passed is False
    assert "did not return a verdict" in by_id["e2"].reasoning.lower()


def test_grade_malformed_expectations_dropped_then_graded():
    expectations = [
        "garbage",
        {"id": "e1", "description": "Has greeting"},
        {"id": "e2"},  # missing description — dropped
    ]
    fake = _verdicts_json(
        {"id": "e1", "passed": True, "reasoning": "Says hello."},
    )

    with patch("app.services.local_inference.generate_sync", return_value=fake):
        result = grade(
            transcript="Hello!",
            outputs_dir=None,
            expectations=expectations,
            tenant_id=uuid.uuid4(),
            session_id=uuid.uuid4(),
        )

    # Only e1 survived validation; both verdicts respected.
    assert [e.id for e in result.expectations] == ["e1"]
    assert result.score == 1.0


def test_grade_no_usable_expectations_returns_empty_result():
    result = grade(
        transcript="Hello!",
        outputs_dir=None,
        expectations=["nope", {"id": "x"}],
        tenant_id=uuid.uuid4(),
        session_id=uuid.uuid4(),
    )
    assert result.expectations == []
    assert result.score == 0.0
    assert result.passed is False


def test_grade_grader_outage_raises():
    expectations = [{"id": "e1", "description": "Has greeting"}]

    with patch("app.services.local_inference.generate_sync", return_value=None):
        with pytest.raises(GraderError):
            grade(
                transcript="Hello!",
                outputs_dir=None,
                expectations=expectations,
                tenant_id=uuid.uuid4(),
                session_id=uuid.uuid4(),
            )


def test_grade_grader_returns_garbage_raises():
    expectations = [{"id": "e1", "description": "Has greeting"}]

    with patch("app.services.local_inference.generate_sync", return_value="not json"):
        with pytest.raises(GraderError):
            grade(
                transcript="Hello!",
                outputs_dir=None,
                expectations=expectations,
                tenant_id=uuid.uuid4(),
                session_id=uuid.uuid4(),
            )


def test_grade_records_tenant_default_model_label():
    expectations = [{"id": "e1", "description": "Has greeting"}]
    fake = _verdicts_json(
        {"id": "e1", "passed": True, "reasoning": "Says hello."},
    )

    db = _FakeDB("claude_code")
    with patch("app.services.local_inference.generate_sync", return_value=fake):
        result = grade(
            transcript="Hello!",
            outputs_dir=None,
            expectations=expectations,
            tenant_id=uuid.uuid4(),
            session_id=uuid.uuid4(),
            db=db,
        )

    assert result.grader_model == "claude-3-5-sonnet-latest"


def test_grade_outputs_dir_included_in_summary(tmp_path):
    (tmp_path / "out.json").write_text('{"k": 1}')
    expectations = [{"id": "e1", "description": "Includes out.json"}]
    fake = _verdicts_json(
        {"id": "e1", "passed": True, "reasoning": "out.json is present."},
    )

    captured = {}

    def _capture(prompt, **kw):
        captured["prompt"] = prompt
        return fake

    with patch("app.services.local_inference.generate_sync", side_effect=_capture):
        grade(
            transcript="generated out.json",
            outputs_dir=tmp_path,
            expectations=expectations,
            tenant_id=uuid.uuid4(),
            session_id=uuid.uuid4(),
        )

    assert "out.json" in captured["prompt"]


def test_grading_result_round_trips_to_dict():
    expectations = [{"id": "e1", "description": "ok"}]
    fake = _verdicts_json(
        {"id": "e1", "passed": True, "reasoning": "yep"},
    )
    with patch("app.services.local_inference.generate_sync", return_value=fake):
        result = grade(
            transcript="hi",
            outputs_dir=None,
            expectations=expectations,
            tenant_id=uuid.uuid4(),
            session_id=uuid.uuid4(),
            eval_id="ev",
            run_id="rn",
        )

    dumped = result.model_dump()
    assert dumped["version"] == 1
    assert dumped["eval_id"] == "ev"
    assert dumped["run_id"] == "rn"
    assert dumped["passed"] is True
    assert dumped["expectations"][0]["passed"] is True
    # graded_at is RFC 3339 UTC
    assert dumped["graded_at"].endswith("Z")
