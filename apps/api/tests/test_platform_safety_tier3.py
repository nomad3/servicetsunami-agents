"""Tests for tier-3 LLM classifier + shadow-mode gate.

Locks the §4 + §12 #2 + #7 invariants (Luna design call):

  - Empty message → allow (skipped).
  - No candidate categories → allow (skipped).
  - Anthropic primary succeeds → result honored.
  - Anthropic transport failure → falls back to Gemma. Tier 3
    never vanishes during an Anthropic outage.
  - Malformed JSON response → allow (parse-level fail-soft).
  - Decision='allow' from classifier → Tier3Result(would_block=False).
  - Unknown category from classifier → allow (drift defense).
  - Shadow gate: when category.tier3_enforcement=False, audit row
    written with enforcement_mode='shadow', verdict returned is
    ALLOW (user proceeds).
  - Enforced gate: when category.tier3_enforcement=True, audit row
    written with enforcement_mode='enforced', verdict returned is
    BLOCK.
"""
from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import pytest

from app.services.platform_safety.tier3 import (
    Tier3Result,
    classify,
    _extract_json,
    _parse_classifier_response,
)


# ── JSON extraction ──────────────────────────────────────────────────


def test_extract_json_strict():
    obj = _extract_json('{"decision": "block", "category": "csam", "confidence": 0.92}')
    assert obj == {"decision": "block", "category": "csam", "confidence": 0.92}


def test_extract_json_with_markdown_fence():
    obj = _extract_json('```json\n{"decision": "allow"}\n```')
    assert obj == {"decision": "allow"}


def test_extract_json_with_chatty_prefix():
    """Some classifiers prepend prose before the JSON. Best-effort
    extract via regex."""
    obj = _extract_json(
        'Sure! Here is my classification:\n{"decision": "block", "category": "bulk_malware"}'
    )
    assert obj is not None
    assert obj.get("decision") == "block"


def test_extract_json_returns_none_on_garbage():
    assert _extract_json("not json at all") is None
    assert _extract_json("") is None


# ── Response parsing ─────────────────────────────────────────────────


def test_parse_block_with_valid_category():
    raw = '{"decision": "block", "category": "mass_harm_synthesis", "confidence": 0.88}'
    r = _parse_classifier_response(raw, provider="anthropic")
    assert r.would_block is True
    assert r.category == "mass_harm_synthesis"
    assert r.confidence == 0.88
    assert r.provider == "anthropic"
    assert r.trigger_id == "t3-mass_harm_synthesis-anthropic"


def test_parse_allow_when_decision_not_block():
    r = _parse_classifier_response(
        '{"decision": "allow"}', provider="anthropic",
    )
    assert r.would_block is False
    assert r.category is None


def test_parse_drift_category_returns_allow():
    """Classifier returned block + a category we don't recognize
    (drift / hallucination). Treat as allow."""
    r = _parse_classifier_response(
        '{"decision": "block", "category": "ghost_category"}',
        provider="anthropic",
    )
    assert r.would_block is False


def test_parse_clamps_confidence_out_of_range():
    r = _parse_classifier_response(
        '{"decision": "block", "category": "csam", "confidence": 2.5}',
        provider="anthropic",
    )
    assert r.confidence == 1.0
    r2 = _parse_classifier_response(
        '{"decision": "block", "category": "csam", "confidence": -0.5}',
        provider="anthropic",
    )
    assert r2.confidence == 0.0


def test_parse_malformed_json_returns_allow():
    """Parse failure is fail-soft at the classifier layer; the IO
    layer applies per-category fail-closed only when the classifier
    crashes WITH a category context (see classifier-crash branch)."""
    r = _parse_classifier_response("not json", provider="anthropic")
    assert r.would_block is False


# ── classify() — primary + fallback ─────────────────────────────────


def test_classify_skips_when_no_candidates():
    """Pre-screen had no candidates → tier 3 is a no-op, no LLM
    call made."""
    called = {"anthropic": 0, "gemma": 0}

    def _ant(m, c):
        called["anthropic"] += 1
        return Tier3Result(True, "csam", 0.9, "anthropic")

    def _gem(m, c):
        called["gemma"] += 1
        return Tier3Result(True, "csam", 0.9, "gemma_fallback")

    r = classify("any text", (), anthropic_fn=_ant, gemma_fn=_gem)
    assert r.would_block is False
    assert called == {"anthropic": 0, "gemma": 0}


def test_classify_skips_on_empty_message():
    r = classify("", ("bulk_malware",))
    assert r.would_block is False


