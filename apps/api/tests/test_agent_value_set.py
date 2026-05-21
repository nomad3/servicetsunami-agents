"""Unit tests for the pure value-layer match engine (PR 1 of #647).

The module under test is intentionally pure: no DB, no IO, no
fixtures needed. Every test parametrizes (action × value_set ×
intent × enabled) and locks the verdict shape.

Locked properties from the design doc §6 + §7:
  - Empty value set never blocks anything.
  - kill-switch OFF makes every call return allow/kill_switch_off.
  - protect + intent='mutate' → block.
  - protect + intent='read' → warn (mention/read is fine).
  - avoid + any intent → warn.
  - pursue + any intent → allow with matched_item populated.
  - Identical (action, value_set, intent, enabled) → identical
    verdict regardless of consultation_point.
  - Unknown point or intent → ValueError.
"""
from __future__ import annotations

import pytest

from app.services.agent_value_set import (
    AgentValueSet,
    ValueItem,
    ValueVerdict,
    consult,
)


def _item(slug: str, *, added_by: str = "operator") -> ValueItem:
    return ValueItem(
        slug=slug,
        description=f"Protect {slug}",
        added_at="2026-05-21T00:00:00+00:00",
        added_by=added_by,
    )


def _vs(*, protect=(), pursue=(), avoid=()) -> AgentValueSet:
    return AgentValueSet(
        protect=[_item(s) for s in protect],
        pursue=[_item(s) for s in pursue],
        avoid=[_item(s) for s in avoid],
    )


# ── Empty / kill-switch invariants ────────────────────────────────────


@pytest.mark.parametrize("point", [
    "routing", "tool", "reflection", "user_signal", "synthesis",
])
@pytest.mark.parametrize("intent", ["read", "mutate"])
def test_empty_value_set_always_allows(point, intent):
    v = consult(
        {"text": "anything goes"},
        AgentValueSet.empty(),
        point=point, intent=intent, enabled=True,
    )
    assert v.decision == "allow"
    assert v.reason == "empty_value_set"
    assert v.matched_item is None
    assert v.consultation_point == point


@pytest.mark.parametrize("point", [
    "routing", "tool", "reflection", "user_signal", "synthesis",
])
@pytest.mark.parametrize("intent", ["read", "mutate"])
def test_kill_switch_off_always_allows(point, intent):
    """Locked: enabled=False makes EVERY consult return allow/kill_switch_off,
    even when a protect item would otherwise block."""
    vs = _vs(protect=["production-main"])
    v = consult(
        {"text": "merge into production-main"},
        vs,
        point=point, intent=intent, enabled=False,
    )
    assert v.decision == "allow"
    assert v.reason == "kill_switch_off"
    assert v.matched_item is None


# ── protect matching ──────────────────────────────────────────────────


def test_protect_mutate_blocks():
    vs = _vs(protect=["production-main"])
    v = consult(
        {"text": "push to production-main"},
        vs,
        point="tool", intent="mutate", enabled=True,
    )
    assert v.decision == "block"
    assert "protect_match" in v.reason
    assert v.matched_item is not None
    assert v.matched_item["slug"] == "production-main"


def test_protect_read_warns_not_blocks():
    """Mentioning a protected item in a read context must NOT block —
    otherwise Luna deadlocks around the very things she's safeguarding.
    Locked by §6 round-1 Luna correction."""
    vs = _vs(protect=["production-main"])
    v = consult(
        {"text": "show me the production-main commit history"},
        vs,
        point="routing", intent="read", enabled=True,
    )
    assert v.decision == "warn"
    assert "read_only" in v.reason
    assert v.matched_item is not None


def test_protect_no_match_passes():
    vs = _vs(protect=["production-main"])
    v = consult(
        {"text": "list the staging deploys"},
        vs,
        point="routing", intent="mutate", enabled=True,
    )
    assert v.decision == "allow"
    assert v.reason == "no_match"


# ── avoid matching ────────────────────────────────────────────────────


@pytest.mark.parametrize("intent", ["read", "mutate"])
def test_avoid_warns_regardless_of_intent(intent):
    """Phase 1 design: avoid is warn-only at any intent. Hard-blocking
    creates operator fatigue (Q4 round-1)."""
    vs = _vs(avoid=["merge-without-review"])
    v = consult(
        {"text": "merge-without-review the hotfix"},
        vs,
        point="tool", intent=intent, enabled=True,
    )
    assert v.decision == "warn"
    assert "avoid_match" in v.reason
    assert v.matched_item is not None


# ── pursue matching ───────────────────────────────────────────────────


def test_pursue_match_allows_with_item_set():
    """pursue produces ALLOW (no block) but surfaces the matched_item
    so the caller can scale affect delta."""
    vs = _vs(pursue=["morning-report"])
    v = consult(
        {"text": "draft the morning-report for Simon"},
        vs,
        point="user_signal", intent="read", enabled=True,
    )
    assert v.decision == "allow"
    assert "pursue_match" in v.reason
    assert v.matched_item is not None
    assert v.matched_item["slug"] == "morning-report"


# ── Priority: protect > avoid > pursue ────────────────────────────────


def test_protect_priority_over_avoid():
    """When the same text matches both protect and avoid items, protect
    wins. Otherwise an avoid (warn) would mask a protect (block)."""
    vs = _vs(
        protect=["production-main"],
        avoid=["production-main"],
    )
    v = consult(
        {"text": "deploy to production-main"},
        vs,
        point="tool", intent="mutate", enabled=True,
    )
    assert v.decision == "block"


