"""Tier-1 #1 of the latency reduction plan: greeting fast-path.

Bench v4 measured "hola luna" at 21 s, 100% Gemma 4 inference. The
agent's persona forces proactive memory recall every turn, even on
trivial greetings. ``_greeting_template`` short-circuits that.

These tests lock the ES/EN heuristic + the qualification rules.
"""
import os
os.environ.setdefault("TESTING", "True")

from app.services.agent_router import _greeting_template


GR = {"name": "greeting or small talk", "tier": "light", "tools": [], "mutation": False}
CAL = {"name": "check calendar or schedule", "tier": "light", "tools": ["calendar"], "mutation": False}


def test_template_fires_on_short_spanish_greeting():
    out = _greeting_template(GR, "hola luna", "luna")
    assert out is not None
    assert out.startswith("¡Hola!")
    assert "Luna" in out


def test_template_fires_on_short_english_greeting():
    out = _greeting_template(GR, "hi", "luna")
    assert out is not None
    assert out.startswith("Hi!")
    assert "Luna" in out


def test_template_uses_agent_slug_friendly_name():
    out = _greeting_template(GR, "hola", "aremko_receptionist")
    assert out is not None
    assert "Aremko Receptionist" in out


def test_template_skips_when_intent_is_not_greeting():
    assert _greeting_template(CAL, "hola", "luna") is None


def test_template_keyword_fallback_when_intent_missing():
    """Intent classifier has a cold-start race (plan §A.3); without this
    fallback the fast-path is 0% effective for ~60 s after every deploy.
    """
    out = _greeting_template(None, "hola", "luna")
    assert out is not None
    assert out.startswith("¡Hola!")


def test_template_keyword_fallback_skips_non_greeting():
    """Intent missing AND not a known greeting → don't fire."""
    assert _greeting_template(None, "tengo una pregunta", "luna") is None
    assert _greeting_template(None, "que pasa con mi reserva", "luna") is None


def test_template_skips_on_question_mark():
    """A user asking 'hola, qué tal?' wants something more than a templated reply."""
    assert _greeting_template(GR, "hola, qué tal?", "luna") is None
    assert _greeting_template(GR, "¿hola?", "luna") is None


def test_template_skips_on_long_message():
    """If the message is too long it likely contains a real ask we shouldn't drop."""
    long = "hola luna, cómo estás hoy con todo lo que tenemos pendiente"
    assert _greeting_template(GR, long, "luna") is None


def test_template_skips_on_empty_message():
    assert _greeting_template(GR, "", "luna") is None
    assert _greeting_template(GR, "   ", "luna") is None


def test_template_handles_buenos_dias():
    out = _greeting_template(GR, "buenos días", "luna")
    assert out is not None
    assert out.startswith("¡Hola!")  # ES branch
