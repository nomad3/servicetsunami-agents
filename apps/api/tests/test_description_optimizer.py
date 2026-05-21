"""Tests for skill_creator.description_optimizer (#301)."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.services.skill_creator import description_optimizer as do


def test_heuristic_collapses_whitespace_and_returns_provenance():
    src = "  Searches the\n\ncodebase  for   matching  patterns. "
    out = do.optimize_description_sync(src, backend="heuristic")
    assert out.original == src
    assert out.optimized == "Searches the codebase for matching patterns."
    assert out.backend == "heuristic"


def test_heuristic_truncates_at_sentence_boundary():
    sentences = ["Sentence number " + str(i) + " contains some content." for i in range(80)]
    src = " ".join(sentences)
    out = do.optimize_description_sync(src, backend="heuristic")
    assert len(out.optimized) <= do.MAX_DESCRIPTION_LEN
    assert out.optimized.endswith(".") or out.optimized.endswith(". ")


def test_sync_entry_rejects_ollama_backend():
    """The sync entry deliberately refuses Ollama — callers must
    use await optimize_ollama() instead of nesting asyncio.run."""
    with pytest.raises(ValueError, match="only supports backend='heuristic'"):
        do.optimize_description_sync("hi", backend="ollama")


@pytest.mark.asyncio
async def test_ollama_returns_rewritten_with_provenance():
    with patch(
        "app.services.local_inference.generate",
        new=AsyncMock(return_value="Searches the codebase for matching patterns when the user asks to grep."),
    ):
        out = await do.optimize_ollama(
            "search code", skill_name="grep", model="gemma4",
        )
    assert out.backend == "ollama"
    assert out.model == "gemma4"
    assert out.optimized.startswith("Searches")


@pytest.mark.asyncio
async def test_ollama_strips_surrounding_quotes():
    """gemma4 occasionally wraps the output in quotes — strip them
    so the description doesn't end up displayed with literal " marks."""
    with patch(
        "app.services.local_inference.generate",
        new=AsyncMock(return_value='"Generates a report."'),
    ):
        out = await do.optimize_ollama("make report")
    assert out.optimized == "Generates a report."


@pytest.mark.asyncio
async def test_ollama_falls_back_to_heuristic_on_empty():
    with patch(
        "app.services.local_inference.generate",
        new=AsyncMock(return_value=""),
    ):
        out = await do.optimize_ollama("  Frequently   asks  about X. ")
    assert out.backend == "heuristic"
    assert out.optimized == "Frequently asks about X."


@pytest.mark.asyncio
async def test_ollama_falls_back_to_heuristic_on_raise():
    with patch(
        "app.services.local_inference.generate",
        new=AsyncMock(side_effect=RuntimeError("ollama unreachable")),
    ):
        out = await do.optimize_ollama("hi")
    assert out.backend == "heuristic"


@pytest.mark.asyncio
async def test_ollama_template_format_handles_braces_in_input():
    """Regression for the str.format brace-escape bug (PR #635 lesson).
    If a user puts literal {curly braces} in their description, the
    template MUST format cleanly rather than dying on KeyError."""
    # The _USER_TEMPLATE has {description} + {skill_name} placeholders
    # that ARE intentional. User-input braces inside description must
    # not collide with them.
    rendered = do._USER_TEMPLATE.format(
        description="My skill processes {json} {payloads}",
        skill_name="test",
    )
    assert "{json}" in rendered
    assert "{payloads}" in rendered
    assert "test" in rendered
