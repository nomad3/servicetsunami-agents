"""Tests for src.mcp_tools.drive.

Four Google Drive tools backed by httpx. We stub ``_get_drive_token``
so the auth lookup never hits the API, then patch ``httpx.AsyncClient``
to script the Drive REST responses.
"""
from __future__ import annotations

import pytest

from src.mcp_tools import drive as dr


@pytest.fixture
def patch_drive(monkeypatch, make_client):
    """Stub _get_drive_token + httpx.AsyncClient. Returns (client, holder)."""

    def _install(token="oauth-tok", side_effect=None, default_status=200, default_json=None):
        async def _get_token(tid, account_email=""):
            return token

        monkeypatch.setattr(dr, "_get_drive_token", _get_token)

        client = make_client(
            default_status=default_status,
            default_json=default_json,
            side_effect=side_effect,
        )
        monkeypatch.setattr(dr.httpx, "AsyncClient", lambda *a, **kw: client)
        return client

    return _install


# ---------------------------------------------------------------------------
# search_drive_files
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_search_drive_files_no_token_returns_error(monkeypatch, mock_ctx):
    async def _none(tid, account_email=""):
        return None

    monkeypatch.setattr(dr, "_get_drive_token", _none)
    out = await dr.search_drive_files(tenant_id="t", ctx=mock_ctx)
    assert "error" in out


@pytest.mark.asyncio
async def test_search_drive_files_returns_file_records(patch_drive, mock_ctx):
    patch_drive(
        default_status=200,
        default_json={
            "files": [
                {
                    "id": "f-1",
                    "name": "report.pdf",
                    "mimeType": "application/pdf",
                    "size": 100,
                    "modifiedTime": "2026-05-01",
                    "webViewLink": "http://x",
                }
            ]
        },
    )
    out = await dr.search_drive_files(tenant_id="t", ctx=mock_ctx, query="name contains 'report'")
    assert out["status"] == "success"
    assert out["total"] == 1
    assert out["files"][0]["id"] == "f-1"
    assert out["files"][0]["type"] == "application/pdf"


@pytest.mark.asyncio
async def test_search_drive_files_token_expired(patch_drive, mock_ctx):
    patch_drive(default_status=401, default_json={})
    out = await dr.search_drive_files(tenant_id="t", ctx=mock_ctx)
    assert "expired" in out["error"]


# ---------------------------------------------------------------------------
# read_drive_file — branches by mime type
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_read_drive_file_google_doc_uses_export(patch_drive, mock_ctx):
    from tests.conftest import _DummyResponse  # type: ignore

    def _side_effect(method, url, kwargs):
        if "/files/f-1" in url and "export" not in url and "alt=media" not in str(kwargs):
            # metadata fetch
            return _DummyResponse(200, {
                "id": "f-1", "name": "Doc", "mimeType": "application/vnd.google-apps.document",
            })
        if "/export" in url:
            return _DummyResponse(200, {}, text="EXPORTED TEXT")
        return _DummyResponse(200, {}, text="")

    patch_drive(side_effect=_side_effect)
    out = await dr.read_drive_file(file_id="f-1", tenant_id="t", ctx=mock_ctx)
    assert out["status"] == "success"
    assert out["content"] == "EXPORTED TEXT"


@pytest.mark.asyncio
async def test_read_drive_file_spreadsheet_exports_csv(patch_drive, mock_ctx):
    from tests.conftest import _DummyResponse  # type: ignore

    def _side_effect(method, url, kwargs):
        if "/export" in url:
            params = kwargs.get("params", {})
            assert params.get("mimeType") == "text/csv"
            return _DummyResponse(200, {}, text="a,b,c")
        return _DummyResponse(200, {
            "id": "f-1", "name": "Sheet",
            "mimeType": "application/vnd.google-apps.spreadsheet",
        })

    patch_drive(side_effect=_side_effect)
    out = await dr.read_drive_file(file_id="f-1", tenant_id="t", ctx=mock_ctx)
    assert out["content"] == "a,b,c"


@pytest.mark.asyncio
async def test_read_drive_file_presentation_exports_text(patch_drive, mock_ctx):
    from tests.conftest import _DummyResponse  # type: ignore

    def _side_effect(method, url, kwargs):
        if "/export" in url:
            return _DummyResponse(200, {}, text="slide text")
        return _DummyResponse(200, {
            "id": "f-1", "name": "Slides",
            "mimeType": "application/vnd.google-apps.presentation",
        })

    patch_drive(side_effect=_side_effect)
    out = await dr.read_drive_file(file_id="f-1", tenant_id="t", ctx=mock_ctx)
    assert out["content"] == "slide text"


@pytest.mark.asyncio
async def test_read_drive_file_regular_downloads(patch_drive, mock_ctx):
    from tests.conftest import _DummyResponse  # type: ignore

    def _side_effect(method, url, kwargs):
        if kwargs.get("params", {}).get("alt") == "media":
            return _DummyResponse(200, {}, text="raw bytes as text")
        return _DummyResponse(200, {
            "id": "f-1", "name": "x.txt", "mimeType": "text/plain",
        })

    patch_drive(side_effect=_side_effect)
    out = await dr.read_drive_file(file_id="f-1", tenant_id="t", ctx=mock_ctx)
    assert out["content"] == "raw bytes as text"


@pytest.mark.asyncio
async def test_read_drive_file_metadata_404(patch_drive, mock_ctx):
    from tests.conftest import _DummyResponse  # type: ignore

    def _side_effect(method, url, kwargs):
        return _DummyResponse(404, {})

    patch_drive(side_effect=_side_effect)
    out = await dr.read_drive_file(file_id="f-x", tenant_id="t", ctx=mock_ctx)
    assert "not found" in out["error"]


