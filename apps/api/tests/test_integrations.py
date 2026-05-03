import pytest

# Full FastAPI app + Postgres/pgvector path — see test_api.py for rationale.
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
from unittest.mock import patch

# Set TESTING environment variable for app.main to skip init_db
os.environ["TESTING"] = "True"

# Use a test database
SQLALCHEMY_DATABASE_URL = "sqlite:///./test.db"
engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Override the get_db dependency for tests
def override_get_db():
    try:
        db = TestingSessionLocal()
        yield db
    finally:
        db.close()

app.dependency_overrides[get_db] = override_get_db

client = TestClient(app)

@pytest.fixture(name="db_session")
def db_session_fixture():
    Base.metadata.create_all(bind=engine)
    yield TestingSessionLocal()
    Base.metadata.drop_all(bind=engine)

@pytest.fixture(name="test_user_data")
def test_user_data_fixture():
    return {
        "email": "test@example.com",
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

# --- Integrations Hub Tests ---
@patch('app.services.n8n_service.deploy_workflow')
@patch('app.services.n8n_service.delete_workflow')
def test_get_available_connectors(mock_delete_workflow, mock_deploy_workflow, db_session, test_user_token):
    response = client.get(
        "/api/v1/integrations/available",
        headers={
            "Authorization": f"Bearer {test_user_token}"
        }
    )
    assert response.status_code == 200
    assert len(response.json()) == 2 # Hardcoded connectors
    assert response.json()[0]["name"] == "Salesforce CRM"

@patch('app.services.n8n_service.deploy_workflow')
@patch('app.services.n8n_service.delete_workflow')
def test_create_integration(mock_delete_workflow, mock_deploy_workflow, db_session, test_user_token):
    mock_deploy_workflow.return_value = {"id": "mock-workflow-id"}
    
    # First, get an available connector ID
    available_connectors_response = client.get(
        "/api/v1/integrations/available",
        headers={
            "Authorization": f"Bearer {test_user_token}"
        }
    )
    connector_id = available_connectors_response.json()[0]["id"]

    data = {"name": "My Salesforce Integration", "config": {"instance_url": "https://my.salesforce.com"}, "connector_id": connector_id}
    response = client.post(
        "/api/v1/integrations/",
        json=data,
        headers={
            "Authorization": f"Bearer {test_user_token}"
        }
    )
    assert response.status_code == 201
    assert response.json()["name"] == data["name"]
    assert "n8n_workflow_id" in response.json()["config"]
    mock_deploy_workflow.assert_called_once()

@patch('app.services.n8n_service.deploy_workflow')
@patch('app.services.n8n_service.delete_workflow')
def test_read_integrations(mock_delete_workflow, mock_deploy_workflow, db_session, test_user_token):
    test_create_integration(mock_delete_workflow, mock_deploy_workflow, db_session, test_user_token)
    response = client.get(
        "/api/v1/integrations/",
        headers={
            "Authorization": f"Bearer {test_user_token}"
        }
    )
    assert response.status_code == 200
    assert len(response.json()) > 0

@patch('app.services.n8n_service.deploy_workflow')
@patch('app.services.n8n_service.delete_workflow')
def test_read_integration_by_id(mock_delete_workflow, mock_deploy_workflow, db_session, test_user_token):
    mock_deploy_workflow.return_value = {"id": "mock-workflow-id-2"}

    available_connectors_response = client.get(
        "/api/v1/integrations/available",
        headers={
            "Authorization": f"Bearer {test_user_token}"
        }
    )
    connector_id = available_connectors_response.json()[0]["id"]

    create_response = client.post(
        "/api/v1/integrations/",
        json={"name": "Another Integration", "config": {"api_key": "abc"}, "connector_id": connector_id},
        headers={
            "Authorization": f"Bearer {test_user_token}"
        }
    )
    integration_id = create_response.json()["id"]
    response = client.get(
        f"/api/v1/integrations/{integration_id}",
        headers={
            "Authorization": f"Bearer {test_user_token}"
        }
    )
    assert response.status_code == 200
    assert response.json()["id"] == integration_id

@patch('app.services.n8n_service.deploy_workflow')
@patch('app.services.n8n_service.delete_workflow')
def test_update_integration(mock_delete_workflow, mock_deploy_workflow, db_session, test_user_token):
    mock_deploy_workflow.return_value = {"id": "mock-workflow-id-3"}

    available_connectors_response = client.get(
        "/api/v1/integrations/available",
        headers={
            "Authorization": f"Bearer {test_user_token}"
        }
    )
    connector_id = available_connectors_response.json()[0]["id"]

    create_response = client.post(
        "/api/v1/integrations/",
        json={"name": "Integration to Update", "config": {"token": "old"}, "connector_id": connector_id},
        headers={
            "Authorization": f"Bearer {test_user_token}"
        }
    )
    integration_id = create_response.json()["id"]
    update_data = {"name": "Updated Integration", "config": {"token": "new"}, "connector_id": connector_id}
    response = client.put(
        f"/api/v1/integrations/{integration_id}",
        json=update_data,
        headers={
            "Authorization": f"Bearer {test_user_token}"
        }
    )
    assert response.status_code == 200
    assert response.json()["name"] == update_data["name"]

@patch('app.services.n8n_service.deploy_workflow')
@patch('app.services.n8n_service.delete_workflow')
def test_delete_integration(mock_delete_workflow, mock_deploy_workflow, db_session, test_user_token):
    mock_deploy_workflow.return_value = {"id": "mock-workflow-id-4"}

    available_connectors_response = client.get(
        "/api/v1/integrations/available",
        headers={
            "Authorization": f"Bearer {test_user_token}"
        }
    )
    connector_id = available_connectors_response.json()[0]["id"]

    create_response = client.post(
        "/api/v1/integrations/",
        json={"name": "Integration to Delete", "config": {"token": "delete"}, "connector_id": connector_id},
        headers={
            "Authorization": f"Bearer {test_user_token}"
        }
    )
    integration_id = create_response.json()["id"]
    response = client.delete(
        f"/api/v1/integrations/{integration_id}",
        headers={
            "Authorization": f"Bearer {test_user_token}"
        }
    )
    assert response.status_code == 204
    mock_delete_workflow.assert_called_once_with(response.json()["config"]["n8n_workflow_id"])
    get_response = client.get(
        f"/api/v1/integrations/{integration_id}",
        headers={
            "Authorization": f"Bearer {test_user_token}"
        }
    )
    assert get_response.status_code == 404
