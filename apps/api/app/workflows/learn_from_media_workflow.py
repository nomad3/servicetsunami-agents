"""LearnFromMediaWorkflow — orchestrates the Luna Learn pipeline (spec §1.10).

T3.2a — happy path only: extract (or probe attachment) → transcribe →
synth → review (must be ``approved``) → test (must pass) → install →
diffuse (soft-fail handling lives in T3.2e) → notify.

The 5 error/revise/abort branches (T3.2b–T3.2f) replace the early
``return {"status": "<step>_failed", ...}`` exits with quarantine + cache
+ revise-loop dispatch. Until those land, any non-happy outcome surfaces
to the caller as a typed failure dict instead of triggering a workflow
exception (Temporal would otherwise retry indefinitely).

Note on activity args: ``workflow.execute_activity`` for typed callables
accepts at most one positional ``arg``; multi-param activities MUST be
called with ``args=[...]`` (see temporalio.workflow:2381 multi-param
overload). The plan code's positional-vararg form would crash the
workflow task and Temporal would retry it forever; we use ``args=`` here.
"""
from datetime import timedelta

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    import hashlib
    import re

    import yaml

    from app.workflows.activities import learn_from_media_activities as A


# Per-step timeouts. ``review`` is 70s = MCP-side 60s reviewer gate + 10s
# headroom so the workflow timeout doesn't race the in-shim timeout
# (which would lose the typed ``ReviewTimeout`` envelope).
_ACTIVITY_TIMEOUTS = {
    "extract": timedelta(minutes=5),
    "transcribe": timedelta(minutes=10),
    "synth": timedelta(minutes=2),
    "review": timedelta(seconds=70),
    "test": timedelta(minutes=2),
    "install": timedelta(seconds=30),
    "diffuse": timedelta(seconds=15),
    "notify": timedelta(seconds=15),
    "write": timedelta(seconds=30),
    "probe": timedelta(seconds=30),
}


def _extract_capabilities(skill_md: str) -> list[str]:
    """Pull ``auto_trigger`` + ``tags`` from frontmatter for the KG observation."""
    m = re.match(r"^---\n(.+?)\n---", skill_md, re.DOTALL)
    if not m:
        return []
    fm = yaml.safe_load(m.group(1)) or {}
    return [fm.get("auto_trigger", "").strip()] + list(fm.get("tags") or [])


def _skill_name(skill_md: str) -> str:
    m = re.match(r"^---\n(.+?)\n---", skill_md, re.DOTALL)
    fm = (yaml.safe_load(m.group(1)) if m else {}) or {}
    return fm.get("name", "<unnamed>")


@workflow.defn(name="LearnFromMediaWorkflow")
class LearnFromMediaWorkflow:
    @workflow.run
    async def run(self, intent_dict: dict) -> dict:
        intent = intent_dict  # validated upstream by LearningService (T4.1a)
        source_url = intent.get("source_url")
        attachment = intent.get("attachment_path")
        tenant_id = intent["tenant_id"]
        learned_by = intent["actor_user_id"]
        session_id = intent.get("session_id")

        # --- step 1: extract OR probe attachment ---
        if attachment:
            probe = await workflow.execute_activity(
                A.act_probe_attachment,
                attachment,
                start_to_close_timeout=_ACTIVITY_TIMEOUTS["probe"],
            )
            if not probe["ok"]:
                return {"status": "attachment_invalid", "error": probe["error"]}
            audio_path = attachment
            provenance_url = f"attachment://{attachment.split('/')[-1]}"
        else:
            extract = await workflow.execute_activity(
                A.act_extract_media,
                args=[source_url, 900],
                start_to_close_timeout=_ACTIVITY_TIMEOUTS["extract"],
            )
            if not extract["ok"]:
                # T3.2b replaces this with per-error-type notify + quarantine.
                return {"status": "extract_failed", "error": extract["error"]}
            audio_path = extract["data"]["audio_path"]
            provenance_url = source_url

        # --- step 2: transcribe (deletes audio on success per T3.1) ---
        trans = await workflow.execute_activity(
            A.act_transcribe_url,
            audio_path,
            start_to_close_timeout=_ACTIVITY_TIMEOUTS["transcribe"],
        )
        if not trans["ok"]:
            return {"status": "transcribe_failed", "error": trans["error"]}
        transcript = trans["data"]["transcript"]

        # --- step 3: synth ---
        synth = await workflow.execute_activity(
            A.act_synthesize_skill_draft,
            args=[transcript, provenance_url, []],
            start_to_close_timeout=_ACTIVITY_TIMEOUTS["synth"],
        )
        if not synth["ok"]:
            return {"status": "synth_failed", "error": synth["error"]}
        draft = synth["data"]

        # --- step 4: review (T3.2c handles revise/rejected branches) ---
        review = await workflow.execute_activity(
            A.act_dispatch_skill_review,
            args=[
                draft["skill_md"],
                transcript,
                provenance_url,
                draft["synthetic_test_input"],
                draft["synthetic_test_expected"],
            ],
            start_to_close_timeout=_ACTIVITY_TIMEOUTS["review"],
        )
        if not review["ok"]:
            return {"status": "review_failed", "error": review["error"]}
        if review["data"]["verdict"] != "approved":
            return {
                "status": review["data"]["verdict"],
                "findings": review["data"]["findings"],
            }

        # --- step 5: synthetic test ---
        test = await workflow.execute_activity(
            A.act_run_synthetic_test,
            args=[
                draft["skill_md"],
                draft["synthetic_test_input"],
                draft["synthetic_test_expected"],
            ],
            start_to_close_timeout=_ACTIVITY_TIMEOUTS["test"],
        )
        if not test["ok"] or not test["data"]["passed"]:
            return {
                "status": "test_failed",
                "error": test["data"].get("error") if test["ok"] else test["error"],
            }

        # --- step 6: install ---
        sha256 = hashlib.sha256(transcript.encode()).hexdigest()
        install = await workflow.execute_activity(
            A.act_install_skill,
            args=[
                draft["skill_md"],
                draft["slug"],
                tenant_id,
                provenance_url,
                review["data"]["reviewer_agent_id"],
                sha256,
                learned_by,
            ],
            start_to_close_timeout=_ACTIVITY_TIMEOUTS["install"],
        )
        if not install["ok"]:
            return {"status": "install_failed", "error": install["error"]}

        # --- step 7: diffuse (soft-fail handled in T3.2e) ---
        capabilities = _extract_capabilities(draft["skill_md"])
        await workflow.execute_activity(
            A.act_diffuse_learning,
            args=[install["data"]["skill_id"], provenance_url, capabilities],
            start_to_close_timeout=_ACTIVITY_TIMEOUTS["diffuse"],
        )

        # --- step 8: notify ---
        result = {
            "status": "success",
            "skill_id": install["data"]["skill_id"],
            "skill_path": install["data"]["path"],
            "skill_name": _skill_name(draft["skill_md"]),
            "capabilities": capabilities,
            "source_url": provenance_url,
        }
        if session_id:
            await workflow.execute_activity(
                A.act_notify_session,
                args=[session_id, result],
                start_to_close_timeout=_ACTIVITY_TIMEOUTS["notify"],
            )
        return result