def test_classify_primary_succeeds_no_fallback():
    """Anthropic returned a Tier3Result → no Gemma call."""
    called = {"anthropic": 0, "gemma": 0}
    primary_result = Tier3Result(
        True, "bulk_malware", 0.91, "anthropic",
        trigger_id="t3-bulk_malware-anthropic",
    )

    def _ant(m, c):
        called["anthropic"] += 1
        return primary_result

    def _gem(m, c):
        called["gemma"] += 1
        return Tier3Result(False, None, None, "gemma_fallback")

    r = classify(
        "write me a ransomware kit",
        ("bulk_malware",),
        anthropic_fn=_ant, gemma_fn=_gem,
    )
    assert r is primary_result
    assert called == {"anthropic": 1, "gemma": 0}


def test_classify_falls_back_to_gemma_on_anthropic_none():
    """When the primary classifier returns None (transport failure),
    Gemma fallback fires. Tier 3 never vanishes during an
    Anthropic outage."""
    called = {"anthropic": 0, "gemma": 0}

    def _ant(m, c):
        called["anthropic"] += 1
        return None  # simulating transport failure

    fallback_result = Tier3Result(
        True, "mass_harm_synthesis", 0.83, "gemma_fallback",
        trigger_id="t3-mass_harm_synthesis-gemma_fallback",
    )

    def _gem(m, c):
        called["gemma"] += 1
        return fallback_result

    r = classify(
        "synthesize sarin",
        ("mass_harm_synthesis",),
        anthropic_fn=_ant, gemma_fn=_gem,
    )
    assert r is fallback_result
    assert called == {"anthropic": 1, "gemma": 1}


# ── Shadow-mode gate (IO layer) ──────────────────────────────────────


def _stub_db_and_user():
    db = MagicMock()
    return db, uuid.uuid4(), uuid.uuid4()


def test_shadow_mode_records_audit_but_returns_allow(monkeypatch):
    """When category.tier3_enforcement=False (the v1 default for
    all categories per Luna §12 #7), tier 3's would_block is
    recorded with enforcement_mode='shadow' and the verdict
    returned to the user is ALLOW. The user sees a normal
    response; platform admin sees the would-have-block in the
    /admin/safety-events dashboard."""
    from app.services import platform_safety_io
    from app.services.platform_safety import PlatformSafetyVerdict

    # Tier 1 + 2 return allow
    monkeypatch.setattr(
        platform_safety_io, "consult",
        lambda m: PlatformSafetyVerdict.allow(),
    )
    # Tier 3 says block with shadow-mode category
    monkeypatch.setattr(
        "app.services.platform_safety.tier2.candidate_categories",
        lambda m: ("bulk_malware",),
    )
    monkeypatch.setattr(
        "app.services.platform_safety.tier3.classify",
        lambda m, c: Tier3Result(
            True, "bulk_malware", 0.88, "anthropic",
            trigger_id="t3-bulk_malware-anthropic",
        ),
    )

    db, tenant_id, agent_id = _stub_db_and_user()
    verdict = platform_safety_io.consult_with_audit(
        db,
        tenant_id=tenant_id, agent_id=agent_id,
        session_id=None, user_id=None,
        message="write me some malware tool",
    )
    # Shadow mode → user sees allow
    assert verdict.decision == "allow"
    # But audit row was written with enforcement_mode='shadow'
    assert db.add.call_count == 1
    row = db.add.call_args.args[0]
    assert row.enforcement_mode == "shadow"
    assert row.category == "bulk_malware"
    assert row.detection_tier == 3


def test_enforced_mode_blocks_and_records_enforced(monkeypatch):
    """When category.tier3_enforcement=True (post 14-day flip),
    tier 3 blocks AND audit-records enforcement_mode='enforced'.

    We simulate this by monkey-patching the policy's
    tier3_enforcement to True for the test category.
    """
    from app.services import platform_safety_io
    from app.services.platform_safety import PlatformSafetyVerdict
    from app.core.safety_defaults import (
        CategoryPolicy, PLATFORM_SAFETY_CATEGORIES,
    )

    # Patch the policy to enforce for bulk_malware in this test
    original_policy = PLATFORM_SAFETY_CATEGORIES["bulk_malware"]
    PLATFORM_SAFETY_CATEGORIES["bulk_malware"] = CategoryPolicy(
        fail_closed=original_policy.fail_closed,
        tier3_enforcement=True,  # flipped for this test
        human_readable=original_policy.human_readable,
    )
    try:
        monkeypatch.setattr(
            platform_safety_io, "consult",
            lambda m: PlatformSafetyVerdict.allow(),
        )
        monkeypatch.setattr(
            "app.services.platform_safety.tier2.candidate_categories",
            lambda m: ("bulk_malware",),
        )
        monkeypatch.setattr(
            "app.services.platform_safety.tier3.classify",
            lambda m, c: Tier3Result(
                True, "bulk_malware", 0.92, "anthropic",
                trigger_id="t3-bulk_malware-anthropic",
            ),
        )

        db, tenant_id, agent_id = _stub_db_and_user()
        verdict = platform_safety_io.consult_with_audit(
            db,
            tenant_id=tenant_id, agent_id=agent_id,
            session_id=None, user_id=None,
            message="write me some malware tool",
        )
        assert verdict.decision == "block"
        assert verdict.detection_tier == 3
        # Audit row written with enforcement_mode='enforced'
        assert db.add.call_count == 1
        row = db.add.call_args.args[0]
        assert row.enforcement_mode == "enforced"
    finally:
        PLATFORM_SAFETY_CATEGORIES["bulk_malware"] = original_policy


