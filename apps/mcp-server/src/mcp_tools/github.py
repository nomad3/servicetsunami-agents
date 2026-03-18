"""GitHub MCP tools.

GitHub repository and issue management tools.
Retrieves GitHub OAuth token from the credential vault via the internal
API endpoint and calls the GitHub REST API on behalf of the tenant.
"""
import base64
import logging
from typing import Optional

import httpx
from mcp.server.fastmcp import Context

from src.mcp_app import mcp
from src.mcp_auth import resolve_tenant_id

logger = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_api_base_url() -> str:
    from src.config import settings
    return settings.API_BASE_URL.rstrip("/")


def _get_internal_key() -> str:
    from src.config import settings
    return settings.API_INTERNAL_KEY


async def _get_github_token(tenant_id: str) -> Optional[str]:
    """Retrieve GitHub OAuth token from the vault."""
    api_base_url = _get_api_base_url()
    internal_key = _get_internal_key()
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{api_base_url}/api/v1/oauth/internal/token/github",
                headers={"X-Internal-Key": internal_key},
                params={"tenant_id": tenant_id},
            )
            if resp.status_code == 200:
                data = resp.json()
                return data.get("oauth_token") or data.get("access_token")
            logger.warning("GitHub credential retrieval returned %s", resp.status_code)
    except Exception:
        logger.exception("Failed to retrieve GitHub credentials")
    return None


def _gh_headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


# ---------------------------------------------------------------------------
# MCP Tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def list_github_repos(
    tenant_id: str = "",
    sort: str = "updated",
    max_results: int = 20,
    ctx: Context = None,
) -> dict:
    """List repositories accessible to the authenticated GitHub user.

    Args:
        tenant_id: Tenant UUID (resolved from session if omitted).
        sort: Sort by: updated, created, pushed, full_name.
        max_results: Maximum repos to return (max 100).
        ctx: MCP request context (injected automatically).

    Returns:
        Dict with status and list of repositories.
    """
    tid = resolve_tenant_id(ctx) or tenant_id
    if not tid:
        return {"error": "tenant_id is required."}

    token = await _get_github_token(tid)
    if not token:
        return {"status": "error", "error": "GitHub not connected. Please connect GitHub in Integrations."}

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                f"{GITHUB_API}/user/repos",
                headers=_gh_headers(token),
                params={
                    "sort": sort,
                    "per_page": min(max_results, 100),
                    "type": "all",
                },
            )
            if resp.status_code == 401:
                return {"status": "error", "error": "GitHub token expired. Please reconnect in Integrations."}
            resp.raise_for_status()
            repos = resp.json()
            return {
                "status": "success",
                "count": len(repos),
                "repos": [
                    {
                        "full_name": r["full_name"],
                        "description": r.get("description") or "",
                        "language": r.get("language"),
                        "updated_at": r.get("updated_at"),
                        "default_branch": r.get("default_branch"),
                        "private": r.get("private"),
                        "open_issues_count": r.get("open_issues_count", 0),
                    }
                    for r in repos
                ],
            }
    except Exception as e:
        logger.exception("list_github_repos failed")
        return {"status": "error", "error": str(e)}


@mcp.tool()
async def get_github_repo(
    repo: str,
    tenant_id: str = "",
    ctx: Context = None,
) -> dict:
    """Get details about a specific GitHub repository.

    Args:
        repo: Full repository name (e.g. "owner/repo-name"). Required.
        tenant_id: Tenant UUID (resolved from session if omitted).
        ctx: MCP request context (injected automatically).

    Returns:
        Dict with repo details including stats, default branch, topics.
    """
    tid = resolve_tenant_id(ctx) or tenant_id
    if not tid:
        return {"error": "tenant_id is required."}
    if not repo:
        return {"error": "repo is required (e.g. 'owner/repo-name')."}

    token = await _get_github_token(tid)
    if not token:
        return {"status": "error", "error": "GitHub not connected."}

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                f"{GITHUB_API}/repos/{repo}",
                headers=_gh_headers(token),
            )
            if resp.status_code == 404:
                return {"status": "error", "error": f"Repository '{repo}' not found."}
            if resp.status_code == 401:
                return {"status": "error", "error": "GitHub token expired."}
            resp.raise_for_status()
            r = resp.json()
            return {
                "status": "success",
                "repo": {
                    "full_name": r["full_name"],
                    "description": r.get("description") or "",
                    "language": r.get("language"),
                    "default_branch": r.get("default_branch"),
                    "private": r.get("private"),
                    "stars": r.get("stargazers_count", 0),
                    "forks": r.get("forks_count", 0),
                    "open_issues": r.get("open_issues_count", 0),
                    "topics": r.get("topics", []),
                    "created_at": r.get("created_at"),
                    "updated_at": r.get("updated_at"),
                    "pushed_at": r.get("pushed_at"),
                },
            }
    except Exception as e:
        logger.exception("get_github_repo failed")
        return {"status": "error", "error": str(e)}


