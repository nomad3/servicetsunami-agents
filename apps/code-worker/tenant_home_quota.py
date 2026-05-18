"""Per-tenant HOME quota walker (task #264 Phase 2).

Phase 1 (PR #540) moved per-tenant ``$HOME`` onto the persistent
workspaces volume at ``<workspaces_root>/<tenant_id>/home/``. Without a
quota, a single noisy tenant — one that keeps ``pip install --user``-ing
ever-bigger ML wheels, or that lets a CLI fill ``.cache/huggingface`` —
can fill the entire volume and starve every other tenant.

This module ships a soft cap. After each CLI invocation the executor
calls ``enforce_quota(tenant_home)``. If the tree is under budget
(2 GiB by default) we return immediately with no prunes. If we're over,
we walk the tree in priority order (least-essential first) and delete
until we drop under budget or we run out of safe-to-delete candidates.

Design notes (from docs/plans/2026-05-17-code-worker-tenant-home-cap-design.md):

- **Pure-Python walk.** The code-worker image strips ``du`` and other
  shell utilities — ``os.scandir`` is the only available primitive.
  ~80 ms on a 5 GB tree per design measurement; cheaper than the
  300 ms a typical Claude turn already spends in startup.

- **Watermark.** Walking on every turn is wasteful — most turns barely
  write anything. ``should_walk`` gates: skip if last walk was <10 min
  ago AND fewer than 10 chunk events have landed since then. The
  delta-chunks counter comes from ``SessionEventEmitter`` close stats.

- **Concurrency.** Two CLI invocations for the same tenant can race —
  we acquire a non-blocking ``fcntl.flock`` on ``.quota-walker.lock``;
  if someone else holds it, this turn skips the walk. Skipping is safe
  because the other walker will bring the tree under budget anyway.

- **Best-effort.** If we can't bring the tree under budget (every
  prunable file is either too new or in the never-touch set), we log
  WARNING and return the partial state. We never raise — the chat
  turn must not fail because of a quota walker bug.
"""
from __future__ import annotations

import errno
import fnmatch
import logging
import os
import time
from pathlib import Path

try:
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX (test envs without fcntl)
    fcntl = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

# 2 GiB soft cap per tenant home dir. Anchored to the same order of
# magnitude as ``_TENANT_WORKSPACE_BUDGET`` in cli_runtime — workspaces
# can legitimately grow with real project content, HOME should not.
DEFAULT_BUDGET_BYTES = 2 * 1024 ** 3

# Files that must NEVER be pruned. Either credential blobs (re-OAuthing
# is a 60-second user-visible flow) or config that downstream CLIs need
# to even start. Matched against the absolute path's basename or, for
# directory matches, the directory name.
_NEVER_TOUCH_FILES = frozenset({
    "oauth_creds.json",
    "credentials.json",
    "google_accounts.json",
})

# Glob patterns matched against any path segment. Lock files survive
# because pip / poetry / npm need them to skip work on the next turn.
_NEVER_TOUCH_GLOBS = ("*.lock",)

# Directory NAMES (not full paths) that the walker refuses to descend
# into for pruning. ``projects/`` is the tenant's actual workspaces dir
# (``alpha workspace clone`` lands repos there) — pruning it would
# destroy user work. ``.git`` is sacred for the same reason.
_NEVER_TOUCH_DIRS = frozenset({"projects", ".git", ".config"})

# 14 days mtime threshold for the "old enough to prune" tiers. Recent
# package installs are likely to be re-used on the next turn; deleting
# them just forces a re-download.
_STALE_AGE_SECONDS = 14 * 24 * 3600

# Watermark gating constants. Walked dirs go into the in-memory cache
# below keyed by tenant_id with (last_walk_ts, last_emitted_chunks_seen).
_WATERMARK_MIN_INTERVAL_S = 10 * 60  # 10 minutes
_WATERMARK_MIN_DELTA_CHUNKS = 10

# Process-local watermark cache. Lost on restart, which is fine — a
# post-restart turn forcing one extra walk is cheaper than persisting
# state. Keyed by tenant_id, value is the running chunk-event count
# observed at the last walk.
_LAST_WALK: dict[str, tuple[float, int]] = {}


def should_walk(tenant_id: str, cumulative_chunks: int) -> bool:
    """Return True iff the quota walker should run for this tenant now.

    Gates:
      1. If we've never walked this tenant in this process, walk.
      2. If the last walk was more than _WATERMARK_MIN_INTERVAL_S ago,
         walk regardless of chunk delta — fresh data is cheap.
      3. Otherwise, walk only if at least _WATERMARK_MIN_DELTA_CHUNKS
         chunk events have landed since the last walk. Bursty CLIs
         (gemini emitting 200 events) need the walk; quiet ones (a
         claude turn that emits 3 ``text`` events and exits) don't.

    ``cumulative_chunks`` is the lifetime emitted-events count for the
    process (or any monotonic counter); the watermark cares about the
    DELTA since the last walk, not the absolute value.
    """
    prev = _LAST_WALK.get(tenant_id)
    if prev is None:
        return True
    last_ts, last_chunks = prev
    elapsed = time.monotonic() - last_ts
    if elapsed >= _WATERMARK_MIN_INTERVAL_S:
        return True
    delta = cumulative_chunks - last_chunks
    return delta >= _WATERMARK_MIN_DELTA_CHUNKS


