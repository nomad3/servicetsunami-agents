---
name: security-review
engine: markdown
version: 1
category: coding
tags: [security, owasp, vulnerabilities, pentest, audit]
auto_trigger: "Use when doing a security audit, reviewing auth code, or before merging sensitive changes"
---

## Description
Deep security audit of changed code focused exclusively on vulnerabilities — more thorough than a regular code review.

# Security Review

## Overview

Deep security audit of changed code. Focuses exclusively on vulnerabilities — more thorough than a regular code review.

**Announce at start:** "Running security review."

## Injection Vectors
- [ ] SQL: all queries use parameterized statements or ORM (never f-strings/concatenation)
- [ ] Shell: no `os.system`, `subprocess.shell=True` with unsanitized input
- [ ] Path traversal: file paths validated against allowed base directories
- [ ] Template injection: user content not rendered as Jinja/Mustache/etc templates
- [ ] LDAP/XPath/NoSQL injection: inputs sanitized for the respective query language

## Authentication & Authorization
- [ ] Every new endpoint has auth middleware applied
- [ ] Role/permission checks happen server-side, not just frontend
- [ ] JWT/session tokens validated (signature, expiry, audience)
- [ ] Password reset tokens are hashed before storage, compared with constant-time function
- [ ] OAuth redirect URIs validated against allowlist
- [ ] Rate limiting on auth endpoints (login, register, reset)

## Secrets & Sensitive Data
- [ ] No API keys, tokens, or passwords hardcoded in source
- [ ] Sensitive values not logged or included in error messages
- [ ] Encryption keys not in environment blocks that override .env (docker-compose footgun)
- [ ] Secrets not committed to Git

## Input Validation
- [ ] All user-supplied data validated at API boundary
- [ ] File uploads: type checked, size limited, stored outside web root
- [ ] Deserialization of user-supplied data avoided (or sandboxed)

## Dependency & Supply Chain
- [ ] New dependencies checked against known CVE databases
- [ ] Pinned versions (not floating `*` or `latest`)
- [ ] Minimal permissions for new service accounts

## Output Format

For each finding:
1. **Vulnerability class** (OWASP category)
2. **File + line**
3. **Severity**: critical | high | medium | low
4. **Description**: what the attack vector is
5. **Fix**: concrete remediation with code example

End with a CVSS-style risk summary.