@mcp.tool()
async def list_github_issues(
    repo: str,
    state: str = "open",
    labels: str = "",
    max_results: int = 20,
    tenant_id: str = "",
    ctx: Context = None,
) -> dict:
    """List issues in a GitHub repository.

    Args:
        repo: Full repository name (e.g. "owner/repo-name"). Required.
        state: Filter by state: open, closed, all.
        labels: Comma-separated label filter (e.g. "bug,enhancement").
        max_results: Maximum issues to return.
        tenant_id: Tenant UUID (resolved from session if omitted).
        ctx: MCP request context (injected automatically).

    Returns:
        Dict with list of issues.
    """
    tid = resolve_tenant_id(ctx) or tenant_id
    if not tid:
        return {"error": "tenant_id is required."}
    if not repo:
        return {"error": "repo is required."}

    token = await _get_github_token(tid)
    if not token:
        return {"status": "error", "error": "GitHub not connected."}

    try:
        params = {
            "state": state,
            "per_page": min(max_results, 100),
            "sort": "updated",
            "direction": "desc",
        }
        if labels:
            params["labels"] = labels

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                f"{GITHUB_API}/repos/{repo}/issues",
                headers=_gh_headers(token),
                params=params,
            )
            if resp.status_code == 404:
                return {"status": "error", "error": f"Repository '{repo}' not found."}
            resp.raise_for_status()
            issues = resp.json()
            # Filter out pull requests (GitHub API returns PRs as issues)
            issues = [i for i in issues if "pull_request" not in i]
            return {
                "status": "success",
                "count": len(issues),
                "issues": [
                    {
                        "number": i["number"],
                        "title": i["title"],
                        "state": i["state"],
                        "author": i["user"]["login"],
                        "labels": [lbl["name"] for lbl in i.get("labels", [])],
                        "created_at": i["created_at"],
                        "updated_at": i["updated_at"],
                        "comments": i.get("comments", 0),
                        "body_preview": (i.get("body") or "")[:300],
                    }
                    for i in issues
                ],
            }
    except Exception as e:
        logger.exception("list_github_issues failed")
        return {"status": "error", "error": str(e)}


@mcp.tool()
async def get_github_issue(
    repo: str,
    issue_number: int,
    tenant_id: str = "",
    ctx: Context = None,
) -> dict:
    """Get details of a specific GitHub issue including comments.

    Args:
        repo: Full repository name (e.g. "owner/repo-name"). Required.
        issue_number: The issue number. Required.
        tenant_id: Tenant UUID (resolved from session if omitted).
        ctx: MCP request context (injected automatically).

    Returns:
        Dict with issue details and comments.
    """
    tid = resolve_tenant_id(ctx) or tenant_id
    if not tid:
        return {"error": "tenant_id is required."}
    if not repo or not issue_number:
        return {"error": "repo and issue_number are required."}

    token = await _get_github_token(tid)
    if not token:
        return {"status": "error", "error": "GitHub not connected."}

    try:
        headers = _gh_headers(token)
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                f"{GITHUB_API}/repos/{repo}/issues/{issue_number}",
                headers=headers,
            )
            if resp.status_code == 404:
                return {"status": "error", "error": f"Issue #{issue_number} not found in {repo}."}
            resp.raise_for_status()
            issue = resp.json()

            comments = []
            if issue.get("comments", 0) > 0:
                c_resp = await client.get(
                    f"{GITHUB_API}/repos/{repo}/issues/{issue_number}/comments",
                    headers=headers,
                    params={"per_page": 30},
                )
                if c_resp.status_code == 200:
                    comments = [
                        {
                            "author": c["user"]["login"],
                            "body": c["body"][:500],
                            "created_at": c["created_at"],
                        }
                        for c in c_resp.json()
                    ]

            return {
                "status": "success",
                "issue": {
                    "number": issue["number"],
                    "title": issue["title"],
                    "state": issue["state"],
                    "author": issue["user"]["login"],
                    "labels": [lbl["name"] for lbl in issue.get("labels", [])],
                    "assignees": [a["login"] for a in issue.get("assignees", [])],
                    "milestone": (issue.get("milestone") or {}).get("title"),
                    "body": (issue.get("body") or "")[:2000],
                    "created_at": issue["created_at"],
                    "updated_at": issue["updated_at"],
                    "comments_count": issue.get("comments", 0),
                    "comments": comments,
                },
            }
    except Exception as e:
        logger.exception("get_github_issue failed")
        return {"status": "error", "error": str(e)}