def test_avoid_priority_over_pursue():
    vs = _vs(
        avoid=["hotfix"],
        pursue=["hotfix"],
    )
    v = consult(
        {"text": "ship the hotfix"},
        vs,
        point="routing", intent="mutate", enabled=True,
    )
    assert v.decision == "warn"
    assert "avoid_match" in v.reason


# ── Action shape walker ───────────────────────────────────────────────


def test_extract_search_text_finds_nested_strings():
    """The action dict varies by point — tool passes
    {tool: name, args: {…}}. The walker must find slug-matchable
    text in nested args, not just top-level fields."""
    vs = _vs(protect=["users-table"])
    v = consult(
        {
            "tool": "sql_exec",
            "args": {"sql": "DELETE FROM users-table WHERE id=1"},
        },
        vs,
        point="tool", intent="mutate", enabled=True,
    )
    assert v.decision == "block"


def test_extract_handles_lists_in_args():
    vs = _vs(protect=["production-main"])
    v = consult(
        {"tool": "git", "args": {"refs": ["staging", "production-main"]}},
        vs,
        point="tool", intent="mutate", enabled=True,
    )
    assert v.decision == "block"


def test_extract_caps_walk_depth():
    """Defensive: an adversarially deep dict shouldn't blow the
    walker. Cap is _WALK_MAX_DEPTH=4 (Review I2 fix from review
    feedback — old depth=2 was too tight for documented tool action
    shape with nested args). Strings at depth > 4 are ignored."""
    from app.services.agent_value_set import _WALK_MAX_DEPTH
    # Build a chain: root dict (d0) → dict (d1) → dict (d2) → dict
    # (d3) → dict (d4) → dict (d5) → string. depth=5 exceeds 4 → skip.
    deep = {"a": {"b": {"c": {"d": {"e": {"f": "production-main"}}}}}}
    vs = _vs(protect=["production-main"])
    v = consult(deep, vs, point="tool", intent="mutate", enabled=True)
    assert v.decision == "allow"
    # Verify cap value matches what the docstring promises.
    assert _WALK_MAX_DEPTH == 4


def test_walker_reaches_strings_inside_tool_args_list():
    """The realistic deepest action shape — tool args with a nested
    list — must reach the strings. (Review I2 verification.) Walk
    path: root dict (d0) → args dict (d1) → list (d2) → string (d3).
    Depth 3 ≤ 4 → captured. With the old depth=2 cap this was a
    bug."""
    from app.services.agent_value_set import _extract_search_text
    action = {
        "tool": "git_push",
        "args": {"refs": ["staging", "production-main"]},
    }
    text = _extract_search_text(action)
    assert "production-main" in text
    assert "staging" in text
    assert "git_push" in text


# ── Consultation-point-agnostic ───────────────────────────────────────


@pytest.mark.parametrize("point", [
    "routing", "tool", "reflection", "user_signal", "synthesis",
])
def test_identical_inputs_produce_identical_verdict_across_points(point):
    """Locked: the consultation_point label is carried but doesn't
    affect the decision. Otherwise the consolidation in one match
    engine is a lie."""
    vs = _vs(protect=["production-main"])
    action = {"text": "deploy to production-main"}
    v = consult(action, vs, point=point, intent="mutate", enabled=True)
    assert v.decision == "block"
    assert v.consultation_point == point  # only the label varies


# ── Input validation ──────────────────────────────────────────────────


def test_unknown_point_raises():
    with pytest.raises(ValueError, match="unknown consultation_point"):
        consult({}, AgentValueSet.empty(),
                point="not_a_point", intent="read", enabled=True)


def test_unknown_intent_raises():
    with pytest.raises(ValueError, match="unknown intent"):
        consult({}, AgentValueSet.empty(),
                point="routing", intent="vaporize", enabled=True)


# ── Slug normalization ────────────────────────────────────────────────


def test_slug_case_insensitive_match():
    """Slugs lowercase on construction; search text lowercases on
    extraction. Operators writing 'Production-Main' as a slug
    still match 'production-main' in chat. (Luna round-6 confirmed
    direct-construction path needed __post_init__ to normalize.)"""
    item = ValueItem(
        slug="Production-Main",  # mixed case input
        description="prod main",
        added_at="x",
        added_by="operator",
    )
    assert item.slug == "production-main"  # normalized

    vs = AgentValueSet(protect=[item])
    v = consult(
        {"text": "TOUCH Production-Main"},
        vs,
        point="tool", intent="mutate", enabled=True,
    )
    assert v.decision == "block"


def test_value_item_post_init_normalizes_whitespace_and_case():
    """Direct ValueItem construction trims whitespace + lowercases.
    Locked: an operator-API write that goes through from_dict AND
    a unit-test fixture that constructs directly must agree on
    the canonical slug shape."""
    item = ValueItem(
        slug="  Production-Main  \n",  # whitespace + case
        description="prod",
        added_at="x",
        added_by="operator",
    )
    assert item.slug == "production-main"


# ── Round-trip serialization ──────────────────────────────────────────


def test_value_set_round_trips_through_dict():
    vs = _vs(protect=["a"], pursue=["b"], avoid=["c"])
    restored = AgentValueSet.from_dict(vs.to_dict())
    assert restored.protect[0].slug == "a"
    assert restored.pursue[0].slug == "b"
    assert restored.avoid[0].slug == "c"
    assert restored.version == vs.version


def test_value_verdict_round_trips_through_dict():
    vs = _vs(protect=["prod"])
    v = consult(
        {"text": "deploy prod"}, vs,
        point="tool", intent="mutate", enabled=True,
    )
    d = v.to_dict()
    assert d["decision"] == "block"
    assert d["matched_item"]["slug"] == "prod"
    assert d["consultation_point"] == "tool"
