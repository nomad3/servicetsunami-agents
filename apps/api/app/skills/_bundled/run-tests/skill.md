---
name: run-tests
engine: markdown
version: 1
category: coding
tags: [testing, pytest, jest, coverage, quality]
auto_trigger: "Use when running the test suite or checking test coverage"
source_repo: https://github.com/angakh/claude-skills-starter
---

## Description
Auto-detect the project's test framework and run the full test suite with coverage reporting.

# Run Tests

## Overview

Auto-detect the project's test framework and run the full test suite with coverage.

**Announce at start:** "Running test suite."

## Detection Logic

Detect the framework by checking for these markers (in order):
1. `pytest.ini` or `pyproject.toml` with `[tool.pytest]` → Python/pytest
2. `package.json` with `"jest"` or `"vitest"` → Node.js
3. `Cargo.toml` → Rust (`cargo test`)
4. `go.mod` → Go (`go test ./...`)

## Execution

### Python / pytest
```bash
pytest --tb=short -q
# With coverage:
pytest --cov=app --cov-report=term-missing -q
```

### Node.js / Jest
```bash
npm test -- --ci --watchAll=false --coverage
```

### Node.js / Vitest
```bash
npx vitest run --coverage
```

### Rust
```bash
cargo test -- --test-output immediate
```

### Go
```bash
go test ./... -v -cover
```

## Output Report

After running, summarize:
- Pass/fail count
- Coverage percentage (if available)
- Any failing tests: test name + error message + file:line
- Suggested fixes for common failures (import errors, missing fixtures, etc.)
