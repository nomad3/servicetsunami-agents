"""Jira tools for agent orchestration.

Uses stored credentials (api_token, email, domain) from the credential vault
to call Jira REST API v3 on behalf of the authenticated tenant.
"""
import base64
import logging
from typing import Optional

import httpx

from config.settings import settings
from tools.knowledge_tools import _resolve_tenant_id

logger = logging.getLogger(__name__)

_api_client: Optional[httpx.AsyncClient] = None


def _get_api_client() -> httpx.AsyncClient:
    global _api_client
    if _api_client is None:
        _api_client = httpx.AsyncClient(
            base_url=settings.api_base_url,
            timeout=30.0,
        )
    return _api_client


async def _get_jira_credentials(tenant_id: str) -> Optional[dict]:
    """Retrieve Jira credentials (api_token, email, domain) from the vault."""
    client = _get_api_client()
    try:
        resp = await client.get(
            "/api/v1/oauth/internal/token/jira",
            headers={"X-Internal-Key": settings.mcp_api_key},
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


# ---------------------------------------------------------------------------
# Jira tools
# ---------------------------------------------------------------------------

async def search_jira_issues(
    tenant_id: str = "auto",
    jql: str = "",
    max_results: int = 20,
) -> dict:
    """Search Jira issues using JQL (Jira Query Language).

    Args:
        tenant_id: Tenant context. Use "auto" if unknown.
        jql: JQL query string. Examples:
             - "project = PROJ" (all issues in project)
             - "assignee = currentUser() AND status != Done"
             - "status = 'In Progress'" (in-progress issues)
             - "created >= -7d" (created in last 7 days)
             - "text ~ 'login bug'" (full-text search)
             Leave empty to get recent issues.
        max_results: Maximum number of issues to return (1-50).

    Returns:
        Dict with list of issue summaries (key, summary, status, assignee, priority).
    """
    tenant_id = _resolve_tenant_id(tenant_id)
    creds = await _get_jira_credentials(tenant_id)
    if not creds:
        return {"error": "Jira not connected. Ask the user to configure Jira in Connected Apps (Integrations page)."}

    api_token = creds.get("api_token")
    email = creds.get("email")
    domain = creds.get("domain", "").rstrip("/")

    if not api_token or not email or not domain:
        return {"error": "Jira credentials incomplete. Need api_token, email, and domain."}

    if not domain.startswith("http"):
        domain = f"https://{domain}"
    if not domain.endswith(".atlassian.net") and "." not in domain.split("//")[-1]:
        domain = f"{domain}.atlassian.net"

    auth = _build_auth_header(email, api_token)
    headers = {"Authorization": auth, "Accept": "application/json"}

    if not jql:
        jql = "ORDER BY updated DESC"

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                f"{domain}/rest/api/3/search",
                headers=headers,
                params={
                    "jql": jql,
                    "maxResults": min(max_results, 50),
                    "fields": "summary,status,assignee,priority,issuetype,created,updated,project",
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


async def get_jira_issue(
    tenant_id: str = "auto",
    issue_key: str = "",
) -> dict:
    """Get full details of a specific Jira issue.

    Args:
        tenant_id: Tenant context. Use "auto" if unknown.
        issue_key: The Jira issue key (e.g., "PROJ-123").

    Returns:
        Dict with full issue details including description, comments, and labels.
    """
    tenant_id = _resolve_tenant_id(tenant_id)
    if not issue_key:
        return {"error": "issue_key is required (e.g., 'PROJ-123')."}

    creds = await _get_jira_credentials(tenant_id)
    if not creds:
        return {"error": "Jira not connected. Ask the user to configure Jira in Connected Apps."}

    api_token = creds.get("api_token")
    email = creds.get("email")
    domain = creds.get("domain", "").rstrip("/")

    if not api_token or not email or not domain:
        return {"error": "Jira credentials incomplete."}

    if not domain.startswith("http"):
        domain = f"https://{domain}"
    if not domain.endswith(".atlassian.net") and "." not in domain.split("//")[-1]:
        domain = f"{domain}.atlassian.net"

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

            # Get comments
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


async def create_jira_issue(
    tenant_id: str = "auto",
    project_key: str = "",
    summary: str = "",
    description: str = "",
    issue_type: str = "Task",
    priority: str = "",
    assignee_email: str = "",
    labels: str = "",
) -> dict:
    """Create a new Jira issue.

    Args:
        tenant_id: Tenant context. Use "auto" if unknown.
        project_key: The project key (e.g., "PROJ"). Required.
        summary: Issue title/summary. Required.
        description: Issue description (plain text).
        issue_type: Issue type: "Task", "Bug", "Story", "Epic". Default: "Task".
        priority: Priority: "Highest", "High", "Medium", "Low", "Lowest". Leave empty for default.
        assignee_email: Email of the person to assign. Leave empty for unassigned.
        labels: Comma-separated labels (e.g., "backend,urgent").

    Returns:
        Dict with created issue key, URL, and details.
    """
    tenant_id = _resolve_tenant_id(tenant_id)
    if not project_key or not summary:
        return {"error": "project_key and summary are required."}

    creds = await _get_jira_credentials(tenant_id)
    if not creds:
        return {"error": "Jira not connected. Ask the user to configure Jira in Connected Apps."}

    api_token = creds.get("api_token")
    email = creds.get("email")
    domain = creds.get("domain", "").rstrip("/")

    if not api_token or not email or not domain:
        return {"error": "Jira credentials incomplete."}

    if not domain.startswith("http"):
        domain = f"https://{domain}"
    if not domain.endswith(".atlassian.net") and "." not in domain.split("//")[-1]:
        domain = f"{domain}.atlassian.net"

    auth = _build_auth_header(email, api_token)
    headers = {
        "Authorization": auth,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

    # Build issue payload
    issue_data = {
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
        issue_data["fields"]["labels"] = [l.strip() for l in labels.split(",") if l.strip()]

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


async def update_jira_issue(
    tenant_id: str = "auto",
    issue_key: str = "",
    summary: str = "",
    description: str = "",
    status: str = "",
    priority: str = "",
    assignee_email: str = "",
    comment: str = "",
) -> dict:
    """Update an existing Jira issue. Only provided fields are changed.

    Args:
        tenant_id: Tenant context. Use "auto" if unknown.
        issue_key: The issue key to update (e.g., "PROJ-123"). Required.
        summary: New summary/title. Leave empty to keep current.
        description: New description. Leave empty to keep current.
        status: Transition to this status (e.g., "In Progress", "Done"). Leave empty to keep current.
        priority: New priority. Leave empty to keep current.
        assignee_email: New assignee email. Leave empty to keep current.
        comment: Add a comment to the issue. Leave empty for no comment.

    Returns:
        Dict with update status.
    """
    tenant_id = _resolve_tenant_id(tenant_id)
    if not issue_key:
        return {"error": "issue_key is required."}

    creds = await _get_jira_credentials(tenant_id)
    if not creds:
        return {"error": "Jira not connected."}

    api_token = creds.get("api_token")
    email = creds.get("email")
    domain = creds.get("domain", "").rstrip("/")

    if not api_token or not email or not domain:
        return {"error": "Jira credentials incomplete."}

    if not domain.startswith("http"):
        domain = f"https://{domain}"
    if not domain.endswith(".atlassian.net") and "." not in domain.split("//")[-1]:
        domain = f"{domain}.atlassian.net"

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
            fields = {}
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
                # Get available transitions
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


async def list_jira_projects(
    tenant_id: str = "auto",
) -> dict:
    """List all accessible Jira projects.

    Args:
        tenant_id: Tenant context. Use "auto" if unknown.

    Returns:
        Dict with list of projects (key, name, type).
    """
    tenant_id = _resolve_tenant_id(tenant_id)
    creds = await _get_jira_credentials(tenant_id)
    if not creds:
        return {"error": "Jira not connected. Ask the user to configure Jira in Connected Apps."}

    api_token = creds.get("api_token")
    email = creds.get("email")
    domain = creds.get("domain", "").rstrip("/")

    if not api_token or not email or not domain:
        return {"error": "Jira credentials incomplete."}

    if not domain.startswith("http"):
        domain = f"https://{domain}"
    if not domain.endswith(".atlassian.net") and "." not in domain.split("//")[-1]:
        domain = f"{domain}.atlassian.net"

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
