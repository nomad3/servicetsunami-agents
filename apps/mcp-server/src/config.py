"""
MCP Server Configuration
"""
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """MCP Server settings loaded from environment"""

    # AgentProvision API
    API_BASE_URL: str = "http://localhost:8000"
    API_INTERNAL_KEY: str = "internal-service-key"

    # PostgreSQL (shared with API — needed for knowledge tools)
    DATABASE_URL: str = ""

    # PostgreSQL
    POSTGRESQL_HOST: str = ""
    POSTGRESQL_TOKEN: str = ""
    POSTGRESQL_WAREHOUSE_ID: str = ""
    POSTGRESQL_CATALOG_PREFIX: str = "tenant_"

    # MCP Server (port 8086 to avoid conflict with dental-erp MCP on 8085)
    MCP_PORT: int = 8086
    MCP_TRANSPORT: str = "streamable-http"

    # Browser / Playwright
    BROWSER_HEADLESS: bool = True
    BROWSER_TIMEOUT: int = 30000
    SCRAPE_MAX_RESULTS: int = 10

    # Search API (optional - falls back to DuckDuckGo if not set)
    SERPER_API_KEY: str = ""
    SEARCH_ENGINE: str = "duckduckgo"  # "duckduckgo", "serper", or "google"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"  # Allow FASTMCP_* and other extra env vars


settings = Settings()
