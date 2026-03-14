# Multi-Model Abstraction Layer Design

## Goal

Enable tenants to choose their LLM provider (Anthropic Claude or Google Gemini) for agent chat, using the existing integration registry + credential vault pattern. ADK uses `before_model_callback` + LiteLLM for per-request provider switching without modifying agent singletons.

## Architecture

The API reads the tenant's active LLM provider from `tenant_features`, retrieves credentials from the Fernet-encrypted vault, and passes provider + model + API key to ADK via `state_delta` (same mechanism as `tenant_id` today). ADK's `before_model_callback` intercepts every LLM call and overrides `llm_request.model` to the tenant's chosen provider/model. Embeddings remain on Gemini Embedding 2 independently.

## Tech Stack

- Google ADK (>=1.21.0) with LiteLLM integration
- ADK `before_model_callback` for per-request model override (official ADK pattern)
- Existing integration registry (`INTEGRATION_CREDENTIAL_SCHEMAS`)
- Existing credential vault (Fernet AES-256)
- `tenant_features` table for active provider selection

---

## Section 1: Integration Registry + Credential Storage

### New Registry Entries

Register `anthropic_llm` and `gemini_llm` as LLM integrations in `INTEGRATION_CREDENTIAL_SCHEMAS` in `apps/api/app/api/v1/integration_configs.py`, following the same pattern as Jira, Slack, GitHub, etc.

```python
"anthropic_llm": {
    "display_name": "Anthropic (Claude)",
    "description": "Use Claude models for agent chat",
    "icon": "FaRobot",
    "credentials": [
        {"key": "api_key", "label": "API Key", "type": "password", "required": True,
         "help": "Get your key at console.anthropic.com"},
        {"key": "model", "label": "Model ID", "type": "text", "required": True,
         "help": "e.g. claude-sonnet-4-5, claude-haiku-4-5"}
    ],
    "auth_type": "manual"
}

"gemini_llm": {
    "display_name": "Google Gemini",
    "description": "Use Gemini models for agent chat (default)",
    "icon": "FaGoogle",
    "credentials": [
        {"key": "api_key", "label": "API Key", "type": "password", "required": True,
         "help": "Get your key at aistudio.google.com"},
        {"key": "model", "label": "Model ID", "type": "text", "required": True,
         "help": "e.g. gemini-2.5-pro, gemini-2.5-flash"}
    ],
    "auth_type": "manual"
}
```

API keys and model IDs are stored Fernet-encrypted via the credential vault — same as all other integrations. No more plaintext JSON in `llm_config.provider_api_keys`.

### Active Provider Selection

Add a column to `tenant_features` (which already has `rl_enabled`, `rl_settings`):

- `active_llm_provider` (String, default `"gemini_llm"`) — points to the integration name

The model string lives in the credential vault alongside the API key. `tenant_features` only stores which provider is active.

### DB Migration

Create `apps/api/migrations/046_add_active_llm_provider.sql`:

```sql
-- Add active LLM provider column to tenant_features
ALTER TABLE tenant_features
    ADD COLUMN IF NOT EXISTS active_llm_provider VARCHAR(50) DEFAULT 'gemini_llm';
```

### Model + Schema Changes

**`apps/api/app/models/tenant_features.py`:** Add `active_llm_provider = Column(String(50), default="gemini_llm")`.

**`apps/api/app/schemas/tenant_features.py`:** Add `active_llm_provider: Optional[str] = "gemini_llm"` to `TenantFeaturesBase` (so it appears in GET responses) and `active_llm_provider: Optional[str] = None` to `TenantFeaturesUpdate` (for partial updates).

The existing `PUT /api/v1/features` endpoint already handles partial updates to `tenant_features`, so the "Set as Active" action uses this endpoint with `{"active_llm_provider": "anthropic_llm"}`.

---

## Section 2: API → ADK Model Passing via `state_delta`

### Data Flow

