"""Pydantic schemas for TenantFeatures."""
from pydantic import BaseModel
from typing import Optional, Dict, Any
from datetime import datetime
import uuid


class TenantFeaturesBase(BaseModel):
    # Core Features
    agents_enabled: bool = True
    agent_groups_enabled: bool = True
    datasets_enabled: bool = True
    chat_enabled: bool = True
    multi_llm_enabled: bool = True
    agent_memory_enabled: bool = True
    # AI Intelligence
    ai_insights_enabled: bool = True
    ai_recommendations_enabled: bool = True
    ai_anomaly_detection: bool = True
    # Limits
    max_agents: int = 10
    max_agent_groups: int = 5
    monthly_token_limit: int = 1000000
    storage_limit_gb: float = 10.0
    # UI
    hide_agentprovision_branding: bool = False
    plan_type: str = "starter"
    # LLM Provider Selection
    active_llm_provider: Optional[str] = "gemini_llm"
    # CLI Orchestrator
    cli_orchestrator_enabled: Optional[bool] = False
    default_cli_platform: Optional[str] = "claude_code"
    # Pinned GitHub account for MCP repo operations (null = autodetect across all)
    github_primary_account: Optional[str] = None
    # CPA software export format for the Bookkeeper Agent's weekly output.
    # AAHA stays canonical; this picks which adapter writes the file.
    # Valid: xlsx | csv | quickbooks_iif | quickbooks_qbo | xero_csv |
    #        sage_intacct_csv
    cpa_export_format: Optional[str] = "xlsx"
    # Reinforcement Learning
    rl_enabled: bool = False
    rl_settings: Optional[Dict[str, Any]] = None
    # Luna Value Layer (#647). Per-tenant kill-switch. Default OFF so a
    # tenant that seeded a draft value set never has blocks fire against
    # their chat path until an operator explicitly opts in. Mirrors the
    # nightly_reflection_enabled pattern.
    value_layer_enabled: bool = False


class TenantFeaturesCreate(TenantFeaturesBase):
    pass


class TenantFeaturesUpdate(BaseModel):
    agents_enabled: Optional[bool] = None
    agent_groups_enabled: Optional[bool] = None
    datasets_enabled: Optional[bool] = None
    chat_enabled: Optional[bool] = None
    multi_llm_enabled: Optional[bool] = None
    agent_memory_enabled: Optional[bool] = None
    ai_insights_enabled: Optional[bool] = None
    ai_recommendations_enabled: Optional[bool] = None
    ai_anomaly_detection: Optional[bool] = None
    max_agents: Optional[int] = None
    max_agent_groups: Optional[int] = None
    monthly_token_limit: Optional[int] = None
    storage_limit_gb: Optional[float] = None
    hide_agentprovision_branding: Optional[bool] = None
    plan_type: Optional[str] = None
    active_llm_provider: Optional[str] = None
    cli_orchestrator_enabled: Optional[bool] = None
    default_cli_platform: Optional[str] = None
    github_primary_account: Optional[str] = None
    cpa_export_format: Optional[str] = None
    rl_enabled: Optional[bool] = None
    rl_settings: Optional[Dict[str, Any]] = None
    value_layer_enabled: Optional[bool] = None


class TenantFeatures(TenantFeaturesBase):
    id: uuid.UUID
    tenant_id: uuid.UUID
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
