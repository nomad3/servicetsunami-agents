---
name: "Web Page Reader"
engine: "python"
script_path: "script.py"
version: 1
category: general
tags: [web, page, read, scrape, url, website, article, content, extract, summarize, browse]
auto_trigger: >
  Read, fetch, or extract content from a specific URL or webpage. Use when the user
  shares a link and wants to know what is on it, asks to read a page, extract text,
  summarize an article, check a webpage, or open a URL. Also matches
  "what does this page say", "read this link", "scrape this site".
description: >
  Fetch a webpage and extract its main text content, stripping navigation,
  ads, and boilerplate. Returns clean text, title, meta description, headings.
inputs:
  - name: url
    type: string
    description: "The full URL to read (e.g., https://example.com/article)"
    required: true
  - name: extract_links
    type: boolean
    description: "Whether to include links found on the page (default: false)"
    required: false
  - name: max_length
    type: integer
    description: "Maximum character length for extracted text (default: 5000)"
    required: false
---

## Description
Fetches a webpage and extracts clean, readable content.

### When This Skill Triggers
- User shares a URL and asks what is on it
- "Read this page", "What does this say", "Summarize this article"
- "Scrape this website", "Get content from this URL"
