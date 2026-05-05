"""Pure-function unit tests for helpers in workflows.py.

These functions don't touch Temporal, the network, or subprocess — so they
exercise without any mocks and give us a cheap coverage floor.
"""
from __future__ import annotations

import json

import pytest

import workflows as wf


# ── _build_allowed_tools_from_mcp ────────────────────────────────────────

class TestBuildAllowedToolsFromMcp:
    def test_empty_config_falls_back_to_default(self):
        out = wf._build_allowed_tools_from_mcp("", "")
        assert "mcp__agentprovision__*" in out

    def test_extra_tools_are_prepended(self):
        out = wf._build_allowed_tools_from_mcp("", "Bash,Read")
        parts = out.split(",")
        assert parts[0] == "Bash"
        assert "Read" in parts
        # Must still include a default mcp wildcard.
        assert any("mcp__" in p for p in parts)

    def test_each_mcp_server_yields_wildcard(self, fake_mcp_config_json):
        out = wf._build_allowed_tools_from_mcp(fake_mcp_config_json, "")
        assert "mcp__agentprovision__*" in out
        assert "mcp__github__*" in out

    def test_invalid_json_does_not_raise(self):
        out = wf._build_allowed_tools_from_mcp("{not json", "")
        assert "mcp__agentprovision__*" in out

    def test_no_mcp_servers_key_falls_back(self):
        out = wf._build_allowed_tools_from_mcp("{}", "")
        assert "mcp__agentprovision__*" in out


# ── _extract_goal ────────────────────────────────────────────────────────

class TestExtractGoal:
    def test_extracts_from_goal_section(self):
        text = "## Goal\nAdd a comment to main\n\n## Other\nFoo"
        assert wf._extract_goal(text) == "Add a comment to main"

    def test_falls_back_to_first_non_header_line(self):
        text = "## Header\n\nDo the thing."
        assert wf._extract_goal(text) == "Do the thing."

    def test_first_non_empty_line_wins_when_no_goal_section(self):
        # The fallback returns the first non-empty, non-header line as-is.
        text = "first line\nsecond line"
        assert wf._extract_goal(text) == "first line"

    def test_truncates_to_70_chars_when_no_lines_at_all(self):
        # When every line begins with '#' the loop falls through to the
        # final ``return task_description[:70]`` branch.
        text = "#" * 200
        assert wf._extract_goal(text) == "#" * 70


# ── _detect_tag ──────────────────────────────────────────────────────────

class TestDetectTag:
    @pytest.mark.parametrize(
        "text, expected",
        [
            # The classifier walks {fix, feat, infra, db, refactor, docs} in
            # order and returns the first matching tag — these strings only
            # hit the intended bucket.
            ("Fix a critical bug in login", "fix"),
            ("Build a brand-new endpoint for users", "feat"),
            ("Update the helm chart for kubernetes deploy", "infra"),
            ("Database migration to alter a schema column", "db"),
            ("Reorganize and clean up the module layout", "refactor"),
            ("Document the readme with a docstring", "docs"),
            # No-match fallback is "feat".
            ("plain unmatched text here", "feat"),
        ],
    )
    def test_keyword_detection(self, text, expected):
        assert wf._detect_tag(text) == expected


# ── credit-exhausted classifiers ─────────────────────────────────────────

class TestCreditExhaustedClassifiers:
    @pytest.mark.parametrize("msg", [
        "Your credit balance is too low to continue",
        "Usage limit reached for this workspace",
        "rate limit reached on your subscription",
        "MAX PLAN LIMIT exceeded",
        "Out of credits — please top up",
    ])
    def test_claude_positive(self, msg):
        assert wf._is_claude_credit_exhausted(msg) is True

    def test_claude_negative(self):
        assert wf._is_claude_credit_exhausted("totally fine response") is False
        assert wf._is_claude_credit_exhausted("") is False
        assert wf._is_claude_credit_exhausted(None) is False  # noqa: defensive

    @pytest.mark.parametrize("msg", [
        "rate limit",
        "quota exceeded",
        "insufficient_quota",
        "HTTP 429 too many requests",
    ])
    def test_codex_positive(self, msg):
        assert wf._is_codex_credit_exhausted(msg) is True

    @pytest.mark.parametrize("msg", [
        "Copilot is not enabled for this user",
        "subscription required",
        "rate limit",
        "forbidden",
        "HTTP 429",
    ])
    def test_copilot_positive(self, msg):
        assert wf._is_copilot_credit_exhausted(msg) is True


# ── _toml_escape / _toml_inline_table ────────────────────────────────────

class TestTomlSerialization:
    def test_escape_backslash_and_quote(self):
        assert wf._toml_escape('a"b\\c') == 'a\\"b\\\\c'

    def test_inline_table_round_trip(self):
        out = wf._toml_inline_table({"X-Tenant-Id": "abc", "X-Internal-Key": "k"})
        # Both keys should be present, quoted, in an inline table.
        assert out.startswith("{ ") and out.endswith(" }")
        assert '"X-Tenant-Id" = "abc"' in out
        assert '"X-Internal-Key" = "k"' in out


# ── _codex_mcp_config_lines ──────────────────────────────────────────────

