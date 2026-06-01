---
name: design-pipeline
engine: markdown
version: 1
category: productivity
tags: [design, prd, ux, user-stories, prototype, product]
auto_trigger: "Use when designing a new feature from scratch: PRD, user stories, UX spec, prototype"
source_repo: https://github.com/angakh/claude-skills-starter
---

## Description
Turn a product idea into a structured design artifact: PRD, user stories, UX spec, and interactive prototype plan.

# Design Pipeline

## Overview

Turn a product idea into a structured design artifact: PRD → user stories → UX spec → interactive prototype plan.

**Announce at start:** "Starting design pipeline."

## Stage 1: PRD (Product Requirements Document)

Extract from the user's description:
- **Problem statement**: what user pain does this solve?
- **Target users**: who will use this?
- **Success metrics**: how do we know this worked?
- **Scope**: what's in v1 vs. later?
- **Non-goals**: what are we explicitly NOT building?

Output a one-page PRD. Get confirmation before proceeding.

## Stage 2: User Stories

Convert PRD into user stories:
```
As a <user type>, I want to <action> so that <benefit>.
Acceptance criteria:
- [ ] <testable condition>
- [ ] <testable condition>
```

Prioritize: Must Have / Should Have / Could Have.

## Stage 3: UX Spec

For each must-have story:
- Screen/component name
- User journey: entry point → interactions → exit
- Key UI elements
- Error states and empty states
- Mobile vs. desktop considerations

## Stage 4: Prototype Plan

Define what to build as a proof of concept:
- Minimum screens/flows to validate the core hypothesis
- Which real data to use vs. mock
- How to make it interactive (React, Figma, static HTML)
- What questions this prototype should answer

## Output

Save to `docs/plans/YYYY-MM-DD-<feature-name>-design.md`. Include all four stages as sections.
