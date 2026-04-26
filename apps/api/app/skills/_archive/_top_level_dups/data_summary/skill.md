---
name: Quick Statistics
engine: tool
tool_class: DataSummaryTool
version: 1
category: data
tags: [statistics, summary, data, overview]
auto_trigger: "Generate statistics, summaries, or overviews of data"
inputs:
  - name: dataset_id
    type: string
    description: "Dataset UUID to summarize"
    required: true
---

## Description
Generate summaries and statistical overviews of datasets automatically. Provides key metrics, distributions, and data quality indicators.
