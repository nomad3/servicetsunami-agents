"""Source of truth for the bundled-agent slug ⇄ name mapping.

Bundled agents live at
    apps/api/app/agents/_bundled/<slug>/skill.md
with the YAML frontmatter carrying a `name:` field that matches the
canonical `Agent.name` operators see (e.g. "Code Reviewer",
"Substrate Sentinel", "Luna").

Two different review-gate modules need this lookup:
  * review_circularity._slug_from_agent + _resolve_escalation
  * reviewer_availability.check_required_reviewers

Before this module existed, both kept their own hardcoded maps —
they enumerated 3 of the 6 bundled slugs that ship today
(`devops`, `sre`, `business-support` were silently missing). The
gates worked *partially* on those agents (path-matching succeeded
because it uses the raw slug, but escalation resolution returned
None).

We discover the mapping at import time from the filesystem so that
adding a new bundled agent is zero-touch — drop `_bundled/<slug>/
skill.md` and both gates pick it up. The discovery is cached for
the process lifetime; tests can call `_reset_cache_for_tests()`
when they mutate the filesystem fixture.

Design: docs/plans/2026-05-24-review-gate-medium-followups-design.md
Motivation: PR #706 review I1 + PR #707 review I2 (2026-05-24).
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Dict, Optional


BUNDLED_AGENTS_ROOT = "apps/api/app/agents/_bundled"


@lru_cache(maxsize=1)
def _slug_to_name() -> Dict[str, str]:
    """Scan _bundled/ dirs at import time and parse `name:` from each
    skill.md frontmatter. Cached for process lifetime."""
    root = _bundled_root_abs()
    out: Dict[str, str] = {}
    if not root.is_dir():
        return out
    for entry in sorted(root.iterdir()):
        if not entry.is_dir():
            continue
        skill_md = entry / "skill.md"
        if not skill_md.is_file():
            continue
        name = _parse_frontmatter_name(skill_md.read_text())
        if name:
            out[entry.name] = name
    return out


def _bundled_root_abs() -> Path:
    """Resolve BUNDLED_AGENTS_ROOT to an absolute path.

    The module lives at apps/api/app/services/bundled_agents.py;
    walk up to the repo root then descend into _bundled/. This keeps
    the lookup deterministic regardless of CWD.
    """
    # apps/api/app/services/bundled_agents.py -> apps/api/app/agents/_bundled
    here = Path(__file__).resolve()
    # services -> app -> api -> apps  (4 parents up to the repo)
    repo_root = here.parents[4]
    return repo_root / BUNDLED_AGENTS_ROOT


_NAME_RE = re.compile(r"^name:\s*(.+?)\s*$", re.MULTILINE)


def _parse_frontmatter_name(text: str) -> Optional[str]:
    """Pull the `name:` value from the leading YAML frontmatter.

    Intentionally regex-based instead of pyyaml: keeps this module
    dependency-free (it's imported at app startup), and the
    frontmatter shape is locked by our own test
    test_bundled_readonly_skills.py.
    """
    if not text.startswith("---\n"):
        return None
    end = text.find("\n---", 4)
    if end == -1:
        return None
    fm = text[4:end]
    m = _NAME_RE.search(fm)
    if not m:
        return None
    return m.group(1).strip().strip('"').strip("'")


def slug_to_name(slug: str) -> Optional[str]:
    """Return the canonical Agent.name for a bundled slug, or None."""
    return _slug_to_name().get(slug)


def name_to_slug(name: str) -> Optional[str]:
    """Return the bundled slug for a canonical Agent.name, or None.

    Case-insensitive on the name comparison so callers can pass
    raw user input without normalizing first.
    """
    if not name:
        return None
    target = name.strip().lower()
    for slug, n in _slug_to_name().items():
        if n.lower() == target:
            return slug
    return None


def all_bundled_slugs() -> list[str]:
    """Return all discovered bundled slugs (sorted)."""
    return sorted(_slug_to_name().keys())


def _reset_cache_for_tests() -> None:
    """Invalidate the cached scan — tests only."""
    _slug_to_name.cache_clear()
