"""Tests for user_signal_classifier.

The heuristic backend is deterministic and always available, so we
exercise it directly. The Ollama backend gets a thin async test that
mocks ``local_inference.generate`` — we don't need a live Ollama in
CI.

Locked behavior to preserve:
- Output in [-1, 1]^3, validated by the dataclass __post_init__.
- Heuristic fallback fires whenever the LLM produces bad JSON or
  out-of-range numbers — this is the constitutive-vs-performative
  safety net described in the module docstring.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.services import user_signal_classifier as usc


# ── PADClassifierResult shape ─────────────────────────────────────────


def test_pad_result_accepts_in_range():
    r = usc.PADClassifierResult(0.5, -0.2, 0.0)
    assert r.pleasure == 0.5
    assert r.arousal == -0.2
    assert r.dominance == 0.0


@pytest.mark.parametrize("axis,value", [
    ("pleasure", 1.5), ("pleasure", -1.5),
    ("arousal", 1.5), ("arousal", -1.5),
    ("dominance", 1.5), ("dominance", -1.5),
])
def test_pad_result_rejects_out_of_range(axis, value):
    kwargs = {"pleasure": 0.0, "arousal": 0.0, "dominance": 0.0}
    kwargs[axis] = value
    with pytest.raises(ValueError):
        usc.PADClassifierResult(**kwargs)


# ── Heuristic backend ─────────────────────────────────────────────────


def test_heuristic_empty_text_is_neutral():
    r = usc.classify_heuristic("")
    assert (r.pleasure, r.arousal, r.dominance) == (0.0, 0.0, 0.0)


def test_heuristic_thanks_is_positive_low_arousal():
    r = usc.classify_heuristic("thanks that worked perfectly")
    assert r.pleasure > 0.0
    assert r.arousal <= 0.2  # No urgency cues


def test_heuristic_frustration_is_negative_high_arousal():
    """User stuck + escalating — pleasure ↓, arousal ↑."""
    r = usc.classify_heuristic("ugh it's still not working!!")
    assert r.pleasure < 0.0
    assert r.arousal > 0.2


def test_heuristic_command_is_dominant():
    r = usc.classify_heuristic("deploy the latest changes")
    assert r.dominance > 0.2


def test_heuristic_hedging_question_is_submissive():
    r = usc.classify_heuristic("could you maybe try again?")
    assert r.dominance < 0.0


def test_heuristic_all_caps_raises_arousal():
    r = usc.classify_heuristic("WHY IS THIS BROKEN")
    assert r.arousal > 0.0


def test_heuristic_clamps_to_bounds():
    """Even with stacked negative signals, output never escapes [-1, 1]."""
    abuse = (
        "ugh broken broken broken fail fail error error error wtf wtf "
        "this is garbage useless terrible awful!!!!!"
    )
    r = usc.classify_heuristic(abuse)
    assert -1.0 <= r.pleasure <= 1.0
    assert -1.0 <= r.arousal <= 1.0
    assert -1.0 <= r.dominance <= 1.0


# ── Ollama backend (mocked) ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_ollama_parses_well_formed_response():
    fake_json = '{"pleasure": 0.4, "arousal": -0.1, "dominance": 0.6}'
    with patch.object(
        usc, "classify_ollama", wraps=usc.classify_ollama
    ), patch(
        "app.services.local_inference.generate",
        new=AsyncMock(return_value=fake_json),
    ):
        r = await usc.classify_ollama("show me the deploys")
    assert r.pleasure == pytest.approx(0.4)
    assert r.arousal == pytest.approx(-0.1)
    assert r.dominance == pytest.approx(0.6)


@pytest.mark.asyncio
async def test_ollama_strips_markdown_fences():
    """Gemma sometimes wraps JSON in ```json fences — must strip."""
    fenced = "```json\n{\"pleasure\": 0.2, \"arousal\": 0.1, \"dominance\": 0.0}\n```"
    with patch(
        "app.services.local_inference.generate",
        new=AsyncMock(return_value=fenced),
    ):
        r = await usc.classify_ollama("ok")
    assert r.pleasure == pytest.approx(0.2)


@pytest.mark.asyncio
async def test_ollama_malformed_json_falls_back_to_heuristic():
    """If the LLM emits prose instead of JSON, the safety-net heuristic
    fires. The classifier never raises to the caller."""
    with patch(
        "app.services.local_inference.generate",
        new=AsyncMock(return_value="I think the user seems happy"),
    ):
        r = await usc.classify_ollama("thanks!")
    # Heuristic for 'thanks!' is positive — confirms fallback fired.
    assert r.pleasure > 0.0


@pytest.mark.asyncio
async def test_ollama_out_of_range_clamps_then_returns():
    """An adversarial LLM emits a >1 value — we clamp to bounds and
    still return a valid result rather than raise."""
    fake_json = '{"pleasure": 5.0, "arousal": -10.0, "dominance": 0.5}'
    with patch(
        "app.services.local_inference.generate",
        new=AsyncMock(return_value=fake_json),
    ):
        r = await usc.classify_ollama("test")
    assert r.pleasure == 1.0
    assert r.arousal == -1.0
    assert r.dominance == 0.5


@pytest.mark.asyncio
async def test_ollama_none_response_falls_back():
    """local_inference.generate returns None on timeout / 5xx —
    classifier must produce a heuristic estimate, not crash."""
    with patch(
        "app.services.local_inference.generate",
        new=AsyncMock(return_value=None),
    ):
        r = await usc.classify_ollama("thanks great")
    # Heuristic for positive text is positive.
    assert r.pleasure > 0.0
