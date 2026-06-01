---
name: smart-commit
engine: markdown
version: 1
category: coding
tags: [git, commit, quality, conventional-commits]
auto_trigger: "Use when ready to commit changes with quality checks and a good commit message"
source_repo: https://github.com/angakh/claude-skills-starter
---

## Description
Run quality checks, then stage and commit with a well-formed conventional commit message.

# Smart Commit

## Overview

Run quality checks, then stage and commit with a well-formed conventional commit message.

**Announce at start:** "Running smart-commit workflow."

## Process

### Step 1: Quality Check
Before committing, verify the code is in a good state:
- Run the test suite: `pytest` (Python) or `npm test -- --ci --watchAll=false` (Node) or the project's test command
- Run linter: `ruff check` (Python) or `npm run lint` (Node)
- If either fails: stop and report the failure. Do NOT commit broken code.

### Step 2: Stage Files
Stage only the files relevant to the change:
```bash
git add <specific files>  # never git add -A blindly
git status               # confirm what's staged
```
Never stage: `.env` files, credentials, large binaries, or files with sensitive data.

### Step 3: Craft Commit Message
Use conventional commits format:
- `feat:` — new feature
- `fix:` — bug fix
- `chore:` — maintenance, deps, config
- `docs:` — documentation only
- `test:` — test additions or changes
- `refactor:` — code change with no behavior change

Message structure:
```
<type>(<optional scope>): <imperative summary under 72 chars>

<optional body: why, not what>
```

### Step 4: Commit
```bash
git commit -m "<message>"
```

### Step 5: Report
State what was committed, the files changed, and the commit hash.
