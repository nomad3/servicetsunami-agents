"""Email MCP tools — Gmail and Outlook.

Email MCP tools for Gmail and Outlook.
Uses httpx.AsyncClient to call Gmail/Outlook APIs via stored OAuth tokens fetched from
the internal credential vault endpoint.
"""
import base64
import html
import json
import logging
import os
import re
import uuid as _uuid
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText
from typing import Optional

import asyncpg
import httpx
from mcp.server.fastmcp import Context

from src.mcp_app import mcp
from src.mcp_auth import resolve_tenant_id

logger = logging.getLogger(__name__)

EMAIL_INTEGRATIONS = ("gmail", "outlook")

# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def _api_base_url() -> str:
    return os.environ.get("API_BASE_URL", "http://api:8000")


def _mcp_api_key() -> str:
    return os.environ.get("MCP_API_KEY", "dev_mcp_key")


def _get_db_url() -> str:
    """Return asyncpg-compatible DATABASE_URL."""
    from src.config import settings
    url = settings.DATABASE_URL or os.environ.get("DATABASE_URL", "")
    return (
        url.replace("postgresql+asyncpg://", "postgresql://")
           .replace("postgresql+psycopg2://", "postgresql://")
    )


# ---------------------------------------------------------------------------
# OAuth / account helpers
# ---------------------------------------------------------------------------

async def _get_oauth_token(
    tenant_id: str, integration_name: str, account_email: Optional[str] = None,
) -> Optional[str]:
    """Retrieve decrypted OAuth access token from the API credential vault."""
    params: dict = {"tenant_id": tenant_id}
    if account_email:
        params["account_email"] = account_email
    try:
        async with httpx.AsyncClient(base_url=_api_base_url(), timeout=30.0) as client:
            resp = await client.get(
                f"/api/v1/oauth/internal/token/{integration_name}",
                headers={"X-Internal-Key": _mcp_api_key()},
                params=params,
            )
            if resp.status_code == 200:
                return resp.json().get("oauth_token")
            logger.warning("Token retrieval for %s returned %s", integration_name, resp.status_code)
    except Exception:
        logger.exception("Failed to retrieve %s token", integration_name)
    return None


async def _get_connected_accounts_for_integration(
    tenant_id: str, integration_name: str,
) -> list[dict]:
    try:
        async with httpx.AsyncClient(base_url=_api_base_url(), timeout=30.0) as client:
            resp = await client.get(
                f"/api/v1/oauth/internal/connected-accounts/{integration_name}",
                headers={"X-Internal-Key": _mcp_api_key()},
                params={"tenant_id": tenant_id},
            )
            if resp.status_code != 200:
                logger.warning(
                    "Connected accounts lookup for %s returned %s",
                    integration_name, resp.status_code,
                )
                return []

            accounts = resp.json().get("accounts", [])
            normalized = []
            for account in accounts:
                email = account.get("account_email")
                normalized.append({
                    "email": email,
                    "account_email": email,
                    "integration_name": integration_name,
                    "provider": "google" if integration_name == "gmail" else "microsoft",
                    "enabled": account.get("enabled", True),
                })
            return normalized
    except Exception:
        logger.exception("Failed to list accounts for %s", integration_name)
        return []


async def _get_all_connected_email_accounts(tenant_id: str) -> list[dict]:
    accounts: list[dict] = []
    for integration_name in EMAIL_INTEGRATIONS:
        accounts.extend(await _get_connected_accounts_for_integration(tenant_id, integration_name))
    return accounts


async def _resolve_email_account(
    tenant_id: str, account_email: str = "",
) -> tuple[Optional[dict], Optional[str]]:
    accounts = await _get_all_connected_email_accounts(tenant_id)
    if not accounts:
        return None, "No email accounts connected. Ask the user to connect Gmail or Outlook in Connected Apps."

    if account_email:
        account = next((a for a in accounts if a.get("email") == account_email), None)
        if not account:
            return None, f"No connected email account found for {account_email}."
        return account, None

    return accounts[0], None


# ---------------------------------------------------------------------------
# Text / body helpers
# ---------------------------------------------------------------------------

def _escape_odata_string(value: str) -> str:
    return value.replace("'", "''")


