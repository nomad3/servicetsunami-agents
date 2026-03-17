---
name: "Web Search"
engine: "python"
script_path: "script.py"
version: 1
category: general
tags: [search, web, google, lookup, find, research, internet, browse, query, information]
auto_trigger: >
  Search the web or Google for information. Use when the user asks to look up, find,
  search for, google, research, or get information about anything — people, companies,
  products, news, events, how-to guides, definitions, comparisons, reviews, prices,
  weather, sports scores, stock prices, or any general knowledge question.
  Also triggers on vague requests like "what is X", "who is X", "tell me about X",
  "find out about X", "check online", "look it up", "any info on X".
chain_to: [entity_extraction]
description: >
  Perform a web search and return the top results with titles, URLs, and snippets.
  Supports Google Custom Search API with automatic fallback to DuckDuckGo.
inputs:
  - name: query
    type: string
    description: "The search query — question, keywords, or topic."
    required: true
  - name: num_results
    type: integer
    description: "Number of results to return (default: 5, max: 10)"
    required: false
  - name: search_type
    type: string
    description: "Type of search: 'general' (default), 'news', 'images'"
    required: false
---

## Description
General-purpose web search. Google Custom Search API with DuckDuckGo fallback.

### When This Skill Triggers
- **Direct search**: "search for X", "google X", "look up X"
- **Questions**: "what is X?", "who is X?", "how does X work?"
- **Vague requests**: "any info on X", "check online for X", "look it up"
- **Specific lookups**: "X pricing", "X vs Y comparison"
