"""T6.4b — Synthesis prompt snapshot + shellout-ban end-to-end.

T2.3 covers regex-on-body for the forbidden-shellout list but doesn't
pin the synthesis PROMPT itself. The prompt is the contract between
the workflow and the LLM — drift here silently degrades every learned
skill the system will ever produce.

This module adds two layers of coverage:

1. **Snapshot test** — asserts ``SYNTHESIS_SYSTEM`` from
   ``learning_prompts.py`` matches the byte-exact snapshot in
   ``tests/snapshots/synthesis_system_prompt.txt``. Updating the
   prompt requires a manual snapshot refresh; review thus diffs the
   prompt change explicitly.
2. **Stub-driven shellout-ban e2e** — replaces ``_llm_synthesize`` with
   a stub that emits a python draft containing a
   ``subprocess.run(['yt-dlp', ...])`` call. Asserts the real
   ``synthesize_skill_draft`` raises ``DraftForbiddenShellout`` so the
   guard fires at the public API surface, not just on the
   regex-helper used internally.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from src.mcp_tools import learning_prompts
from src.mcp_tools import learning as learning_mod
from src.mcp_tools.learning import (
    DraftForbiddenShellout,
    synthesize_skill_draft,
)


_SNAPSHOT_PATH = (
    Path(__file__).parent / "snapshots" / "synthesis_system_prompt.txt"
)


# ── Snapshot ────────────────────────────────────────────────────────────


def test_synthesis_system_prompt_matches_snapshot():
    """The runtime SYNTHESIS_SYSTEM must equal the checked-in snapshot
    byte-for-byte. If this fails the change is intentional → refresh
    the snapshot in the same PR:

        cp <(python -c 'from src.mcp_tools.learning_prompts import SYNTHESIS_SYSTEM; print(SYNTHESIS_SYSTEM, end="")') \\
           apps/mcp-server/tests/snapshots/synthesis_system_prompt.txt
    """
    expected = _SNAPSHOT_PATH.read_text()
    actual = learning_prompts.SYNTHESIS_SYSTEM
    if actual != expected:
        # Show both with explicit repr to make whitespace drift visible.
        pytest.fail(
            "SYNTHESIS_SYSTEM drifted from snapshot.\n"
            f"---- expected (len={len(expected)}) ----\n{expected}\n"
            f"---- actual   (len={len(actual)}) ----\n{actual}\n"
            "If the change is intentional, refresh "
            "tests/snapshots/synthesis_system_prompt.txt."
        )


def test_snapshot_contains_load_bearing_clauses():
    """Defense in depth — a manual snapshot refresh that accidentally
    drops one of the load-bearing rubric clauses must still fail.
    These strings are part of the runtime contract; the snapshot is
    the wire format."""
    s = _SNAPSHOT_PATH.read_text()
    # Engine-selection clauses.
    assert "engine: markdown" in s
    assert "engine: python" in s
    # The PII scrub directive — placeholders + categories.
    assert "PII SCRUB" in s
    assert "<user-name>" in s
    # The forbidden-shellout list.
    for binary in ("yt-dlp", "ffmpeg", "curl", "wget"):
        assert binary in s, f"forbidden binary {binary!r} dropped from prompt"
    # Output contract: JSON with skill_md + synthetic_test.
    assert "skill_md" in s
    assert "synthetic_test" in s
    # Anti-tautology clause for the synthetic_test.
    assert "tautology" in s


# ── Stub-driven shellout-ban end-to-end ─────────────────────────────────


@pytest.mark.asyncio
async def test_synthesize_raises_on_subprocess_shellout(monkeypatch):
    """Stub `_llm_synthesize` so it returns a python draft that tries
    to embed `subprocess.run(['yt-dlp', ...])`. The real
    `synthesize_skill_draft` must raise ``DraftForbiddenShellout`` at
    the public surface — proving the guard is wired end-to-end, not
    just on the underscore regex helper that T2.3 exercises directly.
    """
    bad_skill_md = (
        "---\n"
        "name: bad-shellout\n"
        "engine: python\n"
        "---\n"
        "import subprocess\n"
        "subprocess.run(['yt-dlp', 'https://youtube.com/x'])\n"
    )

    async def _fake_llm(transcript, source_url, hints):
        return bad_skill_md, {"input": {}, "expected": {}}

    monkeypatch.setattr(learning_mod, "_llm_synthesize", _fake_llm)

    with pytest.raises(DraftForbiddenShellout) as exc_info:
        await synthesize_skill_draft(
            transcript="speaker explains topic",
            source_url="https://example.com/v",
        )

    # Error message should cite which pattern tripped so logs / cache
    # entries are diagnosable.
    assert "subprocess" in str(exc_info.value) or "shellout" in str(exc_info.value)


@pytest.mark.asyncio
async def test_synthesize_raises_on_ytdlp_shellout(monkeypatch):
    """Same contract but for a `yt-dlp` invocation that doesn't go
    through `subprocess` (e.g. an `os.system` call). Per spec §1.5
    the forbidden list isn't restricted to subprocess — any embedded
    binary call should trip."""
    bad_skill_md = (
        "---\n"
        "name: ytdlp-shellout\n"
        "engine: python\n"
        "---\n"
        "import os\n"
        "os.system('yt-dlp https://example.com/v')\n"
    )

    async def _fake_llm(transcript, source_url, hints):
        return bad_skill_md, {"input": {}, "expected": {}}

    monkeypatch.setattr(learning_mod, "_llm_synthesize", _fake_llm)

    with pytest.raises(DraftForbiddenShellout):
        await synthesize_skill_draft(
            transcript="t",
            source_url="https://example.com/v",
        )


@pytest.mark.asyncio
async def test_synthesize_allows_markdown_skill_with_shellout_word(monkeypatch):
    """Markdown skills are prose for an agent to read — the body can
    legitimately MENTION `subprocess` or `yt-dlp` (e.g. as part of a
    "how the tool works" explainer) without tripping the guard. The
    forbidden-shellout list applies only to `engine: python`."""
    md_skill = (
        "---\n"
        "name: explainer\n"
        "engine: markdown\n"
        "---\n"
        "## How yt-dlp works\n"
        "It uses subprocess under the hood to invoke ffmpeg.\n"
    )

    async def _fake_llm(transcript, source_url, hints):
        return md_skill, {"input": {"q": "what is yt-dlp"}, "expected": {"contains": "yt-dlp"}}

    monkeypatch.setattr(learning_mod, "_llm_synthesize", _fake_llm)

    out = await synthesize_skill_draft(
        transcript="explainer",
        source_url="https://example.com/v",
    )
    assert out["engine"] == "markdown"
    # Slug derives from the frontmatter `name`.
    assert out["slug"] == "explainer"
