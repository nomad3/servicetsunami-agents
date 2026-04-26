---
name: Lead Scoring
engine: tool
tool_class: LeadScoringTool
version: 1
category: sales
tags: [leads, scoring, qualification, sales, BANT]
auto_trigger: "Score or qualify a lead using configurable rubrics"
inputs:
  - name: entity_id
    type: string
    description: "Knowledge entity UUID to score"
    required: true
  - name: rubric_id
    type: string
    description: "Scoring rubric ID (ai_lead, marketing_signal, or a tenant-defined rubric)"
    required: false
---

## Description
Score entities 0-100 using configurable scoring rubrics. Supports AI lead scoring and marketing signal scoring out of the box; tenants can register additional domain rubrics. Uses LLM analysis of entity data against structured rubric criteria.