def _strip_html(content: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", content, flags=re.IGNORECASE)
    text = re.sub(r"</p\s*>", "\n\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    return html.unescape(re.sub(r"\s+\n", "\n", re.sub(r"[ \t]+", " ", text))).strip()


def _extract_body(payload: dict) -> str:
    """Recursively extract plain text body from Gmail message payload."""
    mime = payload.get("mimeType", "")

    if mime == "text/plain" and payload.get("body", {}).get("data"):
        return base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="replace")

    for part in payload.get("parts", []):
        text = _extract_body(part)
        if text:
            return text

    # Fallback: try HTML
    if mime == "text/html" and payload.get("body", {}).get("data"):
        return base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="replace")

    return ""


def _extract_attachments(payload: dict) -> list:
    """Extract attachment metadata from Gmail message payload."""
    attachments = []

    def _walk(part):
        filename = part.get("filename", "")
        body = part.get("body", {})
        attachment_id = body.get("attachmentId")
        if filename and attachment_id:
            attachments.append({
                "attachment_id": attachment_id,
                "filename": filename,
                "mime_type": part.get("mimeType", ""),
                "size": body.get("size", 0),
            })
        for sub in part.get("parts", []):
            _walk(sub)

    _walk(payload)
    return attachments


def _build_outlook_search(query: str, max_results: int) -> tuple[dict, dict]:
    params = {
        "$top": min(max_results, 20),
        "$orderby": "receivedDateTime DESC",
        "$select": "id,subject,from,receivedDateTime,bodyPreview,isRead",
    }
    headers: dict = {"Prefer": 'outlook.body-content-type="text"'}
    if not query:
        return params, headers

    filters = []
    search_terms = []
    tokens = re.findall(r'(?:[^\s"]+|"[^"]*")+', query)

    for token in tokens:
        raw = token.strip()
        cleaned = raw.strip('"')
        lower = cleaned.lower()

        if lower.startswith("from:"):
            email = _escape_odata_string(cleaned[5:])
            filters.append(f"from/emailAddress/address eq '{email}'")
        elif lower.startswith("to:"):
            email = _escape_odata_string(cleaned[3:])
            filters.append(f"toRecipients/any(r:r/emailAddress/address eq '{email}')")
        elif lower.startswith("subject:"):
            subject = _escape_odata_string(cleaned[8:])
            filters.append(f"contains(subject,'{subject}')")
        elif lower.startswith("newer_than:"):
            match = re.fullmatch(r"newer_than:(\d+)([dh])", lower)
            if match:
                amount = int(match.group(1))
                unit = match.group(2)
                delta = timedelta(days=amount) if unit == "d" else timedelta(hours=amount)
                cutoff = (datetime.now(timezone.utc) - delta).strftime("%Y-%m-%dT%H:%M:%SZ")
                filters.append(f"receivedDateTime ge {cutoff}")
        elif lower == "is:unread":
            filters.append("isRead eq false")
        elif ":" not in cleaned:
            search_terms.append(cleaned)

    if filters:
        params["$filter"] = " and ".join(filters)
    if search_terms:
        params["$search"] = f"\"{' '.join(search_terms)}\""
        headers["ConsistencyLevel"] = "eventual"
        params.pop("$orderby", None)
    return params, headers


# ---------------------------------------------------------------------------
# Entity extraction helper
# ---------------------------------------------------------------------------

def _extract_email_entities(headers: dict, body: str, account_email: str) -> list[dict]:
    """Extract entities from a single email using Python heuristics (no LLM)."""
    entities = []
    seen_emails: set = set()

    def _parse_address(addr_str: str) -> list[tuple[str, str]]:
        results = []
        for match in re.finditer(r'([^<,;]+?)\s*<([^>]+)>', addr_str):
            name = match.group(1).strip().strip('"\'')
            email = match.group(2).strip().lower()
            if email and email != account_email.lower() and '@' in email:
                results.append((name, email))
        # Bare emails without names
        for match in re.finditer(r'[\w.+-]+@[\w-]+\.[\w.-]+', addr_str):
            email = match.group(0).lower()
            if email not in {e for _, e in results} and email != account_email.lower():
                results.append(("", email))
        return results

    for field in ["From", "To", "Cc", "Reply-To"]:
        value = headers.get(field, "")
        for name, email in _parse_address(value):
            if email in seen_emails:
                continue
            seen_emails.add(email)
            if not name:
                name = email.split("@")[0].replace(".", " ").replace("-", " ").title()
            domain = email.split("@")[1] if "@" in email else ""
            entities.append({
                "name": name,
                "entity_type": "person",
                "category": "contact",
                "description": f"Email contact: {email}",
                "properties": {"email": email, "domain": domain, "source": "email_scan"},
            })
            common_domains = {
                "gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "icloud.com",
                "live.com", "me.com", "aol.com", "protonmail.com",
            }
            if domain and domain not in common_domains and domain not in seen_emails:
                seen_emails.add(domain)
                org_name = domain.split(".")[0].title()
                entities.append({
                    "name": org_name,
                    "entity_type": "organization",
                    "category": "company",
                    "description": f"Organization from email domain: {domain}",
                    "properties": {"domain": domain, "source": "email_scan"},
                })

    return entities


