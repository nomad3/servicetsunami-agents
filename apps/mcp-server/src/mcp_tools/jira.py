"""Jira MCP tools.

Jira issue and project management tools.
Fetches Jira credentials (api_token, email, domain) from the credential vault
via the internal API endpoint and calls Jira REST API v3.
"""
import base64
import logging
import os
from typing import Optional

import httpx
from mcp.server.fastmcp import Context

from src.mcp_app import mcp
from src.mcp_auth import resolve_tenant_id

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_api_base_url() -> str:
    from src.config import settings
    return settings.API_BASE_URL.rstrip("/")


def _get_internal_key() -> str:
    from src.config import settings
    return settings.API_INTERNAL_KEY


async def _get_jira_credentials(tenant_id: str) -> Optional[dict]:
    """Retrieve Jira credentials (api_token, email, domain) from the vault."""
    api_base_url = _get_api_base_url()
    internal_key = _get_internal_key()
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{api_base_url}/api/v1/oauth/internal/token/jira",
                headers={"X-Internal-Key": internal_key},
                params={"tenant_id": tenant_id},
            )
            if resp.status_code == 200:
                return resp.json()
            logger.warning("Jira credential retrieval returned %s", resp.status_code)
    except Exception:
        logger.exception("Failed to retrieve Jira credentials")
    return None


def _build_auth_header(email: str, api_token: str) -> str:
    """Build Basic Auth header for Jira API."""
    cred = f"{email}:{api_token}"
    return f"Basic {base64.b64encode(cred.encode()).decode()}"


def _normalize_domain(domain: str) -> str:
    """Ensure domain is a full HTTPS URL with .atlassian.net if needed."""
    domain = domain.rstrip("/")
    if not domain.startswith("http"):
        domain = f"https://{domain}"
    if not domain.endswith(".atlassian.net") and "." not in domain.split("//")[-1]:
        domain = f"{domain}.atlassian.net"
    return domain


# ---------------------------------------------------------------------------
# MCP Tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def search_jira_issues(
    tenant_id: str = "",
    jql: str = "",
    max_results: int = 20,
    ctx: Context = None,
) -> dict:
    """Search Jira issues using JQL (Jira Query Language).

    Args:
        tenant_id: Tenant UUID (resolved from session if omitted).
        jql: JQL query string. Examples:
             - "project = PROJ" (all issues in project)
             - "assignee = currentUser() AND status != Done"
             - "status = 'In Progress'" (in-progress issues)
             - "created >= -7d" (created in last 7 days)
             - "text ~ 'login bug'" (full-text search)
             Leave empty to get recent issues (last 30 days).
        max_results: Maximum number of issues to return (1-50).
        ctx: MCP request context (injected automatically).

    Returns:
        Dict with list of issue summaries (key, summary, status, assignee, priority).
    """
    tid = resolve_tenant_id(ctx) or tenant_id
    if not tid:
        return {"error": "tenant_id is required."}

    creds = await _get_jira_credentials(tid)
    if not creds:
        return {"error": "Jira not connected. Ask the user to configure Jira in Connected Apps (Integrations page)."}

    api_token = creds.get("api_token")
    email = creds.get("email")
    domain = creds.get("domain", "").rstrip("/")

    if not api_token or not email or not domain:
        return {"error": "Jira credentials incomplete. Need api_token, email, and domain."}

    domain = _normalize_domain(domain)
    auth = _build_auth_header(email, api_token)
    headers = {"Authorization": auth, "Accept": "application/json"}

    if not jql:
        jql = "updated >= -30d ORDER BY updated DESC"

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{domain}/rest/api/3/search/jql",
                headers={**headers, "Content-Type": "application/json"},
                json={
                    "jql": jql,
                    "maxResults": min(max_results, 50),
                    "fields": ["summary", "status", "assignee", "priority", "issuetype", "created", "updated", "project"],
                },
            )
            resp.raise_for_status()
            data = resp.json()

            issues = []
            for issue in data.get("issues", []):
                fields = issue.get("fields", {})
                assignee = fields.get("assignee")
                issues.append({
                    "key": issue.get("key"),
                    "summary": fields.get("summary", ""),
                    "status": (fields.get("status") or {}).get("name", ""),
                    "priority": (fields.get("priority") or {}).get("name", ""),
                    "type": (fields.get("issuetype") or {}).get("name", ""),
                    "assignee": assignee.get("displayName", "") if assignee else "Unassigned",
                    "project": (fields.get("project") or {}).get("key", ""),
                    "updated": fields.get("updated", ""),
                })

            return {
                "status": "success",
                "issues": issues,
                "total": data.get("total", len(issues)),
                "jql": jql,
            }

    except httpx.HTTPStatusError as e:
        if e.response.status_code == 401:
            return {"error": "Jira authentication failed. Check API token and email in Connected Apps."}
        if e.response.status_code == 400:
            return {"error": f"Invalid JQL query: {e.response.text[:200]}"}
        return {"error": f"Jira API error: {e.response.status_code}"}
    except Exception as e:
        logger.exception("search_jira_issues failed")
        return {"error": f"Failed to search Jira: {str(e)}"}


