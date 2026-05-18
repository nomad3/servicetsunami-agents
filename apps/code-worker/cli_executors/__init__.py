"""Per-CLI chat executors hoisted out of workflows.py in Phase 1.6.

Each module owns the chat dispatch path for one platform. Executors take
the same (task_input, session_dir, ...) signature as their previous
underscore-prefixed forms in workflows.py. workflows.py re-exports each
public name back under the old `_<name>` alias, so production callers
and existing tests are unaffected.

Why split: workflows.py grew to 2,318 lines mixing dataclass schemas,
Temporal activity wiring, credential-fetch helpers, and 5 per-platform
chat executors. The executors are the largest block (~1,000 lines
together) and the most independently maintainable — splitting them out
shrinks workflows.py to its real responsibility (workflow + activity
orchestration) and gives each platform a clean public module.
"""
from cli_executors.aider import execute_aider_chat
from cli_executors.claude import execute_claude_chat
from cli_executors.codex import execute_codex_chat
from cli_executors.copilot import execute_copilot_chat
from cli_executors.deepseek import execute_deepseek_chat
from cli_executors.gemini import execute_gemini_chat
from cli_executors.glm import execute_glm_chat
from cli_executors.goose import execute_goose_chat
from cli_executors.kimi import execute_kimi_chat
from cli_executors.opencode import execute_opencode_chat

__all__ = [
    "execute_aider_chat",
    "execute_claude_chat",
    "execute_codex_chat",
    "execute_copilot_chat",
    "execute_deepseek_chat",
    "execute_gemini_chat",
    "execute_glm_chat",
    "execute_goose_chat",
    "execute_kimi_chat",
    "execute_opencode_chat",
]
