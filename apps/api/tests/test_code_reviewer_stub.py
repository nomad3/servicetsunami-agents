"""Smoke tests for the deterministic Code Reviewer stub fixture (T6.1).

These tests pin the verdict-routing contract the rest of the Luna
Learn test suite (T6.2 / T6.4b / T6.4c) relies on. The stub is the
hermetic stand-in for the real reviewer agent dispatched by
``apps/mcp-server/src/mcp_tools/learning.py::dispatch_skill_review``;
if its pattern matching drifts, every higher-level integration test
that wires it in starts producing surprising verdicts.

We assert against the public callable, the fixture wiring, AND the
exposed reviewer agent id constant so a rename in any of those
surfaces breaks here first.
"""
from __future__ import annotations

import os

os.environ.setdefault("TESTING", "True")


# ── Direct callable contract ────────────────────────────────────────────


def test_clean_draft_is_approved():
    """A draft with no risky markers approves with empty findings."""
    from fixtures.code_reviewer_stub import reviewer_stub

    skill = (
        "---\nname: ok-skill\nengine: markdown\n---\n"
        "## Description\nThis is a perfectly fine skill body.\n"
    )
    verdict = reviewer_stub(skill)
    assert verdict["verdict"] == "approved"
    assert verdict["findings"] == []
    # The reviewer id is the deterministic stub id — never the real
    # tenant-seeded reviewer UUID.
    assert verdict["reviewer_agent_id"].endswith("beef")


def test_todo_marker_routes_to_revise():
    """Any literal `TODO` in the body should trigger the revise loop."""
    from fixtures.code_reviewer_stub import reviewer_stub

    skill = (
        "---\nname: half-built\nengine: markdown\n---\n"
        "## Step 1\nDo the thing.\n## Step 2\nTODO finish this.\n"
    )
    verdict = reviewer_stub(skill)
    assert verdict["verdict"] == "revise"
    assert verdict["findings"], "revise must surface at least one finding"
    assert "TODO" in verdict["findings"][0]


def test_rm_rf_routes_to_rejected():
    """`rm -rf` in the body trips the hard-reject branch."""
    from fixtures.code_reviewer_stub import reviewer_stub

    skill = (
        "---\nname: danger\nengine: python\n---\n"
        "## Body\nrun `rm -rf /tmp/state` before each invocation\n"
    )
    verdict = reviewer_stub(skill)
    assert verdict["verdict"] == "rejected"
    assert any("rm -rf" in f for f in verdict["findings"])


def test_subprocess_routes_to_rejected():
    """`subprocess` token in the body trips the hard-reject branch too."""
    from fixtures.code_reviewer_stub import reviewer_stub

    skill = (
        "---\nname: shellout\nengine: python\n---\n"
        "import subprocess\nsubprocess.run(['ls'])\n"
    )
    verdict = reviewer_stub(skill)
    assert verdict["verdict"] == "rejected"
    assert any("subprocess" in f for f in verdict["findings"])


def test_reject_takes_precedence_over_revise():
    """A draft containing BOTH a TODO and a subprocess call is rejected,
    not bounced for revision — the hard-reject is the safer outcome."""
    from fixtures.code_reviewer_stub import reviewer_stub

    skill = (
        "---\nname: mixed\nengine: python\n---\n"
        "# TODO replace this\nimport subprocess\n"
    )
    verdict = reviewer_stub(skill)
    assert verdict["verdict"] == "rejected"


# ── Fixture wiring ──────────────────────────────────────────────────────


def test_reviewer_stub_fixture_resolves(reviewer_stub):
    """The conftest fixture must yield the same callable as the import."""
    from fixtures.code_reviewer_stub import reviewer_stub as direct

    assert reviewer_stub is direct


def test_code_reviewer_stub_alias(code_reviewer_stub):
    """The aliased fixture name must also resolve to the same callable."""
    from fixtures.code_reviewer_stub import reviewer_stub as direct

    assert code_reviewer_stub is direct
