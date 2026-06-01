---
name: dep-audit
engine: markdown
version: 1
category: devops
tags: [dependencies, security, cve, audit, packages]
auto_trigger: "Use when auditing dependencies for security vulnerabilities or outdated packages"
source_repo: https://github.com/angakh/claude-skills-starter
---

## Description
Check for outdated packages and known security vulnerabilities in Python and Node.js project dependencies.

# Dependency Audit

## Overview

Check for outdated packages and known security vulnerabilities in project dependencies.

**Announce at start:** "Running dependency audit."

## Python Projects

### Outdated packages
```bash
pip list --outdated
```

### Security vulnerabilities
```bash
pip-audit                     # preferred
# fallback:
safety check --json
```

If `pip-audit` not installed: `pip install pip-audit` first.

### Summarize findings:
- Packages with CVEs: name, version, CVE ID, severity
- Packages more than 2 major versions behind: flag as upgrade candidates

## Node.js Projects

### Security audit
```bash
npm audit --json
```

### Outdated packages
```bash
npm outdated
```

### Summarize findings:
- Critical/high vulnerabilities: package, vulnerability, fix version
- Outdated packages: current vs latest

## Output Format

Group by severity:
1. **Critical/High**: require immediate action — include CVE, affected version, fix
2. **Medium**: should fix before next release
3. **Low/Info**: informational

Recommend: `npm audit fix` or `pip install --upgrade <package>` for auto-fixable issues.
