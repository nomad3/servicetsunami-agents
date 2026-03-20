import os
import sys
import unittest
from unittest import mock

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__))))

from workflows import ChatCliInput, ChatCliResult, _is_claude_credit_exhausted, execute_chat_cli


class TestClaudeCodexFallback(unittest.IsolatedAsyncioTestCase):
    def test_detects_credit_exhaustion(self):
        self.assertTrue(_is_claude_credit_exhausted("CLI exit 1: credit balance is too low"))
        self.assertTrue(_is_claude_credit_exhausted("Monthly usage limit reached"))
        self.assertFalse(_is_claude_credit_exhausted("network timeout"))

    @mock.patch("workflows._execute_codex_chat")
    @mock.patch("workflows._execute_claude_chat")
    @mock.patch("workflows._fetch_github_token")
    async def test_chat_falls_back_to_codex_when_claude_credits_exhausted(
        self,
        mock_fetch_github,
        mock_claude_chat,
        mock_codex_chat,
    ):
        mock_fetch_github.return_value = None
        mock_claude_chat.return_value = ChatCliResult(
            response_text="",
            success=False,
            error="CLI exit 1: credit balance is too low",
        )
        mock_codex_chat.return_value = ChatCliResult(
            response_text="Codex handled it",
            success=True,
            metadata={"platform": "codex"},
        )

        task_input = ChatCliInput(platform="claude_code", message="hello", tenant_id="tenant-1")
        result = await execute_chat_cli(task_input)

        self.assertTrue(result.success)
        self.assertEqual(result.response_text, "Codex handled it")
        self.assertEqual(result.metadata["platform"], "codex")
        self.assertEqual(result.metadata["fallback_from"], "claude_code")
        self.assertEqual(result.metadata["requested_platform"], "claude_code")

    @mock.patch("workflows._execute_codex_chat")
    @mock.patch("workflows._execute_claude_chat")
    @mock.patch("workflows._fetch_github_token")
    async def test_chat_does_not_fallback_for_non_credit_errors(
        self,
        mock_fetch_github,
        mock_claude_chat,
        mock_codex_chat,
    ):
        mock_fetch_github.return_value = None
        mock_claude_chat.return_value = ChatCliResult(
            response_text="",
            success=False,
            error="CLI exit 1: transport failure",
        )

        task_input = ChatCliInput(platform="claude_code", message="hello", tenant_id="tenant-1")
        result = await execute_chat_cli(task_input)

        self.assertFalse(result.success)
        self.assertEqual(result.error, "CLI exit 1: transport failure")
        mock_codex_chat.assert_not_called()


if __name__ == "__main__":
    unittest.main()
