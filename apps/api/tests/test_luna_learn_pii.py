"""T6.4c — PII end-to-end pipeline test.

Spec §3 final row: when the LLM scrubs PII per the SYNTHESIS_SYSTEM
rubric, the installed skill body must contain ``<user-name>`` /
``<address>`` placeholders (not raw values), the ``transcript_sha256``
in the install provenance frontmatter must reflect the transcript
hash the workflow computed, and the KG diffusion observation must
embed only capability metadata — never raw transcript snippets.

The workflow's PII compliance is enforced through the LLM (rather
than a server-side scrubber): the SYNTHESIS_SYSTEM prompt tells the
synthesizer to emit placeholders. This test simulates a compliant
LLM by stubbing ``synthesize_skill_draft``'s MCP response to return a
draft body that already uses placeholders, and asserts the rest of
the pipeline preserves them through the install + diffuse steps.

Per the actual workflow implementation (apps/api/app/workflows/
learn_from_media_workflow.py:607), ``transcript_sha256`` is computed
from the raw transcript returned by ``transcribe_url`` — NOT a
post-hoc scrubbed transcript. The spec §3 row reads "hash of
SCRUBBED transcript" but the LLM is the scrubber, and it doesn't
return a scrubbed transcript — it returns a scrubbed skill_md. This
test pins the *actual* current behavior and explicitly flags the
spec/implementation gap so a future refactor (e.g. server-side
scrubber) can update the assertion in lockstep.
"""
from __future__ import annotations

import hashlib
from unittest.mock import MagicMock

import httpx
import pytest

from app.workflows.activities import learn_from_media_activities as A
from app.workflows.learn_from_media_workflow import LearnFromMediaWorkflow
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

# Reuse the T6.1 reviewer stub for verdict routing.
from fixtures.code_reviewer_stub import reviewer_stub, STUB_REVIEWER_AGENT_ID


# ── Test data — a transcript with PII + a "scrubbed" draft ──────────────


_RAW_TRANSCRIPT = (
    "Today I'm explaining how to handle a customer email from "
    "alice.smith@example.com about her account. She lives at "
    "742 Evergreen Terrace and her phone is 555-867-5309. Alice's "
    "support tier is gold."
)

# Body uses the placeholders the SYNTHESIS_SYSTEM prompt instructs the
# LLM to emit. Raw values must NOT appear here — the test asserts so
# explicitly, defending against a future stub copy/paste regression.
_SCRUBBED_SKILL_MD = (
    "---\n"
    "name: Handle Gold Tier Customer Email\n"
    "engine: markdown\n"
    "category: customer-support\n"
    "tags: [email, support, gold-tier]\n"
    "auto_trigger: \"handle gold tier email\"\n"
    "inputs: []\n"
    "---\n"
    "## Description\n"
    "Walk through a customer support email from a gold-tier account holder.\n"
    "\n"
    "## Steps\n"
    "1. Greet the customer by name: `Hello <user-name>,`\n"
    "2. Confirm the address on file: `<address>` or ask politely if missing.\n"
    "3. If a callback is needed, confirm the phone number `<phone-number>`.\n"
    "4. Look up the account via the customer email `<email>`.\n"
)


# ── Workflow harness ────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _isolate_workspace(monkeypatch, tmp_path):
    monkeypatch.setattr(A, "_WORKSPACE_BASE", tmp_path)


@pytest.fixture
async def env():
    async with await WorkflowEnvironment.start_time_skipping() as e:
        yield e


@pytest.fixture
async def worker(env):
    async with Worker(
        env.client,
        task_queue="luna-learn-pii",
        workflows=[LearnFromMediaWorkflow],
        activities=[
            A.act_extract_media,
            A.act_transcribe_url,
            A.act_synthesize_skill_draft,
            A.act_dispatch_skill_review,
            A.act_run_synthetic_test,
            A.act_install_skill,
            A.act_diffuse_learning,
            A.act_write_cache,
            A.act_write_quarantine,
            A.act_log_test_fail,
            A.act_notify_session,
            A.act_probe_attachment,
            A.act_read_cache,
        ],
    ) as w:
        yield w


def _build_pii_responses(captured: dict) -> dict:
    """Build the MCP stub map for a PII-scrubbed happy path.

    ``captured`` collects each tool's payload so the test can assert
    against the install + diffuse arguments (provenance hash + KG
    observation text)."""

    def _capture(name):
        def _do(payload, _idx):
            captured[name] = payload
            return _responses[name]
        return _do

    def _review(payload, _idx):
        captured["dispatch_skill_review"] = payload
        return reviewer_stub(payload.get("skill_md", ""))

    _responses = {
        "extract_media": {
            "audio_path": "/tmp/x.m4a",
            "metadata": {"duration_s": 75, "title": "Gold tier email"},
        },
        "transcribe_url": {
            "transcript": _RAW_TRANSCRIPT,
            "engine": "whisper",
            "duration_ms": 75_000,
        },
        "synthesize_skill_draft": {
            "skill_md": _SCRUBBED_SKILL_MD,
            "slug": "handle-gold-tier-customer-email",
            "engine": "markdown",
            "synthetic_test_input": {"customer": "<user-name>"},
            "synthetic_test_expected": {"contains": "Hello"},
        },
        "dispatch_skill_review": {"verdict": "approved", "findings": [], "reviewer_agent_id": STUB_REVIEWER_AGENT_ID},
        "run_synthetic_test": {"passed": True, "actual_output": {}, "error": None},
        "install_skill": {
            "skill_id": "sk_pii_001",
            "slug": "handle-gold-tier-customer-email",
            "path": "_tenant/00000000-0000-0000-0000-000000000001/handle-gold-tier-customer-email/skill.md",
        },
        "diffuse_learning": {
            "observation_id": "obs_pii_001",
            "soft_failed": False,
            "error": None,
        },
        "act_notify_session": {"notified": True},
    }

    return {
        "extract_media": _capture("extract_media"),
        "transcribe_url": _capture("transcribe_url"),
        "synthesize_skill_draft": _capture("synthesize_skill_draft"),
        "dispatch_skill_review": _review,
        "run_synthetic_test": _capture("run_synthetic_test"),
        "install_skill": _capture("install_skill"),
        "diffuse_learning": _capture("diffuse_learning"),
        "act_notify_session": _capture("act_notify_session"),
    }


