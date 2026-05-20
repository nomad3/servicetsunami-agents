"""Google Drive MCP tools.

Search, list, read, and manage files in Google Drive via the Drive API v3.
"""
import json
import logging
import os
import uuid as _uuid
from typing import Optional

import httpx
from mcp.server.fastmcp import Context

GOOGLE_DOC_MIME = "application/vnd.google-apps.document"

from src.mcp_app import mcp
from src.mcp_auth import resolve_tenant_id

logger = logging.getLogger(__name__)

API_BASE_URL = os.environ.get("API_BASE_URL", "http://api:8000")
API_INTERNAL_KEY = os.environ.get("MCP_API_KEY", "dev_mcp_key")


async def _get_drive_token(tenant_id: str, account_email: str = "") -> Optional[str]:
    params = {"tenant_id": tenant_id}
    if account_email:
        params["account_email"] = account_email
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(
            f"{API_BASE_URL}/api/v1/oauth/internal/token/google_drive",
            headers={"X-Internal-Key": API_INTERNAL_KEY},
            params=params,
        )
        if resp.status_code == 200:
            return resp.json().get("oauth_token")
    return None


@mcp.tool()
async def search_drive_files(
    query: str = "",
    max_results: int = 10,
    tenant_id: str = "",
    account_email: str = "",
    ctx: Context = None,
) -> dict:
    """Search files in Google Drive.

    Args:
        query: Search query (Google Drive search syntax, e.g., "name contains 'report'",
               "mimeType='application/pdf'", "modifiedTime > '2026-01-01'").
               Leave empty to list recent files.
        max_results: Maximum files to return (default 10, max 50).
        tenant_id: Tenant UUID.
        account_email: Specific Google account.
    """
    tid = resolve_tenant_id(ctx) or tenant_id
    token = await _get_drive_token(tid, account_email)
    if not token:
        return {"error": "Google Drive not connected. Connect Google in Integrations."}

    params = {
        "pageSize": min(max_results, 50),
        "fields": "files(id,name,mimeType,size,modifiedTime,webViewLink,parents,shared)",
        "orderBy": "modifiedTime desc",
    }
    if query:
        params["q"] = query

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(
            "https://www.googleapis.com/drive/v3/files",
            headers={"Authorization": f"Bearer {token}"},
            params=params,
        )
        if resp.status_code == 401:
            return {"error": "Drive token expired. Reconnect Google in Integrations."}
        resp.raise_for_status()
        data = resp.json()

    files = []
    for f in data.get("files", []):
        files.append({
            "id": f["id"],
            "name": f["name"],
            "type": f.get("mimeType", ""),
            "size": f.get("size"),
            "modified": f.get("modifiedTime"),
            "link": f.get("webViewLink"),
            "shared": f.get("shared", False),
        })

    return {"status": "success", "files": files, "total": len(files)}


@mcp.tool()
async def read_drive_file(
    file_id: str,
    tenant_id: str = "",
    account_email: str = "",
    ctx: Context = None,
) -> dict:
    """Read the content of a Google Drive file (text, docs, spreadsheets).

    For Google Docs/Sheets/Slides, exports as plain text.
    For other files, downloads and returns text content.

    Args:
        file_id: File ID from search_drive_files.
        tenant_id: Tenant UUID.
        account_email: Specific Google account.
    """
    tid = resolve_tenant_id(ctx) or tenant_id
    token = await _get_drive_token(tid, account_email)
    if not token:
        return {"error": "Google Drive not connected."}

    headers = {"Authorization": f"Bearer {token}"}

    async with httpx.AsyncClient(timeout=30.0) as client:
        # Get file metadata first
        meta_resp = await client.get(
            f"https://www.googleapis.com/drive/v3/files/{file_id}",
            headers=headers,
            params={"fields": "id,name,mimeType,size"},
        )
        if meta_resp.status_code != 200:
            return {"error": f"File not found: {meta_resp.status_code}"}

        meta = meta_resp.json()
        mime = meta.get("mimeType", "")
        name = meta.get("name", "")

        # Google Docs → export as text
        if "google-apps.document" in mime:
            resp = await client.get(
                f"https://www.googleapis.com/drive/v3/files/{file_id}/export",
                headers=headers,
                params={"mimeType": "text/plain"},
            )
        elif "google-apps.spreadsheet" in mime:
            resp = await client.get(
                f"https://www.googleapis.com/drive/v3/files/{file_id}/export",
                headers=headers,
                params={"mimeType": "text/csv"},
            )
        elif "google-apps.presentation" in mime:
            resp = await client.get(
                f"https://www.googleapis.com/drive/v3/files/{file_id}/export",
                headers=headers,
                params={"mimeType": "text/plain"},
            )
        else:
            # Regular file — download
            resp = await client.get(
                f"https://www.googleapis.com/drive/v3/files/{file_id}",
                headers=headers,
                params={"alt": "media"},
            )

        if resp.status_code != 200:
            return {"error": f"Failed to read file: {resp.status_code}"}

        content = resp.text[:10000]
        return {
            "status": "success",
            "name": name,
            "type": mime,
            "content": content,
            "truncated": len(resp.text) > 10000,
        }


