---
name: "Google Shopping Search"
engine: "python"
script_path: "script.py"
version: 1
category: general
tags: [price, cost, buy, shopping, deal, cheap, expensive, store, purchase, amazon, ebay, order, shop]
auto_trigger: >
  Find prices, deals, or where to buy products. Triggers on: "how much does X cost",
  "price of X", "where to buy X", "best deal on X", "cheapest X", "X for sale",
  "buy X online", "X price comparison", "is X worth the price", "X on sale",
  "affordable X", "X under $Y", "shop for X", "order X", "find me X to buy".
chain_to: []
description: >
  Search for product prices, deals, and shopping results. Appends shopping keywords
  to queries for better price/deal results.
inputs:
  - name: product
    type: string
    description: "The product or item to find prices for."
    required: true
  - name: num_results
    type: integer
    description: "Number of results (default: 5, max: 10)"
    required: false
  - name: budget
    type: string
    description: "Optional budget constraint like 'under $100' or 'cheap'"
    required: false
---

## Description
Product and price search — finds deals, prices, and where to buy things.

### When This Skill Triggers
- "How much does X cost?", "Price of X"
- "Where to buy X?", "Best deal on X"
- "Cheapest X", "X under $100"
- "Is X worth it?", "X on sale"
- Vague: "find me a good X", "I need a X"