# ---------------------------------------------------------------------------
# Embedding helper (best-effort, asyncpg direct)
# ---------------------------------------------------------------------------

async def _get_embedding(text: str, task_type: str = "document") -> Optional[list]:
    """Generate a 768-dim embedding via nomic-embed-text-v1.5 (local, no API key)."""
    try:
        from sentence_transformers import SentenceTransformer
        _model_cache = getattr(_get_embedding, "_model", None)
        if _model_cache is None:
            _get_embedding._model = SentenceTransformer(
                "nomic-ai/nomic-embed-text-v1.5", trust_remote_code=True
            )
        model = _get_embedding._model
        prefix = "search_document: " if task_type == "document" else "search_query: "
        prefixed = f"{prefix}{text[:8000]}"
        embedding = model.encode(prefixed, normalize_embeddings=True)
        return embedding.tolist()
    except Exception as e:
        logger.warning("Embedding generation skipped: %s", e)
        return None


async def _embed_attachment_content(
    tenant_id: str,
    message_id: str,
    attachment_id: str,
    text_content: str,
) -> None:
    """Embed attachment text into the embeddings table for semantic search (best-effort)."""
    if not text_content or text_content.startswith("("):
        return

    db_url = _get_db_url()
    if not db_url:
        return

    try:
        embedding = await _get_embedding(text_content[:8000])
        if embedding is None:
            return

        content_id = f"{message_id}_{attachment_id}"
        emb_id = str(_uuid.uuid4())

        conn = await asyncpg.connect(db_url)
        try:
            await conn.execute(
                "DELETE FROM embeddings WHERE content_type = 'email_attachment' AND content_id = $1",
                content_id,
            )
            await conn.execute(
                """
                INSERT INTO embeddings
                (id, tenant_id, content_type, content_id, embedding, text_content, task_type, model, created_at, updated_at)
                VALUES ($1, $2, 'email_attachment', $3, $4::vector, $5, 'RETRIEVAL_DOCUMENT',
                        'nomic-ai/nomic-embed-text-v1.5', NOW(), NOW())
                """,
                emb_id, tenant_id, content_id, str(embedding), text_content[:8000],
            )
        finally:
            await conn.close()
        logger.info("Embedded attachment %s for tenant %s", content_id, tenant_id[:8])
    except Exception:
        logger.warning("Attachment embedding failed (best-effort)", exc_info=True)


# ---------------------------------------------------------------------------
# MCP Tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def list_connected_email_accounts(
    tenant_id: str = "",
    ctx: Context = None,
) -> dict:
    """List all email accounts connected for this tenant.

    Use this to discover which email accounts are available before searching.
    When the user asks about 'work email' or 'personal email', use this to find
    the right account_email and pass it to search_emails or read_email.

    Args:
        tenant_id: Tenant UUID (resolved from session if omitted).
        ctx: MCP request context (injected automatically).

    Returns:
        Dict with list of connected email accounts and count.
    """
    tid = resolve_tenant_id(ctx) or tenant_id
    try:
        accounts = await _get_all_connected_email_accounts(tid)
        return {"accounts": accounts, "count": len(accounts)}
    except Exception as e:
        logger.exception("list_connected_email_accounts failed")
        return {"error": str(e)}


