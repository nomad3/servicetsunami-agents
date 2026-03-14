---
name: Marketing Signal Scoring
engine: markdown
version: 1
category: marketing
tags: [marketing, engagement, intent, signals]
auto_trigger: "Score leads based on marketing engagement and buying intent signals"
inputs:
  - name: entity_id
    type: string
    description: "Knowledge entity UUID to score"
    required: true
---

## Description

Score leads 0-100 based on marketing engagement, campaign response, and buying intent signals.

## Scoring Rubric (0-100 total)

| Category | Max Points | What to look for |
|---|---|---|
| engagement | 25 | Website visits, content downloads, webinar attendance, demo requests, email open/click rates |
| intent_signals | 25 | Searched for competitor products, visited pricing page, compared solutions, asked for proposal |
| firmographic_fit | 20 | Industry match, company size in ICP range, geography alignment, technology stack compatibility |
| behavioral_recency | 15 | How recent the engagement (last 7 days = highest, last 30 = medium, 30+ = low), frequency of interactions |
| champion_signals | 15 | Multiple contacts engaged, senior decision-maker involved, internal champion identified, shared content internally |

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
    "engagement": "<integer 0-25>",
    "intent_signals": "<integer 0-25>",
    "firmographic_fit": "<integer 0-20>",
    "behavioral_recency": "<integer 0-15>",
    "champion_signals": "<integer 0-15>"
  },
  "reasoning": "<one paragraph explaining the marketing qualification score>"
}
```
