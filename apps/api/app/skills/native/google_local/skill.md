---
name: "Local Business Search"
engine: "python"
script_path: "script.py"
version: 1
category: general
tags: [local, nearby, near, restaurant, store, shop, business, location, place, open, hours, directions, maps]
auto_trigger: >
  Find local businesses, restaurants, or services nearby. Triggers on:
  "find X near me", "best X in [city]", "restaurants near X",
  "coffee shops in X", "X open now", "plumber near me",
  "best dentist in X", "where to eat in X", "bars near X",
  "stores that sell X", "X near [location]", "local X",
  "is there a X nearby", "closest X", "X in my area",
  "recommend a X in [city]", "top rated X in [city]".
chain_to: [entity_extraction]
description: >
  Search for local businesses, services, restaurants by location.
  Adds location-aware keywords to find nearby options.
inputs:
  - name: business_type
    type: string
    description: "Type of business or what you are looking for (e.g., 'Italian restaurant', 'plumber')."
    required: true
  - name: location
    type: string
    description: "City, neighborhood, or area (e.g., 'Santiago', 'near downtown'). Required for good results."
    required: false
  - name: num_results
    type: integer
    description: "Number of results (default: 5, max: 10)"
    required: false
---

## Description
Local business and service search with location awareness.

### When This Skill Triggers
- "Find X near me", "Best X in [city]"
- "Restaurants near X", "Coffee shops in X"
- "Plumber near me", "Dentist in Santiago"
- Vague: "where to eat", "recommend a place"
