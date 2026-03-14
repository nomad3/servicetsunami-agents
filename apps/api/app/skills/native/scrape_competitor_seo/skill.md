---
name: "Scrape Competitor SEO"
engine: "python"
script_path: "script.py"
inputs:
  - name: "url"
    type: "string"
    description: "The full URL of the competitor's webpage to scrape (e.g., https://www.competitor.com)."
    required: true
---

## Description
Accepts a URL and returns key on-page SEO information, including the page title, meta description, H1 tags, and all H2 tags. This is used to quickly analyze a competitor's content strategy and keyword focus.