def _record_walk(tenant_id: str, cumulative_chunks: int) -> None:
    """Update the watermark cache after a (successful or skipped) walk."""
    _LAST_WALK[tenant_id] = (time.monotonic(), cumulative_chunks)


def _is_never_touch(path: Path, base: Path) -> bool:
    """Return True if ``path`` (or any of its parents up to ``base``) is
    in the never-touch set."""
    name = path.name
    if name in _NEVER_TOUCH_FILES:
        return True
    for glob in _NEVER_TOUCH_GLOBS:
        if fnmatch.fnmatchcase(name, glob):
            return True
    # Walk up parents looking for a forbidden directory name.
    try:
        rel = path.relative_to(base)
    except ValueError:
        return True  # outside base — refuse defensively
    for part in rel.parts:
        if part in _NEVER_TOUCH_DIRS:
            return True
    return False


def _scan_size(root: Path) -> int:
    """Pure-Python recursive size in bytes via os.scandir.

    ~80 ms on a 5 GB tree per design measurement. We tolerate
    OSError on individual entries (a sandboxed CLI may have created
    files we can't stat) and skip them — the walker is best-effort.
    """
    total = 0
    stack = [root]
    while stack:
        cur = stack.pop()
        try:
            with os.scandir(cur) as it:
                for entry in it:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(Path(entry.path))
                        else:
                            total += entry.stat(follow_symlinks=False).st_size
                    except OSError:
                        continue
        except (OSError, FileNotFoundError):
            continue
    return total


def _dir_size(root: Path) -> int:
    """Wrapper for callers that want a single dir's size."""
    if not root.exists():
        return 0
    return _scan_size(root)


def _safe_unlink(path: Path) -> int:
    """Delete a single file/symlink; return bytes freed. Best-effort."""
    try:
        if path.is_symlink():
            try:
                path.unlink(missing_ok=True)
            except OSError:
                logger.debug("_safe_unlink(%s) symlink failed", path, exc_info=True)
            return 0
        if path.is_file():
            try:
                size = path.stat().st_size
            except OSError:
                size = 0
            try:
                path.unlink(missing_ok=True)
            except OSError:
                logger.debug("_safe_unlink(%s) failed", path, exc_info=True)
                return 0
            return size
    except OSError:
        logger.debug("_safe_unlink(%s) failed", path, exc_info=True)
    return 0


def _safe_rm_tree(path: Path, home: Path, pruned: list[str]) -> int:
    """Recursively delete ``path``, re-checking never-touch on EVERY descendant.

    For files/symlinks: delete iff not never-touch. Returns bytes freed.

    For directories: descend via ``os.scandir``; delete each child via
    recursive call, then ``os.rmdir(path)`` ONLY if the directory ended
    up empty. A non-empty directory means we refused to delete something
    inside (never-touch hit or an unlink failure), and the protecting
    parent must survive.

    Records each successfully-deleted file path into ``pruned`` (the list
    accumulator). Directories that survive because of protected content
    are NOT recorded. Never raises.

    This is the security boundary: ``shutil.rmtree`` would skip the
    never-touch check on nested files, which would destroy
    ``.cache/foo/oauth_creds.json`` and similar. Do NOT replace with
    ``shutil.rmtree``.
    """
    if _is_never_touch(path, home):
        return 0
    freed = 0
    try:
        # Handle symlinks (incl. symlinks-to-dirs) as a single unlink — do
        # NOT follow into them and prune their target tree.
        if path.is_symlink():
            return _safe_unlink(path)
        if path.is_file():
            n = _safe_unlink(path)
            if n > 0:
                pruned.append(str(path))
                logger.debug("tenant_home_quota: pruned %s (%d bytes)", path, n)
            return n
        if not path.is_dir():
            return 0
    except OSError:
        return 0

    # Directory case — descend.
    try:
        with os.scandir(path) as it:
            children = list(it)
    except OSError:
        return 0
    for child in children:
        cpath = Path(child.path)
        freed += _safe_rm_tree(cpath, home, pruned)

    # Only remove the now-(maybe-)empty directory if it's actually empty.
    try:
        os.rmdir(path)
    except OSError:
        # Non-empty (something inside was never-touch) OR permission
        # error — leave the directory in place.
        pass
    return freed


