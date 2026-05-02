"""GitHub MCP tools — multi-account aware.

A tenant can connect more than one GitHub account (e.g. personal +
employer). The internal credential vault stores one token per account.
The previous version of this module called the credential endpoint
without specifying ``account_email``, so the API returned the FIRST
matching config — which meant Luna only ever saw repos / issues / files
visible to one of the connected accounts.

This module now treats accounts as first-class:

  * ``_list_github_accounts(tenant_id)`` returns ``[(account_email, token), ...]``
    for every connected GitHub account on the tenant.
  * ``_resolve_accounts(tenant_id, account_email)`` narrows that list to a
    specific account when the caller (or an LLM agent) asks for one.
  * **Listing tools** (``list_github_repos``) **fan out across accounts**
    so a free-form "what repos do I have?" returns the union, with each
    repo tagged by the account that owns the access token.
  * **Resource tools** (``get_github_repo``, ``read_github_file``, etc.)
    **try each account in order until one returns 200**. When the caller
    specifies ``account_email`` we skip the search.

Behind the scenes the per-account token is fetched via the existing
``GET /api/v1/oauth/internal/token/github?account_email=...`` endpoint
which already supported the filter — we simply weren't using it.
"""
import base64
import logging
from typing import List, Optional, Tuple

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


async def _list_github_accounts(tenant_id: str) -> List[Tuple[str, str]]:
    """Return ``[(account_email, oauth_token), ...]`` for every connected
    GitHub account on the tenant. Empty list means no accounts wired or
    every credential lookup failed.

    Calls two internal endpoints in sequence — the connected-accounts
    list, then a per-account token fetch — because the platform stores
    one credential bundle per ``(tenant, integration_name, account_email)``
    row and the token endpoint only returns one bundle per request.
    """
    api_base_url = _get_api_base_url()
    internal_key = _get_internal_key()
    headers = {"X-Internal-Key": internal_key}

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            list_resp = await client.get(
                f"{api_base_url}/api/v1/oauth/internal/connected-accounts/github",
                headers=headers,
                params={"tenant_id": tenant_id},
            )
            if list_resp.status_code != 200:
                logger.warning(
                    "GitHub connected-accounts lookup returned %s for tenant=%s",
                    list_resp.status_code, str(tenant_id)[:8],
                )
                return []

            accounts_payload = list_resp.json().get("accounts") or []
            if not accounts_payload:
                return []

            results: List[Tuple[str, str]] = []
            for acct in accounts_payload:
                email = acct.get("account_email")
                if not email:
                    continue
                tok_resp = await client.get(
                    f"{api_base_url}/api/v1/oauth/internal/token/github",
                    headers=headers,
                    params={"tenant_id": tenant_id, "account_email": email},
                )
                if tok_resp.status_code != 200:
                    logger.warning(
                        "GitHub token fetch failed for tenant=%s account=%s status=%s",
                        str(tenant_id)[:8], email, tok_resp.status_code,
                    )
                    continue
                data = tok_resp.json()
                token = data.get("oauth_token") or data.get("access_token")
                if token:
                    results.append((email, token))
            return results
    except Exception:
        logger.exception("Failed to enumerate GitHub accounts for tenant=%s", str(tenant_id)[:8])
        return []


async def _resolve_accounts(
    tenant_id: str,
    account_email: Optional[str],
) -> List[Tuple[str, str]]:
    """Narrow the account list to the one the caller asked for, or all.

    When ``account_email`` is provided AND matches exactly one connected
    account, the result is a single-tuple list. When provided but not
    matched, returns an empty list (so the caller surfaces a helpful
    error rather than silently fanning out to the wrong account).
    """
    accounts = await _list_github_accounts(tenant_id)
    if not accounts:
        return []
    if account_email:
        return [(e, t) for e, t in accounts if e.lower() == account_email.lower()]
    return accounts


