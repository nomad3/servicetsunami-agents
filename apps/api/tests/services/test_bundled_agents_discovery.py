"""Discovery + invariant tests for the bundled_agents lookup.

This module is the source of truth for slug↔name mapping consumed
by both review_circularity and reviewer_availability. We want a
test that fails CI the moment a new bundled agent ships without a
parseable frontmatter `name:` — that's the failure mode the manual
hardcoded maps had (PR #706 review I1: `devops`, `sre`,
`business-support` were silently missing).

Design: docs/plans/2026-05-24-review-gate-medium-followups-design.md
"""

from __future__ import annotations

from pathlib import Path

from app.services.bundled_agents import (
    BUNDLED_AGENTS_ROOT,
    _bundled_root_abs,
    _slug_to_name,
    all_bundled_slugs,
    name_to_slug,
    slug_to_name,
)


def test_discovers_every_bundled_dir() -> None:
    """Every directory under _bundled/ with a skill.md must be discovered.

    This is the regression guard: a new bundled agent landing without
    a parseable frontmatter `name:` would silently miss both review
    gates. Fail CI instead.
    """
    root = _bundled_root_abs()
    on_disk = {
        d.name for d in root.iterdir()
        if d.is_dir() and (d / "skill.md").is_file()
    }
    discovered = set(all_bundled_slugs())
    assert discovered == on_disk, (
        f"bundled_agents mismatch: on_disk={on_disk}, "
        f"discovered={discovered}. A new _bundled/<slug>/skill.md "
        "either is missing the `name:` frontmatter field or has "
        "frontmatter that the parser can't read."
    )


def test_known_slugs_resolve_to_canonical_names() -> None:
    """Lock the shipped name strings — they're the Agent.name we
    match against in DB queries; renaming silently breaks both
    review gates."""
    assert slug_to_name("code-reviewer") == "Code Reviewer"
    assert slug_to_name("substrate-sentinel") == "Substrate Sentinel"
    assert slug_to_name("luna") == "Luna"


def test_name_to_slug_round_trip() -> None:
    for slug in all_bundled_slugs():
        name = slug_to_name(slug)
        assert name is not None
        assert name_to_slug(name) == slug, f"round-trip broken for {slug}"


def test_name_to_slug_is_case_insensitive() -> None:
    """Operators may type 'code reviewer' or 'Code Reviewer' — both work."""
    assert name_to_slug("code reviewer") == "code-reviewer"
    assert name_to_slug("CODE REVIEWER") == "code-reviewer"
    assert name_to_slug("  Code Reviewer  ") == "code-reviewer"


def test_unknown_slug_returns_none() -> None:
    assert slug_to_name("not-a-real-slug") is None
    assert name_to_slug("Not A Real Agent") is None
    assert name_to_slug("") is None


def test_no_lingering_test_state() -> None:
    """If a future test calls _reset_cache_for_tests it shouldn't
    leak: a fresh look-up still finds the on-disk agents."""
    from app.services.bundled_agents import _reset_cache_for_tests

    _reset_cache_for_tests()
    assert "code-reviewer" in all_bundled_slugs()