@mcp.tool()
async def search_emails(
    query: str = "",
    tenant_id: str = "",
    max_results: int = 10,
    account_email: str = "",
    ctx: Context = None,
) -> dict:
    """Search Gmail or Outlook for emails matching a query.

    Args:
        query: Gmail-style search query e.g. 'from:alice@example.com', 'subject:invoice',
               'is:unread', 'newer_than:2d'. Outlook filters are translated automatically.
        tenant_id: Tenant UUID (resolved from session if omitted).
        max_results: Maximum number of emails to return (1-20, default 10).
        account_email: Specific email account to search e.g. 'user@company.com'.
                       If empty, searches the default (first) connected account.
                       Use list_connected_email_accounts to discover available accounts.
        ctx: MCP request context (injected automatically).

    Returns:
        Dict with list of email summaries (subject, from, date, snippet).
    """
    tid = resolve_tenant_id(ctx) or tenant_id
    account, error = await _resolve_email_account(tid, account_email)
    if error:
        return {"error": error}

    integration_name = account["integration_name"]
    token = await _get_oauth_token(tid, integration_name, account.get("email"))
    if not token:
        return {"error": f"{integration_name.title()} not connected. Ask the user to reconnect it in Connected Apps."}

    auth = {"Authorization": f"Bearer {token}"}

    try:
        async with httpx.AsyncClient(timeout=30.0) as provider_client:
            if integration_name == "gmail":
                params: dict = {"maxResults": min(max_results, 20)}
                if query:
                    params["q"] = query
                resp = await provider_client.get(
                    "https://gmail.googleapis.com/gmail/v1/users/me/messages",
                    headers=auth,
                    params=params,
                )
                resp.raise_for_status()
                data = resp.json()
                messages = data.get("messages", [])

                if not messages:
                    return {"status": "success", "emails": [], "message": "No emails found."}

                emails = []
                for msg in messages:
                    detail = await provider_client.get(
                        f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{msg['id']}",
                        headers=auth,
                        params=[
                            ("format", "metadata"),
                            ("metadataHeaders", "Subject"),
                            ("metadataHeaders", "From"),
                            ("metadataHeaders", "Date"),
                        ],
                    )
                    if detail.status_code != 200:
                        continue
                    md = detail.json()
                    hdrs = {h["name"]: h["value"] for h in md.get("payload", {}).get("headers", [])}
                    labels = md.get("labelIds", [])
                    emails.append({
                        "id": msg["id"],
                        "subject": hdrs.get("Subject", "(no subject)"),
                        "from": hdrs.get("From", ""),
                        "date": hdrs.get("Date", ""),
                        "snippet": md.get("snippet", ""),
                        "is_read": "UNREAD" not in labels,
                        "provider": "google",
                        "account_email": account.get("email"),
                    })

                return {"status": "success", "emails": emails, "total": data.get("resultSizeEstimate", len(emails))}

            # Outlook
            ol_params, extra_headers = _build_outlook_search(query, max_results)
            resp = await provider_client.get(
                "https://graph.microsoft.com/v1.0/me/messages",
                headers={**auth, **extra_headers},
                params=ol_params,
            )
            resp.raise_for_status()
            data = resp.json()
            emails_ol = [{
                "id": item.get("id"),
                "subject": item.get("subject") or "(no subject)",
                "from": (item.get("from") or {}).get("emailAddress", {}).get("address", ""),
                "date": item.get("receivedDateTime", ""),
                "snippet": item.get("bodyPreview", ""),
                "is_read": item.get("isRead", False),
                "provider": "microsoft",
                "account_email": account.get("email"),
            } for item in data.get("value", [])]

            if not emails_ol:
                return {"status": "success", "emails": [], "message": "No emails found."}

            return {"status": "success", "emails": emails_ol, "total": len(emails_ol)}

    except httpx.HTTPStatusError as e:
        if e.response.status_code == 401:
            return {"error": f"{integration_name.title()} token expired. Ask user to reconnect it in Connected Apps."}
        return {"error": f"{integration_name.title()} API error: {e.response.status_code}"}
    except Exception as e:
        logger.exception("search_emails failed")
        return {"error": f"Failed to search emails: {str(e)}"}


