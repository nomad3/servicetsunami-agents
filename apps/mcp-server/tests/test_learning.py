"""Tests for src.mcp_tools.learning (T1.2 skeleton).

These cover only the registry shape — every entry raises
``NotImplementedError`` at this stage. The real bodies land in T2.1–T2.7
and get their own tests there.
"""
from __future__ import annotations

import pytest

from src.mcp_tools import learning


EXPECTED_TOOLS = {
    "extract_media",
    "transcribe_url",
    "synthesize_skill_draft",
    "dispatch_skill_review",
    "run_synthetic_test",
    "install_skill",
    "diffuse_learning",
}


def test_learning_module_exports_7_tools():
    assert set(learning.TOOLS.keys()) == EXPECTED_TOOLS


@pytest.mark.parametrize("tool", sorted(EXPECTED_TOOLS))
def test_each_tool_callable(tool):
    assert callable(learning.TOOLS[tool])
