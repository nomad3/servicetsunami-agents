#!/usr/bin/env python3
"""
Community Skills Import Registry
=================================
Documents the source repos and skill slugs for the 12 community skills
bundled into apps/api/app/skills/_bundled/.

These skills are manually curated — they are NOT auto-synced from upstream.
Rationale: upstream repos may change in breaking ways; we need stable skill
bodies in production. When you want to refresh a skill from upstream, use
this script as a reference and update the relevant skill.md manually.

Curation approach:
  1. Identify high-signal skills from community repos (obra/superpowers,
     angakh/claude-skills-starter, etc.)
  2. Copy the skill body verbatim or adapt it to AgentProvision conventions.
  3. Write frontmatter in the AgentProvision SKILL.md format (name, engine,
     version, category, tags, auto_trigger, source_repo).
  4. Place the file in apps/api/app/skills/_bundled/<slug>/skill.md.
  5. Record the source here so future maintainers can find upstream diffs.

To verify all skill files parse cleanly:
  python3 -c "
  import yaml, glob
  files = glob.glob('apps/api/app/skills/_bundled/*/skill.md')
  for f in files:
      parts = open(f).read().split('---')
      meta = yaml.safe_load(parts[1])
      print(f'{meta[\"name\"]:30s} engine={meta[\"engine\"]:10s} category={meta[\"category\"]}')
  print(f'Total: {len(files)} skills')
  "
"""

# Skill registry: slug -> source metadata
COMMUNITY_SKILLS = [
    {
        "slug": "writing-plans",
        "name": "writing-plans",
        "source_repo": "https://github.com/obra/superpowers",
        "source_file": "writing-plans.md",
        "category": "coding",
        "engine": "markdown",
        "notes": "Verbatim from obra/superpowers. Core TDD planning methodology.",
    },
    {
        "slug": "executing-plans",
        "name": "executing-plans",
        "source_repo": "https://github.com/obra/superpowers",
        "source_file": "executing-plans.md",
        "category": "coding",
        "engine": "markdown",
        "notes": "Verbatim from obra/superpowers. Companion to writing-plans.",
    },
    {
        "slug": "code-review",
        "name": "code-review",
        "source_repo": None,
        "source_file": None,
        "category": "coding",
        "engine": "markdown",
        "notes": "Community-derived. Correctness, security, design, test checklist.",
    },
    {
        "slug": "security-review",
        "name": "security-review",
        "source_repo": None,
        "source_file": None,
        "category": "coding",
        "engine": "markdown",
        "notes": "Community-derived. OWASP-focused deep security audit.",
    },
    {
        "slug": "smart-commit",
        "name": "smart-commit",
        "source_repo": "https://github.com/angakh/claude-skills-starter",
        "source_file": "smart-commit.md",
        "category": "coding",
        "engine": "markdown",
        "notes": "Adapted from angakh/claude-skills-starter. Quality-check then conventional commit.",
    },
    {
        "slug": "pr-create",
        "name": "pr-create",
        "source_repo": "https://github.com/angakh/claude-skills-starter",
        "source_file": "pr-create.md",
        "category": "coding",
        "engine": "markdown",
        "notes": "Adapted from angakh/claude-skills-starter. Rich PR description from git history.",
    },
    {
        "slug": "run-tests",
        "name": "run-tests",
        "source_repo": "https://github.com/angakh/claude-skills-starter",
        "source_file": "run-tests.md",
        "category": "coding",
        "engine": "markdown",
        "notes": "Adapted from angakh/claude-skills-starter. Multi-framework test runner.",
    },
    {
        "slug": "quality-gate",
        "name": "quality-gate",
        "source_repo": "https://github.com/angakh/claude-skills-starter",
        "source_file": "quality-gate.md",
        "category": "coding",
        "engine": "markdown",
        "notes": "Adapted from angakh/claude-skills-starter. Lint + typecheck + tests pipeline.",
    },
    {
        "slug": "dep-audit",
        "name": "dep-audit",
        "source_repo": "https://github.com/angakh/claude-skills-starter",
        "source_file": "dep-audit.md",
        "category": "devops",
        "engine": "markdown",
        "notes": "Adapted from angakh/claude-skills-starter. CVE + outdated package audit.",
    },
    {
        "slug": "project-overview",
        "name": "project-overview",
        "source_repo": "https://github.com/angakh/claude-skills-starter",
        "source_file": "project-overview.md",
        "category": "productivity",
        "engine": "markdown",
        "notes": "Adapted from angakh/claude-skills-starter. Session startup context snapshot.",
    },
    {
        "slug": "scaffold",
        "name": "scaffold",
        "source_repo": "https://github.com/angakh/claude-skills-starter",
        "source_file": "scaffold.md",
        "category": "coding",
        "engine": "markdown",
        "notes": (
            "Adapted from angakh/claude-skills-starter. "
            "Boilerplate generator for React, FastAPI, SQLAlchemy, tests, CLI."
        ),
    },
    {
        "slug": "design-pipeline",
        "name": "design-pipeline",
        "source_repo": "https://github.com/angakh/claude-skills-starter",
        "source_file": "design-pipeline.md",
        "category": "productivity",
        "engine": "markdown",
        "notes": (
            "Adapted from angakh/claude-skills-starter. "
            "PRD → user stories → UX spec → prototype plan."
        ),
    },
]


