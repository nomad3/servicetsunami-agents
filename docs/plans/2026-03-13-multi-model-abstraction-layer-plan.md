# Multi-Model Abstraction Layer Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enable tenants to choose between Anthropic Claude and Google Gemini for agent chat, using the integration registry + credential vault + ADK `before_model_callback` pattern.

**Architecture:** API reads tenant's active LLM provider from `tenant_features`, retrieves encrypted credentials from vault, passes provider/model/api_key to ADK via `state_delta`. ADK's `before_model_callback` overrides `llm_request.model` per-invocation using LiteLLM for Anthropic. Agents stay as singletons.

**Tech Stack:** Google ADK (>=1.21.0), LiteLLM, existing integration registry + Fernet credential vault, React + Bootstrap frontend.

**Spec:** `docs/plans/2026-03-13-multi-model-abstraction-layer-design.md`

---

## File Structure

### New Files
| File | Responsibility |
|------|---------------|
| `apps/api/migrations/046_add_active_llm_provider.sql` | DB migration |
| `apps/adk-server/config/model_callback.py` | `before_model_callback` for per-request model override |

### Modified Files
| File | Change |
|------|--------|
| `apps/api/app/models/tenant_features.py` | Add `active_llm_provider` column |
| `apps/api/app/schemas/tenant_features.py` | Add field to Base + Update schemas |
| `apps/api/app/api/v1/integration_configs.py` | Add `anthropic_llm` + `gemini_llm` to registry |
| `apps/api/app/services/chat.py` | Read tenant LLM config, pass `llm_config` in `state_delta` (primary + retry) |
| `apps/adk-server/requirements.txt` | Add `litellm` |
| `apps/adk-server/servicetsunami_supervisor/agent.py` | Register `before_model_callback` |
| `apps/adk-server/servicetsunami_supervisor/personal_assistant.py` | Register callback |
| `apps/adk-server/servicetsunami_supervisor/code_agent.py` | Register callback |
| `apps/adk-server/servicetsunami_supervisor/data_team.py` | Register callback |
| `apps/adk-server/servicetsunami_supervisor/sales_team.py` | Register callback |
| `apps/adk-server/servicetsunami_supervisor/sales_agent.py` | Register callback |
| `apps/adk-server/servicetsunami_supervisor/customer_support.py` | Register callback |
| `apps/adk-server/servicetsunami_supervisor/marketing_team.py` | Register callback |
| `apps/adk-server/servicetsunami_supervisor/marketing_analyst.py` | Register callback |
| `apps/adk-server/servicetsunami_supervisor/web_researcher.py` | Register callback |
| `apps/adk-server/servicetsunami_supervisor/knowledge_manager.py` | Register callback |
| `apps/adk-server/servicetsunami_supervisor/prospecting_team.py` | Register callback |
| `apps/adk-server/servicetsunami_supervisor/prospect_researcher.py` | Register callback |
| `apps/adk-server/servicetsunami_supervisor/prospect_scorer.py` | Register callback |
| `apps/adk-server/servicetsunami_supervisor/prospect_outreach.py` | Register callback |
| `apps/adk-server/servicetsunami_supervisor/deal_team.py` | Register callback |
| `apps/adk-server/servicetsunami_supervisor/deal_analyst.py` | Register callback |
| `apps/adk-server/servicetsunami_supervisor/deal_researcher.py` | Register callback |
| `apps/adk-server/servicetsunami_supervisor/outreach_specialist.py` | Register callback |
| `apps/adk-server/servicetsunami_supervisor/vet_supervisor.py` | Register callback |
| `apps/adk-server/servicetsunami_supervisor/cardiac_analyst.py` | Register callback |
| `apps/adk-server/servicetsunami_supervisor/vet_report_generator.py` | Register callback |
| `apps/adk-server/servicetsunami_supervisor/billing_agent.py` | Register callback |
| `apps/adk-server/servicetsunami_supervisor/data_analyst.py` | Register callback |
| `apps/adk-server/servicetsunami_supervisor/report_generator.py` | Register callback |
| `apps/web/src/pages/LLMSettingsPage.js` | Redesign to use integration registry + vault |
| `apps/web/src/i18n/locales/en/common.json` | Add LLM settings translation keys |
| `apps/web/src/i18n/locales/es/common.json` | Add LLM settings translation keys |
| `docker-compose.yml` | Add `ANTHROPIC_API_KEY` env var to ADK service |