@mcp.tool()
async def read_email(
    message_id: str,
    tenant_id: str = "",
    account_email: str = "",
    ctx: Context = None,
) -> dict:
    """Read the full content of a specific email by its message ID.

    Auto-extracts contact and organization entities from email headers (Python, no LLM).

    Args:
        message_id: Message ID from search_emails results.
        tenant_id: Tenant UUID (resolved from session if omitted).
        account_email: Specific email account to read from. Use the same account
                       that was used in search_emails to find this message.
        ctx: MCP request context (injected automatically).

    Returns:
        Dict with email subject, from, to, date, body text, and attachments list.
    """
    tid = resolve_tenant_id(ctx) or tenant_id
    if not message_id:
        return {"error": "message_id is required. Use search_emails first to get message IDs."}

    account, error = await _resolve_email_account(tid, account_email)
    if error:
        return {"error": error}

    integration_name = account["integration_name"]
    token = await _get_oauth_token(tid, integration_name, account.get("email"))
    if not token:
        return {"error": f"{integration_name.title()} not connected. Ask the user to reconnect it in Connected Apps."}

    auth = {"Authorization": f"Bearer {token}"}

    try:
        async with httpx.AsyncClient(timeout=30.0) as provider_client:
            if integration_name == "gmail":
                resp = await provider_client.get(
                    f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{message_id}",
                    headers=auth,
                    params={"format": "full"},
                )
                resp.raise_for_status()
                msg = resp.json()

                hdrs = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
                body = _extract_body(msg.get("payload", {}))
                attachments = _extract_attachments(msg.get("payload", {}))

                # Auto-extract entities from headers (Python, no LLM)
                entities_created = 0
                db_url = _get_db_url()
                if db_url:
                    try:
                        entities = _extract_email_entities(hdrs, body, account.get("email", ""))
                        if entities:
                            conn = await asyncpg.connect(db_url)
                            try:
                                for ent in entities:
                                    existing = await conn.fetchrow(
                                        """
                                        SELECT id FROM knowledge_entities
                                        WHERE tenant_id = $1 AND name = $2 AND entity_type = $3
                                        LIMIT 1
                                        """,
                                        tid, ent["name"], ent["entity_type"],
                                    )
                                    if not existing:
                                        ent_id = str(_uuid.uuid4())
                                        embedding = await _get_embedding(
                                            f"{ent['name']} {ent.get('description', '')}"
                                        )
                                        if embedding:
                                            await conn.execute(
                                                """
                                                INSERT INTO knowledge_entities
                                                (id, tenant_id, name, entity_type, category, description,
                                                 properties, confidence, embedding, created_at, updated_at)
                                                VALUES ($1, $2, $3, $4, $5, $6, $7, 0.7, $8::vector, NOW(), NOW())
                                                """,
                                                ent_id, tid, ent["name"], ent["entity_type"],
                                                ent.get("category", "contact"),
                                                ent.get("description", ""),
                                                json.dumps(ent.get("properties", {})),
                                                str(embedding),
                                            )
                                        else:
                                            await conn.execute(
                                                """
                                                INSERT INTO knowledge_entities
                                                (id, tenant_id, name, entity_type, category, description,
                                                 properties, confidence, created_at, updated_at)
                                                VALUES ($1, $2, $3, $4, $5, $6, $7, 0.7, NOW(), NOW())
                                                """,
                                                ent_id, tid, ent["name"], ent["entity_type"],
                                                ent.get("category", "contact"),
                                                ent.get("description", ""),
                                                json.dumps(ent.get("properties", {})),
                                            )
                                        entities_created += 1
                            finally:
                                await conn.close()
                    except Exception:
                        logger.debug("Auto entity extraction from read_email failed", exc_info=True)

                result = {
                    "status": "success",
                    "id": message_id,
                    "subject": hdrs.get("Subject", "(no subject)"),
                    "from": hdrs.get("From", ""),
                    "to": hdrs.get("To", ""),
                    "date": hdrs.get("Date", ""),
                    "body": body[:5000],
                    "labels": msg.get("labelIds", []),
                    "attachments": attachments,
                    "provider": "google",
                    "account_email": account.get("email"),
                }
                if entities_created:
                    result["entities_auto_extracted"] = entities_created
                return result

            # Outlook
            resp = await provider_client.get(
                f"https://graph.microsoft.com/v1.0/me/messages/{message_id}",
                headers={**auth, "Prefer": 'outlook.body-content-type="text"'},
                params={
                    "$select": "subject,from,toRecipients,receivedDateTime,body,bodyPreview,internetMessageHeaders",
                },
            )
            resp.raise_for_status()
            msg = resp.json()
            body_ol = (msg.get("body") or {}).get("content") or msg.get("bodyPreview", "")
            if (msg.get("body") or {}).get("contentType", "").lower() == "html":
                body_ol = _strip_html(body_ol)

            return {
                "status": "success",
                "id": message_id,
                "subject": msg.get("subject") or "(no subject)",
                "from": (msg.get("from") or {}).get("emailAddress", {}).get("address", ""),
                "to": ", ".join(
                    recipient.get("emailAddress", {}).get("address", "")
                    for recipient in msg.get("toRecipients", [])
                ),
                "date": msg.get("receivedDateTime", ""),
                "body": body_ol[:5000],
                "labels": [],
                "provider": "microsoft",
                "account_email": account.get("email"),
            }

    except httpx.HTTPStatusError as e:
        if e.response.status_code == 401:
            return {"error": f"{integration_name.title()} token expired. Ask user to reconnect it."}
        return {"error": f"{integration_name.title()} API error: {e.response.status_code}"}
    except Exception as e:
        logger.exception("read_email failed")
        return {"error": f"Failed to read email: {str(e)}"}


