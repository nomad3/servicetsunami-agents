import pytest
import os
import sys
import importlib
from unittest.mock import patch
from pydantic import ValidationError


def test_secret_key_has_no_insecure_default():
    """Settings must raise on startup when critical env vars are missing."""
    env_without_secrets = {k: v for k, v in os.environ.items()
                           if k not in ("SECRET_KEY", "MCP_API_KEY", "API_INTERNAL_KEY")}
    with patch.dict(os.environ, env_without_secrets, clear=True):
        # Remove any cached module so the reload triggers a fresh Settings() call
        sys.modules.pop("app.core.config", None)
        with pytest.raises((ValidationError, Exception)) as exc_info:
            importlib.import_module("app.core.config")
        # Verify the error is about missing required fields, not some unrelated failure
        error_str = str(exc_info.value)
        assert any(field in error_str for field in [
            "SECRET_KEY", "MCP_API_KEY", "API_INTERNAL_KEY",
            "secret_key", "mcp_api_key", "api_internal_key",
        ]), f"Error should mention missing required fields, got: {error_str}"