---

## Chunk 1: Backend Data Layer

### Task 1: DB Migration — `active_llm_provider` Column

**Files:**
- Create: `apps/api/migrations/046_add_active_llm_provider.sql`

- [ ] **Step 1: Create migration file**

```sql
-- Migration 046: Add active LLM provider selection to tenant_features
-- Allows tenants to choose their preferred LLM provider for agent chat

ALTER TABLE tenant_features
    ADD COLUMN IF NOT EXISTS active_llm_provider VARCHAR(50) DEFAULT 'gemini_llm';

COMMENT ON COLUMN tenant_features.active_llm_provider IS 'Integration name of the active LLM provider (e.g. gemini_llm, anthropic_llm)';
```

- [ ] **Step 2: Verify migration syntax**

Run against local DB:
```bash
docker-compose exec db psql -U postgres servicetsunami -f /dev/stdin < apps/api/migrations/046_add_active_llm_provider.sql
```

Expected: `ALTER TABLE` with no errors.

- [ ] **Step 3: Verify column exists**

```bash
docker-compose exec db psql -U postgres servicetsunami -c "\d tenant_features" | grep active_llm
```

Expected: `active_llm_provider | character varying(50) | | | 'gemini_llm'`

- [ ] **Step 4: Commit**

```bash
git add -f apps/api/migrations/046_add_active_llm_provider.sql
git commit -m "feat: add active_llm_provider column to tenant_features"
```

Note: `git add -f` needed because global gitignore may block `.sql` files.

---

### Task 2: TenantFeatures Model + Schema Updates

**Files:**
- Modify: `apps/api/app/models/tenant_features.py:54` (after `plan_type`)
- Modify: `apps/api/app/schemas/tenant_features.py:30,54` (Base + Update schemas)

- [ ] **Step 1: Add column to SQLAlchemy model**

In `apps/api/app/models/tenant_features.py`, add after the `plan_type` line (line 54):

```python
    # LLM Provider Selection
    active_llm_provider = Column(String(50), default="gemini_llm")
```

Also add `String` to the sqlalchemy import if not already present (line 1 should have `from sqlalchemy import Column, ...`). Check existing imports first — `String` is likely already imported since `plan_type` uses it.

- [ ] **Step 2: Add field to TenantFeaturesBase schema**

In `apps/api/app/schemas/tenant_features.py`, add to the `TenantFeaturesBase` class (after the last field, around line 30):

```python
    active_llm_provider: Optional[str] = "gemini_llm"
```

- [ ] **Step 3: Add field to TenantFeaturesUpdate schema**

In the same file, add to the `TenantFeaturesUpdate` class (after the last field, around line 54):

```python
    active_llm_provider: Optional[str] = None
```

- [ ] **Step 4: Verify the feature update endpoint works**

The existing `PUT /api/v1/features` endpoint in `apps/api/app/api/v1/features.py` calls `service.update_features()` which uses `model_dump(exclude_unset=True)` + `setattr()` loop. This will automatically handle `active_llm_provider` with no route changes needed. Verify by reading the service at `apps/api/app/services/features.py:33-50`.

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/models/tenant_features.py apps/api/app/schemas/tenant_features.py
git commit -m "feat: add active_llm_provider to tenant_features model and schemas"
```

---

### Task 3: Register LLM Providers in Integration Registry

**Files:**
- Modify: `apps/api/app/api/v1/integration_configs.py:145` (after `tiktok_ads` entry)

- [ ] **Step 1: Add `anthropic_llm` and `gemini_llm` entries to `INTEGRATION_CREDENTIAL_SCHEMAS`**

In `apps/api/app/api/v1/integration_configs.py`, add after the `"tiktok_ads"` entry (the last entry, around line 145) but still inside the dict:

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
    },
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
    },
```

- [ ] **Step 2: Verify registry returns new entries**

