"""Unit tests for the skills marketplace redesign helpers in skills_new.py.

Exercises the pure functions (validation, export serialization, sanitization,
auto-generated detection) so future refactors surface regressions without
needing a TestClient or DB fixture.
"""
import os
os.environ["TESTING"] = "True"

import json
import pytest
import yaml

from app.schemas.file_skill import FileSkill, SkillInput
from app.api.v1.skills_new import (
    _is_auto_generated_skill,
    _sanitize_tool_name,
    _skill_to_mcp_tool,
    _skill_to_openai_function,
    _skill_to_superpowers_md,
    _skill_to_gws_md,
    _validate_python_script,
    _validate_markdown_script,
    _validate_skill_payload,
)
from fastapi import HTTPException


# ---------------------------------------------------------------------------
# _is_auto_generated_skill
# ---------------------------------------------------------------------------

def test_auto_generated_by_category():
    s = FileSkill(name="x", category="auto-generated")
    assert _is_auto_generated_skill(s)


def test_auto_generated_by_description_case_insensitive():
    s = FileSkill(name="x", category="general", description="Response TIMEOUT pattern in sales")
    assert _is_auto_generated_skill(s)


def test_not_auto_generated_when_neither_matches():
    s = FileSkill(name="x", category="sales", description="Lead qualifier")
    assert not _is_auto_generated_skill(s)


def test_not_auto_generated_when_description_is_none():
    s = FileSkill(name="x", category="general")
    assert not _is_auto_generated_skill(s)


# ---------------------------------------------------------------------------
# _sanitize_tool_name
# ---------------------------------------------------------------------------

def test_sanitize_removes_spaces():
    assert _sanitize_tool_name("My Report Generator") == "my_report_generator"


def test_sanitize_preserves_valid_slug():
    assert _sanitize_tool_name("sql_query_v2") == "sql_query_v2"


def test_sanitize_strips_leading_trailing_junk():
    assert _sanitize_tool_name("---foo!!bar---") == "foo_bar"


def test_sanitize_fallback_for_empty():
    assert _sanitize_tool_name("") == "skill"
    assert _sanitize_tool_name("   ") == "skill"


def test_sanitize_handles_unicode():
    # Accented chars get stripped to produce an ASCII-only name
    assert _sanitize_tool_name("Mañana Ñ") == "ma_ana"


# ---------------------------------------------------------------------------
# _validate_python_script
# ---------------------------------------------------------------------------

def test_python_validation_accepts_valid_execute():
    assert _validate_python_script("def execute(inputs):\n    return {'ok': True}\n") is None


def test_python_validation_rejects_missing_execute():
    err = _validate_python_script("def other(inputs):\n    pass\n")
    assert err is not None
    assert "execute" in err


def test_python_validation_rejects_multi_arg_execute():
    err = _validate_python_script("def execute(inputs, ctx):\n    return {}\n")
    assert err is not None
    assert "positional" in err


def test_python_validation_reports_syntax_error_with_line():
    err = _validate_python_script("def execute(inputs):\n    return ???\n")
    assert err is not None
    assert "syntax error" in err.lower() or "line" in err.lower()


def test_python_validation_accepts_single_arg_with_kwargs():
    # *args and **kwargs don't count as positional; the contract holds.
    assert _validate_python_script("def execute(inputs, **kw):\n    return {}\n") is None


# ---------------------------------------------------------------------------
# _validate_markdown_script
# ---------------------------------------------------------------------------

def test_markdown_validation_accepts_declared_vars():
    assert _validate_markdown_script("Hello {{name}}, use {{email}}.", ["name", "email"]) is None


def test_markdown_validation_rejects_undeclared():
    err = _validate_markdown_script("Hello {{name}}", [])
    assert err is not None
    assert "name" in err


def test_markdown_validation_accepts_var_with_whitespace():
    assert _validate_markdown_script("Hello {{  name  }}", ["name"]) is None


def test_markdown_validation_no_vars_is_valid():
    assert _validate_markdown_script("No templating here.", []) is None


# ---------------------------------------------------------------------------
# _validate_skill_payload (HTTPException dispatcher)
# ---------------------------------------------------------------------------

def test_validate_skill_payload_raises_400_for_bad_python():
    with pytest.raises(HTTPException) as exc:
        _validate_skill_payload("python", "no_execute_here", [])
    assert exc.value.status_code == 400


def test_validate_skill_payload_passes_for_good_python():
    # Should NOT raise
    _validate_skill_payload("python", "def execute(inputs):\n    return {}", [])


def test_validate_skill_payload_skips_shell():
    # Shell skills have no structural contract — anything goes
    _validate_skill_payload("shell", "rm -rf / # no really just kidding", [])


def test_validate_skill_payload_markdown_uses_inputs():
    _validate_skill_payload(
        "markdown",
        "Hello {{name}}",
        [{"name": "name", "type": "string", "required": True}],
    )
    with pytest.raises(HTTPException):
        _validate_skill_payload("markdown", "Hello {{missing}}", [{"name": "name"}])


