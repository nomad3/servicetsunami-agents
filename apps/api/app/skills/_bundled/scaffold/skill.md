---
name: scaffold
engine: markdown
version: 1
category: coding
tags: [scaffold, boilerplate, codegen, templates, components]
auto_trigger: "Use when generating boilerplate for a new component, endpoint, model, or test file"
source_repo: https://github.com/angakh/claude-skills-starter
---

## Description
Generate boilerplate for common code structures: React components, API endpoints, test files, data models, CLI commands.

# Scaffold

## Overview

Generate boilerplate for common code structures: React components, API endpoints, test files, data models, CLI commands.

**Announce at start:** "Scaffolding <component type>."

## Available Templates

### React Component
Creates: `src/components/<Name>/<Name>.js` + `<Name>.test.js` + `index.js`

Structure:
```jsx
import React from 'react';

const <Name> = ({ prop1, prop2 }) => {
  return (
    <div className="<name>">
      {/* component body */}
    </div>
  );
};

export default <Name>;
```

### FastAPI Endpoint
Creates: `app/api/v1/<resource>.py` with standard CRUD routes + schema + service stub

Structure follows the codebase pattern: router, deps injection, service call, response model.

### Python Data Model (SQLAlchemy)
Creates: `app/models/<name>.py` with `tenant_id` FK, `id` UUID PK, standard timestamps.

### Test File
Creates: `tests/test_<name>.py` with fixtures, happy path, and error case stubs.

### CLI Command
Creates a new `alpha <verb>` subcommand stub with argument parsing and help text.

## Usage

User specifies:
- Template type: component | endpoint | model | test | command
- Name/resource: e.g. "UserProfile" or "invoices"
- Any additional context (fields, HTTP methods, etc.)

Generate the files, show the paths created, and summarize what still needs to be filled in.