Start the API and test:
```bash
curl -s http://localhost:8001/api/v1/integration_configs/registry -H "Authorization: Bearer $TOKEN" | python3 -m json.tool | grep -A2 "anthropic_llm\|gemini_llm"
```

Expected: Both `anthropic_llm` and `gemini_llm` appear in the registry response.

- [ ] **Step 3: Commit**

```bash
git add apps/api/app/api/v1/integration_configs.py
git commit -m "feat: register anthropic_llm and gemini_llm in integration registry"
```

---

## Chunk 2: ADK LiteLLM Integration

### Task 4: Add LiteLLM Dependency

**Files:**
- Modify: `apps/adk-server/requirements.txt`

- [ ] **Step 1: Add litellm to requirements**

In `apps/adk-server/requirements.txt`, add after the `google-adk>=1.21.0` line (line 2):

```
litellm>=1.0.0
```

- [ ] **Step 2: Verify import works**

```bash
cd apps/adk-server && pip install litellm && python -c "from google.adk.models.lite_llm import LiteLlm; print('OK')"
```

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add apps/adk-server/requirements.txt
git commit -m "feat: add litellm dependency to ADK server"
```

---

### Task 5: Create `before_model_callback`

**Files:**
- Create: `apps/adk-server/config/model_callback.py`

- [ ] **Step 1: Create the callback module**

```python
"""Per-request model override using ADK's before_model_callback.

Reads llm_config from session state (passed via state_delta by the API)
and overrides llm_request.model to route to the tenant's chosen provider.

Usage: Register on every Agent definition:
    from config.model_callback import llm_model_callback
    agent = Agent(..., before_model_callback=llm_model_callback)
"""
from typing import Optional

from google.adk.agents.callback_context import CallbackContext
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse


# LiteLLM provider prefixes for model string formatting
PROVIDER_PREFIXES = {
    "anthropic_llm": "anthropic",
    # Future providers:
    # "openai_llm": "openai",
    # "deepseek_llm": "deepseek",
}


def llm_model_callback(
    ctx: CallbackContext, llm_request: LlmRequest
) -> Optional[LlmResponse]:
    """Override model per-request based on tenant's llm_config in session state.

    The API passes llm_config in state_delta:
        {"provider": "anthropic_llm", "model": "claude-sonnet-4-5", "api_key": "sk-..."}

    For Gemini: sets llm_request.model to the model string (native ADK).
    For other providers: sets llm_request.model to "provider/model" (LiteLLM format)
        and passes api_key via llm_request.config.
    """
    llm_config = ctx.state.get("llm_config")
    if not llm_config:
        return None  # No override — use default Gemini from settings.adk_model

    provider = llm_config.get("provider")
    model = llm_config.get("model")
    api_key = llm_config.get("api_key")

    if not provider or not model:
        return None  # Incomplete config — use default

    if provider == "gemini_llm":
        # Native Gemini — just override the model string
        llm_request.model = model
        return None

    # Non-Gemini provider — use LiteLLM format
    prefix = PROVIDER_PREFIXES.get(provider)
    if not prefix:
        return None  # Unknown provider — use default

    llm_request.model = f"{prefix}/{model}"

    # Pass API key per-request (thread-safe, no os.environ mutation)
    if api_key:
        if not hasattr(llm_request, "config") or llm_request.config is None:
            llm_request.config = {}
        if isinstance(llm_request.config, dict):
            llm_request.config["api_key"] = api_key
        else:
            # config may be a pydantic model — try attribute setting
            try:
                llm_request.config.api_key = api_key
            except AttributeError:
                pass

    return None
```

- [ ] **Step 2: Verify module imports cleanly**

```bash
cd apps/adk-server && python -c "from config.model_callback import llm_model_callback; print('OK')"
```

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add apps/adk-server/config/model_callback.py
git commit -m "feat: add before_model_callback for per-request LLM provider switching"
```

---

### Task 6: Register Callback on All 25 Agent Files

**Files:**
- Modify: All 25 agent files in `apps/adk-server/servicetsunami_supervisor/`

Each agent file needs two changes:
1. Add import: `from config.model_callback import llm_model_callback`
2. Add kwarg: `before_model_callback=llm_model_callback` to the `Agent()` constructor

