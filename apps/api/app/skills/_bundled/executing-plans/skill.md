---
name: executing-plans
engine: markdown
version: 1
category: coding
tags: [planning, execution, tdd, tasks, implementation]
auto_trigger: "Use when you have a written implementation plan to execute step by step"
source_repo: https://github.com/obra/superpowers
---

## Description
Load a written implementation plan, review it critically, execute all tasks in sequence, and report completion.

# Executing Plans

## Overview

Load plan, review critically, execute all tasks, report when complete.

**Announce at start:** "I'm using the executing-plans skill to implement this plan."

## The Process

### Step 1: Load and Review Plan
1. Read plan file
2. Review critically — identify any questions or concerns
3. If concerns: raise them before starting
4. If no concerns: proceed

### Step 2: Execute Tasks

For each task:
1. Mark as in_progress
2. Follow each step exactly (plan has bite-sized steps)
3. Run verifications as specified
4. Mark as completed

### Step 3: Complete Development

After all tasks complete:
- Verify tests pass
- Present summary of what was built and how to test it

## When to Stop and Ask

**STOP executing immediately when:**
- Hit a blocker (missing dependency, test fails, instruction unclear)
- Plan has critical gaps preventing starting
- Verification fails repeatedly

Ask for clarification rather than guessing.

## Remember
- Review plan critically first
- Follow plan steps exactly
- Don't skip verifications
- Stop when blocked — never guess through a blocker
- Never start implementation on main/master branch without explicit user consent
