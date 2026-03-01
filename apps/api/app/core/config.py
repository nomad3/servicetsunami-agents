from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    SECRET_KEY: str = "secret"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    DATABASE_URL: str = "postgresql://postgres:postgres@db:5432/servicetsunami"
    DATA_STORAGE_PATH: str = "/app/storage"
    TEMPORAL_ADDRESS: str | None = "localhost:7233"
    TEMPORAL_NAMESPACE: str = "default"

    DEFAULT_WORKFLOW_TIMEOUT_SECONDS: int = 600

    # MCP Server Configuration
    MCP_SERVER_URL: str = "http://localhost:8085"
    MCP_API_KEY: str = "dev_mcp_key"  # Change in production
    MCP_ENABLED: bool = True  # Feature flag for MCP/Databricks integration

    # Internal API Key (for MCP server to access credentials)
    API_INTERNAL_KEY: str = "internal-service-key"

    # Databricks Sync Settings
    DATABRICKS_SYNC_ENABLED: bool = True
    DATABRICKS_AUTO_SYNC: bool = True
    DATABRICKS_RETRY_ATTEMPTS: int = 3
    DATABRICKS_RETRY_INTERVAL: int = 300  # seconds (5 minutes)

    # LLM Configuration
    ANTHROPIC_API_KEY: str | None = None
    LLM_MODEL: str = "claude-3-haiku-20240307"
    LLM_MAX_TOKENS: int = 4096
    LLM_TEMPERATURE: float = 0.7

    # ADK (Google Agent Development Kit)
    ADK_BASE_URL: str | None = None
    ADK_APP_NAME: str = "servicetsunami_supervisor"

    # Credential Vault encryption (Fernet key — generate with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
    ENCRYPTION_KEY: str | None = None

    # OAuth2 - Google (Gmail + Calendar)
    GOOGLE_CLIENT_ID: str | None = None
    GOOGLE_CLIENT_SECRET: str | None = None
    GOOGLE_REDIRECT_URI: str = "https://servicetsunami.com/api/v1/oauth/google/callback"

    # OAuth2 - GitHub
    GITHUB_CLIENT_ID: str | None = None
    GITHUB_CLIENT_SECRET: str | None = None
    GITHUB_REDIRECT_URI: str = "https://servicetsunami.com/api/v1/oauth/github/callback"

    # OAuth2 - LinkedIn
    LINKEDIN_CLIENT_ID: str | None = None
    LINKEDIN_CLIENT_SECRET: str | None = None
    LINKEDIN_REDIRECT_URI: str = "https://servicetsunami.com/api/v1/oauth/linkedin/callback"

    # HCA (Deal Intelligence) API
    HCA_API_URL: str = "http://hca-api:3000"
    HCA_SERVICE_KEY: str = ""

    # HealthPets API
    HEALTHPETS_API_URL: str = "http://localhost:8000"

    class Config:
        env_file = ".env"

settings = Settings()