**Here is the complete list with the exact line where `model=settings.adk_model` appears:**

| # | File | Line | Agent Name |
|---|------|------|------------|
| 1 | `agent.py` | 22 | servicetsunami_supervisor |
| 2 | `personal_assistant.py` | 69 | personal_assistant |
| 3 | `code_agent.py` | 12 | code_agent |
| 4 | `data_team.py` | 13 | data_team |
| 5 | `sales_team.py` | 13 | sales_team |
| 6 | `sales_agent.py` | 35 | sales_agent |
| 7 | `customer_support.py` | 22 | customer_support |
| 8 | `marketing_team.py` | 15 | marketing_team |
| 9 | `marketing_analyst.py` | 48 | marketing_analyst |
| 10 | `web_researcher.py` | 173 | web_researcher |
| 11 | `knowledge_manager.py` | 94 | knowledge_manager |
| 12 | `prospecting_team.py` | 15 | prospecting_team |
| 13 | `prospect_researcher.py` | 38 | prospect_researcher |
| 14 | `prospect_scorer.py` | 23 | prospect_scorer |
| 15 | `prospect_outreach.py` | 28 | prospect_outreach |
| 16 | `deal_team.py` | 16 | deal_team |
| 17 | `deal_analyst.py` | 26 | deal_analyst |
| 18 | `deal_researcher.py` | 23 | deal_researcher |
| 19 | `outreach_specialist.py` | 19 | outreach_specialist |
| 20 | `vet_supervisor.py` | 16 | vet_supervisor |
| 21 | `cardiac_analyst.py` | 27 | cardiac_analyst |
| 22 | `vet_report_generator.py` | 85 | vet_report_generator |
| 23 | `billing_agent.py` | 23 | billing_agent |
| 24 | `data_analyst.py` | 33 | data_analyst |
| 25 | `report_generator.py` | 29 | report_generator |

- [ ] **Step 1: Add import and callback to each agent file**

For each file, add this import near the top (after other config imports):
```python
from config.model_callback import llm_model_callback
```

Then add `before_model_callback=llm_model_callback,` as a kwarg to the `Agent()` constructor. Place it right after the `model=settings.adk_model,` line.

**Example transformation for `agent.py`:**

Before:
```python
root_agent = Agent(
    name="servicetsunami_supervisor",
    model=settings.adk_model,
    instruction="""...""",
```

After:
```python
from config.model_callback import llm_model_callback

root_agent = Agent(
    name="servicetsunami_supervisor",
    model=settings.adk_model,
    before_model_callback=llm_model_callback,
    instruction="""...""",
```

Apply this same pattern to all 25 files. Read each file first to find the exact `Agent(` constructor location.

- [ ] **Step 2: Verify ADK server starts without errors**

```bash
cd apps/adk-server && python -c "from servicetsunami_supervisor.agent import root_agent; print(f'Root agent: {root_agent.name}, callback: {root_agent.before_model_callback}')"
```

Expected: `Root agent: servicetsunami_supervisor, callback: <function llm_model_callback at 0x...>`

- [ ] **Step 3: Spot-check a few sub-agents**

```bash
cd apps/adk-server && python -c "
from servicetsunami_supervisor.personal_assistant import personal_assistant
from servicetsunami_supervisor.code_agent import code_agent
from servicetsunami_supervisor.billing_agent import billing_agent
print(f'personal_assistant callback: {personal_assistant.before_model_callback is not None}')
print(f'code_agent callback: {code_agent.before_model_callback is not None}')
print(f'billing_agent callback: {billing_agent.before_model_callback is not None}')
"
```

Expected: All print `True`.

- [ ] **Step 4: Commit**

```bash
cd apps/adk-server && git add servicetsunami_supervisor/*.py
git commit -m "feat: register llm_model_callback on all 25 agent definitions"
```

---

## Chunk 3: API → ADK Wiring

### Task 7: Wire Chat Service to Pass `llm_config` in `state_delta`

**Files:**
- Modify: `apps/api/app/services/chat.py:305-315,405-415`

