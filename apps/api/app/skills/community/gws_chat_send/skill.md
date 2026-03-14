---
auto_trigger: 'Google Chat: Send a message to a space.'
category: productivity
description: 'Google Chat: Send a message to a space.'
engine: markdown
name: gws-chat-send
requires:
  bins:
  - gws
source_repo: https://github.com/googleworkspace/cli
tags:
- chat
- send
version: 1
---

# chat +send

> **PREREQUISITE:** Read `../gws-shared/SKILL.md` for auth, global flags, and security rules. If missing, run `gws generate-skills` to create it.

Send a message to a space

## Usage

```bash
gws chat +send --space <NAME> --text <TEXT>
```

## Flags

| Flag | Required | Default | Description |
|------|----------|---------|-------------|
| `--space` | ✓ | — | Space name (e.g. spaces/AAAA...) |
| `--text` | ✓ | — | Message text (plain text) |

## Examples

```bash
gws chat +send --space spaces/AAAAxxxx --text 'Hello team!'
```

## Tips

- Use 'gws chat spaces list' to find space names.
- For cards or threaded replies, use the raw API instead.

> [!CAUTION]
> This is a **write** command — confirm with the user before executing.

## See Also

- [gws-shared](../gws-shared/SKILL.md) — Global flags and auth
- [gws-chat](../gws-chat/SKILL.md) — All manage chat spaces and messages commands
