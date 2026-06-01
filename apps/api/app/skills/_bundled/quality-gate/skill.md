---
name: quality-gate
engine: markdown
version: 1
category: coding
tags: [lint, typecheck, tests, quality, ci]
auto_trigger: "Use when running the full quality pipeline before committing or merging"
source_repo: https://github.com/angakh/claude-skills-starter
---

## Description
Run the full quality pipeline — lint, typecheck, tests — in sequence, stopping at the first failure.

# Quality Gate

## Overview

Run the full quality pipeline: lint → typecheck → tests, in sequence. Stop at the first failure.

**Announce at start:** "Running quality gate checks."

## Sequence

### Stage 1: Lint
**Python:**
```bash
ruff check app/          # or flake8/pylint if ruff not available
```
**Node.js:**
```bash
npm run lint             # or npx eslint src/
```

If lint fails: report all issues, stop. Do not proceed to typecheck.

### Stage 2: Type Check
**Python (mypy):**
```bash
mypy app/ --ignore-missing-imports
```
**TypeScript:**
```bash
npx tsc --noEmit
```

If type errors found: report them, stop. Do not proceed to tests.

### Stage 3: Tests
Run the full test suite (see `run-tests` skill for framework detection).

## Result Summary

For each stage: PASS or FAIL + count of issues.

If all pass:
```
Quality gate passed: lint (0 issues) | types (0 errors) | tests (N passed)
```

If any fail:
```
Quality gate failed at <stage>: <issue count> issues found
[list issues]
```
