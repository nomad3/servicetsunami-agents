---
name: SQL Query
engine: tool
tool_class: SQLQueryTool
version: 1
category: data
tags: [sql, query, data, analysis, datasets]
auto_trigger: "Run SQL query against connected datasets to retrieve and analyze data"
inputs:
  - name: query
    type: string
    description: "SQL query to execute"
    required: true
---

## Description
Execute SQL queries on connected datasets to retrieve and analyze data. Returns query results as structured data.
