import pytest
import os

# Full FastAPI app + Postgres/pgvector path — see test_api.py for rationale.
pytestmark = pytest.mark.integration

# Set TESTING environment variable BEFORE importing app
os.environ["TESTING"] = "True"

from fastapi.testclient import TestClient
from app.main import app
from app.core.config import settings
import tempfile

client = TestClient(app)

def test_serve_dataset_file_requires_auth():
    """Test internal endpoint requires MCP_API_KEY"""
    response = client.get("/api/v1/internal/storage/datasets/test.parquet")
    assert response.status_code == 401

def test_serve_dataset_file_with_invalid_key():
    """Test invalid API key returns 401"""
    response = client.get(
        "/api/v1/internal/storage/datasets/test.parquet",
        headers={"Authorization": "Bearer wrong-key"}
    )
    assert response.status_code == 401

def test_serve_dataset_file_not_found():
    """Test 404 for non-existent file"""
    response = client.get(
        "/api/v1/internal/storage/datasets/nonexistent.parquet",
        headers={"Authorization": f"Bearer {settings.MCP_API_KEY}"}
    )
    assert response.status_code == 404

def test_serve_dataset_file_prevents_directory_traversal():
    """Test directory traversal attack prevention"""
    # Test with encoded path traversal in filename
    response = client.get(
        "/api/v1/internal/storage/datasets/..%2F..%2F..%2Fetc%2Fpasswd",
        headers={"Authorization": f"Bearer {settings.MCP_API_KEY}"}
    )
    assert response.status_code in [400, 404]  # Either blocked by our check or not found

def test_serve_dataset_file_success(tmp_path):
    """Test successful file serving"""
    # Create temp parquet file
    test_file = tmp_path / "test123.parquet"
    test_file.write_bytes(b"test parquet data")

    # Mock DATA_STORAGE_PATH to tmp_path
    original_path = settings.DATA_STORAGE_PATH
    settings.DATA_STORAGE_PATH = str(tmp_path.parent)

    # Create datasets subdirectory
    datasets_dir = tmp_path.parent / "datasets"
    datasets_dir.mkdir(exist_ok=True)
    (datasets_dir / "test123.parquet").write_bytes(b"test parquet data")

    try:
        response = client.get(
            "/api/v1/internal/storage/datasets/test123.parquet",
            headers={"Authorization": f"Bearer {settings.MCP_API_KEY}"}
        )
        assert response.status_code == 200
        assert response.content == b"test parquet data"
    finally:
        settings.DATA_STORAGE_PATH = original_path
