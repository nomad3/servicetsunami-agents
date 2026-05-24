"""Pin the `knowledge_readonly` invariant.

The `knowledge` group bundles read + write tools together (see
`apps/api/app/services/tool_groups.py`). Agents that advertise a
"read-only" posture (Code Reviewer, Substrate Sentinel, audit
agents) MUST use `knowledge_readonly` to avoid silently inheriting
mutation capability via `record_observation`, `create_entity`,
`merge_entities`, or `update_entity`.

These tests lock the invariant — a future "let me also add X" edit
that drops a write tool into `knowledge_readonly` will break CI
instead of silently widening the read-only surface.

Companion: `tests/agents/test_bundled_read_only_skills_use_readonly_knowledge.py`
parses the bundled skill.md frontmatter to ensure the agents that
exist today actually use `knowledge_readonly`.
"""

from app.services.tool_groups import TOOL_GROUPS, resolve_tool_names


KNOWLEDGE_WRITE_TOOLS = {
    "record_observation",
    "create_entity",
    "merge_entities",
    "update_entity",
}


def test_knowledge_readonly_group_registered() -> None:
    assert "knowledge_readonly" in TOOL_GROUPS, (
        "`knowledge_readonly` tool group missing from TOOL_GROUPS"
    )


def test_knowledge_readonly_exact_membership() -> None:
    # Locking the exact set so future edits are intentional.
    assert set(resolve_tool_names(["knowledge_readonly"])) == {
        "search_knowledge",
        "find_entities",
        "recall_memory",
        "find_relations",
        "get_neighborhood",
        "ask_knowledge_graph",
        "get_entity_timeline",
    }


def test_knowledge_readonly_contains_no_write_tools() -> None:
    resolved = set(resolve_tool_names(["knowledge_readonly"]) or [])
    leaked = resolved & KNOWLEDGE_WRITE_TOOLS
    assert not leaked, (
        f"knowledge_readonly leaked write tools: {leaked}. "
        "Read-only posture is the entire reason this group exists."
    )


def test_knowledge_group_still_includes_writes_backwards_compat() -> None:
    # Operator-curated agents (Luna Supervisor, General Assistant)
    # intentionally use `knowledge` and rely on its write tools.
    # Don't strip them under the guise of "cleanup".
    resolved = set(resolve_tool_names(["knowledge"]) or [])
    missing = KNOWLEDGE_WRITE_TOOLS - resolved
    assert not missing, (
        f"`knowledge` group lost write tools {missing} — "
        "operator-curated supervisors depend on them."
    )
