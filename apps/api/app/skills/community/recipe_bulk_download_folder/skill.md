---
auto_trigger: List and download all files from a Google Drive folder.
category: general
description: List and download all files from a Google Drive folder.
engine: markdown
name: recipe-bulk-download-folder
requires:
  bins:
  - gws
  skills:
  - gws-drive
source_repo: https://github.com/googleworkspace/cli
tags:
- recipe
- bulk
- download
- folder
version: 1
---

# Bulk Download Drive Folder

> **PREREQUISITE:** Load the following skills to execute this recipe: `gws-drive`

List and download all files from a Google Drive folder.

## Steps

1. List files in folder: `gws drive files list --params '{"q": "'\''FOLDER_ID'\'' in parents"}' --format json`
2. Download each file: `gws drive files get --params '{"fileId": "FILE_ID", "alt": "media"}' -o filename.ext`
3. Export Google Docs as PDF: `gws drive files export --params '{"fileId": "FILE_ID", "mimeType": "application/pdf"}' -o document.pdf`
