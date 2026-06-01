---
name: project-overview
engine: markdown
version: 1
category: productivity
tags: [overview, context, git, project-state, setup]
auto_trigger: "Use at the start of a work session to get a full snapshot of the project state"
source_repo: https://github.com/angakh/claude-skills-starter
---

## Description
Pre-compute a comprehensive snapshot of the project state — git status, structure, test health, recent activity — before starting work.

# Project Overview

## Overview

Pre-compute a comprehensive snapshot of the project state before starting work. Eliminates the "let me look around" exploration phase.

**Announce at start:** "Computing project overview."

## What to Gather

### Git State
```bash
git status --short                      # uncommitted changes
git log --oneline -10                   # recent commits
git branch -a | head -20                # branches
git stash list                          # stashed work
```

### Project Structure
```bash
ls -la                                  # root contents
cat package.json | head -30            # Node deps/scripts
cat pyproject.toml 2>/dev/null | head -30  # Python config
cat requirements.txt 2>/dev/null | head -20
```

### Test & Build Status
Run tests (abbreviated): `pytest -q --no-header 2>&1 | tail -5`
Or: `npm test -- --ci --watchAll=false 2>&1 | tail -10`

### Recent Activity
```bash
git log --since="7 days ago" --oneline  # last week's commits
git diff HEAD~5 --stat                  # recent file changes
```

## Output Format

Summarize as:
```
PROJECT: <name>
BRANCH: <current branch> | <N> uncommitted changes
LAST COMMITS: <last 5 one-liners>
TEST STATUS: <pass/fail + count>
HOT FILES: <most recently changed files>
OPEN STASHES: <count>
NOTES: <anything unusual — merge conflicts, detached HEAD, etc.>
```

Hand this context to the user before diving into a task.