@mcp.tool()
async def list_github_pull_requests(
    repo: str,
    state: str = "open",
    max_results: int = 20,
    tenant_id: str = "",
    ctx: Context = None,
) -> dict:
    """List pull requests in a GitHub repository.

    Args:
        repo: Full repository name (e.g. "owner/repo-name"). Required.
        state: Filter by state: open, closed, all.
        max_results: Maximum PRs to return.
        tenant_id: Tenant UUID (resolved from session if omitted).
        ctx: MCP request context (injected automatically).

    Returns:
        Dict with list of pull requests.
    """
    tid = resolve_tenant_id(ctx) or tenant_id
    if not tid:
        return {"error": "tenant_id is required."}
    if not repo:
        return {"error": "repo is required."}

    token = await _get_github_token(tid)
    if not token:
        return {"status": "error", "error": "GitHub not connected."}

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                f"{GITHUB_API}/repos/{repo}/pulls",
                headers=_gh_headers(token),
                params={
                    "state": state,
                    "per_page": min(max_results, 100),
                    "sort": "updated",
                    "direction": "desc",
                },
            )
            if resp.status_code == 404:
                return {"status": "error", "error": f"Repository '{repo}' not found."}
            resp.raise_for_status()
            prs = resp.json()
            return {
                "status": "success",
                "count": len(prs),
                "pull_requests": [
                    {
                        "number": pr["number"],
                        "title": pr["title"],
                        "state": pr["state"],
                        "author": pr["user"]["login"],
                        "branch": pr["head"]["ref"],
                        "base": pr["base"]["ref"],
                        "draft": pr.get("draft", False),
                        "created_at": pr["created_at"],
                        "updated_at": pr["updated_at"],
                        "mergeable_state": pr.get("mergeable_state"),
                    }
                    for pr in prs
                ],
            }
    except Exception as e:
        logger.exception("list_github_pull_requests failed")
        return {"status": "error", "error": str(e)}


@mcp.tool()
async def get_github_pull_request(
    repo: str,
    pr_number: int,
    tenant_id: str = "",
    ctx: Context = None,
) -> dict:
    """Get details of a specific pull request including review status.

    Args:
        repo: Full repository name (e.g. "owner/repo-name"). Required.
        pr_number: The pull request number. Required.
        tenant_id: Tenant UUID (resolved from session if omitted).
        ctx: MCP request context (injected automatically).

    Returns:
        Dict with PR details, files changed, and reviews.
    """
    tid = resolve_tenant_id(ctx) or tenant_id
    if not tid:
        return {"error": "tenant_id is required."}
    if not repo or not pr_number:
        return {"error": "repo and pr_number are required."}

    token = await _get_github_token(tid)
    if not token:
        return {"status": "error", "error": "GitHub not connected."}

    try:
        headers = _gh_headers(token)
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                f"{GITHUB_API}/repos/{repo}/pulls/{pr_number}",
                headers=headers,
            )
            if resp.status_code == 404:
                return {"status": "error", "error": f"PR #{pr_number} not found in {repo}."}
            resp.raise_for_status()
            pr = resp.json()

            files_resp = await client.get(
                f"{GITHUB_API}/repos/{repo}/pulls/{pr_number}/files",
                headers=headers,
                params={"per_page": 50},
            )
            files = []
            if files_resp.status_code == 200:
                files = [
                    {
                        "filename": f["filename"],
                        "status": f["status"],
                        "additions": f["additions"],
                        "deletions": f["deletions"],
                    }
                    for f in files_resp.json()
                ]

            reviews_resp = await client.get(
                f"{GITHUB_API}/repos/{repo}/pulls/{pr_number}/reviews",
                headers=headers,
            )
            reviews = []
            if reviews_resp.status_code == 200:
                reviews = [
                    {
                        "author": rv["user"]["login"],
                        "state": rv["state"],
                        "body": (rv.get("body") or "")[:300],
                    }
                    for rv in reviews_resp.json()
                    if rv["state"] != "PENDING"
                ]

            return {
                "status": "success",
                "pull_request": {
                    "number": pr["number"],
                    "title": pr["title"],
                    "state": pr["state"],
                    "author": pr["user"]["login"],
                    "branch": pr["head"]["ref"],
                    "base": pr["base"]["ref"],
                    "body": (pr.get("body") or "")[:2000],
                    "draft": pr.get("draft", False),
                    "mergeable": pr.get("mergeable"),
                    "additions": pr.get("additions", 0),
                    "deletions": pr.get("deletions", 0),
                    "changed_files": pr.get("changed_files", 0),
                    "created_at": pr["created_at"],
                    "updated_at": pr["updated_at"],
                    "files": files,
                    "reviews": reviews,
                },
            }
    except Exception as e:
        logger.exception("get_github_pull_request failed")
        return {"status": "error", "error": str(e)}


