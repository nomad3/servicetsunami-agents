---
name: Report Generation
engine: tool
tool_class: ReportGenerationTool
version: 1
category: data
tags: [reports, excel, charts, visualization, documents]
auto_trigger: "Generate a report, Excel file, or document with charts and data"
inputs:
  - name: report_type
    type: string
    description: "Type of report to generate"
    required: true
  - name: data
    type: string
    description: "JSON data for the report"
    required: true
---

## Description
Generate structured reports with charts and visualizations. Creates Excel files with bar charts, line charts, pie charts, tables, and metric visualizations.
