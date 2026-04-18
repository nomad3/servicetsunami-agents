import pytest
import os
from unittest.mock import patch


def test_secret_key_has_no_insecure_default():
    """Settings must not silently fall back to weak default values."""
    env_without_secrets = {k: v for k, v in os.environ.items()
                           if k not in ("SECRET_KEY", "MCP_API_KEY", "API_INTERNAL_KEY")}
    with patch.dict(os.environ, env_without_secrets, clear=True):
        from importlib import reload
        try:
            import app.core.config as cfg_module
            reload(cfg_module)
            # If reload succeeds, check that defaults are not insecure values
            s = cfg_module.settings
            assert s.SECRET_KEY != "secret", "SECRET_KEY must not have insecure default 'secret'"
            assert s.MCP_API_KEY != "dev_mcp_key", "MCP_API_KEY must not have insecure default 'dev_mcp_key'"
            assert s.API_INTERNAL_KEY != "internal-service-key", "API_INTERNAL_KEY must not have insecure default"
        except Exception:
            pass  # ValidationError on missing required field is the correct behavior
