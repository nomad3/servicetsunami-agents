"""Per-request model override using ADK's before_model_callback.

Reads llm_config from session state (passed via state_delta by the API)
and overrides the agent's model to route to the tenant's chosen provider.

ADK uses agent.canonical_model (a property) to resolve the LLM class:
- "gemini-*" -> GoogleLLM (native)
- "anthropic/*" -> LiteLlm (via LLMRegistry)

By setting agent.model to "anthropic/{model}", canonical_model resolves
to a LiteLlm instance which routes through LiteLLM -> Anthropic API.

Usage: Register on every Agent definition:
    from config.model_callback import llm_model_callback
    agent = Agent(..., before_model_callback=llm_model_callback)
"""
import os
import logging
from typing import Optional

from google.adk.agents.callback_context import CallbackContext
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse

logger = logging.getLogger(__name__)

# LiteLLM provider prefixes for model string formatting
PROVIDER_PREFIXES = {
    "anthropic_llm": "anthropic",
    # Future providers:
    # "openai_llm": "openai",
    # "deepseek_llm": "deepseek",
}

# Map provider to the env var LiteLLM reads for that provider's API key
PROVIDER_ENV_KEYS = {
    "anthropic_llm": "ANTHROPIC_API_KEY",
    # "openai_llm": "OPENAI_API_KEY",
}


def llm_model_callback(
    callback_context: CallbackContext, llm_request: LlmRequest, **kwargs
) -> Optional[LlmResponse]:
    """Override model per-request based on tenant's llm_config in session state.

    The API passes llm_config in state_delta:
        {"provider": "anthropic_llm", "model": "claude-opus-4-6", "api_key": "sk-..."}

    For Gemini: sets agent.model to the model string (native ADK/GoogleLLM).
    For other providers: sets agent.model to "provider/model" (LiteLLM format)
        so ADK's LLMRegistry resolves it to LiteLlm instead of GoogleLLM.
        Also sets the provider's API key env var for LiteLLM to pick up.
    """
    llm_config = callback_context.state.get("llm_config")
    if not llm_config:
        return None  # No override — use default Gemini from settings.adk_model

    provider = llm_config.get("provider")
    model = llm_config.get("model")
    api_key = llm_config.get("api_key")

    if not provider or not model:
        return None  # Incomplete config — use default

    # Access the agent via the private invocation context
    agent = callback_context._invocation_context.agent

    if provider == "gemini_llm":
        # Native Gemini — just override the model string
        agent.model = model
        llm_request.model = model
        return None

    # Non-Gemini provider — use LiteLLM format
    prefix = PROVIDER_PREFIXES.get(provider)
    if not prefix:
        return None  # Unknown provider — use default

    litellm_model = f"{prefix}/{model}"

    # Override the agent's model string so canonical_model resolves to LiteLlm
    # instead of GoogleLLM. ADK's LLMRegistry matches "anthropic/.*" -> LiteLlm.
    agent.model = litellm_model
    llm_request.model = litellm_model
    logger.info(f"Model callback: switching to {litellm_model}")

    # Set API key via environment variable for LiteLLM to pick up.
    if api_key:
        env_key = PROVIDER_ENV_KEYS.get(provider)
        if env_key:
            os.environ[env_key] = api_key

    return None
