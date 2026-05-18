"""Worker-side ProviderAdapter contract test — design §1.

Mirrors apps/api/tests/cli_orchestrator/test_provider_adapter_contract.py
for the 6 worker-side concrete adapters. The api-side test cannot
exercise these adapters because the worker package
(``apps/code-worker``) is not on ``sys.path`` in api test runs (mirrors
prod — code-worker is a separate container).

What this test validates:

  - Each of the 6 adapters (claude_code, codex, gemini_cli, copilot_cli,
    opencode, shell) implements ``ProviderAdapter`` structurally:
    name + preflight + run + classify_error.

  - Each adapter's signature matches the documented contract.

  - The adapters import WITHOUT dragging ``workflows`` into the import
    graph (Phase 1.6 surface preservation — the executor body's
    ``from workflows import ...`` runs lazily on first call).
"""
from __future__ import annotations

import inspect
import sys

import pytest

from cli_orchestrator.adapters.base import (
    ExecutionRequest,
    ExecutionResult,
    PreflightResult,
    ProviderAdapter,
)
from cli_orchestrator.status import Status

from cli_orchestrator_adapters.claude_code import ClaudeCodeAdapter
from cli_orchestrator_adapters.codex import CodexAdapter
from cli_orchestrator_adapters.copilot_cli import CopilotCliAdapter
from cli_orchestrator_adapters.gemini_cli import GeminiCliAdapter
from cli_orchestrator_adapters.kimi_k2 import KimiK2Adapter
from cli_orchestrator_adapters.opencode import OpencodeAdapter
from cli_orchestrator_adapters.shell import ShellAdapter


ADAPTER_FACTORIES = [
    ("claude_code", ClaudeCodeAdapter),
    ("codex", CodexAdapter),
    ("copilot_cli", CopilotCliAdapter),
    ("gemini_cli", GeminiCliAdapter),
    ("kimi_k2", KimiK2Adapter),
    ("opencode", OpencodeAdapter),
    ("shell", ShellAdapter),
]


@pytest.mark.parametrize("name,factory", ADAPTER_FACTORIES, ids=lambda c: c if isinstance(c, str) else c.__name__)
def test_adapter_implements_protocol(name, factory):
    adapter = factory()
    assert isinstance(adapter, ProviderAdapter), (
        f"{name}: missing one of preflight/run/classify_error/name attribute"
    )
    assert isinstance(adapter.name, str) and adapter.name == name


@pytest.mark.parametrize("name,factory", ADAPTER_FACTORIES, ids=lambda c: c if isinstance(c, str) else c.__name__)
def test_adapter_signatures(name, factory):
    adapter = factory()

    pf_sig = inspect.signature(adapter.preflight)
    pf_params = list(pf_sig.parameters.keys())
    assert pf_params == ["req"], f"{name}.preflight: expected (req), got {pf_params}"

    run_sig = inspect.signature(adapter.run)
    run_params = list(run_sig.parameters.keys())
    assert run_params == ["req"], f"{name}.run: expected (req), got {run_params}"

    cls_sig = inspect.signature(adapter.classify_error)
    cls_params = list(cls_sig.parameters.keys())
    assert cls_params == ["stderr", "exit_code", "exc"], (
        f"{name}.classify_error: expected (stderr, exit_code, exc), got {cls_params}"
    )


def test_adapter_imports_do_not_drag_workflows():
    """Hard-constraint (g) — Phase 1.6 surface preservation.

    Importing the adapter package must NOT load the worker's
    workflows.py module. The executor body's ``from workflows import …``
    lives inside the function so the import graph stays clean.

    We verify by snapshotting sys.modules around a fresh
    importlib.import_module of the adapter modules.
    """
    import importlib

    # Drop any pre-loaded entries so we can snapshot a fresh import.
    drop_keys = [
        k for k in list(sys.modules.keys())
        if k.startswith("cli_orchestrator_adapters") or k == "workflows"
    ]
    for k in drop_keys:
        sys.modules.pop(k, None)

    before = set(sys.modules.keys())
    importlib.import_module("cli_orchestrator_adapters")
    importlib.import_module("cli_orchestrator_adapters.claude_code")
    importlib.import_module("cli_orchestrator_adapters.codex")
    importlib.import_module("cli_orchestrator_adapters.gemini_cli")
    importlib.import_module("cli_orchestrator_adapters.copilot_cli")
    importlib.import_module("cli_orchestrator_adapters.kimi_k2")
    importlib.import_module("cli_orchestrator_adapters.opencode")
    importlib.import_module("cli_orchestrator_adapters.shell")
    after = set(sys.modules.keys())

    new = after - before
    assert "workflows" not in new, (
        f"workflows.py was loaded by the adapter import path: {sorted(new)}"
    )


@pytest.mark.parametrize("name,factory", ADAPTER_FACTORIES, ids=lambda c: c if isinstance(c, str) else c.__name__)
def test_adapter_classify_error_delegates_to_canonical(name, factory):
    """classify_error returns a Status value — uses the canonical classifier.

    Doesn't matter what the input is; we just want a Status back. This
    pins the adapter's classify_error to the canonical signature.
    """
    adapter = factory()
    status = adapter.classify_error(
        stderr="rate_limit exceeded; quota: 1000",
        exit_code=1,
        exc=None,
    )
    assert isinstance(status, Status)
    # Quota signal should classify as QUOTA_EXHAUSTED.
    assert status is Status.QUOTA_EXHAUSTED


def test_shell_adapter_preflight_rejects_missing_cmd():
    """ShellAdapter requires payload['cmd'] — preflight returns
    PROVIDER_UNAVAILABLE on a missing/empty cmd list."""
    adapter = ShellAdapter()
    req = ExecutionRequest(chain=("shell",), platform="shell", payload={})
    result = adapter.preflight(req)
    assert not result.ok
    assert result.status is Status.PROVIDER_UNAVAILABLE