@pytest.mark.asyncio
async def test_read_drive_file_truncates_large_content(patch_drive, mock_ctx):
    from tests.conftest import _DummyResponse  # type: ignore
    big = "y" * 20000

    def _side_effect(method, url, kwargs):
        if "/export" in url:
            return _DummyResponse(200, {}, text=big)
        return _DummyResponse(200, {
            "id": "f-1", "name": "doc",
            "mimeType": "application/vnd.google-apps.document",
        })

    patch_drive(side_effect=_side_effect)
    out = await dr.read_drive_file(file_id="f-1", tenant_id="t", ctx=mock_ctx)
    assert len(out["content"]) == 10000
    assert out["truncated"] is True


# ---------------------------------------------------------------------------
# create_drive_file
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_drive_file_happy_path(patch_drive, mock_ctx):
    patch_drive(default_status=200, default_json={"id": "new-1"})
    out = await dr.create_drive_file(
        name="x.txt", content="hello", tenant_id="t", ctx=mock_ctx
    )
    assert out["status"] == "success"
    assert out["id"] == "new-1"


@pytest.mark.asyncio
async def test_create_drive_file_fallback_metadata_then_patch(patch_drive, mock_ctx):
    """If first multipart call fails, the fallback path POSTs metadata
    then PATCHes content. The final return surfaces the new file id."""
    from tests.conftest import _DummyResponse  # type: ignore

    state = {"first": True}

    def _side_effect(method, url, kwargs):
        if method == "POST" and "upload/drive/v3/files" in url and state["first"]:
            state["first"] = False
            return _DummyResponse(500, {})
        if method == "POST" and "drive/v3/files" in url:
            return _DummyResponse(200, {"id": "new-2"})
        if method == "PATCH":
            return _DummyResponse(200, {})
        return _DummyResponse(500, {})

    patch_drive(side_effect=_side_effect)
    out = await dr.create_drive_file(
        name="x.txt", content="hi", folder_id="folder-1",
        tenant_id="t", ctx=mock_ctx
    )
    assert out["status"] == "success"
    assert out["id"] == "new-2"


@pytest.mark.asyncio
async def test_create_drive_file_google_doc_uses_multipart_with_text_source(
    patch_drive, mock_ctx,
):
    """Bug fix 2026-05-20: when target mime is the native Google Doc
    type, the multipart body must declare the SOURCE part as text/plain
    (not the Doc mime). Drive converts on import using metadata.mimeType.
    Without this, the upload silently produced empty Docs."""
    from tests.conftest import _DummyResponse  # type: ignore

    seen = {}

    def _side_effect(method, url, kwargs):
        if method == "POST" and "upload/drive/v3/files" in url:
            seen["body"] = kwargs.get("content", b"").decode("utf-8")
            seen["ct"] = kwargs.get("headers", {}).get("Content-Type", "")
            return _DummyResponse(200, {"id": "doc-1"})
        return _DummyResponse(500, {})

    patch_drive(side_effect=_side_effect)
    out = await dr.create_drive_file(
        name="Report.gdoc",
        content="# Quarterly numbers",
        mime_type="application/vnd.google-apps.document",
        tenant_id="t",
        ctx=mock_ctx,
    )
    assert out["status"] == "success"
    assert out["id"] == "doc-1"
    assert seen["ct"].startswith("multipart/related; boundary=")
    assert "application/vnd.google-apps.document" in seen["body"]
    assert "Content-Type: text/plain" in seen["body"]
    assert "# Quarterly numbers" in seen["body"]


@pytest.mark.asyncio
async def test_create_drive_file_google_doc_failure_does_not_fallback(
    patch_drive, mock_ctx,
):
    """For native Doc targets the media-PATCH fallback is invalid (it
    creates an empty Doc). Confirm we surface the error instead of
    silently producing a broken Doc."""
    from tests.conftest import _DummyResponse  # type: ignore

    calls = {"posts": 0, "patches": 0}

    def _side_effect(method, url, kwargs):
        if method == "POST":
            calls["posts"] += 1
        if method == "PATCH":
            calls["patches"] += 1
        return _DummyResponse(500, {}, text="boom")

    patch_drive(side_effect=_side_effect)
    out = await dr.create_drive_file(
        name="Report",
        content="x",
        mime_type="application/vnd.google-apps.document",
        tenant_id="t", ctx=mock_ctx,
    )
    assert "error" in out
    assert calls["posts"] == 1  # only the multipart upload, no fallback
    assert calls["patches"] == 0


@pytest.mark.asyncio
async def test_create_drive_file_metadata_create_fails(patch_drive, mock_ctx):
    """Both initial multipart and fallback POST fail → error response."""
    from tests.conftest import _DummyResponse  # type: ignore

    def _side_effect(method, url, kwargs):
        return _DummyResponse(500, {})

    patch_drive(side_effect=_side_effect)
    out = await dr.create_drive_file(
        name="x.txt", content="hi", tenant_id="t", ctx=mock_ctx
    )
    assert "error" in out


# ---------------------------------------------------------------------------
# list_drive_folders
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_drive_folders_returns_folders(patch_drive, mock_ctx):
    patch_drive(
        default_status=200,
        default_json={"files": [{"id": "fld-1", "name": "Reports", "modifiedTime": "x"}]},
    )
    out = await dr.list_drive_folders(tenant_id="t", ctx=mock_ctx)
    assert out["status"] == "success"
    assert out["folders"][0]["name"] == "Reports"


@pytest.mark.asyncio
async def test_list_drive_folders_http_error(patch_drive, mock_ctx):
    patch_drive(default_status=500, default_json={})
    out = await dr.list_drive_folders(tenant_id="t", ctx=mock_ctx)
    assert "error" in out
