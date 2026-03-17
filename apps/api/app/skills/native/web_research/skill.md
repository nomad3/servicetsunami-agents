---
name: "Web Research"
engine: "python"
script_path: "script.py"
version: 1
category: general
tags: [research, deep, investigate, analysis, compare, review, competitive, market, due-diligence, report]
auto_trigger: >
  Perform deep web research on a topic, person, or company. Use when the user wants
  a thorough investigation — "research this company", "deep dive on X",
  "investigate X", "tell me everything about X", "competitive analysis of X",
  "due diligence on X", "compare X vs Y", "market research on X".
chain_to: [entity_extraction]
description: >
  Multi-query web research. Generates multiple search angles, fetches results,
  and compiles a structured research brief.
inputs:
  - name: topic
    type: string
    description: "The topic, person, company, or question to research."
    required: true
  - name: angles
    type: string
    description: "Comma-separated research angles (e.g., 'pricing,reviews,competitors'). Auto-generated if not provided."
    required: false
  - name: max_results_per_angle
    type: integer
    description: "Results per angle (default: 3)"
    required: false
---

## Description
Deep web research with multiple search angles compiled into a brief.

### When This Skill Triggers
- "Research Acme Corp", "Deep dive on X", "Tell me everything about X"
- "Is X legit?", "Due diligence on X", "Compare X vs Y"
- "Market size for X", "X alternatives", "Pros and cons of X"
