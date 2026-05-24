"""Pin that explicitly read-only bundled agents use `knowledge_readonly`.

Code Reviewer and Substrate Sentinel both ship as explicitly
read-only agents (their skill.md prose says so). A future edit to
either skill.md that flips `tool_groups: [..., knowledge_readonly,
...]` back to `[..., knowledge, ...]` would silently re-grant
mutation capability via record_observation, create_entity, etc. —
the exact regression class migration 153 was written to close.

Companion: `test_tool_groups_knowledge_readonly.py` pins the group
itself; this file pins the bundled skill consumers.
"""

from pathlib import Path

import pytest
import yaml


BUNDLED_DIR = (
    Path(__file__).resolve().parents[2]
    / "app"
    / "agents"
    / "_bundled"
)

READ_ONLY_AGENTS = ["code-reviewer", "substrate-sentinel"]


def _read_frontmatter(slug: str) -> dict:
    skill_path = BUNDLED_DIR / slug / "skill.md"
    text = skill_path.read_text()
    assert text.startswith("---\n"), f"{skill_path} missing YAML frontmatter"
    _, frontmatter, _ = text.split("---\n", 2)
    return yaml.safe_load(frontmatter)


@pytest.mark.parametrize("slug", READ_ONLY_AGENTS)
def test_readonly_agent_uses_knowledge_readonly_not_knowledge(slug: str) -> None:
    fm = _read_frontmatter(slug)
    tool_groups = fm.get("tool_groups", [])
    assert "knowledge" not in tool_groups, (
        f"{slug}/skill.md lists `knowledge` (read+write). Use "
        "`knowledge_readonly` — the agent's own prose declares "
        "read-only posture. See migration 153."
    )
    assert "knowledge_readonly" in tool_groups, (
        f"{slug}/skill.md must include `knowledge_readonly` so its "
        "memory recall tools resolve."
    )