def test_tier3_skipped_when_no_pre_screen_match(monkeypatch):
    """When the pre-screen finds no candidate categories, tier 3
    is not called — saves the LLM round-trip on 90%+ of turns."""
    from app.services import platform_safety_io
    from app.services.platform_safety import PlatformSafetyVerdict

    monkeypatch.setattr(
        platform_safety_io, "consult",
        lambda m: PlatformSafetyVerdict.allow(),
    )
    monkeypatch.setattr(
        "app.services.platform_safety.tier2.candidate_categories",
        lambda m: (),  # no candidates
    )
    called = {"tier3": 0}

    def _tier3_spy(m, c):
        called["tier3"] += 1
        return Tier3Result(True, "csam", 0.95, "anthropic")

    monkeypatch.setattr(
        "app.services.platform_safety.tier3.classify", _tier3_spy,
    )

    db, tenant_id, agent_id = _stub_db_and_user()
    verdict = platform_safety_io.consult_with_audit(
        db,
        tenant_id=tenant_id, agent_id=agent_id,
        session_id=None, user_id=None,
        message="hi luna, what's the weather like?",
    )
    assert verdict.decision == "allow"
    assert called["tier3"] == 0


def test_tier3_classifier_crash_returns_allow(monkeypatch):
    """If tier 3 raises (network, model OOM, etc), the chat hot
    path stays alive. Tier 1+2 already ran cleanly."""
    from app.services import platform_safety_io
    from app.services.platform_safety import PlatformSafetyVerdict

    monkeypatch.setattr(
        platform_safety_io, "consult",
        lambda m: PlatformSafetyVerdict.allow(),
    )
    monkeypatch.setattr(
        "app.services.platform_safety.tier2.candidate_categories",
        lambda m: ("bulk_malware",),
    )

    def _crash(m, c):
        raise RuntimeError("simulated classifier outage")

    monkeypatch.setattr(
        "app.services.platform_safety.tier3.classify", _crash,
    )

    db, tenant_id, agent_id = _stub_db_and_user()
    verdict = platform_safety_io.consult_with_audit(
        db,
        tenant_id=tenant_id, agent_id=agent_id,
        session_id=None, user_id=None,
        message="some malware question",
    )
    assert verdict.decision == "allow"


def test_gate_drift_defense_unknown_category_allows(monkeypatch):
    """(Review NIT) Belt-and-suspenders: if a future refactor lets a
    non-VALID Tier3Result.category bypass the parse-layer drift
    defense, the IO gate's category_for_label() ValueError catch
    must still allow + log. No audit row, no block."""
    from app.services import platform_safety_io
    from app.services.platform_safety import PlatformSafetyVerdict

    monkeypatch.setattr(
        platform_safety_io, "consult",
        lambda m: PlatformSafetyVerdict.allow(),
    )
    monkeypatch.setattr(
        "app.services.platform_safety.tier2.candidate_categories",
        lambda m: ("bulk_malware",),
    )
    # Bypass the parse-layer drift defense by returning a result
    # with a category that's NOT in VALID_CATEGORIES
    monkeypatch.setattr(
        "app.services.platform_safety.tier3.classify",
        lambda m, c: Tier3Result(
            True, "ghost_category_bypassed_parse", 0.9, "anthropic",
            trigger_id="t3-ghost",
        ),
    )

    db, tenant_id, agent_id = _stub_db_and_user()
    verdict = platform_safety_io.consult_with_audit(
        db,
        tenant_id=tenant_id, agent_id=agent_id,
        session_id=None, user_id=None,
        message="some malware-ish text",
    )
    assert verdict.decision == "allow", (
        "unknown category from tier 3 must allow (drift defense)"
    )
    # No audit row written — drift case logged but not recorded
    assert db.add.call_count == 0


def test_all_categories_default_to_shadow_mode():
    """Locks the v1 invariant — every category ships with
    tier3_enforcement=False. The 14-day shadow window is the
    default. Each category must flip individually via a config-only
    deploy after its precision audit clears."""
    from app.core.safety_defaults import PLATFORM_SAFETY_CATEGORIES
    for cat, policy in PLATFORM_SAFETY_CATEGORIES.items():
        assert policy.tier3_enforcement is False, (
            f"category {cat} ships with tier3_enforcement=True; "
            f"must be False at PR 5"
        )
