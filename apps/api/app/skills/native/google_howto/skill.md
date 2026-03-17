---
name: "How-To Search"
engine: "python"
script_path: "script.py"
version: 1
category: general
tags: [howto, tutorial, guide, steps, instructions, learn, fix, solve, setup, install, configure, diy]
auto_trigger: >
  Find how-to guides, tutorials, or step-by-step instructions. Triggers on:
  "how do I X", "how to X", "tutorial for X", "guide to X", "steps to X",
  "instructions for X", "teach me X", "learn X", "fix X", "solve X",
  "setup X", "install X", "configure X", "DIY X", "what are the steps to X",
  "walk me through X", "show me how to X", "best way to X",
  "beginner guide to X", "X for beginners", "getting started with X".
chain_to: []
description: >
  Search for tutorials, how-to guides, and step-by-step instructions.
  Optimizes queries with tutorial/guide keywords.
inputs:
  - name: task
    type: string
    description: "What you want to learn how to do."
    required: true
  - name: skill_level
    type: string
    description: "Difficulty: 'beginner', 'intermediate', 'advanced' (default: none)"
    required: false
  - name: num_results
    type: integer
    description: "Number of results (default: 5, max: 10)"
    required: false
---

## Description
Searches for tutorials, guides, and how-to instructions.

### When This Skill Triggers
- "How do I X?", "How to X", "Tutorial for X"
- "Steps to X", "Guide to X", "Teach me X"
- "Fix X", "Setup X", "Install X"
- Vague: "I want to learn X", "Getting started with X"
