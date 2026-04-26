---
name: HCA Deal Intelligence
engine: markdown
version: 1
category: sales
tags: [deals, M&A, scoring, investment]
auto_trigger: "Score companies on sell-likelihood for middle-market M&A advisory"
inputs:
  - name: entity_id
    type: string
    description: "Knowledge entity UUID to score"
    required: true
---

## Description

Score companies 0-100 on sell-likelihood for middle-market M&A advisory.

## Scoring Rubric (0-100 total, weighted)

| Category | Weight | Max Points | What to look for |
|---|---|---|---|
| ownership_succession | 0.30 | 30 | Owner age 55+, years in business 20+, no visible succession plan, owner reducing involvement, key person risk |
| market_timing | 0.25 | 25 | Industry M&A activity trending up, multiples at cycle highs, competitor exits, industry consolidation, regulatory sell pressure |
| company_performance | 0.20 | 20 | Revenue plateau after strong run, revenue $10M-$200M sweet spot, EBITDA margins expanding, customer concentration decreasing, recurring revenue growing |
| external_triggers | 0.15 | 15 | Recent leadership changes (new CFO/COO), hiring for corp dev/M&A roles, capex slowdown, debt maturity approaching, recent press/awards |
| negative_signals | 0.10 | -10 | Recent PE acquisition (-5), recent capital raise (-3), founder very young (-3), rapid hiring/growth mode (-2), new product launches (-2). These REDUCE the score. |

## Entity to Score

- **Name**: {name}
- **Type**: {entity_type}
- **Category**: {category}
- **Description**: {description}
- **Properties**: {properties}
- **Enrichment Data**: {enrichment_data}
- **Source URL**: {source_url}

## Related Entities

{relations_text}

## Output Format

Return ONLY a JSON object with this exact structure:

```json
{
  "score": "<integer 0-100>",
  "breakdown": {
    "ownership_succession": "<integer 0-30>",
    "market_timing": "<integer 0-25>",
    "company_performance": "<integer 0-20>",
    "external_triggers": "<integer 0-15>",
    "negative_signals": "<integer -10 to 0>"
  },
  "reasoning": "<one paragraph explaining the sell-likelihood assessment>"
}
```
