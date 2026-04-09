# Commitment Classifier F1 Evaluation

**Date:** 2026-04-08 23:46:52

**Result:** F1 = 0.722

**Acceptance:** ✅ PASS (target ≥ 0.7)

## Metrics
```json
{
  "n": 194,
  "tp": 48,
  "fp": 1,
  "tn": 109,
  "fn": 36,
  "precision": 0.98,
  "recall": 0.571,
  "f1": 0.722,
  "elapsed_seconds": 203.6,
  "elapsed_per_example_ms": 1049.0,
  "misclassified_count": 37
}
```

## Sample misclassifications (first 20)
- `Can we circle back on this topic next week?` actual=1 predicted=0 conf=0.80
- `Our next check-in is slated for Thursday afternoon.` actual=1 predicted=0 conf=0.90
- `The paperwork needs to be filed by Friday at the latest.` actual=1 predicted=0 conf=0.90
- `Search my Google Drive for any recent documents` actual=1 predicted=0 conf=0.90
- `Scan the website and check yourself` actual=1 predicted=0 conf=0.90
- `We did changes over the codebase check latest commits` actual=1 predicted=0 conf=0.90
- `ok now let’s review PR 87 as well` actual=1 predicted=0 conf=0.90
- `Let’s check competitors to my other company aremko.cl` actual=1 predicted=0 conf=0.90
- `Let’s change the copy of that landings pages to be more accurate with the distributed agent network ` actual=1 predicted=0 conf=0.90
- `i think i fixed the issue check` actual=1 predicted=0 conf=0.90
- `check token usage again by tenant` actual=1 predicted=0 conf=0.90
- `You did the plan already, and I’m going to start developing it` actual=1 predicted=0 conf=0.90
- `tell me everything you know about me` actual=1 predicted=0 conf=1.00
- `See if your memory graph is being populated correctly and the RL system is working as expected` actual=1 predicted=0 conf=0.95
- `Extract all contacts from my personal Gmail and store them as entity with their contact details and ` actual=1 predicted=0 conf=1.00
- `It went through cleanly. The fix is merged on `main` as PR #18 on March 20, 2026, and the actual cha` actual=0 predicted=1 conf=0.90
- `Jira's not connected yet. Head to **ServiceTsunami → Settings → Connected Apps** and link your Jira ` actual=1 predicted=0 conf=0.90
- `Make me a prompt how you would like to be if you were human so I can ask Gemini or ChatGPT to draw i` actual=1 predicted=0 conf=1.00
- `Which medicines are you looking for? You didn't mention them — send me the list and I'll check which` actual=1 predicted=0 conf=0.90
- `Yes don’t use the coding one use general model you suggested` actual=1 predicted=0 conf=1.00