@mcp.tool()
async def read_github_file(
    repo: str,
    path: str,
    ref: str = "",
    tenant_id: str = "",
    ctx: Context = None,
) -> dict:
    """Read a file's content from a GitHub repository.

    Args:
        repo: Full repository name (e.g. "owner/repo-name"). Required.
        path: File path within the repo (e.g. "src/main.py"). Required.
        ref: Branch, tag, or commit SHA (defaults to repo's default branch).
        tenant_id: Tenant UUID (resolved from session if omitted).
        ctx: MCP request context (injected automatically).

    Returns:
        Dict with file content (truncated to 10KB if large).
    """
    tid = resolve_tenant_id(ctx) or tenant_id
    if not tid:
        return {"error": "tenant_id is required."}
    if not repo or not path:
        return {"error": "repo and path are required."}

    token = await _get_github_token(tid)
    if not token:
        return {"status": "error", "error": "GitHub not connected."}

    try:
        params = {}
        if ref:
            params["ref"] = ref

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                f"{GITHUB_API}/repos/{repo}/contents/{path}",
                headers=_gh_headers(token),
                params=params,
            )
            if resp.status_code == 404:
                return {"status": "error", "error": f"File '{path}' not found in {repo}."}
            resp.raise_for_status()
            data = resp.json()

            if data.get("type") == "dir":
                return {
                    "status": "success",
                    "type": "directory",
                    "path": path,
                    "entries": [
                        {"name": e["name"], "type": e["type"], "size": e.get("size", 0)}
                        for e in data
                    ] if isinstance(data, list) else [],
                }

            content = ""
            if data.get("encoding") == "base64" and data.get("content"):
                content = base64.b64decode(data["content"]).decode("utf-8", errors="replace")

            if len(content) > 10000:
                content = content[:10000] + "\n\n... [truncated, file too large] ..."

            return {
                "status": "success",
                "type": "file",
                "path": data.get("path", path),
                "size": data.get("size", 0),
                "content": content,
            }
    except Exception as e:
        logger.exception("read_github_file failed")
        return {"status": "error", "error": str(e)}


@mcp.tool()
async def search_github_code(
    query: str,
    repo: str = "",
    language: str = "",
    max_results: int = 10,
    tenant_id: str = "",
    ctx: Context = None,
) -> dict:
    """Search for code across GitHub repositories.

    Args:
        query: Search query string. Required.
        repo: Limit search to a specific repo (e.g. "owner/repo-name").
        language: Filter by programming language.
        max_results: Maximum results to return.
        tenant_id: Tenant UUID (resolved from session if omitted).
        ctx: MCP request context (injected automatically).

    Returns:
        Dict with matching code snippets and file locations.
    """
    tid = resolve_tenant_id(ctx) or tenant_id
    if not tid:
        return {"error": "tenant_id is required."}
    if not query:
        return {"error": "query is required."}

    token = await _get_github_token(tid)
    if not token:
        return {"status": "error", "error": "GitHub not connected."}

    try:
        q = query
        if repo:
            q += f" repo:{repo}"
        if language:
            q += f" language:{language}"

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                f"{GITHUB_API}/search/code",
                headers=_gh_headers(token),
                params={"q": q, "per_page": min(max_results, 30)},
            )
            resp.raise_for_status()
            data = resp.json()
            return {
                "status": "success",
                "total_count": data.get("total_count", 0),
                "results": [
                    {
                        "repo": item["repository"]["full_name"],
                        "path": item["path"],
                        "name": item["name"],
                        "url": item["html_url"],
                    }
                    for item in data.get("items", [])
                ],
            }
    except Exception as e:
        logger.exception("search_github_code failed")
        return {"status": "error", "error": str(e)}
