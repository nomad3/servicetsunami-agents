---
name: "Google News Search"
engine: "python"
script_path: "script.py"
version: 1
category: general
tags: [news, latest, headlines, breaking, updates, current, events, today, trending, happened]
auto_trigger: >
  Find latest news or current events. Triggers on: "what's happening with X",
  "latest news on X", "any updates on X", "news about X", "what happened with X",
  "breaking news", "headlines about X", "trending today", "current events",
  "did you hear about X", "what's going on with X", "updates on X situation",
  "news today", "what's new with X", "latest on X".
chain_to: [entity_extraction]
description: >
  Search for recent news articles on a topic. Prioritizes fresh results sorted by date.
inputs:
  - name: topic
    type: string
    description: "The news topic, event, person, or company to get news about."
    required: true
  - name: num_results
    type: integer
    description: "Number of news results (default: 5, max: 10)"
    required: false
  - name: timeframe
    type: string
    description: "Time filter: 'today', 'week', 'month' (default: 'week')"
    required: false
---

## Description
Searches for recent news articles, prioritizing freshness.

### When This Skill Triggers
- "What's happening with X?", "Latest news on X"
- "Any updates on the X situation?"
- "Did you hear about X?", "Breaking news about X"
- "What happened today?", "Trending news"
- Vague: "what's going on", "anything new about X"