1. On chat message, API reads tenant's `active_llm_provider` from `tenant_features`
2. API looks up the tenant's `IntegrationConfig` for that provider by `integration_name` + `tenant_id`, then calls `retrieve_credentials_for_skill(db, integration_config.id, tenant_id)` → `{"api_key": "sk-ant-...", "model": "claude-sonnet-4-5"}`
3. API includes `llm_config` in `state_delta` when calling ADK's `/run` endpoint:
   ```json
   {
     "state_delta": {
       "tenant_id": "uuid",
       "llm_config": {
         "provider": "anthropic_llm",
         "model": "claude-sonnet-4-5",
         "api_key": "sk-ant-..."
       }
     }
   }
   ```
4. ADK's `before_model_callback` reads `llm_config` from session state and overrides `llm_request.model`

### Integration Points

**`apps/api/app/services/chat.py`** — The `_generate_agentic_response()` method currently calls `adk_client.run()`. It will be extended to:
1. Query `TenantFeatures` for `active_llm_provider`
2. Query `IntegrationConfig` by `integration_name=active_llm_provider` + `tenant_id`
3. Call `retrieve_credentials_for_skill(db, config.id, tenant_id)` to get decrypted `{"api_key": ..., "model": ...}`
4. Include `llm_config` dict in `state_delta` passed to `adk_client.run()`

**`apps/api/app/services/adk_client.py`** — The `run()` method already accepts `state_delta` dict. The caller just adds `llm_config` to it. No changes needed to `adk_client.py` itself.

**Retry path in `chat.py`:** The `_generate_agentic_response()` method has a retry path (~line 409) that recreates the session with a separate `retry_state_delta`. This retry path must also include `llm_config` — otherwise retries silently fall back to default Gemini. Both the primary `state_delta` and `retry_state_delta` must carry `llm_config`.

### Fallback Behavior

If `active_llm_provider` is `"gemini_llm"` and no credentials are stored in the vault, the `llm_config` is omitted from `state_delta`. ADK falls back to native Gemini using `GOOGLE_API_KEY` from env (current default behavior, zero breaking change).

If `active_llm_provider` is set to a provider with no credentials stored, the API returns a user-friendly error: `"Please configure your API key in LLM Settings before chatting."`

### Security

The API key is passed per-request via internal network (`state_delta`), never stored in ADK's env vars or settings. ADK is stateless regarding credentials.

---

## Section 3: ADK `before_model_callback` (Per-Request Model Override)

### How It Works

ADK agents are module-level singletons instantiated at import time with `model=settings.adk_model`. They **stay as singletons** — no reconstruction per request. Instead, ADK's official `before_model_callback` mechanism intercepts every LLM call and overrides `llm_request.model` based on the tenant's config stored in session state.

