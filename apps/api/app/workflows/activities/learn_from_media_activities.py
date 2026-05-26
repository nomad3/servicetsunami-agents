"""Temporal activities for LearnFromMediaWorkflow (spec §1.10).

Bodies populated by T3.1; this is just the @activity.defn registration.
"""
from temporalio import activity


@activity.defn
async def act_extract_media(url: str, max_duration_s: int = 900) -> dict:
    raise NotImplementedError("T3.1")


@activity.defn
async def act_transcribe_url(audio_path: str) -> dict:
    raise NotImplementedError("T3.1")


@activity.defn
async def act_synthesize_skill_draft(transcript: str, source_url: str, hints: list[str] | None = None) -> dict:
    raise NotImplementedError("T3.1")


@activity.defn
async def act_dispatch_skill_review(*args, **kwargs) -> dict:
    raise NotImplementedError("T3.1")


@activity.defn
async def act_run_synthetic_test(*args, **kwargs) -> dict:
    raise NotImplementedError("T3.1")


@activity.defn
async def act_install_skill(*args, **kwargs) -> dict:
    raise NotImplementedError("T3.1")


@activity.defn
async def act_diffuse_learning(*args, **kwargs) -> dict:
    raise NotImplementedError("T3.1")
