"""Worker-side ProviderAdapter implementations — design §1.

Each module here wraps one of the per-CLI executors hoisted in Phase 1.6
(``cli_executors.<platform>.execute_<platform>_chat``) and exposes the
``ProviderAdapter`` Protocol from the canonical
``cli_orchestrator.adapters.base`` module. The wrapping is mechanical:

  - ``preflight()`` runs ``shutil.which(<binary>)`` (memoised at module
    scope) and returns ``PROVIDER_UNAVAILABLE`` on miss.
  - ``run()`` calls the existing executor function (same signature it
    already had under workflows.py before the hoist), maps the
    ``ChatCliResult`` (response_text/success/error/metadata) to an
    ``ExecutionResult``, classifies on failure, and redacts at the
    boundary.
  - ``classify_error()`` delegates to the canonical classifier.

The adapters are intentionally side-effect-free at module import: a
top-level ``import cli_orchestrator_adapters.claude_code`` does NOT
drag ``workflows.py`` into the import graph (the executor body's
``from workflows import …`` runs LAZILY inside the executor on first
call — Phase 1.6 surface).

**Phase 2 worker scope**: adapters scaffolded + contract-tested + per-
adapter sanity test. The worker's own ``execute_chat_cli`` activity is
NOT rewritten to use a ResilientExecutor in Phase 2 — that's Phase 3+.
"""
from .claude_code import ClaudeCodeAdapter
from .codex import CodexAdapter
from .copilot_cli import CopilotCliAdapter
from .gemini_cli import GeminiCliAdapter
from .kimi_k2 import KimiK2Adapter
from .opencode import OpencodeAdapter
from .shell import ShellAdapter

__all__ = [
    "ClaudeCodeAdapter",
    "CodexAdapter",
    "CopilotCliAdapter",
    "GeminiCliAdapter",
    "KimiK2Adapter",
    "OpencodeAdapter",
    "ShellAdapter",
]
