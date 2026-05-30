"""Manifest-driven vet-practice tenant provisioner (v1).

Generalizes the one-off ``seed_animaldoctor_agent_fleet.py`` into a
reusable, idempotent provisioner. ``provision_vet_practice`` takes a
``tenant_id`` + a ``VetPracticeProfile`` and seeds a ready-to-run
veterinary practice from existing platform primitives:

  - the agent fleet (idempotent upsert on ``(tenant_id, name)``),
  - connector slots (``integration_config`` rows, ``enabled=False``),
  - vet workflow templates (tenant copies of native templates),
  - USER-principal ``agent_permissions`` for the owner,
  - declared value-sets (added_by="seed").

The single Python entrypoint three callers delegate to: the operator
internal endpoint (v1), the ``alpha`` verb (operator UX, deferred), and
the self-serve register hook (deferred — needs a ``TenantCreate`` schema
change, out of scope). This is the Alpha-CLI-kernel pattern.

═══ Idempotency (plan §1.2 + §9 — BUILT INTO this provisioner) ═══
The agent upsert prior art is solid, but the OTHER seed paths blind-
insert. So idempotency is built in HERE, not assumed:
  - Agents       → upsert keyed (tenant_id, name); drift-check managed
                   fields only; never clobber human-set owner/escalation.
  - Connectors   → check (tenant_id, integration_name) FIRST; insert the
                   disabled slot only if absent. Never flip an already-
                   connected tenant back to disabled.
  - Workflows    → check (tenant_id, source_template_id) FIRST; install a
                   tenant copy only if absent (the install_template path
                   itself has no uniqueness guard).
  - Permissions  → check (agent_id, tenant_id, principal_type='user',
                   principal_id, permission) FIRST; insert if absent.
  - Value-sets   → write_value_set is append-only versioned; only write a
                   NEW version when the desired declared set DRIFTS from
                   the latest stored one (re-run = no new version).
Re-running is a clean no-op — asserted by
``test_provision_rerun_is_a_clean_noop``.

═══ Enforced vs declared (plan §9 — Codex correction) ═══
  - ENFORCED v1 guardrails: the ``human_approval`` workflow gate
    (runtime) + USER-principal ``agent_permissions`` (the principal_type
    ``deps.require_agent_permission`` actually checks — deps.py:147).
  - Role-principal permissions are NOT seeded: ``deps.py`` ignores them,
    so seeding ``practice_owner`` / ``veterinarian`` role rows would be
    advertising enforcement that doesn't exist. (Wire role enforcement
    first, then add them — tracked as a follow-up.)
  - Value-sets are seeded DECLARED ONLY. ``value_arbitration.py`` is
    "PURE LIBRARY ONLY — NO RUNTIME WIRING", so the ``tenant_norm`` veto
    does not fire at runtime. We seed them so the hard rules are recorded
    + auditable and enforceable the moment arbitration is wired, but we
    do NOT imply runtime enforcement.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.agent import Agent
from app.models.agent_permission import AgentPermission
from app.models.dynamic_workflow import DynamicWorkflow
from app.models.integration_config import IntegrationConfig
from app.models.user import User
from app.services import agent_value_set_io as value_io
from app.services.provisioning.vet_manifest import (
    VetPracticeManifest,
    get_manifest,
)

log = logging.getLogger(__name__)


# ── profile (the per-tenant binding) ──────────────────────────────────


@dataclass
class VetPracticeProfile:
    """Per-tenant binding for a provisioning run.

    The manifest is the source of truth for the fleet shape; the profile
    carries the per-tenant specifics (owner, mailbox, clinician name) and
    selects which manifest variant to apply."""

    practice_name: str
    practice_type: str = "cardiology"  # "cardiology" | "gp" | "multi_specialty"
    owner_user_id: Optional[uuid.UUID] = None  # falls back to tenant admin
    intake_mailbox: Optional[str] = None
    lead_clinician_name: Optional[str] = None
    fleet_variant: str = "cardiology_v1"


# Agent fields the provisioner writes + drift-checks on every run.
# Anything not here is left alone on an existing row so a human override
# (e.g. a hand-edited persona) survives re-provisioning. owner_user_id and
# escalation_agent_id are seeded once (see _seed_agents) but kept OUT of
# the managed set so a human re-owner / re-route isn't clobbered.
_MANAGED_AGENT_FIELDS = (
    "role",
    "description",
    "capabilities",
    "personality",
    "persona_prompt",
    "tool_groups",
    "default_model_tier",
    "autonomy_level",
    "max_delegation_depth",
    "status",
    "version",
    "tool_groups_review_required",
)


def _empty_counts() -> Dict[str, int]:
    return {"created": 0, "updated": 0, "unchanged": 0}


# ── owner resolution ──────────────────────────────────────────────────


def _resolve_owner_id(
    db: Session, tenant_id: uuid.UUID, profile: VetPracticeProfile
) -> Optional[uuid.UUID]:
    """Resolve the agent owner, ALWAYS scoped to ``tenant_id``.

    If ``profile.owner_user_id`` is supplied it MUST resolve to a ``User``
    on this tenant (``id == owner_user_id AND tenant_id == tenant_id``);
    otherwise we raise ``ValueError`` (tenant-isolation break — the caller
    surfaces a 400). A cross-tenant owner would write the foreign user's id
    into ``Agent.owner_user_id`` + USER-principal ``agent_permissions``,
    handing them implicit access to another tenant's fleet.

    If ``owner_user_id`` is omitted we resolve the tenant's own admin
    (superuser preferred, else the earliest user on the tenant).

    Returns None only if the tenant has no users at all — agents are then
    seeded ownerless (logged WARNING) rather than failing the whole run,
    but this is the "born ownerless" anti-pattern the operator should fix."""
    if profile.owner_user_id is not None:
        owner = (
            db.query(User)
            .filter(
                User.id == profile.owner_user_id,
                User.tenant_id == tenant_id,
            )
            .first()
        )
        if owner is None:
            raise ValueError(
                f"owner_user_id {profile.owner_user_id} does not belong to "
                f"tenant {tenant_id} (cross-tenant owner rejected)"
            )
        return owner.id

    admin = (
        db.query(User)
        .filter(User.tenant_id == tenant_id, User.is_superuser == True)  # noqa: E712
        .order_by(User.id.asc())
        .first()
    )
    if admin is None:
        admin = (
            db.query(User)
            .filter(User.tenant_id == tenant_id)
            .order_by(User.id.asc())
            .first()
        )
    if admin is None:
        log.warning(
            "provision_vet_practice: tenant=%s has no users; agents will "
            "be seeded WITHOUT an owner (operator should set owner_user_id)",
            tenant_id,
        )
        return None
    return admin.id


# ── agents ────────────────────────────────────────────────────────────


def _agent_managed_values(spec: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "role": spec["role"],
        "description": spec["description"],
        "capabilities": list(spec["capabilities"]),
        "personality": spec.get("personality"),
        "persona_prompt": spec["persona_prompt"],
        "tool_groups": list(spec["tool_groups"]),
        "default_model_tier": spec["default_model_tier"],
        "autonomy_level": "supervised",
        "max_delegation_depth": 2,
        "status": "production",
        "version": 1,
        "tool_groups_review_required": bool(
            spec.get("tool_groups_review_required", True)
        ),
    }


def _seed_agents(
    db: Session,
    tenant_id: uuid.UUID,
    manifest: VetPracticeManifest,
    owner_id: Optional[uuid.UUID],
) -> tuple[Dict[str, int], Dict[str, Agent]]:
    """Idempotent upsert of every manifest agent keyed (tenant_id, name).

    Two-pass:
      1. upsert each agent (create or drift-correct managed fields),
      2. resolve escalation_agent_id by name AFTER all agents exist (so a
         forward reference resolves). owner_user_id is set on create only;
         it is NOT in the managed set, so a human re-owner survives re-run.

    Returns (counts, {name: Agent}) — the name map feeds permission +
    value-set seeding."""
    counts = _empty_counts()
    by_name: Dict[str, Agent] = {}

    for spec in manifest.agents:
        name = spec["name"]
        existing = (
            db.query(Agent)
            .filter(Agent.tenant_id == tenant_id, Agent.name == name)
            .first()
        )
        desired = _agent_managed_values(spec)

        if existing is None:
            agent = Agent(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                name=name,
                owner_user_id=owner_id,  # set once, on create
                **desired,
            )
            db.add(agent)
            db.flush()  # assign id so escalation FK + perms can reference it
            by_name[name] = agent
            counts["created"] += 1
            log.info("provision: created agent %s (%s)", name, agent.id)
            continue

        drift = [
            f for f in _MANAGED_AGENT_FIELDS
            if getattr(existing, f) != desired[f]
        ]
        for f in drift:
            setattr(existing, f, desired[f])
        # Backfill owner only if absent — never clobber a human re-owner.
        if existing.owner_user_id is None and owner_id is not None:
            existing.owner_user_id = owner_id
            if "owner_user_id" not in drift:
                drift.append("owner_user_id")
        by_name[name] = existing
        if drift:
            db.add(existing)
            counts["updated"] += 1
            log.info(
                "provision: updated agent %s (%s) drift=%s",
                name, existing.id, ", ".join(drift),
            )
        else:
            counts["unchanged"] += 1

    # Pass 2 — escalation FK by name. Only set when absent (don't clobber a
    # human re-route); resolves a forward reference now that all rows exist.
    for spec in manifest.agents:
        target_name = spec.get("escalation_to")
        if not target_name:
            continue
        agent = by_name.get(spec["name"])
        target = by_name.get(target_name)
        if agent is None or target is None:
            continue
        if agent.escalation_agent_id is None:
            agent.escalation_agent_id = target.id
            db.add(agent)

    return counts, by_name


# ── connector slots ───────────────────────────────────────────────────


def _seed_connector_slots(
    db: Session, tenant_id: uuid.UUID, manifest: VetPracticeManifest
) -> Dict[str, int]:
    """Idempotent connector-slot seeding. ``create_tenant_integration_config``
    blind-inserts (no uniqueness on (tenant_id, integration_name)), so the
    check-then-insert guard lives HERE (plan §9). Existing slots are left
    untouched — never flip an already-connected tenant back to disabled."""
    counts = _empty_counts()
    for slot in manifest.connector_slots:
        name = slot["integration_name"]
        existing = (
            db.query(IntegrationConfig)
            .filter(
                IntegrationConfig.tenant_id == tenant_id,
                IntegrationConfig.integration_name == name,
            )
            .first()
        )
        if existing is not None:
            counts["unchanged"] += 1
            continue
        db.add(
            IntegrationConfig(
                tenant_id=tenant_id,
                integration_name=name,
                enabled=False,  # awaiting credentials
                requires_approval=bool(slot.get("requires_approval", False)),
                rate_limit=slot.get("rate_limit"),
            )
        )
        counts["created"] += 1
        log.info("provision: seeded connector slot %s (disabled)", name)
    return counts


# ── workflow templates ────────────────────────────────────────────────


def _seed_workflow_templates(
    db: Session, tenant_id: uuid.UUID, manifest: VetPracticeManifest
) -> Dict[str, int]:
    """Install per-tenant copies of native templates, idempotently.

    The ``install_template_internal`` path blind-inserts with no
    uniqueness on (tenant_id, source_template_id), so the check-then-
    install guard lives HERE (plan §9). A template named in the manifest
    but absent from the platform is logged + skipped (not fatal)."""
    counts = _empty_counts()
    for template_name in manifest.workflow_templates:
        native = (
            db.query(DynamicWorkflow)
            .filter(
                DynamicWorkflow.name == template_name,
                DynamicWorkflow.tier == "native",
            )
            .first()
        )
        if native is None:
            log.warning(
                "provision: native template %r not found; skipping install "
                "(seed_native_templates may not have run on this tenant)",
                template_name,
            )
            continue

        already = (
            db.query(DynamicWorkflow)
            .filter(
                DynamicWorkflow.tenant_id == tenant_id,
                DynamicWorkflow.source_template_id == native.id,
            )
            .first()
        )
        if already is not None:
            counts["unchanged"] += 1
            continue

        db.add(
            DynamicWorkflow(
                tenant_id=tenant_id,
                name=native.name,
                description=native.description,
                definition=native.definition,
                trigger_config=native.trigger_config,
                tags=native.tags,
                tier="custom",
                source_template_id=native.id,
            )
        )
        native.installs = (native.installs or 0) + 1
        counts["created"] += 1
        log.info(
            "provision: installed workflow %r → tenant=%s copy",
            template_name, tenant_id,
        )
    return counts


# ── permissions (USER-principal only — the enforced axis) ─────────────


def _seed_permissions(
    db: Session,
    tenant_id: uuid.UUID,
    owner_id: Optional[uuid.UUID],
    granted_by: Optional[uuid.UUID],
    agents_by_name: Dict[str, Agent],
) -> Dict[str, int]:
    """Seed USER-principal 'admin' grants for the owner on every agent.

    ``deps.require_agent_permission`` only checks principal_type='user'
    (+ owner/superuser implicit) — see deps.py:147. Seeding role rows
    would be dead infrastructure, so we seed ONLY the enforceable
    user-principal grant (plan §9). Idempotent on
    (agent_id, tenant_id, principal_type, principal_id, permission)."""
    counts = _empty_counts()
    if owner_id is None:
        log.warning(
            "provision: no owner resolved; skipping permission seeding "
            "(no enforceable principal to grant)"
        )
        return counts

    for agent in agents_by_name.values():
        existing = (
            db.query(AgentPermission)
            .filter(
                AgentPermission.agent_id == agent.id,
                AgentPermission.tenant_id == tenant_id,
                AgentPermission.principal_type == "user",
                AgentPermission.principal_id == owner_id,
                AgentPermission.permission == "admin",
            )
            .first()
        )
        if existing is not None:
            counts["unchanged"] += 1
            continue
        db.add(
            AgentPermission(
                agent_id=agent.id,
                tenant_id=tenant_id,
                principal_type="user",
                principal_id=owner_id,
                permission="admin",
                granted_by=granted_by,
            )
        )
        counts["created"] += 1
    return counts


# ── value-sets (DECLARED, not runtime-enforced) ───────────────────────


def _desired_value_set_drifts(
    db: Session,
    tenant_id: uuid.UUID,
    agent_id: uuid.UUID,
    protect: List[Dict[str, Any]],
    pursue: List[Dict[str, Any]],
    avoid: List[Dict[str, Any]],
) -> bool:
    """True if the desired declared slugs differ from the latest stored
    value-set for (tenant, agent). Keeps write_value_set append-only re-
    runs from churning a new version every provision."""
    current = value_io.read_value_set(db, tenant_id=tenant_id, agent_id=agent_id)
    cur = (
        {i.slug for i in current.protect},
        {i.slug for i in current.pursue},
        {i.slug for i in current.avoid},
    )
    want = (
        {str(i["slug"]).strip().lower() for i in protect},
        {str(i["slug"]).strip().lower() for i in pursue},
        {str(i["slug"]).strip().lower() for i in avoid},
    )
    return cur != want


class ValueSetWriteError(RuntimeError):
    """A declared value-set write failed (``write_value_set`` returned None).

    ``write_value_set`` ``db.rollback()``s the session before returning None,
    so by the time this is raised the WHOLE provisioning run's prior staged
    inserts are already gone. We raise (instead of logging + continuing) so
    the run reports a hard FAILURE rather than a SUCCESS summary over a
    half-lost transaction (BLOCKER 1)."""


def _seed_value_sets(
    db: Session,
    tenant_id: uuid.UUID,
    manifest: VetPracticeManifest,
    agents_by_name: Dict[str, Agent],
) -> Dict[str, int]:
    """Seed DECLARED value-sets (added_by='seed') for the manifest's gated
    agents. NOT runtime-enforced — value_arbitration.py is pure-library
    with no runtime wiring (plan §9). Seeded so the hard rules are recorded
    + auditable + enforceable once arbitration is wired. Idempotent: only
    writes a new append-only version when the declared slugs drift.

    ``write_value_set`` ``db.commit()``s internally on success — so this is
    the LAST write of the run and its commit finalizes every prior flushed
    step. A None return means the write FAILED (and already rolled the
    session back); we raise ``ValueSetWriteError`` so the run fails loud
    instead of reporting success over lost inserts (BLOCKER 1)."""
    counts = _empty_counts()
    now = datetime.now(timezone.utc).isoformat()

    for agent_name, sets in manifest.value_sets.items():
        agent = agents_by_name.get(agent_name)
        if agent is None:
            log.warning(
                "provision: value-set declares agent %r not in fleet; skip",
                agent_name,
            )
            continue

        def _stamp(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
            # write_value_set persists each item dict verbatim; stamp the
            # provenance fields ValueItem.from_dict expects so the declared
            # origin is unambiguous in the audit trail.
            return [
                {
                    "slug": str(i["slug"]).strip().lower(),
                    "description": i.get("description", ""),
                    "added_at": now,
                    "added_by": "seed",
                    "evidence_memory_ids": [],
                }
                for i in items
            ]

        protect = _stamp(sets.get("protect", []))
        pursue = _stamp(sets.get("pursue", []))
        avoid = _stamp(sets.get("avoid", []))

        if not _desired_value_set_drifts(
            db, tenant_id, agent.id, protect, pursue, avoid
        ):
            counts["unchanged"] += 1
            continue

        persisted = value_io.write_value_set(
            db,
            tenant_id=tenant_id,
            agent_id=agent.id,
            protect=protect,
            pursue=pursue,
            avoid=avoid,
        )
        if persisted is None:
            # write_value_set has ALREADY rolled the session back. Raising
            # here makes provision_vet_practice fail loud (BLOCKER 1) instead
            # of returning a SUCCESS summary over a half-lost transaction.
            raise ValueSetWriteError(
                f"write_value_set returned None for agent {agent_name} "
                f"({agent.id}); session was rolled back — provisioning run "
                f"FAILED (no half-provisioned tenant)"
            )
        counts["created"] += 1
        log.info(
            "provision: seeded DECLARED value-set v%s for agent %s (%s) "
            "[NOT runtime-enforced — arbitration is pure-library]",
            persisted.version, agent_name, agent.id,
        )
    return counts


# ── dry-run plan ──────────────────────────────────────────────────────


def _dry_run_plan(
    db: Session,
    tenant_id: uuid.UUID,
    manifest: VetPracticeManifest,
    owner_id: Optional[uuid.UUID],
) -> Dict[str, Any]:
    """Describe what WOULD be created without writing anything.

    Counts the objects each section would seed, accounting for what's
    already present (so a dry-run on a partially-provisioned tenant shows
    the *remaining* work)."""
    existing_agents = {
        a.name
        for a in db.query(Agent.name).filter(Agent.tenant_id == tenant_id)
    }
    agents_planned = sum(
        1 for a in manifest.agents if a["name"] not in existing_agents
    )

    existing_slots = {
        s.integration_name
        for s in db.query(IntegrationConfig.integration_name).filter(
            IntegrationConfig.tenant_id == tenant_id
        )
    }
    slots_planned = sum(
        1 for s in manifest.connector_slots
        if s["integration_name"] not in existing_slots
    )

    wf_planned = 0
    for template_name in manifest.workflow_templates:
        native = (
            db.query(DynamicWorkflow)
            .filter(
                DynamicWorkflow.name == template_name,
                DynamicWorkflow.tier == "native",
            )
            .first()
        )
        if native is None:
            continue
        already = (
            db.query(DynamicWorkflow)
            .filter(
                DynamicWorkflow.tenant_id == tenant_id,
                DynamicWorkflow.source_template_id == native.id,
            )
            .first()
        )
        if already is None:
            wf_planned += 1

    return {
        "dry_run": True,
        "variant": manifest.variant,
        "owner_user_id": str(owner_id) if owner_id else None,
        "agents": {"planned": agents_planned},
        "connector_slots": {"planned": slots_planned},
        "workflow_templates": {"planned": wf_planned},
        "permissions": {"planned": agents_planned if owner_id else 0},
        "value_sets": {"planned": len(manifest.value_sets)},
    }


# ── public entrypoint ─────────────────────────────────────────────────


def provision_vet_practice(
    db: Session,
    tenant_id: uuid.UUID,
    profile: Optional[VetPracticeProfile] = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Idempotently provision a vet practice on ``tenant_id``.

    Args:
        db: an open session. On a real run we commit at the end; on
            ``dry_run`` we never write (and roll back any reads' implicit
            transaction defensively).
        tenant_id: the target tenant.
        profile: per-tenant binding. Defaults to a cardiology_v1 profile
            with the owner resolved to the tenant admin.
        dry_run: when True, return the plan and write NOTHING.

    Returns a per-object ``{created|updated|unchanged}`` (or ``planned``
    on dry-run) summary so a re-run is observable and safe."""
    if isinstance(tenant_id, str):
        tenant_id = uuid.UUID(tenant_id)
    if profile is None:
        profile = VetPracticeProfile(practice_name="Vet Practice")

    manifest = get_manifest(profile.fleet_variant)
    owner_id = _resolve_owner_id(db, tenant_id, profile)

    if dry_run:
        # NIT: scope the probe to a SAVEPOINT so rolling it back discards
        # ONLY the provisioner's own (read-only here, but defensively
        # scoped) work — not unrelated uncommitted writes the caller staged
        # before calling us. A blanket db.rollback() would nuke those too.
        nested = db.begin_nested()
        try:
            plan = _dry_run_plan(db, tenant_id, manifest, owner_id)
        finally:
            # Always release the savepoint by rolling it back: a dry-run
            # must write NOTHING on commit, and the outer txn (with the
            # caller's prior staged work) is left intact.
            nested.rollback()
        log.info(
            "provision_vet_practice DRY-RUN tenant=%s variant=%s plan=%s",
            tenant_id, manifest.variant, plan,
        )
        return plan

    try:
        # ── Atomicity contract (BLOCKER 1) ──────────────────────────────
        # write_value_set does an internal db.commit() (and a db.rollback()
        # + None return on failure), so it is the run's COMMIT BOUNDARY. We
        # therefore order ALL other writes BEFORE it and only FLUSH them —
        # never commit — so a later failure rolls the whole run back, and
        # the value-set commit (last) finalizes everything atomically.
        agent_counts, agents_by_name = _seed_agents(
            db, tenant_id, manifest, owner_id
        )
        slot_counts = _seed_connector_slots(db, tenant_id, manifest)
        wf_counts = _seed_workflow_templates(db, tenant_id, manifest)
        perm_counts = _seed_permissions(
            db, tenant_id, owner_id, owner_id, agents_by_name
        )
        # Flush (not commit) so the agent ids are visible to the value-set
        # writer's INSERTs (agent_memory.agent_id is a NOT-NULL FK) while the
        # whole txn stays open + rollback-able.
        db.flush()
        # LAST write. On success its internal commit finalizes the run. On
        # failure it raises ValueSetWriteError (after rolling the session
        # back) → fail loud, NOT a silent success summary.
        vs_counts = _seed_value_sets(db, tenant_id, manifest, agents_by_name)
        # Re-run / no-drift case: _seed_value_sets may not have called
        # write_value_set at all (all value-sets unchanged), so nothing has
        # committed yet — finalize the flushed agent/connector/workflow/
        # permission writes here. When a value-set write DID happen it
        # already committed, making this a harmless no-op.
        db.commit()
    except Exception:
        db.rollback()
        log.exception(
            "provision_vet_practice FAILED tenant=%s variant=%s — rolled back",
            tenant_id, profile.fleet_variant,
        )
        raise

    result = {
        "dry_run": False,
        "variant": manifest.variant,
        "tenant_id": str(tenant_id),
        "owner_user_id": str(owner_id) if owner_id else None,
        "agents": agent_counts,
        "connector_slots": slot_counts,
        "workflow_templates": wf_counts,
        "permissions": perm_counts,
        "value_sets": vs_counts,
    }
    log.info(
        "provision_vet_practice DONE tenant=%s variant=%s result=%s",
        tenant_id, manifest.variant, result,
    )
    return result


__all__ = [
    "VetPracticeProfile",
    "provision_vet_practice",
]