def verify_skills(bundled_dir: str = "apps/api/app/skills/_bundled") -> None:
    """Parse all skill.md files and verify they are executable.

    Checks:
    - Valid YAML frontmatter
    - Required fields (name, engine, category)
    - Executable source exists:
        markdown  → skill.md body (frontmatter stripped) or explicit script_path
        python    → script.py (or script_path override)
        shell     → script.sh (or script_path override)
        tool      → tool_class defined
    """
    import glob
    import os
    import yaml

    files = sorted(glob.glob(f"{bundled_dir}/*/skill.md"))
    errors: list[str] = []
    print(f"{'Slug':30s} {'Engine':12s} {'Category':20s} {'Exec source':25s} {'Status'}")
    print("-" * 115)
    for f in files:
        skill_dir = os.path.dirname(f)
        raw = open(f).read()
        parts = raw.split("---", 2)
        if len(parts) < 3:
            msg = f"  ERROR: {f} — invalid frontmatter (missing closing ---)"
            print(msg); errors.append(msg)
            continue
        try:
            meta = yaml.safe_load(parts[1])
            engine = meta.get("engine", "python")
            default_script = "skill.md" if engine == "markdown" else "script.py"
            script_path = meta.get("script_path", default_script)
            source = meta.get("source_repo", "(internal)") or "(internal)"

            # Verify executable source
            if engine == "markdown":
                src = os.path.join(skill_dir, script_path)
                exec_src = script_path if os.path.exists(src) else "skill.md (body)"
                # skill.md always exists; check body has content after frontmatter
                body = parts[2].strip()
                if not body:
                    raise ValueError("markdown skill has empty body in skill.md")
                status = "OK"
            elif engine == "tool":
                exec_src = meta.get("tool_class", "(missing)")
                status = "OK" if meta.get("tool_class") else "ERROR: tool_class missing"
                if "ERROR" in status:
                    errors.append(f"{f}: {status}")
            else:
                src = os.path.join(skill_dir, script_path)
                exec_src = script_path
                if os.path.exists(src):
                    status = "OK"
                else:
                    status = f"ERROR: {script_path} not found"
                    errors.append(f"{f}: {status}")

            print(
                f"{meta['name']:30s} {engine:12s} {meta['category']:20s} {exec_src:25s} {status}"
            )
        except Exception as e:
            msg = f"  ERROR: {f} — {e}"
            print(msg); errors.append(msg)

    print(f"\nTotal: {len(files)} skills")
    if errors:
        print(f"\n{len(errors)} error(s) found:")
        for e in errors:
            print(f"  {e}")
        raise SystemExit(1)
    else:
        print("All skills verified OK.")


if __name__ == "__main__":
    import sys

    if "--verify" in sys.argv:
        verify_skills()
    else:
        print(__doc__)
        print(f"\nRegistered community skills: {len(COMMUNITY_SKILLS)}")
        for skill in COMMUNITY_SKILLS:
            repo = skill["source_repo"] or "(community-derived)"
            print(f"  {skill['slug']:25s} <- {repo}")
        print("\nRun with --verify to parse all skill.md files.")