def _prune_cache(home: Path, pruned: list[str]) -> int:
    """Tier 1: anything under ``.cache/*`` — pip / hf / npm scratch.

    Uses the recursive walker so nested protected files (e.g.
    ``.cache/foo/oauth_creds.json``) survive even when the top-level
    cache subdir is a candidate for deletion.
    """
    cache = home / ".cache"
    if not cache.is_dir():
        return 0
    freed = 0
    try:
        for entry in os.scandir(cache):
            p = Path(entry.path)
            freed += _safe_rm_tree(p, home, pruned)
    except OSError:
        pass
    return freed


def _prune_site_packages(home: Path, pruned: list[str], cutoff: float) -> int:
    """Tier 2: ``.local/lib/python*/site-packages/*`` older than cutoff."""
    lib = home / ".local" / "lib"
    if not lib.is_dir():
        return 0
    freed = 0
    try:
        for py_entry in os.scandir(lib):
            if not py_entry.is_dir(follow_symlinks=False):
                continue
            if not py_entry.name.startswith("python"):
                continue
            site_pkgs = Path(py_entry.path) / "site-packages"
            if not site_pkgs.is_dir():
                continue
            try:
                for pkg in os.scandir(site_pkgs):
                    p = Path(pkg.path)
                    try:
                        if pkg.stat(follow_symlinks=False).st_mtime > cutoff:
                            continue
                    except OSError:
                        continue
                    freed += _safe_rm_tree(p, home, pruned)
            except OSError:
                continue
    except OSError:
        pass
    return freed


def _prune_other_local_lib(home: Path, pruned: list[str], cutoff: float) -> int:
    """Tier 3: ``.local/lib/*`` (non-python) older than cutoff.

    Catches node_modules, rust crates, etc., that some sandboxed CLIs
    bring along under the user-local prefix.
    """
    lib = home / ".local" / "lib"
    if not lib.is_dir():
        return 0
    freed = 0
    try:
        for entry in os.scandir(lib):
            p = Path(entry.path)
            if entry.is_dir(follow_symlinks=False) and entry.name.startswith("python"):
                continue  # Tier 2 territory
            try:
                if entry.stat(follow_symlinks=False).st_mtime > cutoff:
                    continue
            except OSError:
                continue
            freed += _safe_rm_tree(p, home, pruned)
    except OSError:
        pass
    return freed


def _prune_stale_local(home: Path, pruned: list[str], cutoff: float) -> int:
    """Tier 4: anything under ``.local/`` older than cutoff.

    Catches stale ``.local/share`` data dirs nobody's read in two
    weeks, etc. Most aggressive tier — only fires when the first three
    didn't get us under budget.

    Skips ``.local/bin/`` wholesale: tenants' user-installed CLI binaries
    (e.g. ``pip install --user foo`` console scripts) live there and
    deleting them silently breaks workflows even when stale by mtime.
    """
    local = home / ".local"
    if not local.is_dir():
        return 0
    freed = 0
    stack = [local]
    while stack:
        cur = stack.pop()
        try:
            entries = list(os.scandir(cur))
        except OSError:
            continue
        for entry in entries:
            p = Path(entry.path)
            # Skip user-installed binaries — see docstring.
            if entry.is_dir(follow_symlinks=False) and p == local / "bin":
                continue
            if _is_never_touch(p, home):
                continue
            try:
                st = entry.stat(follow_symlinks=False)
            except OSError:
                continue
            if entry.is_dir(follow_symlinks=False):
                if st.st_mtime > cutoff:
                    # Recent dir — descend rather than prune wholesale.
                    stack.append(p)
                    continue
                freed += _safe_rm_tree(p, home, pruned)
            else:
                if st.st_mtime > cutoff:
                    continue
                n = _safe_unlink(p)
                if n > 0:
                    freed += n
                    pruned.append(str(p))
                    logger.debug(
                        "tenant_home_quota: pruned %s (%d bytes)", p, n,
                    )
    return freed