def _gh_headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _no_account_error(account_email: Optional[str]) -> dict:
    """Standardized error when zero accounts can be resolved."""
    if account_email:
        return {
            "status": "error",
            "error": (
                f"GitHub account '{account_email}' is not connected for this tenant. "
                "Connect it on the Integrations page, or omit account_email to use any."
            ),
        }
    return {
        "status": "error",
        "error": "GitHub not connected. Please connect GitHub in Integrations.",
    }


# ---------------------------------------------------------------------------
# MCP Tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def list_github_repos(
    tenant_id: str = "",
    account_email: str = "",
    sort: str = "updated",
    max_results: int = 20,
    ctx: Context = None,
) -> dict:
    """List repositories accessible to the authenticated GitHub user(s).

    When the tenant has multiple GitHub accounts connected, results from
    every account are merged. Each repo entry is tagged with the
    ``account_email`` of the token that surfaced it so an agent can
    disambiguate later (e.g. "open the issue against the Levi repo").

    Args:
        tenant_id: Tenant UUID (resolved from session if omitted).
        account_email: Optional — restrict to a specific connected
            account. When omitted, fans out across all connected accounts.
        sort: Sort by: updated, created, pushed, full_name.
        max_results: Maximum repos PER ACCOUNT (max 100). The merged
            response can return up to N × #accounts results before
            deduplication.
        ctx: MCP request context (injected automatically).

    Returns:
        Dict with status and a merged list of repositories.
    """
    tid = resolve_tenant_id(ctx) or tenant_id
    if not tid:
        return {"error": "tenant_id is required."}

    accounts = await _resolve_accounts(tid, account_email or None)
    if not accounts:
        return _no_account_error(account_email or None)

    seen_full_names: set = set()
    repos_out: list = []
    last_status_code: Optional[int] = None

    async with httpx.AsyncClient(timeout=30.0) as client:
        for email, token in accounts:
            try:
                resp = await client.get(
                    f"{GITHUB_API}/user/repos",
                    headers=_gh_headers(token),
                    params={
                        "sort": sort,
                        "per_page": min(max_results, 100),
                        "type": "all",
                    },
                )
                last_status_code = resp.status_code
                if resp.status_code == 401:
                    logger.warning("GitHub token expired for account=%s tenant=%s", email, str(tid)[:8])
                    continue
                if resp.status_code >= 300:
                    logger.warning(
                        "list_github_repos: account=%s returned %s",
                        email, resp.status_code,
                    )
                    continue
                for r in resp.json():
                    full_name = r.get("full_name")
                    if not full_name or full_name in seen_full_names:
                        continue
                    seen_full_names.add(full_name)
                    repos_out.append({
                        "full_name": full_name,
                        "description": r.get("description") or "",
                        "language": r.get("language"),
                        "updated_at": r.get("updated_at"),
                        "default_branch": r.get("default_branch"),
                        "private": r.get("private"),
                        "open_issues_count": r.get("open_issues_count", 0),
                        "account_email": email,
                    })
            except Exception:
                logger.exception("list_github_repos: account=%s failed", email)

    if not repos_out:
        if last_status_code == 401:
            return {"status": "error", "error": "GitHub tokens expired. Reconnect in Integrations."}
        return {"status": "success", "count": 0, "repos": [], "accounts_queried": [e for e, _ in accounts]}

    return {
        "status": "success",
        "count": len(repos_out),
        "repos": repos_out,
        "accounts_queried": [e for e, _ in accounts],
    }


async def _try_each_account(
    accounts: List[Tuple[str, str]],
    do_request,
):
    """Run ``do_request(token)`` against each account in order until one
    returns a "found" result. Returns ``(account_email, response)`` of
    the first success, or ``(None, last_error_dict)``.

    ``do_request`` is an async callable that takes an oauth token and
    returns either:
      - ``("ok", result_dict)`` → propagate this directly.
      - ``("not_found", err_dict)`` → try next account.
      - ``("error", err_dict)`` → propagate the error (don't try more).

    The "not_found" path is what makes try-each useful — a 404 against
    one account often means the resource lives under a different
    account, so we try that one too.
    """
    last_err: Optional[dict] = None
    for email, token in accounts:
        outcome, payload = await do_request(token)
        if outcome == "ok":
            payload["account_email"] = email
            return email, payload
        if outcome == "error":
            return None, payload
        last_err = payload  # not_found — try next
    return None, last_err or {"status": "error", "error": "Resource not accessible from any connected GitHub account."}


