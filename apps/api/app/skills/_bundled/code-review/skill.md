---
name: code-review
engine: markdown
version: 1
category: coding
tags: [review, quality, bugs, security, style]
auto_trigger: "Use when reviewing code changes, PRs, or asking for feedback on modified files"
---

## Description
Systematic review of changed code for correctness, maintainability, security, and style. Reviews staged changes or a specified diff.

# Code Review

## Overview

Systematic review of changed code for correctness, maintainability, security, and style. Reviews staged changes or a specified diff.

**Announce at start:** "Running code review on the changes."

## Review Checklist

### Correctness
- [ ] Logic is correct — no off-by-one errors, wrong conditions, missed edge cases
- [ ] Error paths are handled — exceptions caught, errors returned, not silently swallowed
- [ ] Concurrency is safe — no race conditions, shared state is properly guarded
- [ ] Data is validated at system boundaries (user input, external APIs)

### Security
- [ ] No SQL injection vectors (parameterized queries used)
- [ ] No XSS vectors (user input escaped before rendering)
- [ ] No secrets hardcoded or logged
- [ ] Auth checks present on new endpoints
- [ ] No path traversal in file operations

### Design
- [ ] No unnecessary complexity introduced
- [ ] No duplicate logic (DRY)
- [ ] No premature abstractions (YAGNI)
- [ ] Functions/methods have single, clear responsibility
- [ ] Naming is descriptive and consistent with existing codebase

### Tests
- [ ] New code has test coverage
- [ ] Tests cover the happy path AND key failure modes
- [ ] Tests are not just checking implementation details (test behavior, not internals)

## Output Format

For each issue found:
1. **File + line**: exact location
2. **Severity**: blocking | suggestion | nit
3. **Issue**: what's wrong
4. **Fix**: concrete suggestion or code snippet

End with a summary: blocking issues count, suggestions count, overall verdict.
