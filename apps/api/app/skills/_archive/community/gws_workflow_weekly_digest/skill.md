---
auto_trigger: 'Google Workflow: Weekly summary: this week''s meetings + unread email
  count.'
category: productivity
description: 'Google Workflow: Weekly summary: this week''s meetings + unread email
  count.'
engine: markdown
name: gws-workflow-weekly-digest
requires:
  bins:
  - gws
source_repo: https://github.com/googleworkspace/cli
tags:
- workflow
- weekly
- digest
version: 1
---

# workflow +weekly-digest

> **PREREQUISITE:** Read `../gws-shared/SKILL.md` for auth, global flags, and security rules. If missing, run `gws generate-skills` to create it.

Weekly summary: this week's meetings + unread email count

## Usage

```bash
gws workflow +weekly-digest
```

## Flags

| Flag | Required | Default | Description |
|------|----------|---------|-------------|
| `--format` | — | — | Output format: json (default), table, yaml, csv |

## Examples

```bash
gws workflow +weekly-digest
gws workflow +weekly-digest --format table
```

## Tips

- Read-only — never modifies data.
- Combines calendar agenda (week) with gmail triage summary.

## See Also

- [gws-shared](../gws-shared/SKILL.md) — Global flags and auth
- [gws-workflow](../gws-workflow/SKILL.md) — All cross-service productivity workflows commands
