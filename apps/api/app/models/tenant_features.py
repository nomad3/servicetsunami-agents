"""TenantFeatures model for feature flags and limits."""
import uuid
from datetime import datetime
from sqlalchemy import Column, String, Integer, Float, Boolean, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship

from app.db.base import Base


class TenantFeatures(Base):
    """Tenant feature flags and usage limits."""
    __tablename__ = "tenant_features"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), unique=True, nullable=False)

    # Core Features
    agents_enabled = Column(Boolean, default=True)
    agent_groups_enabled = Column(Boolean, default=True)
    datasets_enabled = Column(Boolean, default=True)
    chat_enabled = Column(Boolean, default=True)
    multi_llm_enabled = Column(Boolean, default=True)
    agent_memory_enabled = Column(Boolean, default=True)

    # AI Intelligence Features
    ai_insights_enabled = Column(Boolean, default=True)
    ai_recommendations_enabled = Column(Boolean, default=True)
    ai_anomaly_detection = Column(Boolean, default=True)

    # Reinforcement Learning Features
    rl_enabled = Column(Boolean, default=True)
    rl_settings = Column(JSONB, nullable=False, default=lambda: {
        "exploration_rate": 0.1,
        "opt_in_global_learning": True,
        "use_global_baseline": True,
        "min_tenant_experiences": 50,
        "blend_alpha_growth": 0.01,
        "reward_weights": {"implicit": 0.3, "explicit": 0.5, "admin": 0.2},
        "review_schedule": "weekly",
        "per_decision_overrides": {}
    })

    # Usage Limits
    max_agents = Column(Integer, default=10)
    max_agent_groups = Column(Integer, default=5)
    monthly_token_limit = Column(Integer, default=1000000)
    storage_limit_gb = Column(Float, default=10.0)

    # UI Customization
    hide_agentprovision_branding = Column(Boolean, default=False)

    # Plan Type
    plan_type = Column(String, default="starter")  # starter, professional, enterprise

    # LLM Provider Selection
    active_llm_provider = Column(String(50), default="gemini_llm")

    # CLI Orchestrator
    cli_orchestrator_enabled = Column(Boolean, default=False)
    default_cli_platform = Column(String(50), default="claude_code")

    # Resilient CLI orchestrator (Phase 2 cutover gate). Default OFF —
    # legacy chain-walk path runs at flag=False with byte-identical
    # behaviour. See migration 121 + design doc §3 for cutover plan.
    use_resilient_executor = Column(Boolean, nullable=False, default=False)

    # Shadow-mode sub-flag. When `use_resilient_executor` is FALSE, we
    # run the new path in shadow alongside the legacy path so we can
    # diff outcomes. Default FALSE = stubbed shadow (replays legacy
    # outcome, no real Temporal/LLM dispatch — the cheap mass path).
    # TRUE = real adapter dispatch (~2x cost; only for ~48h internal
    # tenant validation per the cutover plan).
    shadow_mode_real_dispatch = Column(Boolean, nullable=False, default=False)

    # CLI stream-output rollout gate (migration 134). When TRUE the
    # code-worker switches Claude Code to `--output-format stream-json`
    # and streams every reasoning/tool_use/tool_result event into the
    # terminal card. Default OFF prod; seeded ON for the saguilera
    # test tenant. Plan:
    # docs/plans/2026-05-16-terminal-full-cli-output.md §9
    cli_stream_output = Column(Boolean, nullable=False, default=False)

    # NightlyReflectionWorkflow (O2 of #616) per-tenant kill-switch.
    # Default OFF in prod — locked decision #4 in the canonical design.
    # The workflow checks this flag at top-of-run and short-circuits
    # with reason='kill_switch_off' when FALSE. Operators flip per
    # tenant after reviewing dry-run output. Migration 142.
    nightly_reflection_enabled = Column(Boolean, nullable=False, default=False)

    # CPA software export format for the Bookkeeper Agent's weekly
    # categorized output. AAHA stays canonical — the Bookkeeper still
    # categorizes against the AAHA chart of accounts; this just picks
    # which format adapter converts the categorized rows into the
    # CPA's preferred import file. Migration 117.
    # Valid values: xlsx | csv | quickbooks_iif | quickbooks_qbo |
    #               xero_csv | sage_intacct_csv
    cpa_export_format = Column(String(32), nullable=False, default="xlsx")

    # GitHub primary account for repo operations.
    # Pins which connected GitHub account the MCP github tools use as
    # default when the caller doesn't pass an explicit account_email.
    # Useful when a tenant has multiple GitHub accounts wired but only
    # one is intended for repo access (e.g. employer EMU accounts that
    # only have Copilot CLI license, no repo visibility under enterprise
    # policy). Null = fall back to the multi-account fan-out behavior.
    github_primary_account = Column(String(255), nullable=True)

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    tenant = relationship("Tenant", back_populates="features")

    def __repr__(self):
        return f"<TenantFeatures {self.tenant_id} plan={self.plan_type}>"