# ---------------------------------------------------------------------------
# _skill_to_mcp_tool — tool-name safety
# ---------------------------------------------------------------------------

def test_mcp_tool_name_uses_sanitized_slug():
    s = FileSkill(name="Hello World", slug="hello_world", description="d")
    tool = _skill_to_mcp_tool(s)
    assert tool["name"] == "skill_hello_world"


def test_mcp_tool_name_sanitizes_spaces_when_slug_missing():
    s = FileSkill(name="My Report Generator")  # no slug
    tool = _skill_to_mcp_tool(s)
    # Must be a valid tool name per OpenAI: ^[a-zA-Z0-9_-]{1,64}$
    assert tool["name"] == "skill_my_report_generator"
    assert all(c.isalnum() or c in "_-" for c in tool["name"])


def test_mcp_tool_name_length_capped_at_64():
    s = FileSkill(name="x" * 200, slug="x" * 200)
    tool = _skill_to_mcp_tool(s)
    assert len(tool["name"]) <= 64


def test_mcp_tool_input_schema_marks_required():
    s = FileSkill(
        name="q", slug="q",
        inputs=[
            SkillInput(name="a", type="string", required=True),
            SkillInput(name="b", type="number", required=False),
        ],
    )
    tool = _skill_to_mcp_tool(s)
    assert tool["inputSchema"]["required"] == ["a"]
    assert tool["inputSchema"]["properties"]["a"]["type"] == "string"
    assert tool["inputSchema"]["properties"]["b"]["type"] == "number"


# ---------------------------------------------------------------------------
# Export serializers — YAML safety
# ---------------------------------------------------------------------------

def _parse_frontmatter(md: str) -> dict:
    """Extract and YAML-parse the frontmatter of exported SKILL.md."""
    assert md.startswith("---\n")
    _, fm, _body = md.split("---", 2)
    return yaml.safe_load(fm)


def test_superpowers_export_handles_description_with_colons():
    # This broke the old f-string impl — YAML requires quoting values with :
    s = FileSkill(
        name="Query Skill", slug="query", engine="python",
        description="Query: pulls from DB. Use: after login.",
        category="data",
    )
    md = _skill_to_superpowers_md(s)
    fm = _parse_frontmatter(md)
    assert fm["description"] == "Query: pulls from DB. Use: after login."


def test_superpowers_export_handles_description_with_newlines():
    s = FileSkill(
        name="q", slug="q", engine="python",
        description="line one\nline two",
        category="general",
    )
    md = _skill_to_superpowers_md(s)
    fm = _parse_frontmatter(md)
    assert fm["description"].strip() == "line one\nline two"


def test_superpowers_export_handles_tags_with_commas():
    s = FileSkill(
        name="q", slug="q", engine="python",
        description="d", category="general",
        tags=["tag,with,commas", "normal"],
    )
    md = _skill_to_superpowers_md(s)
    fm = _parse_frontmatter(md)
    # YAML-safe: tags round-trip exactly
    assert fm["tags"] == ["tag,with,commas", "normal"]


def test_superpowers_export_coerces_non_string_tags():
    # Pydantic rejects non-str tags at construction time, but legacy data on
    # disk (raw YAML) can still produce non-str tags after .tags is mutated
    # post-construction. Simulate that by bypassing validation and confirm
    # the exporter's defensive str() coercion holds.
    s = FileSkill(name="q", slug="q", engine="python", description="d", category="general")
    # Bypass pydantic validation — mirrors the on-disk legacy case
    object.__setattr__(s, "tags", ["good", 42])
    md = _skill_to_superpowers_md(s)
    fm = _parse_frontmatter(md)
    assert fm["tags"] == ["good", "42"]


def test_superpowers_export_handles_empty_description():
    s = FileSkill(name="q", slug="q", engine="python", category="general")
    md = _skill_to_superpowers_md(s)
    fm = _parse_frontmatter(md)
    assert fm["description"] == ""


def test_gws_export_round_trips_frontmatter():
    s = FileSkill(
        name="Hello: World", slug="hw", engine="python",
        description="Has: colon\nAnd: newline",
        category="sales", tags=["a", "b"],
    )
    md = _skill_to_gws_md(s)
    fm = _parse_frontmatter(md)
    assert fm["title"] == "Hello: World"
    assert fm["category"] == "sales"
    assert fm["tags"] == ["a", "b"]


def test_openai_function_structure():
    s = FileSkill(
        name="My Skill", slug="my_skill", engine="python",
        description="Do a thing", category="general",
        inputs=[SkillInput(name="x", type="string", required=True)],
    )
    fn = _skill_to_openai_function(s)
    assert fn["type"] == "function"
    assert fn["function"]["name"] == "skill_my_skill"
    assert fn["function"]["description"] == "Do a thing"
    assert fn["function"]["parameters"]["required"] == ["x"]
    # Must be valid JSON (OpenAI API requires this shape)
    json.dumps(fn)