@mcp.tool()
async def create_drive_file(
    name: str,
    content: str,
    mime_type: str = "text/plain",
    folder_id: str = "",
    tenant_id: str = "",
    account_email: str = "",
    ctx: Context = None,
) -> dict:
    """Create a new file in Google Drive.

    Args:
        name: File name (e.g., "report.txt", "notes.md").
        content: File content as text.
        mime_type: MIME type (default text/plain). Use application/vnd.google-apps.document for Google Docs.
        folder_id: Parent folder ID. Empty for root.
        tenant_id: Tenant UUID.
        account_email: Specific Google account.
    """
    tid = resolve_tenant_id(ctx) or tenant_id
    token = await _get_drive_token(tid, account_email)
    if not token:
        return {"error": "Google Drive not connected."}

    headers = {"Authorization": f"Bearer {token}"}
    metadata = {"name": name, "mimeType": mime_type}
    if folder_id:
        metadata["parents"] = [folder_id]

    # For native Google Doc targets, the source body must be uploaded as
    # text/plain (or text/html). Drive then converts on import because
    # metadata.mimeType = application/vnd.google-apps.document. Sending
    # the doc mime as the source Content-Type produces a 400 — that is
    # the bug user testing flagged on 2026-05-20: report generated +
    # emailed fine but the Drive→Doc upload silently dropped content.
    is_native_doc = mime_type == GOOGLE_DOC_MIME
    source_mime = "text/plain" if is_native_doc else mime_type

    boundary = f"agentprovision-{_uuid.uuid4().hex}"
    body = (
        f"--{boundary}\r\n"
        f"Content-Type: application/json; charset=UTF-8\r\n\r\n"
        f"{json.dumps(metadata)}\r\n"
        f"--{boundary}\r\n"
        f"Content-Type: {source_mime}\r\n\r\n"
        f"{content}\r\n"
        f"--{boundary}--"
    ).encode("utf-8")

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            "https://www.googleapis.com/upload/drive/v3/files",
            headers={
                **headers,
                "Content-Type": f"multipart/related; boundary={boundary}",
            },
            params={"uploadType": "multipart"},
            content=body,
        )
        if resp.status_code == 200:
            return {
                "status": "success",
                "id": resp.json().get("id"),
                "name": name,
            }

        # Fallback for non-native targets only: create metadata then
        # PATCH media. uploadType=media is invalid against a native
        # Google Doc (the document body is owned by the Docs API, not
        # the Drive bytes channel), so for Doc targets we surface the
        # original error instead of producing an empty Doc.
        if is_native_doc:
            return {
                "error": (
                    f"Failed to create Google Doc: {resp.status_code} "
                    f"{resp.text[:200]}"
                )
            }

        create_resp = await client.post(
            "https://www.googleapis.com/drive/v3/files",
            headers={**headers, "Content-Type": "application/json"},
            json=metadata,
        )
        if create_resp.status_code != 200:
            return {
                "error": f"Failed to create file: {create_resp.status_code}"
            }

        file_id = create_resp.json()["id"]
        await client.patch(
            f"https://www.googleapis.com/upload/drive/v3/files/{file_id}",
            headers={**headers, "Content-Type": mime_type},
            params={"uploadType": "media"},
            content=content.encode(),
        )
        return {"status": "success", "id": file_id, "name": name}


@mcp.tool()
async def list_drive_folders(
    parent_id: str = "root",
    tenant_id: str = "",
    account_email: str = "",
    ctx: Context = None,
) -> dict:
    """List folders in Google Drive.

    Args:
        parent_id: Parent folder ID (default "root" for top-level).
        tenant_id: Tenant UUID.
        account_email: Specific Google account.
    """
    tid = resolve_tenant_id(ctx) or tenant_id
    token = await _get_drive_token(tid, account_email)
    if not token:
        return {"error": "Google Drive not connected."}

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(
            "https://www.googleapis.com/drive/v3/files",
            headers={"Authorization": f"Bearer {token}"},
            params={
                "q": f"'{parent_id}' in parents and mimeType='application/vnd.google-apps.folder'",
                "fields": "files(id,name,modifiedTime)",
                "orderBy": "name",
                "pageSize": 50,
            },
        )
        if resp.status_code != 200:
            return {"error": f"Failed to list folders: {resp.status_code}"}

    folders = [{"id": f["id"], "name": f["name"], "modified": f.get("modifiedTime")} for f in resp.json().get("files", [])]
    return {"status": "success", "folders": folders}