@mcp.tool()
async def send_email(
    to: str,
    subject: str,
    body: str,
    tenant_id: str = "",
    account_email: str = "",
    ctx: Context = None,
) -> dict:
    """Send an email via Gmail or Outlook.

    Args:
        to: Recipient email address.
        subject: Email subject line.
        body: Email body text (plain text).
        tenant_id: Tenant UUID (resolved from session if omitted).
        account_email: Specific email account to send from. If empty, uses default account.
        ctx: MCP request context (injected automatically).

    Returns:
        Dict with send status and message ID.
    """
    tid = resolve_tenant_id(ctx) or tenant_id
    if not to or not subject:
        return {"error": "Both 'to' and 'subject' are required."}

    account, error = await _resolve_email_account(tid, account_email)
    if error:
        return {"error": error}

    integration_name = account["integration_name"]
    token = await _get_oauth_token(tid, integration_name, account.get("email"))
    if not token:
        return {"error": f"{integration_name.title()} not connected. Ask the user to reconnect it in Connected Apps."}

    auth = {"Authorization": f"Bearer {token}"}

    try:
        async with httpx.AsyncClient(timeout=30.0) as provider_client:
            if integration_name == "gmail":
                message = MIMEText(body)
                message["to"] = to
                message["subject"] = subject
                raw = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")

                resp = await provider_client.post(
                    "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
                    headers=auth,
                    json={"raw": raw},
                )
                resp.raise_for_status()
                result = resp.json()

                return {
                    "status": "success",
                    "message_id": result.get("id"),
                    "message": f"Email sent to {to}.",
                    "provider": "google",
                    "account_email": account.get("email"),
                }

            # Outlook
            resp = await provider_client.post(
                "https://graph.microsoft.com/v1.0/me/sendMail",
                headers={**auth, "Content-Type": "application/json"},
                json={
                    "message": {
                        "subject": subject,
                        "body": {
                            "contentType": "Text",
                            "content": body,
                        },
                        "toRecipients": [
                            {"emailAddress": {"address": to}},
                        ],
                    },
                    "saveToSentItems": True,
                },
            )
            resp.raise_for_status()
            return {
                "status": "success",
                "message_id": None,
                "message": f"Email sent to {to}.",
                "provider": "microsoft",
                "account_email": account.get("email"),
            }

    except httpx.HTTPStatusError as e:
        if e.response.status_code == 401:
            return {"error": f"{integration_name.title()} token expired. Ask user to reconnect it."}
        return {"error": f"{integration_name.title()} send failed: {e.response.status_code}"}
    except Exception as e:
        logger.exception("send_email failed")
        return {"error": f"Failed to send email: {str(e)}"}


@mcp.tool()
async def download_attachment(
    message_id: str,
    attachment_id: str,
    tenant_id: str = "",
    account_email: str = "",
    ctx: Context = None,
) -> dict:
    """Download a Gmail attachment and return its extracted text content.

    Use read_email first to get attachment_id and filename from the attachments list.
    Supports PDF, text, CSV, spreadsheets, and common document formats.
    Returns extracted text content (not raw binary). Embeds content for semantic search.

    Args:
        message_id: Gmail message ID (from search_emails or read_email).
        attachment_id: Attachment ID (from read_email attachments list).
        tenant_id: Tenant UUID (resolved from session if omitted).
        account_email: Optional email account to use.
        ctx: MCP request context (injected automatically).

    Returns:
        Dict with size, content (extracted text), and truncated flag.
    """
    tid = resolve_tenant_id(ctx) or tenant_id
    if not message_id or not attachment_id:
        return {"error": "message_id and attachment_id are required. Use read_email first."}

    account, error = await _resolve_email_account(tid, account_email)
    if error:
        return {"error": error}

    integration_name = account["integration_name"]
    if integration_name != "gmail":
        return {"error": "Attachment download is only supported for Gmail accounts."}

    token = await _get_oauth_token(tid, integration_name, account.get("email"))
    if not token:
        return {"error": "Gmail not connected. Ask the user to reconnect it in Connected Apps."}

    auth = {"Authorization": f"Bearer {token}"}

    try:
        async with httpx.AsyncClient(timeout=30.0) as provider_client:
            resp = await provider_client.get(
                f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{message_id}/attachments/{attachment_id}",
                headers=auth,
            )
            resp.raise_for_status()
            data = resp.json()

            raw_bytes = base64.urlsafe_b64decode(data.get("data", ""))
            size = len(raw_bytes)

            text_content: Optional[str] = None

            # Plain text / CSV / code files
            try:
                text_content = raw_bytes.decode("utf-8", errors="replace")
            except Exception:
                pass

            # PDF extraction
            if text_content and text_content.startswith("%PDF"):
                text_content = None
                try:
                    import io
                    import pdfplumber
                    with pdfplumber.open(io.BytesIO(raw_bytes)) as pdf:
                        pages = []
                        for page in pdf.pages:
                            page_text = page.extract_text()
                            if page_text:
                                pages.append(page_text)
                        text_content = "\n\n".join(pages) if pages else "(PDF has no extractable text)"
                except Exception as e:
                    text_content = f"(Could not extract PDF text: {e})"

            # Spreadsheet extraction (xlsx, xls)
            if text_content is None:
                try:
                    import io
                    import pandas as pd
                    df = pd.read_excel(io.BytesIO(raw_bytes))
                    text_content = df.to_string(max_rows=200)
                except Exception:
                    pass

            if text_content is None:
                text_content = f"(Binary file, {size} bytes — cannot extract text)"

            # Embed the extracted text for future semantic search (best-effort)
            await _embed_attachment_content(
                tenant_id=tid,
                message_id=message_id,
                attachment_id=attachment_id,
                text_content=text_content,
            )

            return {
                "status": "success",
                "size": size,
                "content": text_content[:10000],
                "truncated": len(text_content) > 10000 if text_content else False,
                "account_email": account.get("email"),
            }

    except httpx.HTTPStatusError as e:
        return {"error": f"Gmail API error: {e.response.status_code} {e.response.text[:200]}"}
    except Exception as e:
        return {"error": f"Failed to download attachment: {str(e)}"}