class TestCodexMcpConfigLines:
    def test_emits_one_section_per_server(self, fake_mcp_config_json):
        lines = wf._codex_mcp_config_lines(fake_mcp_config_json)
        text = "\n".join(lines)
        assert "[mcp_servers.agentprovision]" in text
        assert "[mcp_servers.github]" in text
        assert 'transport = "streamable_http"' in text
        # Headers from the fixture should be carried as an inline table.
        assert '"X-Tenant-Id" = "abc"' in text

    def test_skips_non_dict_server_entries(self):
        cfg = json.dumps({"mcpServers": {"weird": "not a dict"}})
        assert wf._codex_mcp_config_lines(cfg) == []

    def test_no_headers_key_omits_http_headers_line(self):
        cfg = json.dumps({"mcpServers": {"a": {"url": "http://x"}}})
        text = "\n".join(wf._codex_mcp_config_lines(cfg))
        assert "[mcp_servers.a]" in text
        assert "http_headers" not in text


# ── _extract_codex_last_message / _extract_codex_metadata ────────────────

class TestCodexExtractors:
    def test_last_message_pulls_from_jsonl(self):
        raw = (
            json.dumps({"type": "noise"}) + "\n"
            + json.dumps({"last_agent_message": "  hello there  "}) + "\n"
        )
        assert wf._extract_codex_last_message(raw) == "hello there"

    def test_last_message_returns_empty_when_nothing_matches(self):
        assert wf._extract_codex_last_message("plain text\nno json") == ""

    def test_metadata_extracts_model_and_tokens(self):
        raw = (
            json.dumps({"type": "session_configured", "model": "gpt-5"}) + "\n"
            + json.dumps({"token_usage": {"input_tokens": 10, "output_tokens": 5}}) + "\n"
        )
        meta = wf._extract_codex_metadata(raw)
        assert meta["model"] == "gpt-5"
        assert meta["input_tokens"] == 10
        assert meta["output_tokens"] == 5

    def test_metadata_is_resilient_to_garbage(self):
        meta = wf._extract_codex_metadata("not json\n{also not\n")
        assert meta == {"input_tokens": 0, "output_tokens": 0, "model": None}


# ── _consensus_check ─────────────────────────────────────────────────────

class TestConsensusCheck:
    def _review(self, approved: bool, role: str = "X"):
        return wf.AgentReview(
            agent_role=role,
            approved=approved,
            verdict="APPROVED" if approved else "REJECTED",
            issues=["i1"],
            suggestions=[],
            summary="s",
        )

    def test_passes_when_required_approvals_met(self):
        passed, report = wf._consensus_check(
            [self._review(True), self._review(True), self._review(False)],
            required=2,
        )
        assert passed is True
        assert "PASSED" in report

    def test_fails_when_not_enough_approvals(self):
        passed, report = wf._consensus_check(
            [self._review(False), self._review(False)],
            required=2,
        )
        assert passed is False
        assert "FAILED" in report

    def test_lists_each_reviewer(self):
        passed, report = wf._consensus_check(
            [self._review(True, "A"), self._review(False, "B")],
            required=2,
        )
        assert "[A]" in report
        assert "[B]" in report


class TestSafeCliErrorSnippet:
    """`_safe_cli_error_snippet` strips streaming-JSON output from CLI
    error messages so it never leaks into chat replies (2026-05-05
    incident: copilot CLI exited 1 with empty stderr but a stdout full
    of `{"type":"session.skills_loaded",...}` events; the old
    `result.stderr or result.stdout` pattern dumped the whole stream
    into the user's chat message)."""

    def test_prefers_stderr_over_stdout(self):
        out = wf._safe_cli_error_snippet("real error here", "stdout content", 100)
        assert out == "real error here"

    def test_falls_back_to_stdout_when_stderr_blank(self):
        out = wf._safe_cli_error_snippet("", "plain text error\n", 100)
        assert out == "plain text error"

    def test_strips_streaming_json_to_generic_msg(self):
        # Real captured Copilot CLI streaming JSON shape from the
        # 2026-05-05 incident.
        stream = "\n".join([
            '{"type":"session.skills_loaded","data":{"skills":[]},"id":"x"}',
            '{"type":"session.tools_updated","data":{"model":"copilot"}}',
            '{"type":"session.completed","data":{}}',
        ])
        out = wf._safe_cli_error_snippet("", stream, 1000)
        # Must NOT contain the raw JSON
        assert "session.skills_loaded" not in out
        assert "session.tools_updated" not in out
        # Should produce a generic placeholder
        assert "streaming JSON" in out

    def test_extracts_error_message_from_streaming_event(self):
        stream = "\n".join([
            '{"type":"session.started","data":{}}',
            '{"type":"error","message":"quota exceeded"}',
            '{"type":"session.aborted","data":{}}',
        ])
        out = wf._safe_cli_error_snippet("", stream, 1000)
        assert out == "quota exceeded"

    def test_extracts_error_from_result_event(self):
        stream = '{"type":"result","error":"auth failed: token expired"}'
        out = wf._safe_cli_error_snippet("", stream, 1000)
        assert out == "auth failed: token expired"

    def test_truncates_to_max_len(self):
        out = wf._safe_cli_error_snippet("a" * 5000, "", 100)
        assert len(out) == 100

    def test_empty_inputs_return_empty(self):
        assert wf._safe_cli_error_snippet("", "", 100) == ""
        assert wf._safe_cli_error_snippet(None, None, 100) == ""

    def test_keeps_plain_text_stdout_when_no_stderr(self):
        # Common case: a CLI that writes a usage error to stdout.
        out = wf._safe_cli_error_snippet("", "Usage: cli COMMAND [OPTS]", 100)
        assert "Usage:" in out
