#!/usr/bin/env python3
"""One-shot migration: move skills to the agents/ + skills/ layout.

Old layout (mixed):
    apps/api/app/skills/<slug>/skill.md             # bundled, top-level
    apps/api/app/skills/native/<slug>/skill.md      # bundled, mirrored
    apps/api/app/skills/community/<slug>/skill.md   # 57 GWS imports
    apps/api/app/skills/tenant_<uuid>/<slug>/skill.md  # tenant skills

New layout (Claude Code-style):
    apps/api/app/agents/_bundled/<slug>/skill.md    # engine: agent
    apps/api/app/agents/_tenant/<uuid>/<slug>/skill.md
    apps/api/app/skills/_bundled/<slug>/skill.md    # everything else
    apps/api/app/skills/_tenant/<uuid>/<slug>/skill.md
    apps/api/app/skills/_archive/<...>              # community + orphans + auto-generated

Run once. Idempotent — re-running is a no-op if the new layout already exists.

Usage:
    ./scripts/migrate_skills_layout.py            # dry run
    ./scripts/migrate_skills_layout.py --apply    # actually move files
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OLD_SKILLS_BASE = ROOT / "apps/api/app/skills"
NEW_AGENTS_BASE = ROOT / "apps/api/app/agents"
NEW_SKILLS_BASE = ROOT / "apps/api/app/skills"

# Slugs whose skill.md has `engine: agent` in frontmatter — these become
# AGENT.md files under apps/api/app/agents/_bundled/. Determined by inspection.
AGENT_SLUGS = {
    "luna",
    "integral-business-support",
    "integral-devops",
    "integral-sre",
}


def read_engine(skill_md: Path) -> str:
    """Best-effort engine detection from frontmatter."""
    try:
        for line in skill_md.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("engine:"):
                return line.split(":", 1)[1].strip().strip('"').strip("'")
            if line == "---" and not line.startswith("---"):
                # secondary frontmatter delimiter — stop parsing
                break
    except Exception:
        pass
    return ""


def inventory():
    """Return (agent_dirs, skill_dirs, archive_dirs) by inspecting OLD_SKILLS_BASE."""
    agents = []
    skills = []
    archives = []

    for entry in sorted(OLD_SKILLS_BASE.iterdir()):
        if not entry.is_dir():
            continue
        name = entry.name

        # Already in new layout — skip (idempotency)
        if name in ("_bundled", "_tenant", "_archive"):
            continue

        if name == "community":
            archives.append((entry, NEW_SKILLS_BASE / "_archive" / "community"))
            continue

        if name == "native":
            # Walk natives — split agents from skills
            for native_entry in sorted(entry.iterdir()):
                if not native_entry.is_dir():
                    continue
                slug = native_entry.name
                if slug in ("native", "community") or slug.startswith("tenant_"):
                    archives.append((native_entry, NEW_SKILLS_BASE / "_archive" / "_legacy_nested" / slug))
                    continue
                skill_md = native_entry / "skill.md"
                if not skill_md.exists():
                    continue
                if slug in AGENT_SLUGS or read_engine(skill_md) == "agent":
                    agents.append((native_entry, NEW_AGENTS_BASE / "_bundled" / slug))
                else:
                    skills.append((native_entry, NEW_SKILLS_BASE / "_bundled" / slug))
            continue

        if name.startswith("tenant_"):
            uuid = name[len("tenant_"):]
            # Aremko's tenant has a real binding; the 271e5a66 one is orphan.
            if uuid == "271e5a66-48cc-4885-b55b-cf5b5981b67e":
                archives.append((entry, NEW_SKILLS_BASE / "_archive" / "orphan_tenants" / uuid))
                continue
            for tenant_entry in sorted(entry.iterdir()):
                if not tenant_entry.is_dir():
                    continue
                slug = tenant_entry.name
                skill_md = tenant_entry / "skill.md"
                if not skill_md.exists():
                    continue
                target_root = NEW_AGENTS_BASE / "_tenant" / uuid if read_engine(skill_md) == "agent" else NEW_SKILLS_BASE / "_tenant" / uuid
                target = target_root / slug
                if read_engine(skill_md) == "agent":
                    agents.append((tenant_entry, target))
                else:
                    skills.append((tenant_entry, target))
            continue

        # Top-level slug — should be byte-identical with native/<slug>. Verify
        # before deleting; if it differs, treat as the source of truth.
        skill_md = entry / "skill.md"
        if not skill_md.exists():
            continue
        native_twin = OLD_SKILLS_BASE / "native" / name
        if native_twin.is_dir():
            # native/ already covered this — top-level is a delete candidate
            archives.append((entry, NEW_SKILLS_BASE / "_archive" / "_top_level_dups" / name))
        else:
            # native/ doesn't have it — promote it
            target_root = NEW_AGENTS_BASE / "_bundled" if read_engine(skill_md) == "agent" else NEW_SKILLS_BASE / "_bundled"
            target = target_root / name
            (agents if read_engine(skill_md) == "agent" else skills).append((entry, target))

    return agents, skills, archives


def move(src: Path, dst: Path, apply: bool):
    if not src.exists():
        return "missing"
    if dst.exists():
        return "exists"
    if apply:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
    return "moved" if apply else "would-move"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="actually move files (default: dry run)")
    args = parser.parse_args()

    agents, skills, archives = inventory()

    label = "APPLYING" if args.apply else "DRY RUN"
    print(f"\n=== {label} — skills layout migration ===\n")

    print(f"Agents to move: {len(agents)}")
    for src, dst in agents:
        status = move(src, dst, args.apply)
        print(f"  [{status}] {src.relative_to(ROOT)} -> {dst.relative_to(ROOT)}")

    print(f"\nSkills to move: {len(skills)}")
    for src, dst in skills:
        status = move(src, dst, args.apply)
        print(f"  [{status}] {src.relative_to(ROOT)} -> {dst.relative_to(ROOT)}")

    print(f"\nDirectories to archive: {len(archives)}")
    for src, dst in archives:
        status = move(src, dst, args.apply)
        kind = "tree" if src.is_dir() else "file"
        print(f"  [{status} {kind}] {src.relative_to(ROOT)} -> {dst.relative_to(ROOT)}")

    if not args.apply:
        print("\n(dry run — pass --apply to execute)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
