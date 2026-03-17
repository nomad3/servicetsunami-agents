---
name: "Fact Check Search"
engine: "python"
script_path: "script.py"
version: 1
category: general
tags: [factcheck, verify, true, false, myth, debunk, scam, legit, real, fake, hoax, rumor, claim, proof]
auto_trigger: >
  Verify claims, check facts, or detect scams. Triggers on:
  "is it true that X", "verify X", "fact check X", "is X legit",
  "is X a scam", "is X real or fake", "debunk X", "X myth",
  "X hoax", "X rumor", "can you confirm X", "prove X",
  "is this true", "sounds too good to be true", "X trustworthy",
  "should I trust X", "X reviews complaints", "X BBB rating",
  "is X safe", "X red flags".
chain_to: [entity_extraction]
description: >
  Search for fact-checks, scam reports, and claim verification.
  Prioritizes fact-checking sites and review sources.
inputs:
  - name: claim
    type: string
    description: "The claim, company, or thing to verify."
    required: true
  - name: num_results
    type: integer
    description: "Number of results (default: 5, max: 10)"
    required: false
---

## Description
Fact-checking and claim verification search.

### When This Skill Triggers
- "Is it true that X?", "Verify X"
- "Is X a scam?", "Is X legit?"
- "Fact check this", "Debunk X"
- Vague: "this sounds fishy", "should I trust X?"
