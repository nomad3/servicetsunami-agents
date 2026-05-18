"""Tests for tenant_home_quota — Phase 2 of task #264.

The walker is filesystem-only; no temporal / no httpx / no fakes for the
emitter. We build trees under ``tmp_path`` and assert on what survives.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

import tenant_home_quota


# ── helpers ─────────────────────────────────────────────────────────────

def _write(p: Path, size: int, mtime: float | None = None) -> None:
    """Create a file of ``size`` bytes (sparse-OK contents).

    When ``mtime`` is supplied, the file AND its immediate parent
    directory both get the back-dated mtime — that matches the walker's
    expectation that an "old" package directory has an old dir-mtime
    (which is how pip --user actually leaves things on disk).
    """
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x" * size)
    if mtime is not None:
        os.utime(p, (mtime, mtime))
        os.utime(p.parent, (mtime, mtime))


def _ago(days: float) -> float:
    return time.time() - days * 24 * 3600


@pytest.fixture(autouse=True)
def _reset_watermark():
    """The watermark cache is module-global; reset between tests."""
    tenant_home_quota._LAST_WALK.clear()
    yield
    tenant_home_quota._LAST_WALK.clear()


# ── enforce_quota ───────────────────────────────────────────────────────

class TestEnforceQuotaUnderBudget:
    def test_under_budget_returns_empty_pruned(self, tmp_path):
        home = tmp_path / "home"
        _write(home / ".cache" / "pip" / "wheels" / "foo.whl", 1024)
        _write(home / ".local" / "lib" / "python3.11" / "site-packages" / "pkg" / "__init__.py", 512)

        result = tenant_home_quota.enforce_quota(home, budget_bytes=10 * 1024)

        assert result["pruned"] == []
        assert result["before"] == result["after"]
        # The two files we created are still there.
        assert (home / ".cache" / "pip" / "wheels" / "foo.whl").exists()
        assert (home / ".local" / "lib" / "python3.11" / "site-packages" / "pkg" / "__init__.py").exists()

    def test_missing_home_returns_zero(self, tmp_path):
        ghost = tmp_path / "does-not-exist"
        result = tenant_home_quota.enforce_quota(ghost)
        assert result["before"] == 0
        assert result["after"] == 0
        assert result["pruned"] == []


class TestEnforceQuotaOverBudget:
    def test_prunes_cache_first(self, tmp_path):
        home = tmp_path / "home"
        # 2KB budget, 5KB of .cache, 100B of .local
        _write(home / ".cache" / "huggingface" / "model.bin", 5 * 1024)
        _write(home / ".local" / "lib" / "python3.11" / "site-packages" / "pkg" / "x.py", 100)
        # OAuth creds: must survive even under tight budget.
        _write(home / ".gemini" / "oauth_creds.json", 200)

        result = tenant_home_quota.enforce_quota(home, budget_bytes=2 * 1024)

        # .cache went away.
        assert not (home / ".cache" / "huggingface").exists()
        # OAuth creds survived.
        assert (home / ".gemini" / "oauth_creds.json").exists()
        # .local pkg was still inside budget, untouched.
        assert (home / ".local" / "lib" / "python3.11" / "site-packages" / "pkg" / "x.py").exists()
        assert any("huggingface" in p for p in result["pruned"])
        assert result["after"] < result["before"]

    def test_falls_through_to_site_packages_when_cache_insufficient(self, tmp_path):
        home = tmp_path / "home"
        # Tiny .cache (1KB), huge stale site-packages (10KB).
        _write(home / ".cache" / "pip" / "x.whl", 1024)
        old = _ago(30)
        _write(
            home / ".local" / "lib" / "python3.11" / "site-packages" / "stalepkg" / "x.py",
            10 * 1024, mtime=old,
        )
        # Also a young site-packages pkg — must survive (mtime > cutoff).
        _write(
            home / ".local" / "lib" / "python3.11" / "site-packages" / "freshpkg" / "x.py",
            100,
        )

        result = tenant_home_quota.enforce_quota(home, budget_bytes=2 * 1024)

        # Cache pruned first.
        assert not (home / ".cache" / "pip").exists()
        # Stale package pruned next.
        assert not (home / ".local" / "lib" / "python3.11" / "site-packages" / "stalepkg").exists()
        # Fresh package survives.
        assert (home / ".local" / "lib" / "python3.11" / "site-packages" / "freshpkg" / "x.py").exists()
        assert result["after"] <= 2 * 1024 or len(result["pruned"]) >= 2


class TestNeverTouchInvariants:
    def test_oauth_creds_never_pruned_even_when_over(self, tmp_path):
        home = tmp_path / "home"
        # Stuff enough into .gemini to force aggressive pruning — but the
        # oauth files MUST survive.
        _write(home / ".gemini" / "oauth_creds.json", 100)
        _write(home / ".gemini" / "credentials.json", 100)
        _write(home / ".gemini" / "google_accounts.json", 100)
        # Pad with prunable .cache content.
        _write(home / ".cache" / "junk" / "blob", 10 * 1024)

        tenant_home_quota.enforce_quota(home, budget_bytes=1024)

        assert (home / ".gemini" / "oauth_creds.json").exists()
        assert (home / ".gemini" / "credentials.json").exists()
        assert (home / ".gemini" / "google_accounts.json").exists()

    def test_config_dir_never_pruned(self, tmp_path):
        home = tmp_path / "home"
        old = _ago(30)
        _write(home / ".config" / "myapp" / "settings.toml", 500, mtime=old)
        # Force aggressive prune.
        _write(home / ".cache" / "junk", 10 * 1024)

        tenant_home_quota.enforce_quota(home, budget_bytes=512)

        assert (home / ".config" / "myapp" / "settings.toml").exists()

    def test_lock_files_never_pruned(self, tmp_path):
        home = tmp_path / "home"
        old = _ago(30)
        _write(home / ".local" / "lib" / "python3.11" / "site-packages" / "pkg.lock", 200, mtime=old)
        _write(home / ".cache" / "junk", 10 * 1024)

        tenant_home_quota.enforce_quota(home, budget_bytes=512)

        assert (home / ".local" / "lib" / "python3.11" / "site-packages" / "pkg.lock").exists()

    def test_projects_dir_never_pruned(self, tmp_path):
        # Note: tenant_home_dir layout puts projects/ as a SIBLING of home/
        # but defensive coverage — if a tenant-home tree ever ends up with
        # a projects/ subdir (cloned-repo CLI does it via alpha workspace
        # clone), don't touch it.
        home = tmp_path / "home"
        old = _ago(30)
        _write(home / "projects" / "myrepo" / "src" / "main.py", 1000, mtime=old)
        _write(home / ".cache" / "junk", 10 * 1024)

        tenant_home_quota.enforce_quota(home, budget_bytes=512)

        assert (home / "projects" / "myrepo" / "src" / "main.py").exists()

    def test_git_dirs_never_pruned(self, tmp_path):
        home = tmp_path / "home"
        old = _ago(30)
        _write(home / ".local" / "share" / "myrepo" / ".git" / "HEAD", 200, mtime=old)
        _write(home / ".cache" / "junk", 10 * 1024)

        tenant_home_quota.enforce_quota(home, budget_bytes=512)

        assert (home / ".local" / "share" / "myrepo" / ".git" / "HEAD").exists()


# ── nested never-touch survival (B2) ────────────────────────────────────
#
# These tests guard the security boundary: a top-level "prunable" entry
# (e.g. ``.cache/foo``) must not destroy nested credential blobs / config
# / lock files just because the *parent* is in a prunable tier. The
# recursive walker has to re-check ``_is_never_touch`` on EVERY
# descendant, not only on the top-level subdir. They fail with
# ``shutil.rmtree`` and pass with the ``_safe_rm_tree`` walker.

class TestNestedNeverTouchSurvival:
    def test_nested_oauth_creds_in_cache_survives(self, tmp_path):
        home = tmp_path / "home"
        # Nested oauth blob hidden inside a normally-prunable cache subtree.
        _write(home / ".cache" / "foo" / "oauth_creds.json", 100)
        # Lots of prunable padding so the walker definitely fires on .cache.
        _write(home / ".cache" / "foo" / "blob.bin", 10 * 1024)

        tenant_home_quota.enforce_quota(home, budget_bytes=512)

        assert (home / ".cache" / "foo" / "oauth_creds.json").exists()

    def test_nested_credentials_in_cache_survives(self, tmp_path):
        home = tmp_path / "home"
        _write(home / ".cache" / "gcloud" / "credentials.json", 100)
        _write(home / ".cache" / "gcloud" / "junk.bin", 10 * 1024)

        tenant_home_quota.enforce_quota(home, budget_bytes=512)

        assert (home / ".cache" / "gcloud" / "credentials.json").exists()

    def test_nested_git_under_fully_stale_local_share_survives(self, tmp_path):
        home = tmp_path / "home"
        old = _ago(30)
        # Back-date the ENTIRE ancestor chain so Tier 4 sees a stale dir.
        head = home / ".local" / "share" / "myrepo" / ".git" / "HEAD"
        _write(head, 200, mtime=old)
        # Manually back-date every ancestor (the _write helper only does
        # immediate parent). Tier 4 picks dirs to prune by mtime.
        for ancestor in [
            home / ".local",
            home / ".local" / "share",
            home / ".local" / "share" / "myrepo",
            home / ".local" / "share" / "myrepo" / ".git",
        ]:
            os.utime(ancestor, (old, old))
        # Force aggressive pruning past Tiers 1-3 by padding .cache.
        _write(home / ".cache" / "padding", 10 * 1024)

        tenant_home_quota.enforce_quota(home, budget_bytes=512)

        assert (home / ".local" / "share" / "myrepo" / ".git" / "HEAD").exists()

    def test_nested_lock_under_stale_site_packages_survives(self, tmp_path):
        home = tmp_path / "home"
        old = _ago(30)
        # Stale site-packages dir with a lock file inside — the lock must
        # survive even though its enclosing package is pruning-eligible.
        sp = home / ".local" / "lib" / "python3.11" / "site-packages" / "stalepkg"
        _write(sp / "x.py", 5 * 1024, mtime=old)
        _write(sp / "poetry.lock", 200, mtime=old)
        # Back-date the package dir itself too.
        os.utime(sp, (old, old))
        # Force the walker to reach Tier 2 by stuffing .cache.
        _write(home / ".cache" / "padding", 10 * 1024)

        tenant_home_quota.enforce_quota(home, budget_bytes=512)

        assert (home / ".local" / "lib" / "python3.11" / "site-packages"
                / "stalepkg" / "poetry.lock").exists()


class TestBestEffortPartial:
    def test_returns_partial_state_when_cant_get_under_budget(self, tmp_path):
        home = tmp_path / "home"
        # All content is in the never-touch set: only oauth + projects/.
        # Walker should run all tiers, find nothing to delete, and return
        # without raising.
        _write(home / ".gemini" / "oauth_creds.json", 10 * 1024)
        _write(home / "projects" / "repo" / "huge.bin", 20 * 1024)

        result = tenant_home_quota.enforce_quota(home, budget_bytes=1024)

        # Over-budget after walk — best-effort partial.
        assert result["after"] > 1024
        assert (home / ".gemini" / "oauth_creds.json").exists()
        assert (home / "projects" / "repo" / "huge.bin").exists()


# ── watermark gating (should_walk) ──────────────────────────────────────

class TestShouldWalk:
    def test_first_call_walks(self):
        assert tenant_home_quota.should_walk("tenant-a", cumulative_chunks=0) is True

    def test_recent_walk_with_low_delta_skips(self):
        tenant_home_quota._record_walk("tenant-a", cumulative_chunks=5)
        # 0 new chunks, recent walk -> skip.
        assert tenant_home_quota.should_walk("tenant-a", cumulative_chunks=5) is False

    def test_recent_walk_with_high_delta_walks(self):
        tenant_home_quota._record_walk("tenant-a", cumulative_chunks=5)
        # 50 new chunks crossed the 10-event watermark -> walk.
        assert tenant_home_quota.should_walk("tenant-a", cumulative_chunks=55) is True

    def test_old_walk_walks_regardless_of_delta(self, monkeypatch):
        tenant_home_quota._record_walk("tenant-a", cumulative_chunks=5)
        # Fast-forward monotonic by 11 minutes.
        real_monotonic = time.monotonic
        offset = 11 * 60
        monkeypatch.setattr(time, "monotonic", lambda: real_monotonic() + offset)
        assert tenant_home_quota.should_walk("tenant-a", cumulative_chunks=5) is True


# ── maybe_enforce_quota (watermark + walk integration) ──────────────────

class TestMaybeEnforceQuota:
    def test_skips_when_watermark_says_skip(self, tmp_path):
        home = tmp_path / "home"
        _write(home / ".cache" / "junk", 10 * 1024)
        # First call walks.
        first = tenant_home_quota.maybe_enforce_quota(
            "tenant-a", home, cumulative_chunks=0, budget_bytes=512,
        )
        assert first is not None
        # Second call right after — same chunk count — should skip.
        second = tenant_home_quota.maybe_enforce_quota(
            "tenant-a", home, cumulative_chunks=0, budget_bytes=512,
        )
        assert second is None

    def test_returns_walk_result_on_walk(self, tmp_path):
        home = tmp_path / "home"
        _write(home / ".cache" / "junk", 10 * 1024)
        result = tenant_home_quota.maybe_enforce_quota(
            "tenant-a", home, cumulative_chunks=0, budget_bytes=512,
        )
        assert result is not None
        assert "before" in result and "after" in result and "pruned" in result