This is the [officially recommended pattern](https://github.com/google/adk-python/issues/3647) for runtime model switching in ADK.

### New File: `apps/adk-server/config/model_callback.py`

```python
"""Per-request model override using ADK's before_model_callback."""
from typing import Optional
from google.adk.agents.callback_context import CallbackContext
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse


def before_model_callback(
    ctx: CallbackContext, llm_request: LlmRequest
) -> Optional[LlmResponse]:
    """Override model per-request based on tenant's llm_config in session state."""
    llm_config = ctx.state.get("llm_config")
    if not llm_config:
        return None  # No override — use default Gemini

    provider = llm_config.get("provider")
    model = llm_config.get("model")
    api_key = llm_config.get("api_key")

    if not provider or not model:
        return None  # Incomplete config — use default

    if provider == "gemini_llm":
        # Native Gemini with custom model string
        llm_request.model = model
    elif provider == "anthropic_llm":
        # LiteLLM format: "anthropic/{model}"
        # api_key passed per-request via llm_request.config — NOT os.environ
        llm_request.model = f"anthropic/{model}"
        if api_key:
            llm_request.config = llm_request.config or {}
            llm_request.config["api_key"] = api_key

    return None  # Continue to LLM call with overridden model
```

**Important:** API keys are passed per-request via `llm_request.config["api_key"]`, NOT via `os.environ`. LiteLLM's `completion()` accepts `api_key` as a direct parameter. This avoids process-global env var mutation and is thread-safe for concurrent multi-tenant requests. The implementation task should verify the exact `llm_request.config` structure supported by the installed ADK/LiteLLM version and adjust accordingly.

### Registering the Callback on ALL Agents

ADK does **not** propagate `before_model_callback` from parent to sub-agents. Each agent's callback is resolved independently. Therefore, the callback must be registered on **every agent that makes LLM calls**.

The callback is defined once in `apps/adk-server/config/model_callback.py` and imported by each agent file:

```python
# In every agent file (personal_assistant.py, data_analyst.py, etc.)
from config.model_callback import before_model_callback

agent = Agent(
    name="...",
    model=settings.adk_model,  # Default — overridden per-request by callback
    before_model_callback=before_model_callback,
    ...
)
```

This is a one-line import + one kwarg addition per agent file. All 25 agent definitions need this change.

### Reading `llm_config` from `state_delta`

The existing `StripPrefixMiddleware` in `apps/adk-server/server.py` already intercepts `/run` POST requests and reads `state_delta.tenant_id`. The `llm_config` passes through as part of `state_delta` and becomes available in `ctx.state` during the callback — no middleware changes needed.

### New Dependency

Add `litellm` to `apps/adk-server/requirements.txt`.

### Thread Safety

`before_model_callback` is invoked per-invocation context, not shared across concurrent requests. Each request gets its own `CallbackContext` and `LlmRequest`. API keys are passed per-request via `llm_request.config`, not via `os.environ`. This is fully thread-safe.

---

## Section 4: Frontend LLM Settings Tab Redesign

### Current State

`LLMSettingsPage.js` shows a grid of provider cards with an API key input and a "Connected/Not Configured" badge. Uses dedicated `/llm/providers/status` and `/llm/providers/{name}/key` endpoints via `apps/web/src/services/llm.js`.

### New Design

Rewire to use the integration registry + credential vault endpoints (same pattern as `IntegrationsPanel.js`). The page will use `integrationConfigService` (or a new `llmSettingsService`) instead of the current `llm.js` service. Each provider card shows:

- **API Key** field (password input)
- **Model ID** field (free text input)
- **Save** button — stores credentials via `POST /integration_configs/{id}/credentials`
- **"Set as Active"** button — calls `PUT /api/v1/features` with `{"active_llm_provider": "anthropic_llm"}`
- **Active badge** — green "Active" on the selected provider, gray "Available" on others

### Data Loading

On page load:
1. `GET /integration_configs/registry` — filter entries where name ends with `_llm` to get LLM provider schemas
2. `GET /integration_configs` — get tenant's integration configs to check which have credentials stored
3. `GET /api/v1/features` — get `active_llm_provider` to show the active badge

### Flow

1. Tenant enters API key + model ID for Anthropic → Save → credentials go to vault via `POST /integration_configs/{id}/credentials`
2. Tenant clicks "Set as Active" → calls `PUT /api/v1/features` with `{"active_llm_provider": "anthropic_llm"}`
3. Active provider badge updates immediately
4. Next chat message uses Anthropic

### Service Layer Changes

The `apps/web/src/services/llm.js` service becomes dead code. The page either imports the existing integration config service or creates a thin wrapper. The existing `/llm/*` routes remain functional but are no longer used by the frontend.

### i18n

English + Spanish translation keys for new labels, following existing `common.json` and namespace patterns.

---

## Section 5: Local Testing Setup

### Docker Compose

- Add `ANTHROPIC_API_KEY` as an optional env var to the ADK service in `docker-compose.yml` (local dev convenience — not required when credentials come from vault)
- `litellm` added to `apps/adk-server/requirements.txt`

### Testing Flow

1. Start stack: `docker-compose up --build`
2. Login, go to LLM Settings
3. Add Anthropic integration: paste API key + model `claude-sonnet-4-5`
4. Click "Set as Active"
5. Open a chat session → agent responds via Claude instead of Gemini
6. Switch back to Gemini → chat uses Gemini again

### What Stays Unchanged

- **Embeddings**: Gemini Embedding 2 (768-dim) — independent of chat provider, already has 992+ vectors
- **Claude Code agent**: Claude Code CLI with OAuth token — completely separate path
- **Default behavior**: If tenant hasn't configured any LLM provider, falls back to Gemini with `GOOGLE_API_KEY` from env (zero breaking change)

### Deprecation

The old `llm_config.provider_api_keys` plaintext JSON column and the legacy `/llm/providers/{name}/key` endpoint become dead code. Deprecated but not removed in this phase to avoid migration risk. The `apps/web/src/services/llm.js` frontend service also becomes dead code.

---

## Scope Boundaries

### In Scope

- Register Anthropic + Gemini as LLM integrations in credential registry
- Encrypted credential storage via existing vault
- DB migration for `tenant_features.active_llm_provider`
- Model + schema updates for `TenantFeatures`
- API passes provider config to ADK via `state_delta`
- ADK `before_model_callback` using LiteLLM for Anthropic
- LLM Settings tab redesign to use integration pattern
- Local docker-compose testing with Anthropic
- Error handling when credentials missing
- English + Spanish i18n

### Out of Scope

- Embedding model switching (stays Gemini Embedding 2)
- Per-agent model overrides (all agents use tenant's choice)
- Cost tracking per provider (future enhancement — currently hardcoded Gemini pricing in `chat.py`)
- Additional providers beyond Anthropic + Gemini (future: add registry entries + elif in callback)
- Migration/removal of old `llm_providers`/`llm_models`/`llm_configs` tables
- Budget limits or rate limiting per provider
- Per-tenant Gemini API key override (Gemini uses env `GOOGLE_API_KEY` for now; per-tenant Gemini keys would require LiteLLM wrapping for Gemini too — future enhancement)

---

## Files Changed Summary

### Backend (API)
| File | Change |
|------|--------|
| `apps/api/migrations/046_add_active_llm_provider.sql` | **New** — ALTER TABLE migration |
| `apps/api/app/models/tenant_features.py` | Add `active_llm_provider` column |
| `apps/api/app/schemas/tenant_features.py` | Add field to schemas |
| `apps/api/app/api/v1/integration_configs.py` | Add `anthropic_llm` + `gemini_llm` to registry |
| `apps/api/app/services/chat.py` | Read tenant LLM config, pass in `state_delta` (both primary and retry paths) |

### Backend (ADK)
| File | Change |
|------|--------|
| `apps/adk-server/requirements.txt` | Add `litellm` |
| `apps/adk-server/config/model_callback.py` | **New** — `before_model_callback` function |
| `apps/adk-server/servicetsunami_supervisor/agent.py` | Register `before_model_callback` on root agent |
| `apps/adk-server/**/*_agent.py` (all 25 agent files) | Add `before_model_callback=before_model_callback` import + kwarg |

### Frontend
| File | Change |
|------|--------|
| `apps/web/src/pages/LLMSettingsPage.js` | Rewire to integration registry + vault endpoints |
| `apps/web/src/i18n/locales/en/common.json` | New translation keys |
| `apps/web/src/i18n/locales/es/common.json` | New translation keys |

### Infrastructure
| File | Change |
|------|--------|
| `docker-compose.yml` | Add optional `ANTHROPIC_API_KEY` env var to ADK service |

---

## Risk & Mitigation

| Risk | Mitigation |
|------|-----------|
| LiteLLM tool-call format mismatch | ADK officially documents LiteLLM integration; tool calling handled by translation layer |
| API key in `state_delta` exposed in transit | Internal network only (docker-compose/k8s service mesh); key never persisted in ADK |
| Tenant switches provider mid-conversation | New provider applies on next message; session history preserved in DB |
| LiteLLM dependency size | ~50MB added to ADK image; acceptable trade-off |
| Default Gemini breaks if GOOGLE_API_KEY missing | Same as today — existing behavior unchanged |
| Missing credentials at chat time | API returns clear error message directing user to LLM Settings |
| `before_model_callback` not propagated to sub-agents | Callback registered on all 25 agent files individually (one import + one kwarg each) |
