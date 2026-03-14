---
auto_trigger: 'Google Workflow: Today''s meetings + open tasks as a standup summary.'
category: productivity
description: 'Google Workflow: Today''s meetings + open tasks as a standup summary.'
engine: markdown
name: gws-workflow-standup-report
requires:
  bins:
  - gws
source_repo: https://github.com/googleworkspace/cli
tags:
- workflow
- standup
- report
version: 1
---

# workflow +standup-report

> **PREREQUISITE:** Read `../gws-shared/SKILL.md` for auth, global flags, and security rules. If missing, run `gws generate-skills` to create it.

Today's meetings + open tasks as a standup summary

## Usage

```bash
gws workflow +standup-report
```

## Flags

| Flag | Required | Default | Description |
|------|----------|---------|-------------|
| `--format` | — | — | Output format: json (default), table, yaml, csv |

## Examples

```bash
gws workflow +standup-report
gws workflow +standup-report --format table
```

## Tips

- Read-only — never modifies data.
- Combines calendar agenda (today) with tasks list.

## See Also

- [gws-shared](../gws-shared/SKILL.md) — Global flags and auth
- [gws-workflow](../gws-workflow/SKILL.md) — All cross-service productivity workflows commands
