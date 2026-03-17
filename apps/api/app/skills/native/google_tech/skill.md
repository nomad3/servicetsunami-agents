---
name: "Tech & Code Search"
engine: "python"
script_path: "script.py"
version: 1
category: coding
tags: [code, error, stackoverflow, github, documentation, docs, api, debug, bug, programming, library, framework, package]
auto_trigger: >
  Search for technical documentation, code solutions, or error fixes. Triggers on:
  "how to fix error X", "X documentation", "X API reference",
  "X stack overflow", "X github", "X npm package", "X python library",
  "debug X", "X not working", "X throws error", "X example code",
  "X best practices", "X tutorial programming", "X code snippet",
  "why does X give error Y", "X deprecated alternative",
  "X vs Y framework", "X migration guide", "X changelog".
chain_to: []
description: >
  Search for technical docs, code solutions, error fixes, and API references.
  Prioritizes StackOverflow, GitHub, and official docs.
inputs:
  - name: query
    type: string
    description: "Technical question, error message, or documentation topic."
    required: true
  - name: language
    type: string
    description: "Programming language context (e.g., 'python', 'javascript')"
    required: false
  - name: num_results
    type: integer
    description: "Number of results (default: 5, max: 10)"
    required: false
---

## Description
Technical search optimized for code, errors, docs, and Stack Overflow.

### When This Skill Triggers
- "How to fix error X", "X documentation"
- "X API reference", "X not working"
- "X example code", "X best practices"
- Vague: "help with X error", "X broken"
