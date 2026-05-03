"""Tests for multi-provider LLM integration."""
import pytest

# Drives full app + Postgres/pgvector — see test_api.py for rationale.
pytestmark = pytest.mark.integration

from sqlalchemy import inspect
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
import os

# Set TESTING environment variable for app.main to skip init_db
os.environ["TESTING"] = "True"

from app.main import app
from app.db.base import Base
from app.db.session import SessionLocal, engine
from app.api.deps import get_db


# Override the get_db dependency for tests
def override_get_db():
    try:
        db = SessionLocal()
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db


@pytest.fixture(name="client")
def client_fixture():
    """Create a test client."""
    return TestClient(app)


@pytest.fixture(name="db_session")
def db_session_fixture():
    """Create a database session for tests."""
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    yield db
    db.close()
    Base.metadata.drop_all(bind=engine)


@pytest.fixture(name="auth_headers")
def auth_headers_fixture(db_session, client):
    """Create auth headers with a valid token."""
    # Register a user
    client.post(
        "/api/v1/auth/register",
        json={
            "user_in": {
                "email": "test@example.com",
                "password": "testpassword",
                "full_name": "Test User"
            },
            "tenant_in": {
                "name": "Test Tenant"
            }
        }
    )

    # Log in to get a token
    response = client.post(
        "/api/v1/auth/login",
        data={
            "username": "test@example.com",
            "password": "testpassword"
        },
        headers={
            "Content-Type": "application/x-www-form-urlencoded"
        }
    )
    token = response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_llm_config_has_provider_api_keys():
    """LLMConfig should have provider_api_keys JSON field."""
    from app.models.llm_config import LLMConfig

    # Check that the model has the column
    mapper = inspect(LLMConfig)
    column_names = [col.key for col in mapper.columns]

    assert "provider_api_keys" in column_names, "LLMConfig should have provider_api_keys column"

    # Check the column type
    provider_api_keys_col = mapper.columns.provider_api_keys
    assert provider_api_keys_col.type.__class__.__name__ == "JSON", "provider_api_keys should be JSON type"


def test_provider_factory_returns_openai_client_for_openai():
    """Factory should return OpenAI client for openai provider."""
    from app.services.llm.provider_factory import LLMProviderFactory

    factory = LLMProviderFactory()
    with patch('app.services.llm.provider_factory.OpenAI') as mock_openai:
        mock_openai.return_value = MagicMock()
        client = factory.get_client("openai", "sk-test-key")
        mock_openai.assert_called_once_with(
            api_key="sk-test-key",
            base_url="https://api.openai.com/v1"
        )


def test_provider_factory_returns_openai_client_for_deepseek():
    """Factory should return OpenAI client with DeepSeek base_url."""
    from app.services.llm.provider_factory import LLMProviderFactory

    factory = LLMProviderFactory()
    with patch('app.services.llm.provider_factory.OpenAI') as mock_openai:
        mock_openai.return_value = MagicMock()
        client = factory.get_client("deepseek", "sk-deep-key")
        mock_openai.assert_called_once_with(
            api_key="sk-deep-key",
            base_url="https://api.deepseek.com/v1"
        )


def test_provider_factory_returns_anthropic_adapter():
    """Factory should return AnthropicAdapter for anthropic provider."""
    from app.services.llm.provider_factory import LLMProviderFactory

    factory = LLMProviderFactory()
    with patch('app.services.llm.provider_factory.AnthropicAdapter') as mock_adapter:
        mock_adapter.return_value = MagicMock()
        client = factory.get_client("anthropic", "sk-ant-key")
        mock_adapter.assert_called_once_with("sk-ant-key")


def test_anthropic_adapter_converts_messages():
    """AnthropicAdapter should convert OpenAI format to Anthropic format."""
    from app.services.llm.provider_factory import AnthropicAdapter

    with patch('app.services.llm.provider_factory.anthropic') as mock_anthropic:
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(type="text", text="Hello!")]
        mock_response.usage = MagicMock(input_tokens=10, output_tokens=5)
        mock_response.stop_reason = "end_turn"
        mock_client.messages.create.return_value = mock_response
        mock_anthropic.Anthropic.return_value = mock_client

        adapter = AnthropicAdapter("sk-ant-key")
        response = adapter.chat.completions.create(
            model="claude-sonnet-4-20250514",
            messages=[
                {"role": "system", "content": "You are helpful."},
                {"role": "user", "content": "Hi"}
            ],
            max_tokens=100
        )

        # Verify Anthropic was called with converted format
        mock_client.messages.create.assert_called_once()
        call_kwargs = mock_client.messages.create.call_args[1]
        assert call_kwargs["system"] == "You are helpful."
        assert call_kwargs["messages"] == [{"role": "user", "content": "Hi"}]

        # Verify response is OpenAI-compatible
        assert response.choices[0].message.content == "Hello!"
        assert response.usage.prompt_tokens == 10
        assert response.usage.completion_tokens == 5


def test_llm_service_uses_router_to_select_model():
    """LLMService should use router to select model and factory to create client."""
    from app.services.llm.service import LLMService
    import uuid

    mock_db = MagicMock()
    tenant_id = uuid.uuid4()

    with patch('app.services.llm.service.LLMRouter') as mock_router_class, \
         patch('app.services.llm.service.LLMProviderFactory') as mock_factory_class:

        # Setup mocks
        mock_router = MagicMock()
        mock_model = MagicMock()
        mock_model.model_id = "gpt-4o"
        mock_model.provider.name = "openai"
        mock_model.id = uuid.uuid4()
        mock_router.select_model.return_value = mock_model

        mock_config = MagicMock()
        mock_config.provider_api_keys = {"openai": "sk-test"}
        mock_router.get_tenant_config.return_value = mock_config

        mock_router_class.return_value = mock_router

        mock_factory = MagicMock()
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.usage.prompt_tokens = 100
        mock_response.usage.completion_tokens = 50
        mock_client.chat.completions.create.return_value = mock_response
        mock_factory.get_client.return_value = mock_client
        mock_factory_class.return_value = mock_factory

        # Create service and call
        service = LLMService(mock_db, tenant_id)
        response = service.generate_response(
            messages=[{"role": "user", "content": "Hello"}],
            task_type="general"
        )

        # Verify router was used
        mock_router.select_model.assert_called_once_with(tenant_id, "general")
        mock_factory.get_client.assert_called_once_with("openai", "sk-test")
        mock_client.chat.completions.create.assert_called_once()


def test_llm_config_schema_accepts_provider_keys():
    """LLMConfigCreate schema should accept provider_api_keys."""
    from app.schemas.llm_config import LLMConfigCreate
    import uuid

    config = LLMConfigCreate(
        name="test",
        primary_model_id=uuid.uuid4(),
        provider_api_keys={"openai": "sk-test", "deepseek": "sk-deep"}
    )
    assert config.provider_api_keys["openai"] == "sk-test"
    assert config.provider_api_keys["deepseek"] == "sk-deep"


def test_set_provider_key(client, auth_headers):
    """POST /llm/providers/{name}/key should set API key for provider."""
    response = client.post(
        "/api/v1/llm/providers/openai/key",
        headers=auth_headers,
        json={"api_key": "sk-test-key-12345"}
    )

    assert response.status_code == 200
    assert response.json()["success"] == True

    # Verify it's stored (via status endpoint)
    status = client.get("/api/v1/llm/providers/status", headers=auth_headers)
    openai_status = next(p for p in status.json() if p["name"] == "openai")
    assert openai_status["configured"] == True
