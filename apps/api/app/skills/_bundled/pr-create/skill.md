---
name: pr-create
engine: markdown
version: 1
category: coding
tags: [git, pr, github, pull-request]
auto_trigger: "Use when creating a pull request with a well-structured description"
source_repo: https://github.com/angakh/claude-skills-starter
---

## Description
Create a pull request with a rich, auto-generated description from git history and diff.

# PR Create

## Overview

Create a pull request with a rich, auto-generated description from git history and diff.

**Announce at start:** "Creating pull request."

## Process

### Step 1: Gather Context
```bash
git log main..HEAD --oneline          # commits on this branch
git diff main...HEAD --stat           # files changed
git diff main...HEAD                  # full diff for summary
```

### Step 2: Confirm Branch is Clean
- Run tests: if they fail, stop and report
- Ensure all intended files are committed

### Step 3: Push Branch
```bash
git push origin <branch-name> -u
```

### Step 4: Draft PR Description
Structure:
```markdown
## Summary
- <bullet: what this PR does>
- <bullet: why>

## Changes
- <file or component>: <what changed and why>

## Test Plan
- [ ] <how to verify the happy path>
- [ ] <how to verify edge cases>

## Notes
<anything reviewers should know: migration needed, feature flag, breaking change>
```

### Step 5: Create PR
```bash
gh pr create \
  --title "<type>: <concise title>" \
  --body "<description from Step 4>"
```

### Step 6: Report
Return the PR URL.
