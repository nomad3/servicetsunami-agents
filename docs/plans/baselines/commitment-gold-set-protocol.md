# Commitment Classifier Gold Set — Labeling Protocol

This document defines what counts as a "commitment" for the Gemma4
classifier evaluation. The gold set lives at
`apps/api/tests/fixtures/commitment_gold_set.jsonl` (200 examples,
100 real + 100 synthetic).

A **commitment** is a statement where THE SPEAKER (user or assistant)
commits THEMSELVES OR SOMEONE ELSE to a future action with a specific
or implicit deadline.

## True commitment (label: 1)

- "I'll send you the report by Friday" — explicit, dated, first-person
- "Luna, follow up with Ray tomorrow" — directive to assistant
- "We need to ship this before the merge freeze" — first-person plural, dated
- "I promise I'll review the PR tonight" — explicit promise
- "Voy a llamar al cliente mañana" — Spanish, explicit, dated
- "Confirmed for 3pm Thursday" — meeting confirmation
- "I owe you that doc" — obligation acknowledgment

## NOT a commitment (label: 0)

- "Ray usually sends reports on Fridays" — third-person description
- "It would be nice to ship before the freeze" — wish, not commitment
- "I sent the report yesterday" — past tense
- "What if we shipped on Friday?" — hypothetical / question
- "Gap 3 is about commitment tracking" — meta-discussion of the feature
- "The commitment record table has 47 rows" — describing data
- "I'm thinking about reviewing the PR" — intent without commitment

## Edge cases (label carefully)

- "I'll try to get to it" — soft. Label as 0 unless paired with deadline.
- "Maybe tomorrow" — hedged. Label 0.
- "I should probably review this" — soft intent. Label 0.
- "Done" / "OK will do" — only label 1 if responding to a clear ask.

## JSONL format

Each line is one example:

```json
{
  "text": "<message text>",
  "role": "user|assistant",
  "label": 0|1,
  "title": "<short title — only if label=1>",
  "due_at": "<ISO datetime — only if explicit, else null>",
  "type": "action|delivery|response|meeting",
  "source": "real|synthetic",
  "labeled_by": "<who labeled this>"
}
```

## Acceptance gate

Phase 1 plan §11.1 anti-success criterion #3: F1 ≥ 0.7 to ship.
F1 < 0.7 → STOP and surface to user. Do not retire commitment_extractor.py.
