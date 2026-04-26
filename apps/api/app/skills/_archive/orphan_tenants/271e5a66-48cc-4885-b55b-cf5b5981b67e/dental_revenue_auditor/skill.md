---
auto_trigger: revenue,pending treatment, money on the table, or asks to  audit the
  CDT codes in the CSV
category: data
chain_to:
- "Accounting \u2014 Tool Handler (d62acf)"
engine: python
inputs:
- description: he raw dental CSV export from the DSO practice management system
  name: csv_data
  required: false
  type: string
name: dental_revenue_auditor
script_path: script.py
tags:
- DSO
- Revenue
- CDT
- Dental-Audit
version: 1
---

## Description
dentifies high-value dental revenue opportunities by mapping CDT codes to fee amounts. Focuses on 'In-Progress' and 'Scheduled' statuses for restorative (D2000+) and surgical (D7000+) procedures to eliminate revenue leak