This task modifies `_generate_agentic_response()` to read the tenant's active LLM provider, retrieve credentials from the vault, and include `llm_config` in the `state_delta` passed to ADK.

- [ ] **Step 1: Add imports at top of `chat.py`**

At the top of `apps/api/app/services/chat.py`, add these imports (near the existing service imports):

```python
from app.models.tenant_features import TenantFeatures
from app.models.integration_config import IntegrationConfig
from app.services.orchestration.credential_vault import retrieve_credentials_for_skill
```

Check that `IntegrationConfig` is the correct model name by reading `apps/api/app/models/integration_config.py`.

- [ ] **Step 2: Create helper function to build `llm_config`**

Add this helper function above `_generate_agentic_response()` in `chat.py`:

```python
def _get_tenant_llm_config(db: Session, tenant_id) -> Optional[dict]:
    """Read tenant's active LLM provider and return config for ADK state_delta.

    Returns dict with {provider, model, api_key} or None if using default Gemini.
    """
    import uuid as _uuid

    # Read tenant's active provider from tenant_features
    features = db.query(TenantFeatures).filter(
        TenantFeatures.tenant_id == tenant_id
    ).first()

    if not features or not features.active_llm_provider:
        return None  # Use default Gemini

    provider = features.active_llm_provider
    if provider == "gemini_llm":
        return None  # Default Gemini — no override needed

    # Look up integration config for this provider
    config = db.query(IntegrationConfig).filter(
        IntegrationConfig.tenant_id == tenant_id,
        IntegrationConfig.integration_name == provider,
        IntegrationConfig.enabled == True
    ).first()

    if not config:
        return {"error": f"Provider '{provider}' is not configured. Go to LLM Settings to set it up."}

    # Retrieve decrypted credentials from vault
    try:
        creds = retrieve_credentials_for_skill(db, config.id, tenant_id)
    except Exception:
        return {"error": "Failed to retrieve credentials. Please re-save your API key in LLM Settings."}

    if not creds or "api_key" not in creds or "model" not in creds:
        return {"error": "Missing API key or model ID. Please configure them in LLM Settings."}

    return {
        "provider": provider,
        "model": creds["model"],
        "api_key": creds["api_key"],
    }
```

- [ ] **Step 3: Add `llm_config` to primary `state_delta`**

In `_generate_agentic_response()`, find the state_delta building block (around line 307-311):

```python
state_delta = {"tenant_id": str(session.tenant_id)}
if sender_phone:
    state_delta["whatsapp_phone"] = sender_phone
if memory_context:
    state_delta["memory_context"] = memory_context
```

Add after this block:

```python
# Include tenant's LLM provider config for ADK model override
llm_config = _get_tenant_llm_config(db, session.tenant_id)
if llm_config and "error" in llm_config:
    # Tenant selected a provider but credentials are missing/broken
    return [{"content": {"parts": [{"text": llm_config["error"]}]}, "author": "agent"}]
if llm_config:
    state_delta["llm_config"] = llm_config
```

- [ ] **Step 4: Add `llm_config` to retry `state_delta`**

Find the retry state_delta building block (around line 409-413). It rebuilds state_delta for the retry. Add the same `llm_config` inclusion there:

```python
# Include tenant's LLM provider config for ADK model override (retry path)
if llm_config:
    state_delta["llm_config"] = llm_config
```

Note: `llm_config` was already computed in step 3 and is still in scope within the same method. No need to re-query.

- [ ] **Step 5: Verify chat service loads without import errors**

```bash
cd apps/api && python -c "from app.services.chat import _get_tenant_llm_config; print('OK')"
```

Expected: `OK`

- [ ] **Step 6: Commit**

```bash
git add apps/api/app/services/chat.py
git commit -m "feat: wire chat service to pass tenant LLM config to ADK via state_delta"
```

---

## Chunk 4: Frontend + Docker

### Task 8: Redesign LLM Settings Page

**Files:**
- Modify: `apps/web/src/pages/LLMSettingsPage.js`

Rewrite the LLM Settings page to use the integration registry + credential vault pattern. Instead of calling `/llm/providers/status`, it loads LLM provider entries from the integration registry, shows credential forms, and allows setting the active provider.

