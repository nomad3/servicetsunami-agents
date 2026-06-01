---
name: writing-plans
engine: markdown
version: 1
category: coding
tags: [planning, tdd, implementation, tasks]
auto_trigger: "Use when creating an implementation plan for a multi-step feature or task"
source_repo: https://github.com/obra/superpowers
---

## Description
Create comprehensive, bite-sized implementation plans with full file structure mapping, TDD steps, and zero placeholders.

# Writing Plans

## Overview

Write comprehensive implementation plans assuming the engineer has zero context for our codebase and questionable taste. Document everything they need to know: which files to touch for each task, code, testing, docs they might need to check, how to test it. Give them the whole plan as bite-sized tasks. DRY. YAGNI. TDD. Frequent commits.

Assume they are a skilled developer, but know almost nothing about our toolset or problem domain. Assume they don't know good test design very well.

**Announce at start:** "I'm using the writing-plans skill to create the implementation plan."

## Scope Check

If the spec covers multiple independent subsystems, suggest breaking this into separate plans — one per subsystem. Each plan should produce working, testable software on its own.

## File Structure

Before defining tasks, map out which files will be created or modified and what each one is responsible for.

- Design units with clear boundaries and well-defined interfaces.
- Prefer smaller, focused files over large ones that do too much.
- Files that change together should live together.
- In existing codebases, follow established patterns.

## Bite-Sized Task Granularity

**Each step is one action (2-5 minutes):**
- "Write the failing test" — step
- "Run it to make sure it fails" — step
- "Implement the minimal code to make the test pass" — step
- "Run the tests and make sure they pass" — step
- "Commit" — step

## Task Structure

Each task block:
- State exact file paths to create/modify/test
- Provide actual code in every step (no placeholders)
- Provide exact commands with expected output
- Use checkbox syntax for tracking: `- [ ] Step N: ...`

## No Placeholders

Never write: "TBD", "TODO", "implement later", "add error handling", "write tests for the above" without actual test code, "similar to Task N".

## Self-Review

After writing the plan:
1. Spec coverage: Can you point to a task for every requirement?
2. Placeholder scan: Any red flags from the No Placeholders section?
3. Type consistency: Do signatures in later tasks match earlier definitions?

Fix inline. Then save the plan to `docs/plans/YYYY-MM-DD-<feature-name>.md`.