@mcp.tool()
async def get_github_repo(
    repo: str,
    tenant_id: str = "",
    account_email: str = "",
    ctx: Context = None,
) -> dict:
    """Get details about a specific GitHub repository.

    Tries each connected GitHub account in order until one returns 200
    (private repos only visible to the right account). Pass
    ``account_email`` to skip the search.

    Args:
        repo: Full repository name (e.g. "owner/repo-name"). Required.
        tenant_id: Tenant UUID (resolved from session if omitted).
        account_email: Optional — restrict to a specific connected account.
        ctx: MCP request context (injected automatically).
    """
    tid = resolve_tenant_id(ctx) or tenant_id
    if not tid:
        return {"error": "tenant_id is required."}
    if not repo:
        return {"error": "repo is required (e.g. 'owner/repo-name')."}

    accounts = await _resolve_accounts(tid, account_email or None)
    if not accounts:
        return _no_account_error(account_email or None)

    async def _do(token: str):
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                f"{GITHUB_API}/repos/{repo}",
                headers=_gh_headers(token),
            )
        if resp.status_code == 404:
            return ("not_found", {"status": "error", "error": f"Repository '{repo}' not found."})
        if resp.status_code == 401:
            return ("not_found", {"status": "error", "error": "GitHub token expired."})
        if resp.status_code >= 300:
            return ("error", {"status": "error", "error": f"GitHub returned {resp.status_code}", "body": resp.text[:500]})
        r = resp.json()
        return ("ok", {
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
        })

    _, payload = await _try_each_account(accounts, _do)
    return payload


@mcp.tool()
async def list_github_issues(
    repo: str,
    state: str = "open",
    labels: str = "",
    max_results: int = 20,
    tenant_id: str = "",
    account_email: str = "",
    ctx: Context = None,
) -> dict:
    """List issues in a GitHub repository. Tries each connected account
    until the repo is accessible.
    """
    tid = resolve_tenant_id(ctx) or tenant_id
    if not tid:
        return {"error": "tenant_id is required."}
    if not repo:
        return {"error": "repo is required."}

    accounts = await _resolve_accounts(tid, account_email or None)
    if not accounts:
        return _no_account_error(account_email or None)

    params = {
        "state": state,
        "per_page": min(max_results, 100),
        "sort": "updated",
        "direction": "desc",
    }
    if labels:
        params["labels"] = labels

    async def _do(token: str):
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                f"{GITHUB_API}/repos/{repo}/issues",
                headers=_gh_headers(token),
                params=params,
            )
        if resp.status_code == 404:
            return ("not_found", {"status": "error", "error": f"Repository '{repo}' not found."})
        if resp.status_code == 401:
            return ("not_found", {"status": "error", "error": "GitHub token expired."})
        if resp.status_code >= 300:
            return ("error", {"status": "error", "error": f"GitHub returned {resp.status_code}"})
        issues = [i for i in resp.json() if "pull_request" not in i]
        return ("ok", {
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
        })

    _, payload = await _try_each_account(accounts, _do)
    return payload


