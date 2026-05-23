import os
from pydantic import field_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):

    @field_validator("*", mode="before")
    @classmethod
    def strip_strings(cls, v):
        """Strip trailing whitespace from all string env vars (K8s secrets add newlines)."""
        if isinstance(v, str):
            return v.strip()
        return v
    SECRET_KEY: str
    ALGORITHM: str = "HS256"
    # F7 split — domain-specific JWT signing secrets.
    # Default to SECRET_KEY (filled in model_post_init below) so PR2 is a
    # no-behavior-change kid-plumbing step. PR4 introduces real distinct
    # values via macOS Keychain hydration.
    # Spec: docs/superpowers/specs/2026-05-22-subproject-a-infra-secret-hardening-design.md
    JWT_USER_SECRET: str | None = None
    JWT_AGENT_TOKEN_SECRET: str | None = None
    JWT_OAUTH_STATE_SECRET: str | None = None
    # 24 hours. Long-running clients (Luna desktop) call /auth/refresh
    # proactively 5 minutes before expiry to avoid mid-session logouts.
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440
    # Long-lived refresh credential (DB-backed, rotation w/ reuse
    # detection). The CLI mints one at /auth/login and swaps via
    # /auth/token/refresh whenever the access_token expires, so users
    # stay signed in across reboots and laptop closures for the full
    # window. 30 days matches GitHub CLI / Cloud SDK gcloud; raise to
    # 60–90 if support tickets show people logging in monthly.
    REFRESH_TOKEN_EXPIRE_DAYS: int = 30
    # When True, /auth/login and /auth/token/refresh trust the
    # `CF-Connecting-IP` / `X-Forwarded-For` / `X-Real-IP` headers and
    # record the first hop on the refresh_tokens row. When False
    # (the local-dev default), we fall back to request.client.host so
    # an attacker can't spoof their audit-row IP by setting the header
    # themselves on a direct-to-uvicorn call. Helm prod values must
    # set this True since Cloudflare tunnel + nginx are always in the
    # path there. PR #442 review finding I-2.
    TRUSTED_FORWARD_HEADERS: bool = False
    # Grace window (seconds) after a refresh_token rotation where a
    # replay of the just-rotated row returns the cached child instead
    # of triggering reuse-detection chain-burn. Defends against the
    # legitimate-concurrent-CLI race where `alpha chat` and `alpha
    # watch` both hit 401 at the same second and both try to exchange
    # the same refresh credential. PR #442 review finding B-1.
    # Set to 0 to disable the grace window (recommended only if you
    # have an external lock).
    REFRESH_REUSE_GRACE_SECONDS: int = 30
    DATABASE_URL: str = "postgresql://postgres:postgres@db:5432/agentprovision"
    # Default storage path: /app/storage in container (set by IN_DOCKER=1 in
    # Dockerfile), ./storage for local dev. Helm/compose should set
    # DATA_STORAGE_PATH explicitly in production to avoid relying on detection.
    DATA_STORAGE_PATH: str = "/app/storage" if os.environ.get("IN_DOCKER") == "1" else "./storage"
    TEMPORAL_ADDRESS: str | None = "localhost:7233"
    TEMPORAL_NAMESPACE: str = "default"
    REDIS_URL: str = "redis://redis:6379/0"

    # Public-facing base URL the API surfaces to clients (e.g. the device-flow
    # verification_uri printed by the CLI). Empty default falls back to
    # relative URLs for local dev; production must set this in the env.
    PUBLIC_BASE_URL: str = ""

    DEFAULT_WORKFLOW_TIMEOUT_SECONDS: int = 600

    # MCP Server Configuration
    MCP_SERVER_URL: str = "http://localhost:8085"
    MCP_API_KEY: str
    MCP_ENABLED: bool = True  # Feature flag for MCP integration

    # Internal API Key (for MCP server to access credentials)
    API_INTERNAL_KEY: str

    # Google AI (Gemini Embeddings)
    GOOGLE_API_KEY: str = ""

    # LLM Configuration
    ANTHROPIC_API_KEY: str | None = None
    LLM_MODEL: str = "claude-3-haiku-20240307"
    LLM_MAX_TOKENS: int = 4096
    LLM_TEMPERATURE: float = 0.7

    # Credential Vault encryption (Fernet key — generate with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
    ENCRYPTION_KEY: str | None = None
    PLATFORM_SHARED_CREDENTIALS_TENANT_ID: str | None = None
    PLATFORM_CODEX_AUTH_JSON: str | None = None
    PLATFORM_CLAUDE_CODE_TOKEN: str | None = None
    PLATFORM_GEMINI_CLI_TOKEN: str | None = None

    # OAuth2 - Google (Gmail + Calendar)
    GOOGLE_CLIENT_ID: str | None = None
    GOOGLE_CLIENT_SECRET: str | None = None
    GOOGLE_REDIRECT_URI: str = "https://agentprovision.com/api/v1/oauth/google/callback"

    # OAuth2 - GitHub
    GITHUB_CLIENT_ID: str | None = None
    GITHUB_CLIENT_SECRET: str | None = None
    GITHUB_REDIRECT_URI: str = "https://agentprovision.com/api/v1/oauth/github/callback"

    # OAuth2 - LinkedIn
    LINKEDIN_CLIENT_ID: str | None = None
    LINKEDIN_CLIENT_SECRET: str | None = None
    LINKEDIN_REDIRECT_URI: str = "https://agentprovision.com/api/v1/oauth/linkedin/callback"

    # OAuth2 - Microsoft (Outlook Mail)
    MICROSOFT_CLIENT_ID: str | None = None
    MICROSOFT_CLIENT_SECRET: str | None = None
    MICROSOFT_REDIRECT_URI: str = "https://agentprovision.com/api/v1/oauth/microsoft/callback"

    # HCA (Deal Intelligence) API
    HCA_API_URL: str = "http://hca-api:3000"
    HCA_SERVICE_KEY: str = ""

    # HealthPets API
    HEALTHPETS_API_URL: str = "http://localhost:8000"

    # Memory-First Phase 1 cutover flags
    USE_MEMORY_V2: bool = True
    USE_MEMORY_V2_TENANT_ALLOWLIST: list[str] = []

    # CLI Phase 1 ship (#177): when True, `/api/v1/tasks-fanout/run`
    # dispatches a real `FanoutChatCliWorkflow` to Temporal on the
    # `agentprovision-code` queue instead of the in-memory prototype
    # stub. Default False so the stub remains the demo-safe path; set
    # to True in apps/api/.env (or Helm values) once code-worker has
    # the FanoutChatCliWorkflow registered (worker.py line ~40).
    # Rollback is one env-var flip — the stub stays as the fallback.
    USE_REAL_FANOUT_WORKFLOW: bool = False

    # Deployment environment. Used to gate dev-only side-effects
    # (most notably the demo-user seed at startup; see init_db.py).
    # Values: "local" / "dev" / "staging" / "production". Anything
    # outside the {local, dev} set is treated as production-shape
    # (no demo seed, no debug toggles).
    #
    # FAIL-CLOSED DEFAULT (security review round 6, B6-1): the
    # default is "production" so a deploy that forgets to set
    # ENVIRONMENT does NOT silently seed `test@example.com /
    # DemoPass123!` into the production database. Local dev sets
    # ENVIRONMENT=local in apps/api/.env (already done).
    ENVIRONMENT: str = "production"


    # Transactional email (password recovery, invitations, system
    # notifications). Set EMAIL_SMTP_HOST + the four companions to a
    # real relay (Gmail SMTP, AWS SES, Postmark, Mailgun) in production.
    # When unset, `email_sender.send_email` falls back to log-only so
    # local dev keeps working without secrets. EMAIL_FROM_NAME is
    # cosmetic; EMAIL_FROM must be a deliverable address.
    EMAIL_SMTP_HOST: str | None = None
    EMAIL_SMTP_PORT: int = 587
    EMAIL_SMTP_USERNAME: str | None = None
    EMAIL_SMTP_PASSWORD: str | None = None
    EMAIL_FROM: str = "noreply@agentprovision.com"
    EMAIL_FROM_NAME: str = "AgentProvision"
    # STARTTLS is the default on port 587 (msa). Set to false only when
    # talking to localhost:25 in tests; SSL-from-the-start (port 465)
    # is uncommon enough we don't model it here yet.
    EMAIL_SMTP_USE_TLS: bool = True

    def model_post_init(self, __context) -> None:
        """Apply SECRET_KEY as the fallback for any unset JWT domain secret.

        PR2 (F7a kid plumbing): all three secrets default to SECRET_KEY so
        the cluster sees zero behavior change. PR4 (F7b) replaces these
        defaults with distinct values hydrated from macOS Keychain.
        """
        if self.JWT_USER_SECRET is None:
            self.JWT_USER_SECRET = self.SECRET_KEY
        if self.JWT_AGENT_TOKEN_SECRET is None:
            self.JWT_AGENT_TOKEN_SECRET = self.SECRET_KEY
        if self.JWT_OAUTH_STATE_SECRET is None:
            self.JWT_OAUTH_STATE_SECRET = self.SECRET_KEY


    class Config:
        env_file = ".env"
        extra = "ignore"

settings = Settings()
