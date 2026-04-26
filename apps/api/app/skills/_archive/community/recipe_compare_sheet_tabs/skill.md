---
auto_trigger: Read data from two tabs in a Google Sheet to compare and identify differences.
category: general
description: Read data from two tabs in a Google Sheet to compare and identify differences.
engine: markdown
name: recipe-compare-sheet-tabs
requires:
  bins:
  - gws
  skills:
  - gws-sheets
source_repo: https://github.com/googleworkspace/cli
tags:
- recipe
- compare
- sheet
- tabs
version: 1
---

# Compare Two Google Sheets Tabs

> **PREREQUISITE:** Load the following skills to execute this recipe: `gws-sheets`

Read data from two tabs in a Google Sheet to compare and identify differences.

## Steps

1. Read the first tab: `gws sheets +read --spreadsheet SHEET_ID --range "January!A1:D"`
2. Read the second tab: `gws sheets +read --spreadsheet SHEET_ID --range "February!A1:D"`
3. Compare the data and identify changes
