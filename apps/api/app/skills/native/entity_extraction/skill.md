---
name: Entity Extraction
engine: tool
tool_class: EntityExtractionTool
version: 1
category: automation
tags: [extraction, entities, NER, text, knowledge]
auto_trigger: "Extract people, companies, and concepts from text"
inputs:
  - name: text
    type: string
    description: "Text to extract entities from"
    required: true
---

## Description
Extract people, companies, locations, and concepts from text using NER. Identified entities can be stored in the knowledge graph for future reference.