def enforce_quota(
    tenant_home: str | os.PathLike,
    budget_bytes: int = DEFAULT_BUDGET_BYTES,
) -> dict:
    """Walk ``tenant_home`` and prune non-essential subtrees until under budget.

    Returns ``{"before": int, "after": int, "pruned": list[str]}`` —
    sizes in bytes, ``pruned`` is the list of absolute paths removed.
    Never raises.

    Concurrency: takes a non-blocking ``fcntl.flock`` on
    ``<home>/.quota-walker.lock``. If another process holds it, returns
    the current size + empty prune list (the other walker will do the
    work).
    """
    home = Path(tenant_home)
    if not home.is_dir():
        return {"before": 0, "after": 0, "pruned": [], "skipped": False}

    # Acquire the per-tenant flock to prevent two concurrent walkers
    # from racing on the same tree. Non-blocking — if someone else has
    # it, this turn skips the walk.
    lock_path = home / ".quota-walker.lock"
    lock_fd: int | None = None
    if fcntl is not None:
        try:
            lock_fd = os.open(str(lock_path), os.O_CREAT | os.O_WRONLY, 0o600)
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                if exc.errno in (errno.EWOULDBLOCK, errno.EAGAIN):
                    logger.debug(
                        "tenant_home_quota: flock held by another walker for %s; skipping",
                        home,
                    )
                    try:
                        os.close(lock_fd)
                    except OSError:
                        pass
                    # I2: do not _dir_size on the skipped path; it's
                    # 80-1000ms of wasted I/O on a tree we won't walk.
                    # I1: signal "skipped, do NOT advance watermark".
                    return {"before": 0, "after": 0, "pruned": [], "skipped": True}
                # Other OSError — bail without locking, walk anyway.
                logger.debug(
                    "tenant_home_quota: flock errored for %s (%s); proceeding without lock",
                    home, exc,
                )
                try:
                    os.close(lock_fd)
                except OSError:
                    pass
                lock_fd = None
        except OSError as exc:
            logger.debug(
                "tenant_home_quota: lock file open failed for %s (%s); proceeding without lock",
                home, exc,
            )
            lock_fd = None

    try:
        before = _scan_size(home)
        if before <= budget_bytes:
            return {"before": before, "after": before, "pruned": [], "skipped": False}

        pruned: list[str] = []
        cutoff = time.time() - _STALE_AGE_SECONDS

        # Tier 1: .cache/*
        _prune_cache(home, pruned)
        if _scan_size(home) <= budget_bytes:
            after = _scan_size(home)
            logger.info(
                "tenant_home_quota: %s pruned tier=1 before=%d after=%d files=%d",
                home, before, after, len(pruned),
            )
            return {"before": before, "after": after, "pruned": pruned, "skipped": False}

        # Tier 2: stale site-packages
        _prune_site_packages(home, pruned, cutoff)
        if _scan_size(home) <= budget_bytes:
            after = _scan_size(home)
            logger.info(
                "tenant_home_quota: %s pruned tier=2 before=%d after=%d files=%d",
                home, before, after, len(pruned),
            )
            return {"before": before, "after": after, "pruned": pruned, "skipped": False}

        # Tier 3: other stale .local/lib/*
        _prune_other_local_lib(home, pruned, cutoff)
        if _scan_size(home) <= budget_bytes:
            after = _scan_size(home)
            logger.info(
                "tenant_home_quota: %s pruned tier=3 before=%d after=%d files=%d",
                home, before, after, len(pruned),
            )
            return {"before": before, "after": after, "pruned": pruned, "skipped": False}

        # Tier 4: anything stale under .local/
        _prune_stale_local(home, pruned, cutoff)

        after = _scan_size(home)
        if after > budget_bytes:
            logger.warning(
                "tenant_home_quota: %s still over budget after prune "
                "(before=%d after=%d budget=%d pruned=%d entries)",
                home, before, after, budget_bytes, len(pruned),
            )
        else:
            logger.info(
                "tenant_home_quota: %s pruned tier=4 before=%d after=%d files=%d",
                home, before, after, len(pruned),
            )
        return {"before": before, "after": after, "pruned": pruned, "skipped": False}
    finally:
        if lock_fd is not None:
            if fcntl is not None:
                try:
                    fcntl.flock(lock_fd, fcntl.LOCK_UN)
                except OSError:
                    logger.debug("tenant_home_quota: flock unlock failed", exc_info=True)
            try:
                os.close(lock_fd)
            except OSError:
                pass


def maybe_enforce_quota(
    tenant_id: str,
    tenant_home: str | os.PathLike,
    cumulative_chunks: int,
    budget_bytes: int = DEFAULT_BUDGET_BYTES,
) -> dict | None:
    """Watermark-gated wrapper around ``enforce_quota``.

    Call this from CLI executors after ``run_cli_with_heartbeat`` returns.
    Returns the enforce_quota result dict if we walked, or ``None`` if
    the watermark said skip. Always non-raising.
    """
    try:
        if not should_walk(tenant_id, cumulative_chunks):
            return None
        result = enforce_quota(tenant_home, budget_bytes=budget_bytes)
        # I1: do NOT advance the watermark when the walk was skipped
        # because another process held the flock. Otherwise this process
        # would think it "just walked" and refuse to walk again for 10
        # minutes, even though no data was actually scanned here.
        if not result.get("skipped"):
            _record_walk(tenant_id, cumulative_chunks)
        return result
    except Exception:  # noqa: BLE001
        logger.warning(
            "tenant_home_quota.maybe_enforce_quota(%s) failed",
            tenant_id, exc_info=True,
        )
        return None
