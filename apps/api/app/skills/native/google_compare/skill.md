---
name: "Comparison Search"
engine: "python"
script_path: "script.py"
version: 1
category: general
tags: [compare, vs, versus, better, difference, alternative, which, best, top, ranking, benchmark, review]
auto_trigger: >
  Compare two or more things, find alternatives, or determine which is best. Triggers on:
  "X vs Y", "X versus Y", "compare X and Y", "which is better X or Y",
  "difference between X and Y", "X alternative", "best X for Y",
  "top X tools", "X or Y", "should I use X or Y", "X compared to Y",
  "pros and cons of X vs Y", "ranking of X", "benchmark X vs Y",
  "X review vs Y review", "switch from X to Y".
chain_to: []
description: >
  Search for comparisons, alternatives, and head-to-head reviews between
  products, tools, services, or concepts.
inputs:
  - name: items
    type: string
    description: "What to compare (e.g., 'React vs Vue', 'iPhone vs Samsung'). Can be two items or a category."
    required: true
  - name: criteria
    type: string
    description: "What matters most (e.g., 'performance', 'price', 'ease of use')"
    required: false
  - name: num_results
    type: integer
    description: "Number of results (default: 5, max: 10)"
    required: false
---

## Description
Comparison and alternative search for head-to-head evaluations.

### When This Skill Triggers
- "X vs Y", "Compare X and Y"
- "Which is better, X or Y?", "Difference between X and Y"
- "Best alternative to X", "Top X tools"
- Vague: "should I switch to X?", "X or Y?"