- [ ] **Step 1: Rewrite LLMSettingsPage.js**

Replace the entire content of `apps/web/src/pages/LLMSettingsPage.js` with:

```jsx
import React, { useState, useEffect, useCallback } from 'react';
import { Container, Row, Col, Card, Form, Button, Badge, Alert, Spinner } from 'react-bootstrap';
import { FaRobot, FaGoogle, FaKey, FaSave, FaCheck, FaEye, FaEyeSlash } from 'react-icons/fa';
import { useTranslation } from 'react-i18next';
import api from '../services/api';

const PROVIDER_ICONS = {
  anthropic_llm: FaRobot,
  gemini_llm: FaGoogle,
};

const LLM_PROVIDER_SUFFIX = '_llm';

export default function LLMSettingsPage() {
  const { t } = useTranslation('common');
  const [providers, setProviders] = useState([]);
  const [credentials, setCredentials] = useState({});
  const [activeProvider, setActiveProvider] = useState('gemini_llm');
  const [showKeys, setShowKeys] = useState({});
  const [saving, setSaving] = useState({});
  const [saveSuccess, setSaveSuccess] = useState({});
  const [activating, setActivating] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const loadData = useCallback(async () => {
    try {
      setLoading(true);
      // Load registry entries filtered to LLM providers
      const registryRes = await api.get('/integration_configs/registry');
      const llmProviders = registryRes.data.filter(e => e.integration_name.endsWith(LLM_PROVIDER_SUFFIX));

      // Load tenant's integration configs to check which have credentials
      const configsRes = await api.get('/integration_configs');
      const configMap = {};
      configsRes.data.forEach(c => { configMap[c.integration_name] = c; });

      // Load tenant features to get active provider
      const featuresRes = await api.get('/features');
      const active = featuresRes.data?.active_llm_provider || 'gemini_llm';

      // For each LLM provider, check credential status
      const providersWithStatus = await Promise.all(llmProviders.map(async (p) => {
        const config = configMap[p.name];
        let credStatus = {};
        if (config) {
          try {
            const statusRes = await api.get(`/integration_configs/${config.id}/credentials/status`);
            (statusRes.data.stored_keys || []).forEach(key => { credStatus[key] = true; });
          } catch { /* no creds yet */ }
        }
        return { ...p, config, credStatus, configured: Object.keys(credStatus).length > 0, name: p.integration_name };
      }));

      setProviders(providersWithStatus);
      setActiveProvider(active);
      setError(null);
    } catch (err) {
      setError(err.message || 'Failed to load LLM providers');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { loadData(); }, [loadData]);

  const handleCredentialChange = (providerName, key, value) => {
    setCredentials(prev => ({
      ...prev,
      [providerName]: { ...(prev[providerName] || {}), [key]: value }
    }));
  };

  const handleSave = async (provider) => {
    const creds = credentials[provider.name];
    if (!creds) return;

    setSaving(prev => ({ ...prev, [provider.name]: true }));
    try {
      // Ensure integration config exists
      let configId = provider.config?.id;
      if (!configId) {
        const createRes = await api.post('/integration_configs', {
          integration_name: provider.name,
          enabled: true,
        });
        configId = createRes.data.id;
      }

      // Store each credential field
      for (const [key, value] of Object.entries(creds)) {
        if (value && value.trim()) {
          await api.post(`/integration_configs/${configId}/credentials`, {
            credential_key: key,
            value: value.trim(),
            credential_type: key === 'api_key' ? 'api_key' : 'config',
          });
        }
      }

      setSaveSuccess(prev => ({ ...prev, [provider.name]: true }));
      setCredentials(prev => ({ ...prev, [provider.name]: {} }));
      setTimeout(() => setSaveSuccess(prev => ({ ...prev, [provider.name]: false })), 3000);
      await loadData();
    } catch (err) {
      setError(`Failed to save ${provider.display_name} credentials: ${err.message}`);
    } finally {
      setSaving(prev => ({ ...prev, [provider.name]: false }));
    }
  };

  const handleSetActive = async (providerName) => {
    setActivating(true);
    try {
      await api.put('/features', { active_llm_provider: providerName });
      setActiveProvider(providerName);
    } catch (err) {
      setError(`Failed to set active provider: ${err.message}`);
    } finally {
      setActivating(false);
    }
  };

  if (loading) {
    return (
      <Container className="py-4 text-center">
        <Spinner animation="border" className="text-info" />
        <p className="text-light mt-2">{t('loading', 'Loading...')}</p>
      </Container>
    );
  }

  return (
    <Container fluid className="py-4">
      <h2 className="text-light mb-1">{t('llm.title', 'LLM Providers')}</h2>
      <p className="text-secondary mb-4">{t('llm.subtitle', 'Configure which AI model powers your agent chat')}</p>

      {error && <Alert variant="danger" dismissible onClose={() => setError(null)}>{error}</Alert>}

      <Row xs={1} md={2} lg={3} className="g-4">
        {providers.map(provider => {
          const Icon = PROVIDER_ICONS[provider.name] || FaKey;
          const isActive = activeProvider === provider.name;
          const providerCreds = credentials[provider.name] || {};

          return (
            <Col key={provider.name}>
              <Card className="h-100" style={{
                background: 'rgba(255,255,255,0.05)',
                border: isActive ? '1px solid rgba(0, 210, 255, 0.5)' : '1px solid rgba(255,255,255,0.1)',
                borderRadius: '12px',
              }}>
                <Card.Body>
                  <div className="d-flex justify-content-between align-items-center mb-3">
                    <div className="d-flex align-items-center gap-2">
                      <Icon size={24} className="text-info" />
                      <h5 className="text-light mb-0">{provider.display_name}</h5>
                    </div>
                    <div className="d-flex gap-2">
                      {provider.configured && (
                        <Badge bg="success" className="px-2 py-1">
                          {t('llm.configured', 'Configured')}
                        </Badge>
                      )}
                      {isActive && (
                        <Badge bg="info" className="px-2 py-1">
                          {t('llm.active', 'Active')}
                        </Badge>
                      )}
                    </div>
                  </div>

                  <p className="text-secondary small mb-3">{provider.description}</p>

                  {(provider.credentials || []).map(cred => (
                    <Form.Group key={cred.key} className="mb-2">
                      <Form.Label className="text-light small">{cred.label}</Form.Label>
                      <div className="d-flex gap-2">
                        <Form.Control
                          type={cred.type === 'password' && !showKeys[`${provider.name}_${cred.key}`] ? 'password' : 'text'}
                          size="sm"
                          placeholder={provider.credStatus[cred.key] ? t('llm.keyMasked', 'Saved (enter new value to update)') : cred.help || ''}
                          value={providerCreds[cred.key] || ''}
                          onChange={e => handleCredentialChange(provider.name, cred.key, e.target.value)}
                          style={{ background: 'rgba(255,255,255,0.08)', border: '1px solid rgba(255,255,255,0.15)', color: '#fff' }}
                        />
                        {cred.type === 'password' && (
                          <Button
                            variant="outline-secondary"
                            size="sm"
                            onClick={() => setShowKeys(prev => ({
                              ...prev,
                              [`${provider.name}_${cred.key}`]: !prev[`${provider.name}_${cred.key}`]
                            }))}
                          >
                            {showKeys[`${provider.name}_${cred.key}`] ? <FaEyeSlash /> : <FaEye />}
                          </Button>
                        )}
                      </div>
                    </Form.Group>
                  ))}

                  <div className="d-flex gap-2 mt-3">
                    <Button
                      variant="outline-info"
                      size="sm"
                      disabled={saving[provider.name] || !Object.values(providerCreds).some(v => v?.trim())}
                      onClick={() => handleSave(provider)}
                    >
                      {saving[provider.name] ? (
                        <Spinner animation="border" size="sm" />
                      ) : saveSuccess[provider.name] ? (
                        <><FaCheck className="me-1" /> {t('llm.saved', 'Saved')}</>
                      ) : (
                        <><FaSave className="me-1" /> {t('llm.saveKeys', 'Save')}</>
                      )}
                    </Button>

                    {!isActive && provider.configured && (
                      <Button
                        variant="info"
                        size="sm"
                        disabled={activating}
                        onClick={() => handleSetActive(provider.name)}
                      >
                        {activating ? (
                          <Spinner animation="border" size="sm" />
                        ) : (
                          t('llm.setActive', 'Set as Active')
                        )}
                      </Button>
                    )}
                  </div>
                </Card.Body>
              </Card>
            </Col>
          );
        })}
      </Row>
    </Container>
  );
}
```