@mcp.tool()
async def get_jira_issue(
    issue_key: str,
    tenant_id: str = "",
    ctx: Context = None,
) -> dict:
    """Get full details of a specific Jira issue.

    Args:
        issue_key: The Jira issue key (e.g., "PROJ-123"). Required.
        tenant_id: Tenant UUID (resolved from session if omitted).
        ctx: MCP request context (injected automatically).

    Returns:
        Dict with full issue details including description, comments, and labels.
    """
    tid = resolve_tenant_id(ctx) or tenant_id
    if not tid:
        return {"error": "tenant_id is required."}
    if not issue_key:
        return {"error": "issue_key is required (e.g., 'PROJ-123')."}

    creds = await _get_jira_credentials(tid)
    if not creds:
        return {"error": "Jira not connected. Ask the user to configure Jira in Connected Apps."}

    api_token = creds.get("api_token")
    email = creds.get("email")
    domain = creds.get("domain", "").rstrip("/")

    if not api_token or not email or not domain:
        return {"error": "Jira credentials incomplete."}

    domain = _normalize_domain(domain)
    auth = _build_auth_header(email, api_token)
    headers = {"Authorization": auth, "Accept": "application/json"}

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                f"{domain}/rest/api/3/issue/{issue_key}",
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()

            fields = data.get("fields", {})
            assignee = fields.get("assignee")

            # Extract description text from ADF (Atlassian Document Format)
            description = ""
            desc_adf = fields.get("description")
            if desc_adf and isinstance(desc_adf, dict):
                for block in desc_adf.get("content", []):
                    for inline in block.get("content", []):
                        if inline.get("type") == "text":
                            description += inline.get("text", "")
                    description += "\n"
            description = description.strip()[:3000]

            # Get comments (last 5)
            comments = []
            for comment in (fields.get("comment", {}).get("comments", []) or [])[-5:]:
                body = ""
                comment_adf = comment.get("body")
                if comment_adf and isinstance(comment_adf, dict):
                    for block in comment_adf.get("content", []):
                        for inline in block.get("content", []):
                            if inline.get("type") == "text":
                                body += inline.get("text", "")
                comments.append({
                    "author": (comment.get("author") or {}).get("displayName", ""),
                    "body": body[:500],
                    "created": comment.get("created", ""),
                })

            return {
                "status": "success",
                "key": data.get("key"),
                "summary": fields.get("summary", ""),
                "description": description,
                "status_name": (fields.get("status") or {}).get("name", ""),
                "priority": (fields.get("priority") or {}).get("name", ""),
                "type": (fields.get("issuetype") or {}).get("name", ""),
                "assignee": assignee.get("displayName", "") if assignee else "Unassigned",
                "reporter": (fields.get("reporter") or {}).get("displayName", ""),
                "project": (fields.get("project") or {}).get("name", ""),
                "labels": fields.get("labels", []),
                "created": fields.get("created", ""),
                "updated": fields.get("updated", ""),
                "comments": comments,
            }

    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            return {"error": f"Issue '{issue_key}' not found."}
        if e.response.status_code == 401:
            return {"error": "Jira authentication failed. Check credentials."}
        return {"error": f"Jira API error: {e.response.status_code}"}
    except Exception as e:
        logger.exception("get_jira_issue failed")
        return {"error": f"Failed to get issue: {str(e)}"}