@mcp.tool()
async def get_github_issue(
    repo: str,
    issue_number: int,
    tenant_id: str = "",
    account_email: str = "",
    ctx: Context = None,
) -> dict:
    """Get details of a specific GitHub issue including comments."""
    tid = resolve_tenant_id(ctx) or tenant_id
    if not tid:
        return {"error": "tenant_id is required."}
    if not repo or not issue_number:
        return {"error": "repo and issue_number are required."}

    accounts = await _resolve_accounts(tid, account_email or None)
    if not accounts:
        return _no_account_error(account_email or None)

    async def _do(token: str):
        headers = _gh_headers(token)
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                f"{GITHUB_API}/repos/{repo}/issues/{issue_number}",
                headers=headers,
            )
            if resp.status_code == 404:
                return ("not_found", {"status": "error", "error": f"Issue #{issue_number} not found in {repo}."})
            if resp.status_code == 401:
                return ("not_found", {"status": "error", "error": "GitHub token expired."})
            if resp.status_code >= 300:
                return ("error", {"status": "error", "error": f"GitHub returned {resp.status_code}"})
            issue = resp.json()
            comments: list = []
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
        return ("ok", {
            "status": "success",
            "issue": {
                "number": issue["number"],
                "title": issue["title"],
                "state": issue["state"],
                "author": issue["user"]["login"],
                "labels": [lbl["name"] for lbl in issue.get("labels", [])],
                "body": (issue.get("body") or "")[:2000],
                "created_at": issue["created_at"],
                "updated_at": issue["updated_at"],
                "comments_count": issue.get("comments", 0),
                "comments": comments,
            },
        })

    _, payload = await _try_each_account(accounts, _do)
    return payload


@mcp.tool()
async def list_github_pull_requests(
    repo: str,
    state: str = "open",
    max_results: int = 20,
    tenant_id: str = "",
    account_email: str = "",
    ctx: Context = None,
) -> dict:
    """List pull requests in a GitHub repository."""
    tid = resolve_tenant_id(ctx) or tenant_id
    if not tid:
        return {"error": "tenant_id is required."}
    if not repo:
        return {"error": "repo is required."}

    accounts = await _resolve_accounts(tid, account_email or None)
    if not accounts:
        return _no_account_error(account_email or None)

    async def _do(token: str):
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
            return ("not_found", {"status": "error", "error": f"Repository '{repo}' not found."})
        if resp.status_code == 401:
            return ("not_found", {"status": "error", "error": "GitHub token expired."})
        if resp.status_code >= 300:
            return ("error", {"status": "error", "error": f"GitHub returned {resp.status_code}"})
        prs = resp.json()
        return ("ok", {
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
        })

    _, payload = await _try_each_account(accounts, _do)
    return payload


@mcp.tool()
async def get_github_pull_request(
    repo: str,
    pr_number: int,
    tenant_id: str = "",
    account_email: str = "",
    ctx: Context = None,
) -> dict:
    """Get details of a specific pull request including files + reviews."""
    tid = resolve_tenant_id(ctx) or tenant_id
    if not tid:
        return {"error": "tenant_id is required."}
    if not repo or not pr_number:
        return {"error": "repo and pr_number are required."}

    accounts = await _resolve_accounts(tid, account_email or None)
    if not accounts:
        return _no_account_error(account_email or None)

    async def _do(token: str):
        headers = _gh_headers(token)
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                f"{GITHUB_API}/repos/{repo}/pulls/{pr_number}",
                headers=headers,
            )
            if resp.status_code == 404:
                return ("not_found", {"status": "error", "error": f"PR #{pr_number} not found in {repo}."})
            if resp.status_code == 401:
                return ("not_found", {"status": "error", "error": "GitHub token expired."})
            if resp.status_code >= 300:
                return ("error", {"status": "error", "error": f"GitHub returned {resp.status_code}"})
            pr = resp.json()
            files: list = []
            files_resp = await client.get(
                f"{GITHUB_API}/repos/{repo}/pulls/{pr_number}/files",
                headers=headers,
                params={"per_page": 50},
            )
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
            reviews: list = []
            reviews_resp = await client.get(
                f"{GITHUB_API}/repos/{repo}/pulls/{pr_number}/reviews",
                headers=headers,
            )
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
        return ("ok", {
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
        })

    _, payload = await _try_each_account(accounts, _do)
    return payload


