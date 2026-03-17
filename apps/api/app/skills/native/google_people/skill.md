---
name: "People Search"
engine: "python"
script_path: "script.py"
version: 1
category: general
tags: [person, people, linkedin, who, background, profile, bio, founder, ceo, executive, team, contact]
auto_trigger: >
  Look up a person's background, profile, or role. Triggers on:
  "who is X", "find X on LinkedIn", "X background", "X profile",
  "X LinkedIn", "what does X do", "where does X work", "X bio",
  "X founder", "X CEO", "look up X person", "find info on X person",
  "X experience", "X resume", "X career", "who runs X",
  "who founded X", "team behind X", "X contact info".
chain_to: [entity_extraction]
description: >
  Search for information about a specific person — their role, company,
  LinkedIn profile, background, and public information.
inputs:
  - name: person_name
    type: string
    description: "The person's name to look up."
    required: true
  - name: context
    type: string
    description: "Additional context like company name, role, or city to narrow results."
    required: false
  - name: num_results
    type: integer
    description: "Number of results (default: 5, max: 10)"
    required: false
---

## Description
Searches for a person's public profile, role, company, and background.

### When This Skill Triggers
- "Who is X?", "Find X on LinkedIn"
- "X background", "Where does X work?"
- "Who runs X company?", "X founder/CEO"
- Vague: "look up this person", "find X"
