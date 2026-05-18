"""Tests for ``cli_runtime.tenant_home_dir`` (task #267 Phase 1).

Mirrors the shape of ``test_cli_cwd_tenant_workspace.TestResolveCliCwd``
for the sibling ``tenant_workspace_dir``: same WORKSPACES_ROOT
monkeypatch fixture, same UUID guard semantics, same per-tenant subtree
expectations.

Why a separate file (not ``test_cli_cwd_tenant_workspace.py``): the
workspace-cwd tests are about subprocess ``cwd`` end-to-end; these are
unit-scoped at the helper level. Co-locating them in a leaner file
keeps the cwd-scoped suite's intent clear.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import cli_runtime


TENANT_UUID = "11111111-1111-4111-8111-111111111111"
TENANT_OTHER = "22222222-2222-4222-8222-222222222222"


@pytest.fixture
def fake_workspaces_root(tmp_path, monkeypatch):
    """Redirect WORKSPACES_ROOT to a pytest tmp dir.

    Same fixture shape as ``test_cli_cwd_tenant_workspace.fake_workspaces_root``
    — the parent of ``<tenant_id>/home`` must exist as a real directory so
    the helper's ``mkdir(parents=True, exist_ok=True)`` succeeds without
    a permission error against ``/var/agentprovision/workspaces`` on the
    pytest host.
    """
    root = tmp_path / "workspaces"
    root.mkdir()
    monkeypatch.setattr(cli_runtime, "WORKSPACES_ROOT", Path(root))
    return root


class TestTenantHomeDir:
    def test_valid_uuid_returns_path_under_workspaces_root(
        self, fake_workspaces_root,
    ):
        out = cli_runtime.tenant_home_dir(TENANT_UUID)
        # Created on first access, lives under <root>/<tenant>/home.
        assert out.is_dir()
        assert out.name == "home"
        assert out.parent.name == TENANT_UUID
        assert out.parent.parent == fake_workspaces_root

    def test_idempotent_on_second_call(self, fake_workspaces_root):
        """``mkdir(exist_ok=True)`` so re-calls for an existing tenant
        must not raise — code-worker hits this on every chat turn."""
        first = cli_runtime.tenant_home_dir(TENANT_UUID)
        # Drop a marker file so we can prove the dir was reused.
        (first / "marker").write_text("present")
        second = cli_runtime.tenant_home_dir(TENANT_UUID)
        assert second == first
        assert (second / "marker").read_text() == "present"

    def test_per_tenant_isolation(self, fake_workspaces_root):
        a = cli_runtime.tenant_home_dir(TENANT_UUID)
        b = cli_runtime.tenant_home_dir(TENANT_OTHER)
        assert a != b
        assert TENANT_UUID in str(a)
        assert TENANT_OTHER in str(b)

    # ── UUID guard parity with ``tenant_workspace_dir`` (review I1) ─────

    def test_rejects_non_uuid_tenant_id_matching_workspace_helper(
        self, fake_workspaces_root,
    ):
        """Same UUID guard as ``tenant_workspace_dir`` (review I1).

        Covers path traversal, garbage strings, and empty/None in one
        spec so a future relaxation of the regex breaks both helpers'
        guards in lockstep.
        """
        for bad in ("../escape", "not-a-uuid", "", None):
            with pytest.raises(ValueError):
                cli_runtime.tenant_home_dir(bad)  # type: ignore[arg-type]
        # And no sibling-of-root directory got materialized.
        assert not (fake_workspaces_root.parent / "escape").exists()
