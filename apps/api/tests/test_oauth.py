import os
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_db
from app.api.v1.oauth import _refresh_access_token
from app.core.config import settings
from app.db.base import Base
from app.db.session import SessionLocal, engine
from app.main import app

os.environ["TESTING"] = "True"


def override_get_db():
    try:
        db = SessionLocal()
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db
client = TestClient(app)


@pytest.fixture(name="db_session")
def db_session_fixture():
    Base.metadata.create_all(bind=engine)
    yield SessionLocal()
    Base.metadata.drop_all(bind=engine)


@pytest.fixture(name="test_user_data")
def test_user_data_fixture():
    return {
        "email": "oauth-test@example.com",
        "password": "testpassword",
        "full_name": "OAuth Test User",
        "tenant_name": "OAuth Test Tenant",
    }


@pytest.fixture(name="test_user_token")
def test_user_token_fixture(db_session, test_user_data):
    client.post(
        "/api/v1/auth/register",
        json={
            "user_in": {
                "email": test_user_data["email"],
                "password": test_user_data["password"],
                "full_name": test_user_data["full_name"],
            },
            "tenant_in": {
                "name": test_user_data["tenant_name"],
            },
        },
    )
    response = client.post(
        "/api/v1/auth/login",
        data={
            "username": test_user_data["email"],
            "password": test_user_data["password"],
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    return response.json()["access_token"]


def test_registry_includes_outlook_oauth(db_session, test_user_token):
    response = client.get(
        "/api/v1/integration-configs/registry",
        headers={"Authorization": f"Bearer {test_user_token}"},
    )

    assert response.status_code == 200
    outlook = next(
        item for item in response.json()
        if item["integration_name"] == "outlook"
    )
    assert outlook["auth_type"] == "oauth"
    assert outlook["oauth_provider"] == "microsoft"


def test_microsoft_authorize_returns_expected_auth_url(db_session, test_user_token):
    with patch.object(settings, "MICROSOFT_CLIENT_ID", "ms-client-id"), patch.object(
        settings, "MICROSOFT_CLIENT_SECRET", "ms-client-secret"
    ), patch.object(
        settings, "MICROSOFT_REDIRECT_URI", "http://localhost:8001/api/v1/oauth/microsoft/callback"
    ):
        response = client.get(
            "/api/v1/oauth/microsoft/authorize",
            headers={"Authorization": f"Bearer {test_user_token}"},
        )

    assert response.status_code == 200
    auth_url = response.json()["auth_url"]
    parsed = urlparse(auth_url)
    params = parse_qs(parsed.query)
    scopes = set(params["scope"][0].split(" "))

    assert parsed.scheme == "https"
    assert parsed.netloc == "login.microsoftonline.com"
    assert parsed.path.endswith("/oauth2/v2.0/authorize")
    assert params["client_id"] == ["ms-client-id"]
    assert params["redirect_uri"] == ["http://localhost:8001/api/v1/oauth/microsoft/callback"]
    assert params["response_type"] == ["code"]
    assert params["prompt"] == ["select_account"]
    assert params["response_mode"] == ["query"]
    assert "state" in params
    assert {"Mail.Read", "Mail.Send", "User.Read", "offline_access"}.issubset(scopes)


def test_refresh_access_token_supports_microsoft_rotation():
    mock_response = MagicMock()
    mock_response.raise_for_status.return_value = None
    mock_response.json.return_value = {
        "access_token": "new-access-token",
        "refresh_token": "rotated-refresh-token",
    }
    mock_client = MagicMock()
    mock_client.post.return_value = mock_response

    with patch.object(settings, "MICROSOFT_CLIENT_ID", "ms-client-id"), patch.object(
        settings, "MICROSOFT_CLIENT_SECRET", "ms-client-secret"
    ), patch("app.api.v1.oauth.httpx.Client") as mock_httpx_client:
        mock_httpx_client.return_value.__enter__.return_value = mock_client
        tokens = _refresh_access_token("microsoft", "old-refresh-token")

    assert tokens == {
        "access_token": "new-access-token",
        "refresh_token": "rotated-refresh-token",
    }
    _, kwargs = mock_client.post.call_args
    assert kwargs["data"]["grant_type"] == "refresh_token"
    assert kwargs["data"]["refresh_token"] == "old-refresh-token"
    assert "Mail.Read" in kwargs["data"]["scope"]
