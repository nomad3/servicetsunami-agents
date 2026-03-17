---
name: "Job Search"
engine: "python"
script_path: "script.py"
version: 1
category: general
tags: [jobs, hiring, career, remote, position, vacancy, employment, work, salary, apply, role, opportunity, openings]
auto_trigger: >
  Search for job listings or career opportunities. Triggers on:
  "find jobs for X", "X job openings", "remote X jobs", "hiring X",
  "X positions available", "X salary", "X career opportunities",
  "companies hiring X", "job boards for X", "apply for X",
  "X jobs near me", "X remote positions", "freelance X opportunities",
  "X contract work", "who is hiring X", "looking for X work",
  "X job market", "internship for X", "entry level X jobs".
chain_to: [entity_extraction]
description: >
  Search for job listings, openings, and career opportunities.
  Searches across job boards and company career pages.
inputs:
  - name: role
    type: string
    description: "Job title or skill (e.g., 'DevOps Engineer', 'Python developer')."
    required: true
  - name: location
    type: string
    description: "Location preference: city name or 'remote' (default: 'remote')"
    required: false
  - name: experience_level
    type: string
    description: "Level: 'entry', 'mid', 'senior', 'lead' (default: none)"
    required: false
  - name: num_results
    type: integer
    description: "Number of results (default: 7, max: 10)"
    required: false
---

## Description
Job search across major boards and company career pages.

### When This Skill Triggers
- "Find X jobs", "Remote X positions"
- "Who's hiring for X?", "X salary range"
- "Companies hiring X near me"
- Vague: "looking for work in X", "any X openings?"
