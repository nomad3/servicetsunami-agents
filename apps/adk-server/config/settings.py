"""ADK Server configuration using pydantic-settings."""
from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    """Environment configuration for ADK server."""

    # Google AI - Using Google AI Studio with API key
    google_genai_use_vertexai: bool = False
    adk_model: str = "gemini-2.5-flash"

    # Database (shared with FastAPI)
    database_url: str = "postgresql://postgres:postgres@localhost:5432/servicetsunami"
    database_host: str = "localhost"
    database_port: int = 5432
    database_name: str = "servicetsunami"
    database_user: str = "postgres"
    database_password: str = "postgres"

    # JWT Auth (shared SECRET_KEY with FastAPI)
    secret_key: str = "secret"
    algorithm: str = "HS256"

    # MCP Server (Databricks)
    mcp_server_url: str = "http://mcp-server:8000"
    mcp_api_key: str = "dev_mcp_key"
    mcp_tenant_code: str = "scdp"

    # MCP Scraper Server (Playwright web scraping)
    mcp_scraper_url: str = "http://servicetsunami-mcp"

    # FastAPI backend (for ADK -> API callbacks like lead scoring)
    api_base_url: str = "http://servicetsunami-api"

    # Anthropic (for Claude vision in cardiac_analyst)
    anthropic_api_key: str = ""

    # Health-Pets API (for billing callbacks)
    healthpets_api_url: str = "http://localhost:8000"

    # Vertex AI Vector Search
    vertex_project: str = "ai-agency-479516"
    vertex_location: str = "us-central1"
    vector_index_id: str = ""
    vector_endpoint_id: str = ""

    # Embedding model
    embedding_model: str = "text-embedding-005"
    embedding_dimensions: int = 768

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