- [ ] **Step 2: Verify page renders in dev mode**

```bash
cd apps/web && npm start
```

Navigate to the LLM Settings page. Verify:
- Both Anthropic and Gemini cards appear
- Credential fields (API Key + Model ID) render correctly
- Save button is disabled when fields are empty

- [ ] **Step 3: Commit**

```bash
git add apps/web/src/pages/LLMSettingsPage.js
git commit -m "feat: redesign LLM settings page to use integration registry + vault"
```

---

### Task 9: i18n Translation Keys

**Files:**
- Modify: `apps/web/src/i18n/locales/en/common.json`
- Modify: `apps/web/src/i18n/locales/es/common.json`

- [ ] **Step 1: Add English keys**

In `apps/web/src/i18n/locales/en/common.json`, add these keys inside the existing JSON object (e.g., after the `sidebar_desc` section):

```json
  "llm": {
    "title": "LLM Providers",
    "subtitle": "Configure which AI model powers your agent chat",
    "configured": "Configured",
    "active": "Active",
    "keyMasked": "Saved (enter new value to update)",
    "saveKeys": "Save",
    "saved": "Saved",
    "setActive": "Set as Active"
  }
```

- [ ] **Step 2: Add Spanish keys**

In `apps/web/src/i18n/locales/es/common.json`, add the corresponding section:

```json
  "llm": {
    "title": "Proveedores LLM",
    "subtitle": "Configura qué modelo de IA usa tu chat de agentes",
    "configured": "Configurado",
    "active": "Activo",
    "keyMasked": "Guardado (ingresa un nuevo valor para actualizar)",
    "saveKeys": "Guardar",
    "saved": "Guardado",
    "setActive": "Establecer como Activo"
  }
```

- [ ] **Step 3: Commit**

```bash
git add apps/web/src/i18n/locales/en/common.json apps/web/src/i18n/locales/es/common.json
git commit -m "feat: add English and Spanish i18n keys for LLM settings"
```

---

### Task 10: Docker Compose + Local Testing

**Files:**
- Modify: `docker-compose.yml:95-116` (adk-server environment section)

- [ ] **Step 1: Add `ANTHROPIC_API_KEY` env var to ADK service**

In `docker-compose.yml`, in the `adk-server` service `environment` section (around line 112, after the `ADK_MODEL` line), add:

```yaml
    - ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY:-}
```

- [ ] **Step 2: Rebuild and start the stack**

```bash
docker-compose down && docker-compose up --build -d
```

Wait for all services to start:
```bash
docker-compose logs -f adk-server 2>&1 | head -20
```

Verify ADK server starts without errors and `litellm` is importable.

- [ ] **Step 3: Run the migration**

```bash
docker-compose exec db psql -U postgres servicetsunami -f /dev/stdin < apps/api/migrations/046_add_active_llm_provider.sql
```

- [ ] **Step 4: Test end-to-end with Anthropic**

1. Login to the web app
2. Navigate to LLM Settings
3. Enter Anthropic API key and model ID (e.g. `claude-sonnet-4-5`)
4. Click Save
5. Click "Set as Active"
6. Open a chat session
7. Send a message — response should come from Claude, not Gemini
8. Verify in ADK logs:
   ```bash
   docker-compose logs -f adk-server 2>&1 | grep -i "anthropic\|litellm\|claude"
   ```

- [ ] **Step 5: Test fallback to Gemini**

1. Go back to LLM Settings
2. Click "Set as Active" on Gemini (or remove Anthropic credentials)
3. Send another chat message — should use Gemini again

- [ ] **Step 6: Commit**

```bash
git add docker-compose.yml
git commit -m "feat: add ANTHROPIC_API_KEY env var to ADK docker-compose service"
```
