import pytest

# This suite drives the full FastAPI app + Postgres/pgvector stack via TestClient.
# It cannot run on a stock SQLite engine (JSONB columns, pgvector indexes), so we
# mark the whole module as integration. Default `pytest -m "not integration"`
# runs skip it; the dedicated integration job in tests.yaml runs it against a
# real Postgres service container.
pytestmark = pytest.mark.integration

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.main import app
from app.db.base import Base
from app.db.session import SessionLocal, engine
from app.api.deps import get_db
from app.core.config import settings
import os
import uuid

# TESTING=True + PYTEST_CURRENT_TEST together route session.py to the
# dedicated agentprovision_test database on localhost:8003. See
# app/db/session.py for the gating logic.
os.environ["TESTING"] = "True"

# Override the get_db dependency for tests
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
    Base.metadata.create_all(bind=engine) # Create tables for tests
    yield SessionLocal()
    Base.metadata.drop_all(bind=engine) # Drop tables after tests

@pytest.fixture(name="test_user_data")
def test_user_data_fixture():
    return {
        "email": f"test-{uuid.uuid4().hex[:8]}@example.com",
        "password": "testpassword",
        "full_name": "Test User",
        "tenant_name": "Test Tenant"
    }

@pytest.fixture(name="test_user_token")
def test_user_token_fixture(db_session, test_user_data):
    # Register a user
    client.post(
        "/api/v1/auth/register",
        json={
            "user_in": {
                "email": test_user_data["email"],
                "password": test_user_data["password"],
                "full_name": test_user_data["full_name"]
            },
            "tenant_in": {
                "name": test_user_data["tenant_name"]
            }
        }
    )
    # Log in to get a token
    response = client.post(
        "/api/v1/auth/login",
        data={
            "username": test_user_data["email"],
            "password": test_user_data["password"]
        },
        headers={
            "Content-Type": "application/x-www-form-urlencoded"
        }
    )
    return response.json()["access_token"]

def test_create_user_and_tenant(db_session, test_user_data):
    response = client.post(
        "/api/v1/auth/register",
        json={
            "user_in": {
                "email": test_user_data["email"],
                "password": test_user_data["password"],
                "full_name": test_user_data["full_name"]
            },
            "tenant_in": {
                "name": test_user_data["tenant_name"]
            }
        }
    )
    assert response.status_code == 200
    assert response.json()["email"] == test_user_data["email"]
    assert "id" in response.json()
    assert "tenant_id" in response.json()

def test_login_for_access_token(db_session, test_user_data):
    # Ensure user is registered first
    test_create_user_and_tenant(db_session, test_user_data)

    response = client.post(
        "/api/v1/auth/login",
        data={
            "username": test_user_data["email"],
            "password": test_user_data["password"]
        },
        headers={
            "Content-Type": "application/x-www-form-urlencoded"
        }
    )
    assert response.status_code == 200
    assert "access_token" in response.json()
    assert response.json()["token_type"] == "bearer"

def test_read_users_me(db_session, test_user_token):
    response = client.get(
        "/api/v1/auth/users/me",
        headers={
            "Authorization": f"Bearer {test_user_token}"
        }
    )
    assert response.status_code == 200
    assert "email" in response.json()
    assert "id" in response.json()

def test_get_analytics_summary(db_session, test_user_token):
    response = client.get(
        "/api/v1/analytics/summary",
        headers={
            "Authorization": f"Bearer {test_user_token}"
        }
    )
    assert response.status_code == 200
    assert "total_agents" in response.json()
    assert "total_data_sources" in response.json()
    assert "total_data_pipelines" in response.json()
    assert "total_notebooks" in response.json()