@mcp.tool()
async def deep_scan_emails(
    tenant_id: str = "",
    days: int = 365,
    max_emails: int = 500,
    account_email: str = "",
    ctx: Context = None,
) -> dict:
    """Bulk scan emails and extract entities WITHOUT using LLM per email.

    This tool does all heavy lifting in Python:
    1. Fetches emails in PAGINATED batches via Gmail API (handles 1000+ emails)
    2. Extracts people + organizations from headers using regex (no LLM)
    3. Stores entities in the knowledge graph via direct DB operations
    4. Embeds entity descriptions for semantic search
    5. Returns a summary

    Much faster and cheaper than reading emails one by one through the LLM.

    Args:
        tenant_id: Tenant UUID (resolved from session if omitted).
        days: How many days back to scan (default 365 — one full year).
        max_emails: Maximum emails to process (default 500).
        account_email: Specific account to scan. If empty, scans all connected accounts.
        ctx: MCP request context (injected automatically).

    Returns:
        Dict with counts of emails scanned, entities created, and relations created.
    """
    tid = resolve_tenant_id(ctx) or tenant_id

    # Discover connected accounts
    if account_email:
        account, error = await _resolve_email_account(tid, account_email)
        if error:
            return {"error": error}
        accounts = [account]
    else:
        accounts = await _get_all_connected_email_accounts(tid)
        if not accounts:
            return {"error": "No email accounts connected. Ask user to connect Gmail in Connected Apps."}

    db_url = _get_db_url()

    total_scanned = 0
    total_entities_created = 0
    total_relations_created = 0
    all_entity_names: list = []

    async with httpx.AsyncClient(timeout=30.0) as provider_client:
        for account in accounts:
            if account["integration_name"] != "gmail":
                continue

            token = await _get_oauth_token(tid, account["integration_name"], account.get("email"))
            if not token:
                continue

            auth = {"Authorization": f"Bearer {token}"}
            acct_email = account.get("email", "")

            try:
                # Paginate through Gmail API to get all messages
                messages = []
                page_token = None
                remaining = max_emails

                while remaining > 0:
                    params = {
                        "maxResults": min(remaining, 100),
                        "q": f"newer_than:{days}d",
                    }
                    if page_token:
                        params["pageToken"] = page_token

                    resp = await provider_client.get(
                        "https://gmail.googleapis.com/gmail/v1/users/me/messages",
                        headers=auth,
                        params=params,
                    )
                    if resp.status_code != 200:
                        break
                    data = resp.json()
                    batch = data.get("messages", [])
                    messages.extend(batch)
                    remaining -= len(batch)

                    page_token = data.get("nextPageToken")
                    if not page_token or not batch:
                        break

                all_entities: list = []
                for msg in messages:
                    try:
                        detail = await provider_client.get(
                            f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{msg['id']}",
                            headers=auth,
                            params=[
                                ("format", "metadata"),
                                ("metadataHeaders", "From"),
                                ("metadataHeaders", "To"),
                                ("metadataHeaders", "Cc"),
                                ("metadataHeaders", "Reply-To"),
                                ("metadataHeaders", "Subject"),
                                ("metadataHeaders", "Date"),
                            ],
                        )
                        if detail.status_code != 200:
                            continue

                        md = detail.json()
                        hdrs = {h["name"]: h["value"] for h in md.get("payload", {}).get("headers", [])}
                        entities = _extract_email_entities(hdrs, md.get("snippet", ""), acct_email)
                        all_entities.extend(entities)
                        total_scanned += 1
                    except Exception:
                        continue

                # Deduplicate by email/domain
                seen: set = set()
                unique_entities: list = []
                for ent in all_entities:
                    key = ent["properties"].get("email") or ent["properties"].get("domain", ent["name"])
                    if key not in seen:
                        seen.add(key)
                        unique_entities.append(ent)

                # Store entities in knowledge graph via asyncpg
                if unique_entities and db_url:
                    try:
                        conn = await asyncpg.connect(db_url)
                        try:
                            for ent in unique_entities:
                                existing = await conn.fetchrow(
                                    """
                                    SELECT id FROM knowledge_entities
                                    WHERE tenant_id = $1 AND name = $2 AND entity_type = $3
                                    LIMIT 1
                                    """,
                                    tid, ent["name"], ent["entity_type"],
                                )

                                if existing:
                                    await conn.execute(
                                        """
                                        UPDATE knowledge_entities
                                        SET properties = properties || $1::jsonb, updated_at = NOW()
                                        WHERE id = $2
                                        """,
                                        json.dumps(ent.get("properties", {})), str(existing["id"]),
                                    )
                                    continue

                                ent_id = str(_uuid.uuid4())
                                embed_text = f"{ent['name']} {ent.get('description', '')}"
                                embedding = await _get_embedding(embed_text)

                                if embedding:
                                    await conn.execute(
                                        """
                                        INSERT INTO knowledge_entities
                                        (id, tenant_id, name, entity_type, category, description,
                                         properties, confidence, embedding, created_at, updated_at)
                                        VALUES ($1, $2, $3, $4, $5, $6, $7, 0.7, $8::vector, NOW(), NOW())
                                        """,
                                        ent_id, tid, ent["name"], ent["entity_type"],
                                        ent.get("category", "contact"), ent.get("description", ""),
                                        json.dumps(ent.get("properties", {})), str(embedding),
                                    )
                                else:
                                    await conn.execute(
                                        """
                                        INSERT INTO knowledge_entities
                                        (id, tenant_id, name, entity_type, category, description,
                                         properties, confidence, created_at, updated_at)
                                        VALUES ($1, $2, $3, $4, $5, $6, $7, 0.7, NOW(), NOW())
                                        """,
                                        ent_id, tid, ent["name"], ent["entity_type"],
                                        ent.get("category", "contact"), ent.get("description", ""),
                                        json.dumps(ent.get("properties", {})),
                                    )
                                total_entities_created += 1
                                all_entity_names.append(ent["name"])

                                # Create "works_at" relation for people with organizations
                                if ent["entity_type"] == "person" and ent["properties"].get("domain"):
                                    domain = ent["properties"]["domain"]
                                    org = await conn.fetchrow(
                                        """
                                        SELECT id FROM knowledge_entities
                                        WHERE tenant_id = $1 AND entity_type = 'organization'
                                        AND properties->>'domain' = $2
                                        LIMIT 1
                                        """,
                                        tid, domain,
                                    )
                                    if org:
                                        rel_id = str(_uuid.uuid4())
                                        try:
                                            await conn.execute(
                                                """
                                                INSERT INTO knowledge_relations
                                                (id, tenant_id, from_entity_id, to_entity_id,
                                                 relation_type, strength, created_at)
                                                VALUES ($1, $2, $3, $4, 'works_at', 0.8, NOW())
                                                ON CONFLICT DO NOTHING
                                                """,
                                                rel_id, tid, ent_id, str(org["id"]),
                                            )
                                            total_relations_created += 1
                                        except Exception:
                                            pass
                        finally:
                            await conn.close()
                    except Exception:
                        logger.exception("Failed to store entities from email scan")

            except Exception:
                logger.exception("Email scan failed for %s", acct_email)

    return {
        "status": "success",
        "emails_scanned": total_scanned,
        "entities_created": total_entities_created,
        "relations_created": total_relations_created,
        "sample_entities": all_entity_names[:20],
        "accounts_scanned": len(accounts),
    }