@mcp.tool()
async def read_github_file(
    repo: str,
    path: str,
    ref: str = "",
    tenant_id: str = "",
    account_email: str = "",
    ctx: Context = None,
) -> dict:
    """Read a file's content from a GitHub repository."""
    tid = resolve_tenant_id(ctx) or tenant_id
    if not tid:
        return {"error": "tenant_id is required."}
    if not repo or not path:
        return {"error": "repo and path are required."}

    accounts = await _resolve_accounts(tid, account_email or None)
    if not accounts:
        return _no_account_error(account_email or None)

    params = {}
    if ref:
        params["ref"] = ref

    async def _do(token: str):
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                f"{GITHUB_API}/repos/{repo}/contents/{path}",
                headers=_gh_headers(token),
                params=params,
            )
        if resp.status_code == 404:
            return ("not_found", {"status": "error", "error": f"File '{path}' not found in {repo}."})
        if resp.status_code == 401:
            return ("not_found", {"status": "error", "error": "GitHub token expired."})
        if resp.status_code >= 300:
            return ("error", {"status": "error", "error": f"GitHub returned {resp.status_code}"})
        data = resp.json()
        if data.get("type") == "dir":
            return ("ok", {
                "status": "success",
                "type": "directory",
                "path": path,
                "entries": [
                    {"name": e["name"], "type": e["type"], "size": e.get("size", 0)}
                    for e in data
                ] if isinstance(data, list) else [],
            })
        content = ""
        if data.get("encoding") == "base64" and data.get("content"):
            content = base64.b64decode(data["content"]).decode("utf-8", errors="replace")
        if len(content) > 10000:
            content = content[:10000] + "\n\n... [truncated, file too large] ..."
        return ("ok", {
            "status": "success",
            "type": "file",
            "path": data.get("path", path),
            "size": data.get("size", 0),
            "content": content,
        })

    _, payload = await _try_each_account(accounts, _do)
    return payload


@mcp.tool()
async def search_github_code(
    query: str,
    repo: str = "",
    language: str = "",
    max_results: int = 10,
    tenant_id: str = "",
    account_email: str = "",
    ctx: Context = None,
) -> dict:
    """Search for code across GitHub repositories.

    Each account sees a different set of private repos, so when no
    ``account_email`` is specified we run the search against every
    connected account and merge results (deduplicating by html_url).
    """
    tid = resolve_tenant_id(ctx) or tenant_id
    if not tid:
        return {"error": "tenant_id is required."}
    if not query:
        return {"error": "query is required."}

    accounts = await _resolve_accounts(tid, account_email or None)
    if not accounts:
        return _no_account_error(account_email or None)

    q = query
    if repo:
        q += f" repo:{repo}"
    if language:
        q += f" language:{language}"

    seen_urls: set = set()
    merged_results: list = []
    total_count = 0

    async with httpx.AsyncClient(timeout=30.0) as client:
        for email, token in accounts:
            try:
                resp = await client.get(
                    f"{GITHUB_API}/search/code",
                    headers=_gh_headers(token),
                    params={"q": q, "per_page": min(max_results, 30)},
                )
                if resp.status_code >= 300:
                    logger.warning(
                        "search_github_code: account=%s returned %s",
                        email, resp.status_code,
                    )
                    continue
                data = resp.json()
                total_count = max(total_count, data.get("total_count", 0))
                for item in data.get("items", []):
                    url = item.get("html_url")
                    if not url or url in seen_urls:
                        continue
                    seen_urls.add(url)
                    merged_results.append({
                        "repo": item["repository"]["full_name"],
                        "path": item["path"],
                        "name": item["name"],
                        "url": url,
                        "account_email": email,
                    })
            except Exception:
                logger.exception("search_github_code: account=%s failed", email)

    return {
        "status": "success",
        "total_count": total_count,
        "results": merged_results[:max_results],
        "accounts_queried": [e for e, _ in accounts],
    }


# ---------------------------------------------------------------------------
# Backward-compat helper (deprecated — use _resolve_accounts)
# ---------------------------------------------------------------------------

async def _get_github_token(
    tenant_id: str,
    account_email: Optional[str] = None,
) -> Optional[str]:
    """Deprecated single-token getter retained for any external callers
    that may still import it. Prefer ``_resolve_accounts`` so multi-account
    semantics are honored.

    When ``account_email`` is provided, returns that account's token.
    When omitted, returns the first connected account's token (legacy).
    """
    accounts = await _resolve_accounts(tenant_id, account_email)
    if not accounts:
        return None
    return accounts[0][1]
