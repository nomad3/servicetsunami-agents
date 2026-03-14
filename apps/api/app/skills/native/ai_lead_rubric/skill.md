---
name: AI Lead Scoring
engine: markdown
version: 1
category: sales
tags: [leads, scoring, AI, qualification, BANT]
auto_trigger: "Score leads for AI platform fit using the AI Lead Scoring rubric"
inputs:
  - name: entity_id
    type: string
    description: "Knowledge entity UUID to score"
    required: true
---

## Description

Score leads 0-100 based on likelihood of becoming a customer for an AI/agent orchestration platform.

## Scoring Rubric (0-100 total)

| Category | Max Points | What to look for |
|---|---|---|
| hiring | 25 | Job posts mentioning AI, ML, agents, orchestration, automation, platform engineering |
| tech_stack | 20 | Uses or evaluates LangChain, OpenAI, Anthropic, CrewAI, AutoGen, or similar agent frameworks |
| funding | 20 | Recent funding round (Series A/B/C within 12 months scores highest) |
| company_size | 15 | Mid-market (50-500 employees) and growth-stage companies score highest |
| news | 10 | Recent product launches, partnerships, expansions, AI initiatives |
| direct_fit | 10 | Explicit mentions of orchestration needs, multi-agent workflows, workflow automation |

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
    "hiring": "<integer 0-25>",
    "tech_stack": "<integer 0-20>",
    "funding": "<integer 0-20>",
    "company_size": "<integer 0-15>",
    "news": "<integer 0-10>",
    "direct_fit": "<integer 0-10>"
  },
  "reasoning": "<one paragraph explaining the score>"
}
```
