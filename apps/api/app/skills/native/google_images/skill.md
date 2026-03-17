---
name: "Image Search"
engine: "python"
script_path: "script.py"
version: 1
category: general
tags: [images, pictures, photos, visual, gallery, logo, icon, infographic, diagram, screenshot, illustration]
auto_trigger: >
  Find images, pictures, or visual content. Triggers on:
  "find images of X", "show me pictures of X", "X photos",
  "X logo", "X infographic", "X diagram", "X screenshot",
  "what does X look like", "pictures of X", "visual of X",
  "X gallery", "image search for X", "find a photo of X",
  "X illustration", "X icon".
chain_to: []
description: >
  Search for images using Google Image Search or web image results.
inputs:
  - name: query
    type: string
    description: "What to search images for."
    required: true
  - name: image_type
    type: string
    description: "Type: 'photo', 'clipart', 'lineart', 'face', 'animated' (default: any)"
    required: false
  - name: num_results
    type: integer
    description: "Number of results (default: 5, max: 10)"
    required: false
---

## Description
Image search using Google Image Search API.

### When This Skill Triggers
- "Find images of X", "Show me pictures of X"
- "What does X look like?", "X logo"
- "X diagram", "X infographic"
- Vague: "show me X", "I want to see X"