@mcp.tool()
async def create_jira_issue(
    project_key: str,
    summary: str,
    tenant_id: str = "",
    description: str = "",
    issue_type: str = "Task",
    priority: str = "",
    assignee_email: str = "",
    labels: str = "",
    ctx: Context = None,
) -> dict:
    """Create a new Jira issue.

    Args:
        project_key: The project key (e.g., "PROJ"). Required.
        summary: Issue title/summary. Required.
        tenant_id: Tenant UUID (resolved from session if omitted).
        description: Issue description (plain text).
        issue_type: Issue type: "Task", "Bug", "Story", "Epic". Default: "Task".
        priority: Priority: "Highest", "High", "Medium", "Low", "Lowest". Leave empty for default.
        assignee_email: Email of the person to assign. Leave empty for unassigned.
        labels: Comma-separated labels (e.g., "backend,urgent").
        ctx: MCP request context (injected automatically).

    Returns:
        Dict with created issue key, URL, and details.
    """
    tid = resolve_tenant_id(ctx) or tenant_id
    if not tid:
        return {"error": "tenant_id is required."}
    if not project_key or not summary:
        return {"error": "project_key and summary are required."}

    creds = await _get_jira_credentials(tid)
    if not creds:
        return {"error": "Jira not connected. Ask the user to configure Jira in Connected Apps."}

    api_token = creds.get("api_token")
    email = creds.get("email")
    domain = creds.get("domain", "").rstrip("/")

    if not api_token or not email or not domain:
        return {"error": "Jira credentials incomplete."}

    domain = _normalize_domain(domain)
    auth = _build_auth_header(email, api_token)
    headers = {
        "Authorization": auth,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

    # Build issue payload
    issue_data: dict = {
        "fields": {
            "project": {"key": project_key.upper()},
            "summary": summary,
            "issuetype": {"name": issue_type},
        }
    }

    if description:
        issue_data["fields"]["description"] = {
            "type": "doc",
            "version": 1,
            "content": [
                {
                    "type": "paragraph",
                    "content": [{"type": "text", "text": description}],
                }
            ],
        }

    if priority:
        issue_data["fields"]["priority"] = {"name": priority}

    if assignee_email:
        issue_data["fields"]["assignee"] = {"id": assignee_email}

    if labels:
        issue_data["fields"]["labels"] = [lbl.strip() for lbl in labels.split(",") if lbl.strip()]

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{domain}/rest/api/3/issue",
                headers=headers,
                json=issue_data,
            )
            resp.raise_for_status()
            result = resp.json()

            issue_key = result.get("key", "")
            browse_url = f"{domain}/browse/{issue_key}" if issue_key else ""

            return {
                "status": "success",
                "key": issue_key,
                "id": result.get("id", ""),
                "url": browse_url,
                "message": f"Created {issue_key}: {summary}",
            }

    except httpx.HTTPStatusError as e:
        error_body = e.response.text[:300]
        if e.response.status_code == 400:
            return {"error": f"Invalid issue data: {error_body}"}
        if e.response.status_code == 401:
            return {"error": "Jira authentication failed. Check credentials."}
        return {"error": f"Jira create failed ({e.response.status_code}): {error_body}"}
    except Exception as e:
        logger.exception("create_jira_issue failed")
        return {"error": f"Failed to create issue: {str(e)}"}