def _install_stub(monkeypatch, responses):
    async def fake(tool, payload):
        if tool not in responses:
            raise RuntimeError(f"unexpected MCP call {tool!r}")
        v = responses[tool]
        if callable(v):
            return v(payload, 0)
        return v

    monkeypatch.setattr(A, "_call_mcp", fake)


# ── The PII pipeline assertions ─────────────────────────────────────────


async def test_pii_pipeline_preserves_placeholders_through_install(
    env, worker, monkeypatch
):
    """Compliance check: the scrubbed skill_md body the synthesizer
    returned must reach ``install_skill`` unmodified (no raw PII
    re-injected by intermediate steps).

    Also pins ``transcript_sha256`` to the hash the workflow actually
    computes today — sha256 of the raw transcript bytes. If a future
    refactor adds a server-side scrubber and re-hashes, update this
    assertion in lockstep.
    """
    captured: dict = {}
    responses = _build_pii_responses(captured)
    _install_stub(monkeypatch, responses)

    intent = {
        "source_url": "https://youtu.be/gold-tier-customer",
        "tenant_id": "00000000-0000-0000-0000-000000000001",
        "actor_user_id": "luna_agent_uuid",
        "dry_run": False,
    }
    result = await env.client.execute_workflow(
        LearnFromMediaWorkflow.run,
        intent,
        id="luna-learn-pii-happy",
        task_queue="luna-learn-pii",
    )

    assert result["status"] == "success", result

    # 1) install_skill saw the scrubbed skill_md (placeholders intact,
    #    raw PII never propagated).
    inst = captured["install_skill"]
    body = inst["skill_md"]
    assert "<user-name>" in body
    assert "<address>" in body
    assert "<phone-number>" in body
    assert "<email>" in body
    # Raw PII strings must NOT appear in the installed body — if they
    # do, the pipeline leaked a name/address/phone past the LLM scrub.
    for raw in (
        "alice.smith@example.com",
        "742 Evergreen Terrace",
        "555-867-5309",
        "Alice",
    ):
        assert raw not in body, (
            f"raw PII fragment {raw!r} leaked into installed skill body — "
            "the synthesizer scrub regressed or a downstream step "
            "re-injected the raw transcript content."
        )

    # 2) transcript_sha256 matches the workflow's actual computation —
    #    hash of the raw transcript (spec gap noted in module docstring).
    expected_sha = hashlib.sha256(_RAW_TRANSCRIPT.encode()).hexdigest()
    assert inst["transcript_sha256"] == expected_sha, (
        f"transcript_sha256 drift: expected {expected_sha}, "
        f"got {inst['transcript_sha256']}"
    )

    # 3) Other provenance fields are populated.
    assert inst["source_url"] == intent["source_url"]
    assert inst["reviewer_agent_id"] == STUB_REVIEWER_AGENT_ID
    assert inst["learned_by_agent_id"] == intent["actor_user_id"]


async def test_diffuse_observation_contains_no_transcript_snippets(
    env, worker, monkeypatch
):
    """The KG observation written by ``diffuse_learning`` must surface
    capability metadata + the source URL + the skill id — but NEVER
    transcript text. Even a single PII-laden sentence leaking into
    semantic recall is a privacy regression that's hard to claw back."""
    captured: dict = {}
    responses = _build_pii_responses(captured)
    _install_stub(monkeypatch, responses)

    intent = {
        "source_url": "https://youtu.be/gold-tier-customer",
        "tenant_id": "00000000-0000-0000-0000-000000000001",
        "actor_user_id": "luna_agent_uuid",
        "dry_run": False,
    }
    await env.client.execute_workflow(
        LearnFromMediaWorkflow.run,
        intent,
        id="luna-learn-pii-diffuse",
        task_queue="luna-learn-pii",
    )

    diffuse = captured["diffuse_learning"]

    # The observation payload shape is established by T2.7; the
    # diffuse activity collects ``skill_id``, ``slug``, ``source_url``,
    # and capability metadata derived from the SKILL.md frontmatter.
    # Regardless of how the payload is structured, the raw transcript
    # MUST NOT appear anywhere in the values.
    flat = repr(diffuse)
    for raw in (
        "alice.smith@example.com",
        "742 Evergreen Terrace",
        "555-867-5309",
        "Alice",
        # A unique fragment of the prose that wouldn't appear in a
        # capability list — proves no transcript copy snuck through.
        "her account",
    ):
        assert raw not in flat, (
            f"raw transcript fragment {raw!r} found in diffuse_learning "
            f"payload — semantic recall would surface PII. Payload: {flat}"
        )

    # And positively: the source URL and skill id ARE in the payload
    # so peer agents can find the new capability.
    assert intent["source_url"] in flat
    assert "sk_pii_001" in flat or "handle-gold-tier-customer-email" in flat