@mcp.tool()
async def update_jira_issue(
    issue_key: str,
    tenant_id: str = "",
    summary: str = "",
    description: str = "",
    status: str = "",
    priority: str = "",
    assignee_email: str = "",
    comment: str = "",
    ctx: Context = None,
) -> dict:
    """Update an existing Jira issue. Only provided fields are changed.

    Args:
        issue_key: The issue key to update (e.g., "PROJ-123"). Required.
        tenant_id: Tenant UUID (resolved from session if omitted).
        summary: New summary/title. Leave empty to keep current.
        description: New description. Leave empty to keep current.
        status: Transition to this status (e.g., "In Progress", "Done"). Leave empty to keep current.
        priority: New priority. Leave empty to keep current.
        assignee_email: New assignee email. Leave empty to keep current.
        comment: Add a comment to the issue. Leave empty for no comment.
        ctx: MCP request context (injected automatically).

    Returns:
        Dict with update status and list of applied changes.
    """
    tid = resolve_tenant_id(ctx) or tenant_id
    if not tid:
        return {"error": "tenant_id is required."}
    if not issue_key:
        return {"error": "issue_key is required."}

    creds = await _get_jira_credentials(tid)
    if not creds:
        return {"error": "Jira not connected."}

    api_token = creds.get("api_token")
    email = creds.get("email")
    domain = creds.get("domain", "").rstrip("/")

    if not api_token or not email or not domain:
        return {"error": "Jira credentials incomplete."}

    domain = _normalize_domain(domain)
    auth = _build_auth_header(email, api_token)
    headers = {
        "Authorization": auth,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

    results = []

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            # Update fields
            fields: dict = {}
            if summary:
                fields["summary"] = summary
            if description:
                fields["description"] = {
                    "type": "doc",
                    "version": 1,
                    "content": [
                        {
                            "type": "paragraph",
                            "content": [{"type": "text", "text": description}],
                        }
                    ],
                }
            if priority:
                fields["priority"] = {"name": priority}
            if assignee_email:
                fields["assignee"] = {"id": assignee_email}

            if fields:
                resp = await client.put(
                    f"{domain}/rest/api/3/issue/{issue_key}",
                    headers=headers,
                    json={"fields": fields},
                )
                resp.raise_for_status()
                results.append("fields updated")

            # Transition status
            if status:
                trans_resp = await client.get(
                    f"{domain}/rest/api/3/issue/{issue_key}/transitions",
                    headers=headers,
                )
                trans_resp.raise_for_status()
                transitions = trans_resp.json().get("transitions", [])

                target = next(
                    (t for t in transitions if t["name"].lower() == status.lower()),
                    None,
                )
                if target:
                    await client.post(
                        f"{domain}/rest/api/3/issue/{issue_key}/transitions",
                        headers=headers,
                        json={"transition": {"id": target["id"]}},
                    )
                    results.append(f"status → {status}")
                else:
                    available = [t["name"] for t in transitions]
                    results.append(f"status '{status}' not available (options: {available})")

            # Add comment
            if comment:
                await client.post(
                    f"{domain}/rest/api/3/issue/{issue_key}/comment",
                    headers=headers,
                    json={
                        "body": {
                            "type": "doc",
                            "version": 1,
                            "content": [
                                {
                                    "type": "paragraph",
                                    "content": [{"type": "text", "text": comment}],
                                }
                            ],
                        }
                    },
                )
                results.append("comment added")

            return {
                "status": "success",
                "key": issue_key,
                "updates": results,
                "message": f"Updated {issue_key}: {', '.join(results)}",
            }

    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            return {"error": f"Issue '{issue_key}' not found."}
        if e.response.status_code == 401:
            return {"error": "Jira authentication failed."}
        return {"error": f"Jira update failed: {e.response.status_code}"}
    except Exception as e:
        logger.exception("update_jira_issue failed")
        return {"error": f"Failed to update issue: {str(e)}"}


@mcp.tool()
async def list_jira_projects(
    tenant_id: str = "",
    ctx: Context = None,
) -> dict:
    """List all accessible Jira projects.

    Args:
        tenant_id: Tenant UUID (resolved from session if omitted).
        ctx: MCP request context (injected automatically).

    Returns:
        Dict with list of projects (key, name, type) and count.
    """
    tid = resolve_tenant_id(ctx) or tenant_id
    if not tid:
        return {"error": "tenant_id is required."}

    creds = await _get_jira_credentials(tid)
    if not creds:
        return {"error": "Jira not connected. Ask the user to configure Jira in Connected Apps."}

    api_token = creds.get("api_token")
    email = creds.get("email")
    domain = creds.get("domain", "").rstrip("/")

    if not api_token or not email or not domain:
        return {"error": "Jira credentials incomplete."}

    domain = _normalize_domain(domain)
    auth = _build_auth_header(email, api_token)
    headers = {"Authorization": auth, "Accept": "application/json"}

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                f"{domain}/rest/api/3/project",
                headers=headers,
            )
            resp.raise_for_status()
            projects = resp.json()

            return {
                "status": "success",
                "projects": [
                    {
                        "key": p.get("key"),
                        "name": p.get("name"),
                        "type": p.get("projectTypeKey", ""),
                    }
                    for p in projects
                ],
                "count": len(projects),
            }

    except httpx.HTTPStatusError as e:
        if e.response.status_code == 401:
            return {"error": "Jira authentication failed. Check credentials."}
        return {"error": f"Jira API error: {e.response.status_code}"}
    except Exception as e:
        logger.exception("list_jira_projects failed")
        return {"error": f"Failed to list projects: {str(e)}"}
