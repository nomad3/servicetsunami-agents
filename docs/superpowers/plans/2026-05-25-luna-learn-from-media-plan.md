# Luna Learn from Media Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the Luna Learn meta-skill: user sends a YouTube/IG link → Luna transcribes → synthesizes a SKILL.md → cross-agent code-review → synthetic test → install into tenant library → KG diffusion. Spec: [`docs/superpowers/specs/2026-05-25-luna-learn-from-media-design.md`](../specs/2026-05-25-luna-learn-from-media-design.md).

**Architecture:** Hybrid (Approach C). 7 new MCP primitives in a `learning` tool group execute under a Temporal Dynamic Workflow `LearnFromMediaWorkflow`. Luna's chat turn dispatches the workflow + ACKs the user; workflow runs async; completion notifies back via chat. New `alpha learn` CLI + WhatsApp URL trigger as entry points. New bundled `_bundled/luna_learn_from_media/skill.md` is the orchestration template Luna reads.

**Tech Stack:** Python (`yt-dlp`, `ffmpeg`, FastAPI), Temporal workflows (existing pattern in `apps/api/app/workflows/`), PostgreSQL (existing `skill_registry` table — UNIQUE constraint already at migration 043), Rust CLI (`apps/agentprovision-cli/`), MCP server (`apps/mcp-server/`), Anthropic Claude Sonnet for synthesis.

**PR strategy:** Per `feedback_chain_pr_branches` + `feedback_single_pr_for_feature`: chained sub-branches off `spec/luna-learn-from-media`, one per phase. All squashed-merge as a single PR after final review (avoids N build storms on the single Mac runner per `feedback_single_pr_for_feature`).

---

## §0 — Resolutions to spec §7 open questions

- **LLM model tier for `synthesize_skill_draft`**: Claude Sonnet (full tier). Skill synthesis is high-stakes (we generate installable code). Configurable via env var `LUNA_LEARN_SYNTHESIS_MODEL` defaulting to `claude-sonnet-4-6`.
- **Max revise retries**: 2 per spec. Configurable via env var `LUNA_LEARN_MAX_REVISE_RETRIES` defaulting to `2`.
- **WhatsApp URL regex**: enumerated in T4.2 below. YouTube + youtu.be + IG reel/p variants.
- **UNIQUE(tenant_id, slug) constraint**: ✅ VERIFIED EXISTS — `apps/api/migrations/043_add_skill_registry.sql:19` (`uq_skill_registry_slug_tenant UNIQUE (slug, tenant_id)`). No migration needed.
- **yt-dlp + ffmpeg in code-worker image**: NO. Synthesis prompt forbids skill-embedded shellouts to these tools. Verified by T2.3 unit test (synthesis output must not reference `yt-dlp` or `ffmpeg`).

## §0b — Spec accuracy corrections (to apply during impl)

- Spec says `apps/api/app/agents/luna/AGENT.md` for Luna agent config. Actual path is `apps/api/app/agents/_bundled/luna/skill.md`. Luna's `tool_groups` lives in the DB (per `apps/api/app/models/agent.py:30`), seeded for Luna Supervisor by migration `154_expand_luna_supervisor_tool_groups.sql`. The bundled `skill.md` file may also carry `tool_groups` frontmatter (verified at `apps/api/app/agents/_bundled/code-reviewer/skill.md:9`). T5.2 adds a migration `NNN_luna_learning_tool_group.sql` AND updates the bundled `skill.md` frontmatter (mirroring `code-reviewer`'s pattern).

## §0d — mcp-server import path convention (resolved during T1.2 impl)

mcp-server tests + production code use `from src.mcp_tools import learning` (with the `src.` prefix), NOT `from mcp_tools import learning`. Several test code samples in this plan show the bare form — implementer subagents should mirror neighboring test files in `apps/mcp-server/tests/` for the actual import path.

## §0c — Conservative defaults applied (Luna's plan-review dispatch failed)

Luna's iteration-1 plan-review dispatch timed out at the Cloudflare gateway (524) twice — same failure mode as her tighter spec-review dispatch. She already co-designed every spec section in 3 prior rounds + ratified the full design. Conservative defaults applied here, documented for revisit:

- **dispatch_skill_review shape**: kept as a synchronous Temporal activity (T2.4) rather than refactored into a workflow signal Luna handles in her reasoning loop. Reason: spec §1.10 already commits to "workflow runs all 7 primitives as activities" — making one a signal would break the symmetry. If Luna later prefers signal-based, single-activity refactor.
- **Completion notification channel**: ChatMessage with `context.kind="learn_complete"` to Luna's session, picked up by existing WhatsApp message-out plumbing (matches `post_chat_memory.py` pattern at `apps/api/app/workflows/post_chat_memory.py`). If Luna prefers a separate signal channel, change is contained to T3.5.
- **Bundled skill content**: full pipeline description so Luna can mentally model the flow, with the dispatch step being the only executable action. Skill body becomes Luna's "how I learn" reference document (legible to other agents reading her toolkit).

## File structure

**New files:**

| Path | Responsibility |
|---|---|
| `apps/mcp-server/src/mcp_tools/learning.py` | 7 MCP primitive implementations |
| `apps/mcp-server/tests/test_learning.py` | Unit tests for 7 primitives |
| `apps/api/app/schemas/learning.py` | Pydantic models: `LearningIntent`, `SkillDraft`, `ReviewResult`, `TestResult`, `LearningJobState` |
| `apps/api/app/services/learning_service.py` | Service layer: dispatches workflow from CLI/WhatsApp/chat entry points; handles cache/quarantine paths |
| `apps/api/app/workflows/learn_from_media_workflow.py` | Temporal Dynamic Workflow `LearnFromMediaWorkflow` |
| `apps/api/app/workflows/activities/learn_from_media_activities.py` | Temporal activities (one per MCP primitive + cache/quarantine writes) |
| `apps/api/tests/test_luna_learn_integration.py` | End-to-end integration test |
| `apps/agentprovision-cli/src/commands/learn.rs` | `alpha learn` CLI command |
| `apps/agentprovision-cli/src/commands/learn_test.rs` | CLI unit tests |
| `apps/api/app/skills/_bundled/luna_learn_from_media/skill.md` | Orchestration template (Luna reads this) |
| `apps/api/app/services/url_intent_router.py` | URL pattern detection + intent routing helper |

**Modified files:**

| Path | What changes |
|---|---|
| `apps/api/app/services/whatsapp_service.py:_detect_inbound_media` | Add URL detection + learning-intent dispatch |
| `apps/api/app/agents/_bundled/luna/skill.md` | Add `tool_groups: [..., learning]` frontmatter |
| `apps/mcp-server/Dockerfile` | Add `yt-dlp` (pip) + `ffmpeg` (apt) |
| `apps/api/app/workflows/__init__.py` | Register `LearnFromMediaWorkflow` + activities |
| `apps/mcp-server/src/mcp_tools/__init__.py` | Register `learning` module |
| `apps/api/app/services/cron_jobs.py` (or wherever cron lives) | Add `0 4 * * *` audio cleanup sweep |

---

## Phase 0 — Prerequisites

### Task 0.1: Add yt-dlp + ffmpeg to mcp-server image

**Files:**
- Modify: `apps/mcp-server/Dockerfile`

- [ ] **Step 1: Read current Dockerfile**

Run: `cat apps/mcp-server/Dockerfile`
Expected: a `FROM python:...` Dockerfile with `pip install` and apt steps.

- [ ] **Step 2: Add ffmpeg to apt step + yt-dlp to pip step**

Edit `apps/mcp-server/Dockerfile`:
- Find the `RUN apt-get update && apt-get install -y` block; add `ffmpeg` to the package list (alphabetical order if existing).
- Find the `RUN pip install` block (or `requirements.txt`); add `yt-dlp` (pin to `>=2024.10.0` for current IG/YT extractor support).

- [ ] **Step 3: Local sanity build**

Run: `docker build -t mcp-server-test apps/mcp-server/ 2>&1 | tail -20`
Expected: build succeeds; final image contains both binaries.

- [ ] **Step 4: Verify binaries inside container**

Run: `docker run --rm mcp-server-test bash -c "which yt-dlp && which ffmpeg && yt-dlp --version && ffmpeg -version | head -1"`
Expected: both paths print, versions print without error.

- [ ] **Step 5: Commit**

```bash
git switch -c impl/luna-learn-t01-deps spec/luna-learn-from-media
git add apps/mcp-server/Dockerfile
git commit -m "deps(mcp-server): add yt-dlp + ffmpeg for Luna Learn"
git push -u origin impl/luna-learn-t01-deps
```

---

## Phase 1 — Schemas + skeletons

### Task 1.1: Pydantic schemas for the learning subsystem

**Files:**
- Create: `apps/api/app/schemas/learning.py`
- Test: `apps/api/tests/test_schema_learning.py` (new)

- [ ] **Step 1: Write failing tests**

Create `apps/api/tests/test_schema_learning.py`:
```python
import pytest
from app.schemas.learning import (
    LearningIntent, SkillDraft, ReviewVerdict, ReviewResult,
    TestResult, LearningJobState,
)

def test_learning_intent_url():
    intent = LearningIntent(source_url="https://youtu.be/abc123", tenant_id="t1", actor_user_id="u1")
    assert intent.source_url == "https://youtu.be/abc123"

def test_learning_intent_attachment():
    intent = LearningIntent(attachment_path="/tmp/x.mp4", tenant_id="t1", actor_user_id="u1")
    assert intent.attachment_path == "/tmp/x.mp4"

def test_learning_intent_requires_url_or_attachment():
    with pytest.raises(ValueError):
        LearningIntent(tenant_id="t1", actor_user_id="u1")

def test_skill_draft_has_test_payload():
    d = SkillDraft(
        skill_md="---\nname: foo\nengine: markdown\n---\nbody",
        slug="foo", engine="markdown",
        synthetic_test_input={"x": 1}, synthetic_test_expected={"y": 2},
    )
    assert d.engine == "markdown"

def test_review_verdict_values():
    assert {ReviewVerdict.APPROVED, ReviewVerdict.REVISE, ReviewVerdict.REJECTED}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps/api && pytest tests/test_schema_learning.py -v`
Expected: FAIL with "No module named 'app.schemas.learning'"

- [ ] **Step 3: Write minimal implementation**

Create `apps/api/app/schemas/learning.py`:
```python
"""Pydantic models for Luna Learn from Media subsystem."""
from __future__ import annotations
from enum import Enum
from pydantic import BaseModel, Field, model_validator


class LearningIntent(BaseModel):
    """A request to learn from media. Either source_url or attachment_path required."""
    source_url: str | None = None
    attachment_path: str | None = None
    tenant_id: str
    actor_user_id: str
    resume_job_id: str | None = None
    dry_run: bool = False

    @model_validator(mode="after")
    def _one_of_url_or_attachment(self) -> "LearningIntent":
        if not self.source_url and not self.attachment_path and not self.resume_job_id:
            raise ValueError("source_url, attachment_path, or resume_job_id required")
        return self


class SkillDraft(BaseModel):
    skill_md: str
    slug: str
    engine: str
    synthetic_test_input: dict
    synthetic_test_expected: dict


class ReviewVerdict(str, Enum):
    APPROVED = "approved"
    REVISE = "revise"
    REJECTED = "rejected"


class ReviewResult(BaseModel):
    verdict: ReviewVerdict
    findings: list[str] = Field(default_factory=list)
    reviewer_agent_id: str


class TestResult(BaseModel):
    passed: bool
    actual_output: dict | None = None
    error: str | None = None


class LearningJobState(BaseModel):
    """Persisted cache state for --resume-last."""
    job_id: str
    source_url: str | None
    transcript: str | None = None
    draft: SkillDraft | None = None
    last_review: ReviewResult | None = None
    last_test: TestResult | None = None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd apps/api && pytest tests/test_schema_learning.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git switch -c impl/luna-learn-t11-schemas impl/luna-learn-t01-deps
git add apps/api/app/schemas/learning.py apps/api/tests/test_schema_learning.py
git commit -m "feat(luna-learn): pydantic schemas for learning subsystem"
git push -u origin impl/luna-learn-t11-schemas
```

### Task 1.2a: MCP-server HTTP shim — typed-exception → status-code mapping

**Files:**
- Modify: `apps/mcp-server/src/mcp_tools/__init__.py` (or wherever the tool-dispatch HTTP entrypoint lives)
- Test: `apps/mcp-server/tests/test_learning_http_shim.py` (new)

**Why this exists** (review NEW-IMPORTANT-1): Tools in `learning.py` raise Python exceptions (`ReviewerNotProvisioned`, `ReviewTimeout`, `MediaPrivate`, `MediaNotFound`, `MediaGeoBlocked`, `MediaAntiScrape`, `MediaTooLong`, `DraftInvalid`, `DraftForbiddenShellout`, `SlugExhausted`). The Temporal activities in T3.1 call these tools over HTTP and need to translate exceptions into status codes so the `_STATUS_TO_TYPE` map in T3.1 can branch. Without this shim, every typed exception becomes a generic 500 and T3.2c's distinct cache-vs-quarantine branching collapses.

**Contract** — when `POST /tools/<tool_name>` raises one of these exceptions, the shim returns the indicated status + a JSON body `{"error_type": "<ClassName>", "message": "<exception message>"}`:

| Exception class | HTTP status |
|---|---|
| `MediaTooLong` | 413 |
| `MediaPrivate` | 451 |
| `MediaNotFound` | 404 |
| `MediaGeoBlocked` | 403 |
| `MediaAntiScrape` | 429 |
| `DraftInvalid` | 422 |
| `DraftForbiddenShellout` | 424 |
| `ReviewerNotProvisioned` | 503 |
| `ReviewTimeout` | 504 |
| `SlugExhausted` | 409 |
| Any other Exception | 500 + `error_type: "UnknownError"` |

**Note on the chosen codes**: 451 (RFC 7725 "Unavailable For Legal Reasons") and 424 (WebDAV "Failed Dependency") are repurposed for internal use. The status codes are advisory; the `error_type` field in the response body is authoritative for branching. The Temporal activities' `_STATUS_TO_TYPE` map in T3.1 is just a fast-path lookup — if the body has `error_type`, it overrides the map.

- [ ] **Step 1: Write failing tests** for each exception → status mapping

```python
# apps/mcp-server/tests/test_learning_http_shim.py
import pytest
from fastapi.testclient import TestClient
from mcp_server.http_app import app  # whatever the FastAPI/Starlette app is
from unittest.mock import patch

client = TestClient(app)

@pytest.mark.parametrize("exc_name,status", [
    ("MediaTooLong", 413), ("MediaPrivate", 451), ("MediaNotFound", 404),
    ("MediaGeoBlocked", 403), ("MediaAntiScrape", 429),
    ("DraftInvalid", 422), ("DraftForbiddenShellout", 424),
    ("ReviewerNotProvisioned", 503), ("ReviewTimeout", 504),
    ("SlugExhausted", 409),
])
def test_typed_exception_maps_to_status(exc_name, status):
    from mcp_tools import learning as L
    exc_cls = getattr(L, exc_name)
    with patch.object(L, "extract_media", side_effect=exc_cls("boom")):
        # tool name doesn't matter — using extract_media as a dispatch target
        r = client.post("/tools/extract_media", json={"url": "x"},
                        headers={"X-Internal-Key": "test-key"})
        assert r.status_code == status
        assert r.json()["error_type"] == exc_name
        assert "boom" in r.json()["message"]
```

- [ ] **Step 2-4: Implement + test passing**

```python
# In whichever module hosts the tool-dispatch HTTP entrypoint
from mcp_tools.learning import (
    MediaTooLong, MediaPrivate, MediaNotFound, MediaGeoBlocked, MediaAntiScrape,
    DraftInvalid, DraftForbiddenShellout, ReviewerNotProvisioned, ReviewTimeout,
    SlugExhausted,
)

_EXC_STATUS = {
    MediaTooLong: 413, MediaPrivate: 451, MediaNotFound: 404,
    MediaGeoBlocked: 403, MediaAntiScrape: 429,
    DraftInvalid: 422, DraftForbiddenShellout: 424,
    ReviewerNotProvisioned: 503, ReviewTimeout: 504,
    SlugExhausted: 409,
}


async def _dispatch_tool(name: str, payload: dict):
    tool = TOOLS[name]
    try:
        return await tool(**payload)
    except tuple(_EXC_STATUS.keys()) as e:
        status = _EXC_STATUS[type(e)]
        raise HTTPException(status_code=status, detail={
            "error_type": type(e).__name__, "message": str(e),
        })
    except Exception as e:
        raise HTTPException(status_code=500, detail={
            "error_type": "UnknownError", "message": str(e),
        })
```

- [ ] **Step 5: Commit**

```bash
git switch -c impl/luna-learn-t12a-http-shim impl/luna-learn-t11-schemas
git commit -m "feat(luna-learn): MCP HTTP shim — typed-exception → status-code mapping"
git push -u origin impl/luna-learn-t12a-http-shim
```

### Task 1.2: MCP tool group skeleton

**Files:**
- Create: `apps/mcp-server/src/mcp_tools/learning.py`
- Modify: `apps/mcp-server/src/mcp_tools/__init__.py`
- Test: `apps/mcp-server/tests/test_learning.py` (new)

- [ ] **Step 1: Write failing test for tool registration**

Create `apps/mcp-server/tests/test_learning.py`:
```python
import pytest
from mcp_tools import learning

def test_learning_module_exports_7_tools():
    expected = {
        "extract_media", "transcribe_url", "synthesize_skill_draft",
        "dispatch_skill_review", "run_synthetic_test", "install_skill",
        "diffuse_learning",
    }
    assert set(learning.TOOLS.keys()) == expected

@pytest.mark.parametrize("tool", [
    "extract_media", "transcribe_url", "synthesize_skill_draft",
    "dispatch_skill_review", "run_synthetic_test", "install_skill",
    "diffuse_learning",
])
def test_each_tool_callable(tool):
    assert callable(learning.TOOLS[tool])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps/mcp-server && pytest tests/test_learning.py -v`
Expected: FAIL with "No module named 'mcp_tools.learning'"

- [ ] **Step 3: Write minimal skeleton**

Create `apps/mcp-server/src/mcp_tools/learning.py`:
```python
"""Luna Learn — MCP primitives for the meta-skill (spec §1.1)."""
from __future__ import annotations
from typing import Callable


async def extract_media(url: str, max_duration_s: int = 900) -> dict:
    raise NotImplementedError("T2.1")


async def transcribe_url(audio_path: str) -> dict:
    raise NotImplementedError("T2.2")


async def synthesize_skill_draft(transcript: str, source_url: str, hints: list[str] | None = None) -> dict:
    raise NotImplementedError("T2.3")


async def dispatch_skill_review(
    skill_md: str, transcript: str, source_url: str,
    synthetic_test_input: dict, synthetic_test_expected: dict,
) -> dict:
    raise NotImplementedError("T2.4")


async def run_synthetic_test(skill_md: str, test_input: dict, test_expected: dict) -> dict:
    raise NotImplementedError("T2.5")


async def install_skill(
    skill_md: str, slug: str, tenant_id: str,
    source_url: str, reviewer_agent_id: str,
    transcript_sha256: str, learned_by_agent_id: str,
) -> dict:
    raise NotImplementedError("T2.6")


async def diffuse_learning(skill_id: str, source_url: str, capabilities: list[str]) -> dict:
    raise NotImplementedError("T2.7")


TOOLS: dict[str, Callable] = {
    "extract_media": extract_media,
    "transcribe_url": transcribe_url,
    "synthesize_skill_draft": synthesize_skill_draft,
    "dispatch_skill_review": dispatch_skill_review,
    "run_synthetic_test": run_synthetic_test,
    "install_skill": install_skill,
    "diffuse_learning": diffuse_learning,
}
```

Update `apps/mcp-server/src/mcp_tools/__init__.py` to import `learning` (follow the existing pattern of how `skills`/`knowledge` are imported).

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd apps/mcp-server && pytest tests/test_learning.py -v`
Expected: 8 passed (1 export check + 7 callable checks).

- [ ] **Step 5: Commit**

```bash
git switch -c impl/luna-learn-t12-skeleton impl/luna-learn-t11-schemas
git add apps/mcp-server/src/mcp_tools/learning.py apps/mcp-server/src/mcp_tools/__init__.py apps/mcp-server/tests/test_learning.py
git commit -m "feat(luna-learn): MCP tool group skeleton (7 NotImplementedError stubs)"
git push -u origin impl/luna-learn-t12-skeleton
```

### Task 1.3: Temporal workflow skeleton

**Files:**
- Create: `apps/api/app/workflows/learn_from_media_workflow.py`
- Create: `apps/api/app/workflows/activities/learn_from_media_activities.py`
- Modify: `apps/api/app/workflows/__init__.py`

- [ ] **Step 1: Write failing test**

Create `apps/api/tests/test_learn_from_media_workflow_skeleton.py`:
```python
def test_workflow_registered():
    from app.workflows import learn_from_media_workflow as w
    assert hasattr(w, "LearnFromMediaWorkflow")

def test_activities_registered():
    from app.workflows.activities import learn_from_media_activities as a
    expected = {
        "act_extract_media", "act_transcribe_url",
        "act_synthesize_skill_draft", "act_dispatch_skill_review",
        "act_run_synthetic_test", "act_install_skill",
        "act_diffuse_learning",
    }
    actual = {n for n in dir(a) if n.startswith("act_")}
    assert expected.issubset(actual)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps/api && pytest tests/test_learn_from_media_workflow_skeleton.py -v`
Expected: FAIL with import errors.

- [ ] **Step 3: Create skeletons matching existing Temporal pattern**

Reference an existing workflow (e.g. `apps/api/app/workflows/coalition_workflow.py`) for the import shape + `@workflow.defn` + `@activity.defn` decorators.

Create `apps/api/app/workflows/activities/learn_from_media_activities.py`:
```python
"""Temporal activities for LearnFromMediaWorkflow (spec §1.10)."""
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
```

Create `apps/api/app/workflows/learn_from_media_workflow.py`:
```python
"""LearnFromMediaWorkflow — orchestrates the Luna Learn pipeline (spec §1.10)."""
from datetime import timedelta
from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from app.workflows.activities import learn_from_media_activities as A


@workflow.defn(name="LearnFromMediaWorkflow")
class LearnFromMediaWorkflow:
    @workflow.run
    async def run(self, intent_dict: dict) -> dict:
        # T3.2 implements the actual orchestration body.
        raise NotImplementedError("T3.2")
```

Register in `apps/api/app/workflows/__init__.py` (follow existing pattern).

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd apps/api && pytest tests/test_learn_from_media_workflow_skeleton.py -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git switch -c impl/luna-learn-t13-workflow-skeleton impl/luna-learn-t12-skeleton
git add apps/api/app/workflows/learn_from_media_workflow.py apps/api/app/workflows/activities/learn_from_media_activities.py apps/api/app/workflows/__init__.py apps/api/tests/test_learn_from_media_workflow_skeleton.py
git commit -m "feat(luna-learn): Temporal workflow + activities skeleton"
git push -u origin impl/luna-learn-t13-workflow-skeleton
```

---

## Phase 2 — MCP primitive implementations (one task per primitive, TDD)

> Each Phase-2 task branches off the previous (impl/luna-learn-t12-skeleton → t21 → t22 → ...). Final squash-merge after Phase 7 review.

### Task 2.1: `extract_media` — yt-dlp wrapper

**Files:**
- Modify: `apps/mcp-server/src/mcp_tools/learning.py`
- Test: `apps/mcp-server/tests/test_learning.py`

**Behavior contract (from spec §1.1 + §3):**
- Calls `yt-dlp -x --audio-format m4a -o <path> <url>` via subprocess
- Rejects upfront if probed duration > `max_duration_s`
- Maps yt-dlp errors to typed exceptions: `MediaPrivate`, `MediaNotFound`, `MediaGeoBlocked`, `MediaAntiScrape`, `MediaTooLong`, `MediaUnknownError`
- Writes audio to `/var/agentprovision/workspaces/_learning/<job_id>.audio`
- Returns `{audio_path, metadata: {title, duration_s, uploader, source_platform}}`

- [ ] **Step 1: Write failing tests** (cover happy path + each typed exception)

```python
# In apps/mcp-server/tests/test_learning.py, append:
import pytest
from unittest.mock import patch, MagicMock
from mcp_tools.learning import (
    extract_media, MediaPrivate, MediaNotFound, MediaGeoBlocked,
    MediaAntiScrape, MediaTooLong,
)

@pytest.mark.asyncio
async def test_extract_media_happy_path(tmp_path):
    with patch("mcp_tools.learning._run_yt_dlp") as mock_run:
        mock_run.return_value = {
            "title": "Demo", "duration": 90,
            "uploader": "Acme", "extractor": "youtube",
            "_filename": str(tmp_path / "abc.m4a"),
        }
        result = await extract_media("https://youtu.be/abc123")
        assert result["metadata"]["duration_s"] == 90
        assert result["metadata"]["source_platform"] == "youtube"

@pytest.mark.asyncio
async def test_extract_media_too_long():
    with patch("mcp_tools.learning._probe_duration") as p:
        p.return_value = 1200  # 20 min > 900
        with pytest.raises(MediaTooLong):
            await extract_media("https://youtu.be/abc123", max_duration_s=900)

@pytest.mark.asyncio
@pytest.mark.parametrize("stderr,exc", [
    ("Private video", MediaPrivate),
    ("Video unavailable", MediaNotFound),
    ("This video is not available in your country", MediaGeoBlocked),
    ("HTTP Error 429", MediaAntiScrape),
])
async def test_extract_media_error_mapping(stderr, exc):
    with patch("mcp_tools.learning._run_yt_dlp") as r:
        r.side_effect = RuntimeError(stderr)
        with pytest.raises(exc):
            await extract_media("https://example.com/x")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps/mcp-server && pytest tests/test_learning.py -v -k extract_media`
Expected: FAIL with import / NotImplementedError errors.

- [ ] **Step 3: Implement `extract_media` with helpers `_run_yt_dlp` + `_probe_duration` + typed exceptions**

In `apps/mcp-server/src/mcp_tools/learning.py`:
```python
import asyncio
import os
import shutil
import uuid
from pathlib import Path

_LEARNING_DIR = Path("/var/agentprovision/workspaces/_learning")


class MediaError(Exception): ...
class MediaPrivate(MediaError): ...
class MediaNotFound(MediaError): ...
class MediaGeoBlocked(MediaError): ...
class MediaAntiScrape(MediaError): ...
class MediaTooLong(MediaError): ...


async def _probe_duration(url: str) -> int:
    """yt-dlp --get-duration <url> → seconds."""
    proc = await asyncio.create_subprocess_exec(
        "yt-dlp", "--get-duration", url,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(err.decode() or "yt-dlp probe failed")
    # Format: "HH:MM:SS" or "MM:SS"; convert to seconds.
    parts = out.decode().strip().split(":")
    secs = 0
    for p in parts:
        secs = secs * 60 + int(p)
    return secs


async def _run_yt_dlp(url: str, output_path: str) -> dict:
    proc = await asyncio.create_subprocess_exec(
        "yt-dlp", "-x", "--audio-format", "m4a",
        "-o", output_path, "--print-json", url,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(err.decode() or "yt-dlp failed")
    import json
    return json.loads(out.decode().splitlines()[-1])


def _map_ytdlp_error(stderr: str) -> type[MediaError]:
    s = stderr.lower()
    if "private" in s or "sign in" in s or "age" in s:
        return MediaPrivate
    if "unavailable" in s or "removed" in s or "404" in s:
        return MediaNotFound
    if "not available in your country" in s or "geo" in s:
        return MediaGeoBlocked
    if "429" in s or "rate" in s or "blocked" in s:
        return MediaAntiScrape
    return MediaError


async def extract_media(url: str, max_duration_s: int = 900) -> dict:
    """Spec §1.1 extract_media."""
    _LEARNING_DIR.mkdir(parents=True, exist_ok=True)
    job_id = uuid.uuid4().hex
    output_path = str(_LEARNING_DIR / f"{job_id}.%(ext)s")
    try:
        dur = await _probe_duration(url)
    except RuntimeError as e:
        raise _map_ytdlp_error(str(e))(str(e)) from e
    if dur > max_duration_s:
        raise MediaTooLong(f"duration {dur}s exceeds cap {max_duration_s}s")
    try:
        meta = await _run_yt_dlp(url, output_path)
    except RuntimeError as e:
        raise _map_ytdlp_error(str(e))(str(e)) from e
    return {
        "audio_path": meta.get("_filename") or str(_LEARNING_DIR / f"{job_id}.m4a"),
        "metadata": {
            "title": meta.get("title"),
            "duration_s": meta.get("duration"),
            "uploader": meta.get("uploader"),
            "source_platform": meta.get("extractor"),
        },
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd apps/mcp-server && pytest tests/test_learning.py -v -k extract_media`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git switch -c impl/luna-learn-t21-extract-media impl/luna-learn-t13-workflow-skeleton
git add apps/mcp-server/src/mcp_tools/learning.py apps/mcp-server/tests/test_learning.py
git commit -m "feat(luna-learn): extract_media — yt-dlp wrapper with typed errors + duration cap"
git push -u origin impl/luna-learn-t21-extract-media
```

### Task 2.2: `transcribe_url` — wrap existing transcription client

**Files:**
- Modify: `apps/mcp-server/src/mcp_tools/learning.py`
- Test: `apps/mcp-server/tests/test_learning.py`

- [ ] **Step 1: Write failing tests**

```python
@pytest.mark.asyncio
async def test_transcribe_url_calls_existing_client(tmp_path):
    audio = tmp_path / "x.m4a"; audio.write_bytes(b"\x00" * 100)
    with patch("mcp_tools.learning._transcribe_bytes_async") as t:
        t.return_value = {"transcript": "hello", "duration_ms": 1500, "engine": "whisper"}
        result = await transcribe_url(str(audio))
        assert result["transcript"] == "hello"
        assert result["engine"] == "whisper"

@pytest.mark.asyncio
async def test_transcribe_url_missing_file():
    with pytest.raises(FileNotFoundError):
        await transcribe_url("/nonexistent/path.m4a")
```

- [ ] **Step 2: Run → fail**

Run: `cd apps/mcp-server && pytest tests/test_learning.py -v -k transcribe_url`
Expected: FAIL.

- [ ] **Step 3: Implement** (calls existing `transcription_client` over the internal API)

In `apps/mcp-server/src/mcp_tools/learning.py` add:
```python
import httpx
import os

_API_BASE = os.environ.get("AGENTPROVISION_API_BASE", "http://api:8000")


async def _transcribe_bytes_async(audio_bytes: bytes) -> dict:
    """Hits the existing transcription endpoint."""
    async with httpx.AsyncClient(timeout=60.0) as client:
        files = {"file": ("audio.m4a", audio_bytes, "audio/mp4")}
        r = await client.post(
            f"{_API_BASE}/api/v1/media/transcribe",
            files=files,
            headers={"X-Internal-Key": os.environ["MCP_API_KEY"]},
        )
        r.raise_for_status()
        return r.json()


async def transcribe_url(audio_path: str) -> dict:
    """Spec §1.1 transcribe_url. Wraps existing transcription_client."""
    p = Path(audio_path)
    if not p.exists():
        raise FileNotFoundError(audio_path)
    return await _transcribe_bytes_async(p.read_bytes())
```

- [ ] **Step 4: Run → pass**

Run: `cd apps/mcp-server && pytest tests/test_learning.py -v -k transcribe_url`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git switch -c impl/luna-learn-t22-transcribe impl/luna-learn-t21-extract-media
git add apps/mcp-server/src/mcp_tools/learning.py apps/mcp-server/tests/test_learning.py
git commit -m "feat(luna-learn): transcribe_url — wraps existing transcription endpoint"
git push -u origin impl/luna-learn-t22-transcribe
```

### Task 2.3: `synthesize_skill_draft` — LLM call with engine selection + PII scrub

**Files:**
- Modify: `apps/mcp-server/src/mcp_tools/learning.py`
- Create: `apps/mcp-server/src/mcp_tools/learning_prompts.py` (separate so prompt is reviewable + golden-testable)
- Test: `apps/mcp-server/tests/test_learning.py`

**Behavior contract:**
- Single Claude Sonnet (`claude-sonnet-4-6`) call. Model id via `LUNA_LEARN_SYNTHESIS_MODEL` env var.
- Prompt embeds: §1.5 engine selection rubric, §1.6 frontmatter schema, PII-scrub instruction, synthetic-test generation requirement, FORBIDDEN: `yt-dlp`/`ffmpeg` shellouts inside python-engine skills.
- Validates output via existing `_validate_skill_payload` from `apps/api/app/api/v1/skills_new.py:162`. On parse fail → raises `DraftInvalid` so the workflow can treat it as a `revise` cycle.
- Generates kebab-case slug from skill name.

- [ ] **Step 1: Write failing tests** (mock LLM client)

```python
@pytest.mark.asyncio
async def test_synthesize_returns_valid_draft():
    with patch("mcp_tools.learning._llm_synthesize") as llm:
        llm.return_value = (
            "---\nname: Fix Printer Error 41\nengine: markdown\n"
            "category: support\ntags: [printer]\n"
            "auto_trigger: \"Fix printer error 41\"\n"
            "inputs: []\n---\nUnplug the printer and ..."
        ), {"input": {"code": 41}, "expected": {"resolved": True}}
        result = await synthesize_skill_draft("transcript text", "https://x.com/v")
        assert result["engine"] == "markdown"
        assert result["slug"] == "fix-printer-error-41"

@pytest.mark.asyncio
async def test_synthesize_parses_invalid_draft_raises():
    from mcp_tools.learning import DraftInvalid
    with patch("mcp_tools.learning._llm_synthesize") as llm:
        llm.return_value = "not valid yaml at all", {}
        with pytest.raises(DraftInvalid):
            await synthesize_skill_draft("t", "u")

@pytest.mark.asyncio
async def test_synthesize_emits_python_when_clearly_deterministic():
    with patch("mcp_tools.learning._llm_synthesize") as llm:
        llm.return_value = (
            "---\nname: Mod-7 Compute\nengine: python\nscript: compute.py\n"
            "category: data\ntags: []\nauto_trigger: \"Compute mod-7\"\n"
            "inputs:\n  - name: x\n    type: number\n    description: input\n    required: true\n---\n",
            {"input": {"x": 14}, "expected": {"y": 0}},
        )
        result = await synthesize_skill_draft("given x compute x mod 7", "u")
        assert result["engine"] == "python"

@pytest.mark.asyncio
async def test_synthesize_forbids_ytdlp_in_python_draft():
    from mcp_tools.learning import DraftForbiddenShellout
    with patch("mcp_tools.learning._llm_synthesize") as llm:
        llm.return_value = (
            "---\nname: bad\nengine: python\nscript: bad.py\n---\n"
            "import subprocess; subprocess.run(['yt-dlp', '...'])",
            {},
        )
        with pytest.raises(DraftForbiddenShellout):
            await synthesize_skill_draft("t", "u")
```

- [ ] **Step 2: Run → fail**

Run: `cd apps/mcp-server && pytest tests/test_learning.py -v -k synthesize`
Expected: FAIL.

- [ ] **Step 3: Implement**

Create `apps/mcp-server/src/mcp_tools/learning_prompts.py`:
```python
"""Prompt templates for Luna Learn synthesis (spec §1.5)."""

SYNTHESIS_SYSTEM = """You are synthesizing a SKILL.md from a video transcript.
RUBRIC (engine selection):
- Default to `engine: markdown` — a prompt template that another agent reads as instructions.
- Emit `engine: python` ONLY when ALL of: (a) deterministic transformation/computation with
  clear inputs/outputs, (b) not non-trivially expressible as markdown, (c) no external
  API/network calls implied. When ambiguous → markdown.
- FORBIDDEN in python skills: any subprocess call to `yt-dlp`, `ffmpeg`, `curl`, `wget`, or
  similar binaries. External calls go through MCP tools, not skill-embedded shellouts.

PII SCRUB: scrub personal names, addresses, phone numbers, emails, account/credential strings
from the body. Replace with placeholders like `<user-name>`, `<address>`.

OUTPUT a JSON object with two keys:
  skill_md: full SKILL.md content (frontmatter + body)
  synthetic_test: {"input": {...}, "expected": {...}}
The synthetic test MUST be a substantive validation of the skill's behavior — a reviewer will
verify it isn't a tautology.

FRONTMATTER fields: name, engine, category, tags, auto_trigger, inputs (per existing schema).
"""

SYNTHESIS_USER = """Transcript:
{transcript}

Source URL: {source_url}

{hints_block}

Synthesize the SKILL.md per the system rubric."""
```

In `apps/mcp-server/src/mcp_tools/learning.py` add:
```python
import re
import json
from .learning_prompts import SYNTHESIS_SYSTEM, SYNTHESIS_USER


class DraftInvalid(Exception): ...
class DraftForbiddenShellout(Exception): ...


_FORBIDDEN_PATTERNS = [
    r"\byt[-_]?dlp\b", r"\bffmpeg\b", r"\bffprobe\b",
    r"subprocess\.run.*\b(curl|wget)\b",
]


def _slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s[:60] or "learned-skill"


async def _llm_synthesize(transcript: str, source_url: str, hints: list[str]) -> tuple[str, dict]:
    """Anthropic call returning (skill_md, synthetic_test_dict)."""
    import anthropic
    model = os.environ.get("LUNA_LEARN_SYNTHESIS_MODEL", "claude-sonnet-4-6")
    hints_block = ("Reviewer feedback to address:\n" + "\n".join(f"- {h}" for h in hints)) if hints else ""
    client = anthropic.AsyncAnthropic()
    resp = await client.messages.create(
        model=model, max_tokens=4096,
        system=SYNTHESIS_SYSTEM,
        messages=[{"role": "user", "content": SYNTHESIS_USER.format(
            transcript=transcript, source_url=source_url, hints_block=hints_block)}],
    )
    payload = json.loads(resp.content[0].text)
    return payload["skill_md"], payload["synthetic_test"]


async def synthesize_skill_draft(transcript: str, source_url: str, hints: list[str] | None = None) -> dict:
    skill_md, test = await _llm_synthesize(transcript, source_url, hints or [])
    # Parse frontmatter for name + engine.
    fm_match = re.match(r"^---\n(.+?)\n---", skill_md, re.DOTALL)
    if not fm_match:
        raise DraftInvalid("missing frontmatter")
    import yaml
    try:
        fm = yaml.safe_load(fm_match.group(1))
    except yaml.YAMLError as e:
        raise DraftInvalid(f"YAML parse: {e}") from e
    if "name" not in fm or "engine" not in fm:
        raise DraftInvalid("frontmatter missing name or engine")
    # Forbid yt-dlp/ffmpeg shellouts in python drafts.
    if fm["engine"] == "python":
        body = skill_md[fm_match.end():]
        for pat in _FORBIDDEN_PATTERNS:
            if re.search(pat, body, re.IGNORECASE):
                raise DraftForbiddenShellout(f"forbidden shellout: {pat}")
    return {
        "skill_md": skill_md,
        "slug": _slugify(fm["name"]),
        "engine": fm["engine"],
        "synthetic_test_input": test.get("input", {}),
        "synthetic_test_expected": test.get("expected", {}),
    }
```

- [ ] **Step 4: Run → pass**

Run: `cd apps/mcp-server && pytest tests/test_learning.py -v -k synthesize`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git switch -c impl/luna-learn-t23-synthesize impl/luna-learn-t22-transcribe
git add apps/mcp-server/src/mcp_tools/learning.py apps/mcp-server/src/mcp_tools/learning_prompts.py apps/mcp-server/tests/test_learning.py
git commit -m "feat(luna-learn): synthesize_skill_draft — LLM synthesis with engine selection + PII scrub + shellout ban"
git push -u origin impl/luna-learn-t23-synthesize
```

### Task 2.4: `dispatch_skill_review` — Code Reviewer agent dispatch

**Files:**
- Modify: `apps/mcp-server/src/mcp_tools/learning.py`
- Test: `apps/mcp-server/tests/test_learning.py`

**Behavior contract:**
- Calls internal `POST /api/v1/agents/dispatch` (or whatever existing endpoint dispatches an agent) with target `agent_id=755796a4-4cc4-4d1c-99e5-dd9c4f7d0f22` and a structured review payload including the synthetic test.
- 60-second timeout.
- Returns typed `ReviewerNotProvisioned` on registry 404 (so workflow can route to cache+notify per §3).

- [ ] **Step 1: Write failing tests**

```python
@pytest.mark.asyncio
async def test_dispatch_review_approved():
    with patch("mcp_tools.learning._dispatch_agent") as d:
        d.return_value = {"verdict": "approved", "findings": []}
        r = await dispatch_skill_review("md", "t", "u", {}, {})
        assert r["verdict"] == "approved"

@pytest.mark.asyncio
async def test_dispatch_review_reviewer_not_provisioned():
    from mcp_tools.learning import ReviewerNotProvisioned
    with patch("mcp_tools.learning._dispatch_agent") as d:
        d.side_effect = httpx.HTTPStatusError("404", request=MagicMock(), response=MagicMock(status_code=404))
        with pytest.raises(ReviewerNotProvisioned):
            await dispatch_skill_review("md", "t", "u", {}, {})

@pytest.mark.asyncio
async def test_dispatch_review_timeout():
    with patch("mcp_tools.learning._dispatch_agent") as d:
        d.side_effect = asyncio.TimeoutError()
        from mcp_tools.learning import ReviewTimeout
        with pytest.raises(ReviewTimeout):
            await dispatch_skill_review("md", "t", "u", {}, {})
```

- [ ] **Step 2: Run → fail**

Run: `cd apps/mcp-server && pytest tests/test_learning.py -v -k dispatch_review`
Expected: FAIL.

- [ ] **Step 3: Implement**

```python
CODE_REVIEWER_AGENT_ID = "755796a4-4cc4-4d1c-99e5-dd9c4f7d0f22"
REVIEW_TIMEOUT_S = 60


class ReviewerNotProvisioned(Exception): ...
class ReviewTimeout(Exception): ...


async def _dispatch_agent(agent_id: str, payload: dict) -> dict:
    async with httpx.AsyncClient(timeout=REVIEW_TIMEOUT_S) as client:
        r = await client.post(
            f"{_API_BASE}/api/v1/agents/{agent_id}/dispatch",
            json=payload,
            headers={"X-Internal-Key": os.environ["MCP_API_KEY"]},
        )
        r.raise_for_status()
        return r.json()


async def dispatch_skill_review(
    skill_md: str, transcript: str, source_url: str,
    synthetic_test_input: dict, synthetic_test_expected: dict,
) -> dict:
    payload = {
        "task": "review_synthesized_skill",
        "skill_md": skill_md,
        "transcript": transcript,
        "source_url": source_url,
        "synthetic_test": {
            "input": synthetic_test_input,
            "expected": synthetic_test_expected,
        },
    }
    try:
        result = await asyncio.wait_for(
            _dispatch_agent(CODE_REVIEWER_AGENT_ID, payload),
            timeout=REVIEW_TIMEOUT_S,
        )
    except asyncio.TimeoutError as e:
        raise ReviewTimeout() from e
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            raise ReviewerNotProvisioned() from e
        raise
    return {
        "verdict": result.get("verdict", "revise"),
        "findings": result.get("findings", []),
        "reviewer_agent_id": CODE_REVIEWER_AGENT_ID,
    }
```

- [ ] **Step 4: Run → pass**

Run: `cd apps/mcp-server && pytest tests/test_learning.py -v -k dispatch_review`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git switch -c impl/luna-learn-t24-review impl/luna-learn-t23-synthesize
git add apps/mcp-server/src/mcp_tools/learning.py apps/mcp-server/tests/test_learning.py
git commit -m "feat(luna-learn): dispatch_skill_review — Code Reviewer agent dispatch with typed errors"
git push -u origin impl/luna-learn-t24-review
```

### Task 2.5: `run_synthetic_test` — execute skill against synthetic input

**Files:**
- Modify: `apps/mcp-server/src/mcp_tools/learning.py`
- Test: `apps/mcp-server/tests/test_learning.py`

**Behavior contract:**
- Writes draft `skill_md` to a temp dir
- Dispatches execution to code-worker via internal `POST /api/v1/skills/execute-draft` (NEW endpoint — see T2.5b below)
- Compares `actual_output` (subset match) against `test_expected`
- Returns `{passed, actual_output, error?}`

- [ ] **Step 1: Add `POST /api/v1/skills/execute-draft` internal endpoint** (T2.5b)

Modify `apps/api/app/api/v1/skills_new.py`: add an internal endpoint accepting a temporary skill_md + input, executes against the existing skill-execution path (in-process or via code-worker dispatch), returns output. Internal-key gated only.

- [ ] **Step 2: Write failing tests for `run_synthetic_test`**

```python
@pytest.mark.asyncio
async def test_run_synthetic_test_pass():
    with patch("mcp_tools.learning._execute_draft") as e:
        e.return_value = {"resolved": True, "extra": 1}
        r = await run_synthetic_test("md", {"code": 41}, {"resolved": True})
        assert r["passed"] is True

@pytest.mark.asyncio
async def test_run_synthetic_test_fail_value_mismatch():
    with patch("mcp_tools.learning._execute_draft") as e:
        e.return_value = {"resolved": False}
        r = await run_synthetic_test("md", {"code": 41}, {"resolved": True})
        assert r["passed"] is False
        assert "resolved" in r["actual_output"]

@pytest.mark.asyncio
async def test_run_synthetic_test_execution_error():
    with patch("mcp_tools.learning._execute_draft") as e:
        e.side_effect = RuntimeError("syntax error")
        r = await run_synthetic_test("md", {}, {})
        assert r["passed"] is False
        assert "syntax error" in r["error"]
```

- [ ] **Step 3: Run → fail**

Run: `cd apps/mcp-server && pytest tests/test_learning.py -v -k synthetic_test`
Expected: FAIL.

- [ ] **Step 4: Implement + run → pass**

```python
async def _execute_draft(skill_md: str, inputs: dict) -> dict:
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post(
            f"{_API_BASE}/api/v1/skills/execute-draft",
            json={"skill_md": skill_md, "inputs": inputs},
            headers={"X-Internal-Key": os.environ["MCP_API_KEY"]},
        )
        r.raise_for_status()
        return r.json()


def _subset_match(actual: dict, expected: dict) -> bool:
    return all(actual.get(k) == v for k, v in expected.items())


async def run_synthetic_test(skill_md: str, test_input: dict, test_expected: dict) -> dict:
    try:
        actual = await _execute_draft(skill_md, test_input)
    except Exception as e:
        return {"passed": False, "actual_output": None, "error": str(e)}
    return {
        "passed": _subset_match(actual, test_expected),
        "actual_output": actual,
        "error": None,
    }
```

Run: `cd apps/mcp-server && pytest tests/test_learning.py -v -k synthetic_test`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git switch -c impl/luna-learn-t25-test impl/luna-learn-t24-review
git add apps/mcp-server/src/mcp_tools/learning.py apps/mcp-server/tests/test_learning.py apps/api/app/api/v1/skills_new.py
git commit -m "feat(luna-learn): run_synthetic_test + internal /skills/execute-draft endpoint"
git push -u origin impl/luna-learn-t25-test
```

### Task 2.6: `install_skill` — provenance frontmatter + slug serialization + audit

**Files:**
- Modify: `apps/mcp-server/src/mcp_tools/learning.py`
- Test: `apps/mcp-server/tests/test_learning.py`

**Behavior contract:**
- Injects `provenance:` block per spec §1.6
- Writes to `_tenant/<uuid>/<slug>/skill.md` (NEVER `_bundled/`)
- DB insert with `ON CONFLICT (slug, tenant_id) DO NOTHING` retry up to `-v5`
- `library_revisions` row with `actor=learned_by_agent_id, reason=f"learned from {source_url}"`
- All in a single transaction; rollback on FS failure

- [ ] **Step 1: Write failing tests**

```python
@pytest.mark.asyncio
async def test_install_skill_injects_provenance(tmp_path):
    md_in = "---\nname: Test\nengine: markdown\n---\nbody"
    with patch("mcp_tools.learning._install_via_api") as ins:
        ins.return_value = {"skill_id": "s1", "path": str(tmp_path / "skill.md")}
        r = await install_skill(
            md_in, "test", "tenant1",
            source_url="https://x.com/v",
            reviewer_agent_id="755796a4-...",
            transcript_sha256="abc" * 21 + "abc",
            learned_by_agent_id="cfb6dd14-...",
        )
        sent_md = ins.call_args.kwargs["skill_md"]
        assert "provenance:" in sent_md
        assert "source_url: https://x.com/v" in sent_md
        assert "transcript_sha256:" in sent_md

@pytest.mark.asyncio
async def test_install_skill_slug_conflict_retries():
    """Concurrent installs resolve to distinct slugs."""
    call_count = 0
    def fake_install(skill_md, slug, **kw):
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise httpx.HTTPStatusError("409", request=MagicMock(),
                response=MagicMock(status_code=409))
        return {"skill_id": "s", "path": f"/x/{slug}/skill.md"}
    with patch("mcp_tools.learning._install_via_api", side_effect=fake_install):
        r = await install_skill(
            "---\nname: X\nengine: markdown\n---\n", "test", "tenant1",
            "https://x.com/v", "755796a4-...", "abc"*21+"abc", "cfb6dd14-...",
        )
        assert r["path"].endswith("/test-v3/skill.md")

@pytest.mark.asyncio
async def test_install_skill_exhausts_slug_retries():
    from mcp_tools.learning import SlugExhausted
    with patch("mcp_tools.learning._install_via_api") as ins:
        ins.side_effect = httpx.HTTPStatusError("409", request=MagicMock(),
            response=MagicMock(status_code=409))
        with pytest.raises(SlugExhausted):
            await install_skill(
                "---\nname: X\nengine: markdown\n---\n", "test", "tenant1",
                "https://x.com/v", "755796a4-...", "abc"*21+"abc", "cfb6dd14-...",
            )
```

- [ ] **Step 2: Run → fail**

Run: `cd apps/mcp-server && pytest tests/test_learning.py -v -k install_skill`
Expected: FAIL.

- [ ] **Step 3: Implement** + add internal API endpoint `POST /api/v1/skills/install-learned` that does transactional insert + filesystem write

```python
from datetime import datetime, timezone


class SlugExhausted(Exception): ...


SLUG_MAX_RETRIES = 5


def _inject_provenance(skill_md: str, *, source_url: str, reviewer_agent_id: str,
                       transcript_sha256: str, learned_by_agent_id: str) -> str:
    """Insert provenance block into existing frontmatter (spec §1.6)."""
    iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    block = (
        "provenance:\n"
        f"  source_url: {source_url}\n"
        f"  synthesis_date: \"{iso}\"\n"
        f"  reviewer_agent_id: {reviewer_agent_id}\n"
        f"  transcript_sha256: {transcript_sha256}\n"
        f"  learned_by_agent_id: {learned_by_agent_id}\n"
    )
    return re.sub(r"^(---\n)", f"\\1{block}", skill_md, count=1)


async def _install_via_api(skill_md: str, slug: str, tenant_id: str,
                            learned_by_agent_id: str, source_url: str) -> dict:
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.post(
            f"{_API_BASE}/api/v1/skills/install-learned",
            json={
                "skill_md": skill_md, "slug": slug, "tenant_id": tenant_id,
                "actor_user_id": learned_by_agent_id,
                "reason": f"learned from {source_url}",
            },
            headers={"X-Internal-Key": os.environ["MCP_API_KEY"]},
        )
        r.raise_for_status()
        return r.json()


async def install_skill(
    skill_md: str, slug: str, tenant_id: str,
    source_url: str, reviewer_agent_id: str,
    transcript_sha256: str, learned_by_agent_id: str,
) -> dict:
    md = _inject_provenance(
        skill_md,
        source_url=source_url,
        reviewer_agent_id=reviewer_agent_id,
        transcript_sha256=transcript_sha256,
        learned_by_agent_id=learned_by_agent_id,
    )
    for attempt in range(1, SLUG_MAX_RETRIES + 1):
        candidate = slug if attempt == 1 else f"{slug}-v{attempt}"
        try:
            return await _install_via_api(
                md, candidate, tenant_id, learned_by_agent_id, source_url,
            )
        except httpx.HTTPStatusError as e:
            if e.response.status_code != 409:
                raise
            # Conflict → next suffix.
    raise SlugExhausted(f"could not allocate slug for {slug!r} after {SLUG_MAX_RETRIES} attempts")
```

Add the `POST /api/v1/skills/install-learned` endpoint in `apps/api/app/api/v1/skills_new.py` — transactional DB insert with unique-constraint-aware retry semantics + filesystem write to `_tenant/<uuid>/<slug>/skill.md` + `library_revisions` audit row. Return 409 on unique-constraint violation.

- [ ] **Step 4: Run → pass**

Run: `cd apps/mcp-server && pytest tests/test_learning.py -v -k install_skill`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git switch -c impl/luna-learn-t26-install impl/luna-learn-t25-test
git add apps/mcp-server/src/mcp_tools/learning.py apps/mcp-server/tests/test_learning.py apps/api/app/api/v1/skills_new.py
git commit -m "feat(luna-learn): install_skill — provenance injection + slug-conflict retries + library_revisions audit"
git push -u origin impl/luna-learn-t26-install
```

### Task 2.7: `diffuse_learning` — KG observation

**Files:**
- Modify: `apps/mcp-server/src/mcp_tools/learning.py`
- Test: `apps/mcp-server/tests/test_learning.py`

**Behavior contract:**
- Calls existing `record_observation` MCP path (or its HTTP equivalent on api): "Learned new capability 'X' from <URL>. Capabilities: [...]. Skill: skill_id=Y."
- Soft-fail: returns `{observation_id: None, soft_failed: True, error: ...}` if KG is down. Caller (workflow) caches per §1.11 but does NOT abort install.

- [ ] **Step 1: Write failing tests**

```python
@pytest.mark.asyncio
async def test_diffuse_success():
    with patch("mcp_tools.learning._record_observation") as r:
        r.return_value = {"observation_id": "obs-1"}
        result = await diffuse_learning("skill-1", "https://x.com/v", ["fix-printer"])
        assert result["observation_id"] == "obs-1"
        assert result["soft_failed"] is False

@pytest.mark.asyncio
async def test_diffuse_soft_fails_on_kg_down():
    with patch("mcp_tools.learning._record_observation") as r:
        r.side_effect = httpx.HTTPError("KG unavailable")
        result = await diffuse_learning("skill-1", "https://x.com/v", ["fix-printer"])
        assert result["observation_id"] is None
        assert result["soft_failed"] is True
        assert "KG unavailable" in result["error"]
```

- [ ] **Step 2: Run → fail**

Run: `cd apps/mcp-server && pytest tests/test_learning.py -v -k diffuse`
Expected: FAIL.

- [ ] **Step 3: Implement**

```python
async def _record_observation(text: str, metadata: dict) -> dict:
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.post(
            f"{_API_BASE}/api/v1/knowledge/observations",
            json={"text": text, "metadata": metadata},
            headers={"X-Internal-Key": os.environ["MCP_API_KEY"]},
        )
        r.raise_for_status()
        return r.json()


async def diffuse_learning(skill_id: str, source_url: str, capabilities: list[str]) -> dict:
    text = (
        f"Learned new capability from {source_url}. "
        f"Capabilities: {', '.join(capabilities)}. Skill: {skill_id}."
    )
    metadata = {
        "kind": "luna_learn",
        "skill_id": skill_id,
        "source_url": source_url,
        "capabilities": capabilities,
    }
    try:
        r = await _record_observation(text, metadata)
        return {"observation_id": r["observation_id"], "soft_failed": False}
    except Exception as e:
        return {"observation_id": None, "soft_failed": True, "error": str(e)}
```

- [ ] **Step 4: Run → pass**

Run: `cd apps/mcp-server && pytest tests/test_learning.py -v -k diffuse`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git switch -c impl/luna-learn-t27-diffuse impl/luna-learn-t26-install
git add apps/mcp-server/src/mcp_tools/learning.py apps/mcp-server/tests/test_learning.py
git commit -m "feat(luna-learn): diffuse_learning — KG observation with soft-fail semantics"
git push -u origin impl/luna-learn-t27-diffuse
```

---

## Phase 3 — Workflow wiring

### Task 3.1: Temporal activities wrap MCP primitives

**Files:**
- Modify: `apps/api/app/workflows/activities/learn_from_media_activities.py`
- Test: `apps/api/tests/test_learn_activities.py` (new)

Each activity is a thin async wrapper that calls the MCP primitive via the mcp-server HTTP surface (Temporal worker runs in api/orchestration-worker, not mcp-server). Typed-exception mapping for workflow branching. Pattern reference: `apps/api/tests/test_coalition_activities.py`.

**Activity result shape** (Temporal serialization-friendly):
```python
{"ok": bool, "data": dict | None, "error": {"type": str, "message": str} | None}
```
The typed exceptions from learning.py (`MediaPrivate`, `MediaNotFound`, `MediaGeoBlocked`, `MediaAntiScrape`, `MediaTooLong`, `DraftInvalid`, `DraftForbiddenShellout`, `ReviewerNotProvisioned`, `ReviewTimeout`, `SlugExhausted`) become `{"ok": False, "error": {"type": "MediaPrivate", "message": "..."}}`. The workflow body branches on `error.type`.

> **Status-code mapping is internal-only** (review NEW-IMPORTANT-3): The HTTP status codes used by the T1.2a shim (e.g. 451 for `MediaPrivate`, 424 for `DraftForbiddenShellout`) repurpose RFC codes for internal signaling. They are NOT meant to be exposed publicly. The `error_type` field in the response body is the **authoritative** branch key — the `_STATUS_TO_TYPE` map below is a fast-path lookup, and the envelope code below prefers `body["error_type"]` over the status-code map when present.

- [ ] **Step 1: Write failing test for one activity (act_extract_media)** as template

```python
# apps/api/tests/test_learn_activities.py
import pytest
from unittest.mock import patch, AsyncMock
from app.workflows.activities.learn_from_media_activities import (
    act_extract_media, act_transcribe_url, act_synthesize_skill_draft,
    act_dispatch_skill_review, act_run_synthetic_test, act_install_skill,
    act_diffuse_learning, act_write_cache, act_write_quarantine,
    act_notify_session, act_probe_attachment,
)

@pytest.mark.asyncio
async def test_act_extract_media_ok():
    with patch("app.workflows.activities.learn_from_media_activities._call_mcp") as call:
        call.return_value = {"audio_path": "/tmp/x", "metadata": {"duration_s": 90}}
        r = await act_extract_media("https://youtu.be/abc", 900)
        assert r["ok"] is True
        assert r["data"]["audio_path"] == "/tmp/x"

@pytest.mark.asyncio
@pytest.mark.parametrize("status,etype", [
    (451, "MediaPrivate"), (404, "MediaNotFound"),
    (403, "MediaGeoBlocked"), (429, "MediaAntiScrape"),
    (413, "MediaTooLong"),
])
async def test_act_extract_media_typed_errors(status, etype):
    import httpx
    with patch("app.workflows.activities.learn_from_media_activities._call_mcp") as call:
        call.side_effect = httpx.HTTPStatusError(
            etype, request=AsyncMock(),
            response=AsyncMock(status_code=status, json=lambda: {"error_type": etype, "message": "x"}),
        )
        r = await act_extract_media("https://x.com/v", 900)
        assert r["ok"] is False
        assert r["error"]["type"] == etype
```

(Parallel tests for the other 10 activities follow the same shape; abbreviated here — the implementer writes one block per activity.)

- [ ] **Step 2: Run → fail**

Run: `cd apps/api && pytest tests/test_learn_activities.py -v -k extract_media`
Expected: FAIL with NotImplementedError or import errors.

- [ ] **Step 3: Implement** — each activity is a thin httpx call to the mcp-server endpoint that wraps the corresponding `mcp_tools.learning` primitive

```python
# apps/api/app/workflows/activities/learn_from_media_activities.py
import os
import httpx
from temporalio import activity

_MCP_BASE = os.environ.get("MCP_SERVER_BASE", "http://mcp-tools:8000")  # REST FastAPI port (NOT 8001; that's FastMCP streamable). Resolved during T1.2a review.
_TOOL_PREFIX = "/agentprovision/v1/tools"  # matches existing server.py convention; T1.2a registered the learning shim here
_HEADERS = {"X-Internal-Key": os.environ.get("MCP_API_KEY", "")}

# Maps MCP-side HTTP status → typed exception name (mirrors learning.py exceptions).
_STATUS_TO_TYPE = {
    451: "MediaPrivate", 404: "MediaNotFound",
    403: "MediaGeoBlocked", 429: "MediaAntiScrape",
    413: "MediaTooLong", 422: "DraftInvalid",
    424: "DraftForbiddenShellout", 503: "ReviewerNotProvisioned",
    504: "ReviewTimeout", 409: "SlugExhausted",
}


async def _call_mcp(tool: str, payload: dict) -> dict:
    async with httpx.AsyncClient(timeout=120.0) as client:
        r = await client.post(f"{_MCP_BASE}{_TOOL_PREFIX}/{tool}", json=payload, headers=_HEADERS)
        r.raise_for_status()
        return r.json()


def _wrap(coro):
    """Convert (success | HTTPStatusError) → Temporal result envelope.

    Body's `error_type` is AUTHORITATIVE; `_STATUS_TO_TYPE` is only the
    fast-path fallback when the body is missing or malformed (matches the
    T1.2a shim contract).
    """
    async def wrapper(*args, **kwargs):
        try:
            data = await coro(*args, **kwargs)
            return {"ok": True, "data": data, "error": None}
        except httpx.HTTPStatusError as e:
            try:
                body = e.response.json()
            except Exception:
                body = {}
            etype = body.get("error_type") or _STATUS_TO_TYPE.get(e.response.status_code, "UnknownError")
            return {"ok": False, "data": None, "error": {"type": etype, "message": body.get("message", str(e))}}
    return wrapper


@activity.defn
@_wrap
async def act_extract_media(url: str, max_duration_s: int = 900) -> dict:
    return await _call_mcp("extract_media", {"url": url, "max_duration_s": max_duration_s})


@activity.defn
@_wrap
async def act_transcribe_url(audio_path: str) -> dict:
    try:
        return await _call_mcp("transcribe_url", {"audio_path": audio_path})
    finally:
        # Spec §1.12: delete audio on success path. Failure path leaves it
        # for quarantine bundle (T3.3 will copy from this location).
        from pathlib import Path
        p = Path(audio_path)
        if p.exists():
            p.unlink(missing_ok=True)


@activity.defn
@_wrap
async def act_synthesize_skill_draft(transcript: str, source_url: str, hints: list[str] | None = None) -> dict:
    return await _call_mcp("synthesize_skill_draft", {
        "transcript": transcript, "source_url": source_url, "hints": hints or [],
    })


@activity.defn
@_wrap
async def act_dispatch_skill_review(skill_md: str, transcript: str, source_url: str,
                                     synthetic_test_input: dict, synthetic_test_expected: dict) -> dict:
    return await _call_mcp("dispatch_skill_review", {
        "skill_md": skill_md, "transcript": transcript, "source_url": source_url,
        "synthetic_test_input": synthetic_test_input,
        "synthetic_test_expected": synthetic_test_expected,
    })


@activity.defn
@_wrap
async def act_run_synthetic_test(skill_md: str, test_input: dict, test_expected: dict) -> dict:
    return await _call_mcp("run_synthetic_test", {
        "skill_md": skill_md, "test_input": test_input, "test_expected": test_expected,
    })


@activity.defn
@_wrap
async def act_install_skill(skill_md: str, slug: str, tenant_id: str,
                             source_url: str, reviewer_agent_id: str,
                             transcript_sha256: str, learned_by_agent_id: str) -> dict:
    return await _call_mcp("install_skill", {
        "skill_md": skill_md, "slug": slug, "tenant_id": tenant_id,
        "source_url": source_url, "reviewer_agent_id": reviewer_agent_id,
        "transcript_sha256": transcript_sha256,
        "learned_by_agent_id": learned_by_agent_id,
    })


@activity.defn
@_wrap
async def act_diffuse_learning(skill_id: str, source_url: str, capabilities: list[str]) -> dict:
    return await _call_mcp("diffuse_learning", {
        "skill_id": skill_id, "source_url": source_url, "capabilities": capabilities,
    })


# Cache + quarantine + notify + attachment-probe activities are T3.3, T3.5,
# and T4.4b — same envelope shape; bodies in those tasks.
```

T2.6's `install_skill` server endpoint must return HTTP `409` on slug exhaustion (so the envelope maps to `SlugExhausted`); 422 on draft-parse failure; 503 when Code Reviewer agent registry returns 404; 504 on review timeout. Add these contract clauses to T2.4 + T2.6 internal-endpoint specs (see T2.5b-bis below).

- [ ] **Step 4: Run → pass**

Run: `cd apps/api && pytest tests/test_learn_activities.py -v`
Expected: all activity tests pass.

- [ ] **Step 5: Commit**

```bash
git switch -c impl/luna-learn-t31-activities impl/luna-learn-t27-diffuse
git add apps/api/app/workflows/activities/learn_from_media_activities.py apps/api/tests/test_learn_activities.py
git commit -m "feat(luna-learn): Temporal activities wrapping the 7 MCP primitives + typed-error envelope"
git push -u origin impl/luna-learn-t31-activities
```

### Task 3.2: `LearnFromMediaWorkflow` body (decomposed into 6 sub-tasks per spec §2 branches)

**Files (all 6 sub-tasks):**
- Modify: `apps/api/app/workflows/learn_from_media_workflow.py`
- Test: `apps/api/tests/test_learn_workflow.py` (new — extended across sub-tasks)

Test scaffolding (used in every sub-task):

> **Important** (review NEW-IMPORTANT-2): Temporal's `Worker` captures activity function references at construction time, BEFORE the test body runs. `monkeypatch.setattr(A, "act_X", stub)` rebinds the module attribute but the worker still calls the original `A.act_X`. Patch `_call_mcp` (which every real activity calls into via `_wrap`) instead — that way the real activities run, the envelope decorator runs, but the HTTP boundary is mocked. Cleaner, fewer surface bugs.

```python
# apps/api/tests/test_learn_workflow.py
import pytest
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker
from app.workflows.learn_from_media_workflow import LearnFromMediaWorkflow
from app.workflows.activities import learn_from_media_activities as A

@pytest.fixture
async def env():
    async with await WorkflowEnvironment.start_time_skipping() as e:
        yield e

@pytest.fixture
async def worker(env):
    async with Worker(
        env.client, task_queue="learn-test",
        workflows=[LearnFromMediaWorkflow],
        activities=[
            A.act_extract_media, A.act_transcribe_url, A.act_synthesize_skill_draft,
            A.act_dispatch_skill_review, A.act_run_synthetic_test, A.act_install_skill,
            A.act_diffuse_learning, A.act_write_cache, A.act_write_quarantine,
            A.act_notify_session, A.act_probe_attachment,
        ],
    ) as w:
        yield w


def _mock_mcp_responses(monkeypatch, responses: dict[str, dict]):
    """Replace A._call_mcp with a dispatcher returning per-tool stub data.

    `responses` keys are tool names ("extract_media", "transcribe_url", ...),
    values are the raw dict the real tool would return.
    """
    async def fake(tool: str, payload: dict):
        if tool not in responses:
            raise RuntimeError(f"unexpected MCP call to {tool!r}")
        return responses[tool]
    monkeypatch.setattr(A, "_call_mcp", fake)
```

#### Task 3.2a — Happy path (no revise, install + diffuse success)

- [ ] **Step 1: Write failing test** — patches `_call_mcp` (NOT the activity functions, per NEW-IMPORTANT-2 note above)

```python
@pytest.mark.asyncio
async def test_workflow_happy_path(env, worker, monkeypatch):
    _mock_mcp_responses(monkeypatch, {
        "extract_media": {"audio_path": "/tmp/x.m4a", "metadata": {"duration_s": 90, "title": "T"}},
        "transcribe_url": {"transcript": "hello world", "engine": "whisper", "duration_ms": 90000},
        "synthesize_skill_draft": {
            "skill_md": "---\nname: Fix Printer\nengine: markdown\nauto_trigger: \"Fix printer\"\ninputs: []\n---\nUnplug it",
            "slug": "fix-printer", "engine": "markdown",
            "synthetic_test_input": {"x": 1}, "synthetic_test_expected": {"y": 2},
        },
        "dispatch_skill_review": {"verdict": "approved", "findings": [], "reviewer_agent_id": "755796a4-..."},
        "run_synthetic_test": {"passed": True, "actual_output": {"y": 2}, "error": None},
        "install_skill": {"skill_id": "s1", "path": "/x/_tenant/t1/fix-printer/skill.md"},
        "diffuse_learning": {"observation_id": "obs1", "soft_failed": False},
    })
    # act_notify_session writes to session DB; stub at the DB-write boundary.
    monkeypatch.setattr(A, "_write_session_message", lambda *a, **k: None)
    # act_transcribe_url's success-path delete touches the filesystem; ensure path exists.
    from pathlib import Path; Path("/tmp/x.m4a").write_bytes(b"x")

    result = await env.client.execute_workflow(
        LearnFromMediaWorkflow.run,
        {"source_url": "https://youtu.be/abc123", "tenant_id": "t1", "actor_user_id": "u1"},
        id="test-happy", task_queue="learn-test",
    )
    assert result["status"] == "success"
    assert result["skill_id"] == "s1"
    assert "fix-printer" in result["skill_path"]
    assert result["skill_name"] == "Fix Printer"
```

> Subsequent T3.2b–T3.2f tests follow the same pattern: build the `responses` dict for the happy steps, then either omit the failing-step key (forcing the `RuntimeError` in `_mock_mcp_responses`) or have the value RAISE the appropriate `httpx.HTTPStatusError` per the response-code map from T1.2a. Example for T3.2c reviewer-not-provisioned:
> ```python
> import httpx
> async def fake(tool: str, payload: dict):
>     if tool == "dispatch_skill_review":
>         raise httpx.HTTPStatusError(
>             "503", request=AsyncMock(),
>             response=AsyncMock(status_code=503, json=lambda: {"error_type": "ReviewerNotProvisioned", "message": "not in tenant"}),
>         )
>     ...
> ```

- [ ] **Step 2: Run → fail** (workflow body still NotImplementedError)

- [ ] **Step 3: Implement happy-path body**

```python
import os
from datetime import timedelta
from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    import hashlib, re, yaml
    from app.workflows.activities import learn_from_media_activities as A


_ACTIVITY_TIMEOUTS = {
    "extract": timedelta(minutes=5),
    "transcribe": timedelta(minutes=10),
    "synth": timedelta(minutes=2),
    "review": timedelta(seconds=70),  # MCP-side gates at 60s; give workflow 10s headroom
    "test": timedelta(minutes=2),
    "install": timedelta(seconds=30),
    "diffuse": timedelta(seconds=15),
    "notify": timedelta(seconds=15),
    "write": timedelta(seconds=30),
    "probe": timedelta(seconds=30),
}


def _extract_capabilities(skill_md: str) -> list[str]:
    """Pull tags + auto_trigger from frontmatter for the KG observation."""
    m = re.match(r"^---\n(.+?)\n---", skill_md, re.DOTALL)
    if not m:
        return []
    fm = yaml.safe_load(m.group(1))
    return [fm.get("auto_trigger", "").strip()] + list(fm.get("tags") or [])


def _skill_name(skill_md: str) -> str:
    m = re.match(r"^---\n(.+?)\n---", skill_md, re.DOTALL)
    fm = yaml.safe_load(m.group(1)) if m else {}
    return fm.get("name", "<unnamed>")


@workflow.defn(name="LearnFromMediaWorkflow")
class LearnFromMediaWorkflow:
    @workflow.run
    async def run(self, intent_dict: dict) -> dict:
        intent = intent_dict  # validated upstream by LearningService
        source_url = intent.get("source_url")
        attachment = intent.get("attachment_path")
        tenant_id = intent["tenant_id"]
        learned_by = intent["actor_user_id"]
        session_id = intent.get("session_id")
        job_id = workflow.info().workflow_id

        # --- step 1: extract OR probe attachment ---
        if attachment:
            probe = await workflow.execute_activity(
                A.act_probe_attachment, attachment,
                start_to_close_timeout=_ACTIVITY_TIMEOUTS["probe"],
            )
            if not probe["ok"]:
                return {"status": "attachment_invalid", "error": probe["error"]}
            audio_path = attachment
            audio_meta = probe["data"]
            provenance_url = f"attachment://{attachment.split('/')[-1]}"
        else:
            extract = await workflow.execute_activity(
                A.act_extract_media, source_url, 900,
                start_to_close_timeout=_ACTIVITY_TIMEOUTS["extract"],
            )
            if not extract["ok"]:
                # T3.2b handles per-error-type branches; happy path stops here.
                return {"status": "extract_failed", "error": extract["error"]}
            audio_path = extract["data"]["audio_path"]
            audio_meta = extract["data"]["metadata"]
            provenance_url = source_url

        # --- step 2: transcribe (also deletes audio on success per T3.1) ---
        trans = await workflow.execute_activity(
            A.act_transcribe_url, audio_path,
            start_to_close_timeout=_ACTIVITY_TIMEOUTS["transcribe"],
        )
        if not trans["ok"]:
            return {"status": "transcribe_failed", "error": trans["error"]}
        transcript = trans["data"]["transcript"]

        # --- step 3: synth ---
        synth = await workflow.execute_activity(
            A.act_synthesize_skill_draft, transcript, provenance_url, [],
            start_to_close_timeout=_ACTIVITY_TIMEOUTS["synth"],
        )
        if not synth["ok"]:
            return {"status": "synth_failed", "error": synth["error"]}
        draft = synth["data"]

        # --- step 4: review (T3.2c handles revise/rejected branches) ---
        review = await workflow.execute_activity(
            A.act_dispatch_skill_review,
            draft["skill_md"], transcript, provenance_url,
            draft["synthetic_test_input"], draft["synthetic_test_expected"],
            start_to_close_timeout=_ACTIVITY_TIMEOUTS["review"],
        )
        if not review["ok"]:
            return {"status": "review_failed", "error": review["error"]}
        if review["data"]["verdict"] != "approved":
            return {"status": review["data"]["verdict"], "findings": review["data"]["findings"]}

        # --- step 5: test ---
        test = await workflow.execute_activity(
            A.act_run_synthetic_test,
            draft["skill_md"], draft["synthetic_test_input"], draft["synthetic_test_expected"],
            start_to_close_timeout=_ACTIVITY_TIMEOUTS["test"],
        )
        if not test["ok"] or not test["data"]["passed"]:
            return {"status": "test_failed", "error": test["data"].get("error") if test["ok"] else test["error"]}

        # --- step 6: install ---
        sha256 = hashlib.sha256(transcript.encode()).hexdigest()
        install = await workflow.execute_activity(
            A.act_install_skill,
            draft["skill_md"], draft["slug"], tenant_id, provenance_url,
            review["data"]["reviewer_agent_id"], sha256, learned_by,
            start_to_close_timeout=_ACTIVITY_TIMEOUTS["install"],
        )
        if not install["ok"]:
            return {"status": "install_failed", "error": install["error"]}

        # --- step 7: diffuse (soft-fail) ---
        capabilities = _extract_capabilities(draft["skill_md"])
        diffuse = await workflow.execute_activity(
            A.act_diffuse_learning,
            install["data"]["skill_id"], provenance_url, capabilities,
            start_to_close_timeout=_ACTIVITY_TIMEOUTS["diffuse"],
        )
        # diffuse soft-fail handling lives in T3.2e

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
                A.act_notify_session, session_id, result,
                start_to_close_timeout=_ACTIVITY_TIMEOUTS["notify"],
            )
        return result
```

- [ ] **Step 4: Run → pass**

Run: `cd apps/api && pytest tests/test_learn_workflow.py::test_workflow_happy_path -v`
Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
git switch -c impl/luna-learn-t32a-happy impl/luna-learn-t31-activities
git add apps/api/app/workflows/learn_from_media_workflow.py apps/api/tests/test_learn_workflow.py
git commit -m "feat(luna-learn): workflow happy path (extract → transcribe → synth → review:approved → test:pass → install → diffuse → notify)"
git push -u origin impl/luna-learn-t32a-happy
```

#### Task 3.2b — Extract-error per-type branches + notify+quarantine

- [ ] **Step 1: Write failing tests** for each typed extract error (MediaPrivate, MediaNotFound, MediaGeoBlocked, MediaAntiScrape, MediaTooLong) — each asserts the right user-facing notify message per spec §3 + a quarantine write.

- [ ] **Step 2-4: Implement** the branches in the workflow body: replace `return {"status": "extract_failed", ...}` with a dispatch to per-type notify message helpers + `act_write_quarantine` call. Notify message strings come straight from spec §3 rows.

- [ ] **Step 5: Commit** as `impl/luna-learn-t32b-extract-errors`.

#### Task 3.2c — Review branches: `revise` loop (max 2 retries) + `rejected` + `reviewer-down` distinct from `timeout`

- [ ] **Step 1: Write failing tests**:
  - revise→approved (hints flow into next synth call)
  - revise×3 → revise-exhausted → quarantine
  - rejected → quarantine + notify with reviewer reason
  - `error.type == "ReviewerNotProvisioned"` → **cache** (recoverable per spec §1.11) + notify with --resume-last hint
  - `error.type == "ReviewTimeout"` → **quarantine** (terminal per spec §3) + notify

This is the distinction reviewer flagged (I6).

- [ ] **Step 2-4: Implement** the for-loop with `max_retries = int(os.environ.get("LUNA_LEARN_MAX_REVISE_RETRIES", "2"))` + branch on `error.type` for cache vs quarantine.

- [ ] **Step 5: Commit** as `impl/luna-learn-t32c-review-branches`.

#### Task 3.2d — Test-failure → quarantine + library_revisions audit row

- [ ] **Step 1: Write failing test** that asserts test_failed branch writes a library_revisions row with `result: "rejected_test_fail"`.

- [ ] **Step 2-4: Implement** + add a `act_log_test_fail` activity that hits an internal `POST /api/v1/skills/library/internal/audit-rejection` endpoint (added in T2.6's API surface as a follow-on).

- [ ] **Step 5: Commit** as `impl/luna-learn-t32d-test-fail`.

#### Task 3.2e — Diffuse soft-fail → cache (do not abort install)

- [ ] **Step 1: Write failing test**: diffuse_learning returns `{soft_failed: True}` → workflow STILL returns `status: success` + an additional `diffuse_cached: true` key + a `act_write_cache` call recording the pending diffusion.

- [ ] **Step 2-4: Implement** the post-install branch (do not propagate the soft-fail to user; user sees success with a note that semantic discovery may be delayed).

- [ ] **Step 5: Commit** as `impl/luna-learn-t32e-diffuse-soft-fail`.

#### Task 3.2f — Install rollback (DB error + FS write failure after DB row reserved)

- [ ] **Step 1: Write failing test** (B4 from reviewer):
  - DB error during insert → result `install_failed`, no FS write, no library_revisions row
  - FS write fails after DB row reserved → DB row deleted on rollback (verify with DB query)
  - This is the spec §1.7 "No TOCTOU" claim made testable.

- [ ] **Step 2-4: Implement** the rollback semantics in the server endpoint (T2.6's `/api/v1/skills/install-learned`) — wrap DB row insert + FS write in a single `async with` transaction with explicit FS-failure rollback. The workflow side just sees the error envelope.

- [ ] **Step 5: Commit** as `impl/luna-learn-t32f-install-rollback`.

### Task 3.3: Quarantine + Cache write helpers (`act_write_quarantine`, `act_write_cache`)

**Files:**
- Modify: `apps/api/app/workflows/activities/learn_from_media_activities.py`
- Test: `apps/api/tests/test_learn_cache_quarantine.py` (new)

`_tenant_root(tenant_id)` resolves to `/var/agentprovision/workspaces/_tenant/<uuid>/` per existing tenant-workspace convention (referenced in `transcription_client.py:48` for `_transcribe/`; same root, different namespace dir).

- [ ] **Step 1: Write failing tests** including the cache⊕quarantine mutual-exclusion invariant (B3 from reviewer)

```python
import pytest
from pathlib import Path
from app.workflows.activities.learn_from_media_activities import (
    _tenant_root, act_write_cache, act_write_quarantine,
)

def test_tenant_root_resolves(tmp_path, monkeypatch):
    monkeypatch.setattr("app.workflows.activities.learn_from_media_activities._WORKSPACE_BASE", tmp_path)
    assert _tenant_root("uuid-1") == tmp_path / "_tenant" / "uuid-1"

@pytest.mark.asyncio
async def test_write_quarantine_layout(tmp_path, monkeypatch):
    monkeypatch.setattr("app.workflows.activities.learn_from_media_activities._WORKSPACE_BASE", tmp_path)
    r = await act_write_quarantine(
        tenant_id="t1", job_id="2026-05-25-123000-fix-printer",
        transcript="raw transcript with PII", draft={"skill_md": "---\n..."},
        review={"verdict": "rejected"}, test_result=None, abort_reason="rejected by reviewer",
    )
    qdir = tmp_path / "_tenant" / "t1" / "_learning_quarantine" / "2026-05-25-123000-fix-printer"
    assert (qdir / "transcript.txt").read_text() == "raw transcript with PII"
    assert (qdir / "abort_reason.txt").read_text() == "rejected by reviewer"

@pytest.mark.asyncio
async def test_write_cache_layout(tmp_path, monkeypatch):
    monkeypatch.setattr("app.workflows.activities.learn_from_media_activities._WORKSPACE_BASE", tmp_path)
    r = await act_write_cache(
        tenant_id="t1", job_id="job-1",
        transcript="scrubbed", draft={"skill_md": "---\nname: x..."},
        last_review={"verdict": "revise"}, last_test=None,
    )
    cdir = tmp_path / "_tenant" / "t1" / "_learning_cache" / "job-1"
    assert (cdir / "transcript.txt").exists()
    assert (cdir / "draft.md").exists()

@pytest.mark.asyncio
async def test_cache_and_quarantine_are_mutually_exclusive(tmp_path, monkeypatch):
    """Spec §1.11 invariant: same job_id never appears in both."""
    monkeypatch.setattr("app.workflows.activities.learn_from_media_activities._WORKSPACE_BASE", tmp_path)
    job_id = "job-mutex-test"
    await act_write_cache(tenant_id="t1", job_id=job_id, transcript="x", draft={}, last_review=None, last_test=None)
    # Caller invariant: must not also quarantine. Verify by asserting that a write_quarantine
    # call for the same job_id raises (the helper checks for existing cache entry first).
    from app.workflows.activities.learn_from_media_activities import CacheAndQuarantineConflict
    with pytest.raises(CacheAndQuarantineConflict):
        await act_write_quarantine(
            tenant_id="t1", job_id=job_id, transcript="x", draft={},
            review={}, test_result=None, abort_reason="x",
        )
```

- [ ] **Step 2: Run → fail**

- [ ] **Step 3: Implement helpers + invariant check**

```python
import json
from pathlib import Path

_WORKSPACE_BASE = Path("/var/agentprovision/workspaces")


def _tenant_root(tenant_id: str) -> Path:
    return _WORKSPACE_BASE / "_tenant" / tenant_id


class CacheAndQuarantineConflict(Exception):
    """Spec §1.11: a job_id may exist in cache OR quarantine, never both."""


@activity.defn
async def act_write_cache(tenant_id: str, job_id: str, transcript: str,
                          draft: dict, last_review: dict | None,
                          last_test: dict | None) -> dict:
    cdir = _tenant_root(tenant_id) / "_learning_cache" / job_id
    qdir = _tenant_root(tenant_id) / "_learning_quarantine"
    # Mutex check: any quarantine dir with the same job_id?
    if qdir.exists() and any(d.name.endswith(job_id) for d in qdir.iterdir() if d.is_dir()):
        raise CacheAndQuarantineConflict(f"{job_id} already quarantined")
    cdir.mkdir(parents=True, exist_ok=True)
    (cdir / "transcript.txt").write_text(transcript or "")
    (cdir / "draft.md").write_text(draft.get("skill_md", ""))
    if last_review:
        (cdir / "review.json").write_text(json.dumps(last_review))
    if last_test:
        (cdir / "test.json").write_text(json.dumps(last_test))
    return {"cache_dir": str(cdir)}


@activity.defn
async def act_write_quarantine(tenant_id: str, job_id: str, transcript: str,
                                draft: dict, review: dict, test_result: dict | None,
                                abort_reason: str) -> dict:
    cdir = _tenant_root(tenant_id) / "_learning_cache" / job_id
    if cdir.exists():
        raise CacheAndQuarantineConflict(f"{job_id} already in cache")
    qdir = _tenant_root(tenant_id) / "_learning_quarantine" / job_id
    qdir.mkdir(parents=True, exist_ok=True)
    (qdir / "transcript.txt").write_text(transcript or "")
    (qdir / "draft.md").write_text(draft.get("skill_md", "") if draft else "")
    (qdir / "review.json").write_text(json.dumps(review) if review else "{}")
    if test_result:
        (qdir / "test_result.json").write_text(json.dumps(test_result))
    (qdir / "abort_reason.txt").write_text(abort_reason)
    return {"quarantine_dir": str(qdir)}
```

- [ ] **Step 4: Run → pass** (4 tests including mutex invariant)

- [ ] **Step 5: Commit**

```bash
git switch -c impl/luna-learn-t33-cache-quarantine impl/luna-learn-t32f-install-rollback
git add apps/api/app/workflows/activities/learn_from_media_activities.py apps/api/tests/test_learn_cache_quarantine.py
git commit -m "feat(luna-learn): cache + quarantine helpers + §1.11 mutual-exclusion invariant test"
git push -u origin impl/luna-learn-t33-cache-quarantine
```

### Task 3.4: Resume path — `LearningIntent.resume_job_id` short-circuit

**Files:** modify workflow + service layer.

Reads cached `LearningJobState` from `_tenant/<uuid>/_learning_cache/<job_id>/`, picks up from the failed step.

- [ ] **Step 1-5: TDD** the resume short-circuit. Covers reviewer-down resume (re-runs dispatch_skill_review) + KG-down resume (re-runs diffuse_learning).

```bash
git switch -c impl/luna-learn-t34-resume impl/luna-learn-t33-quarantine-cache
# ... commits
```

### Task 3.5: Completion notification back to Luna's session

**Files:** new activity `act_notify_session`; modify workflow.

Writes a ChatMessage(role="agent", context.kind="learn_complete") to the session_id passed in the intent. WhatsApp service picks it up via existing message-out plumbing.

- [ ] **Step 1-5: TDD** the notify path. Mock session message writer; verify the message payload matches spec §2 step 8 format.

```bash
git switch -c impl/luna-learn-t35-notify impl/luna-learn-t34-resume
# ... commits
```

---

## Phase 4 — Entry surfaces

### Task 4.1a: `LearningService` — shared dispatch helper (service layer ONLY)

**Files:**
- Create: `apps/api/app/services/learning_service.py`
- Test: `apps/api/tests/test_learning_service.py` (new)

`LearningService.dispatch(intent: LearningIntent) → workflow_id` connects to Temporal and starts `LearnFromMediaWorkflow`. Pure service-layer; the HTTP route is T4.4c.

- [ ] **Step 1-5: TDD** the dispatch helper, mocking Temporal client.

```bash
git switch -c impl/luna-learn-t41a-service impl/luna-learn-t35-notify
git commit -m "feat(luna-learn): LearningService.dispatch helper"
# ... push
```

(T4.4c later wires the HTTP route around this helper. The split keeps service vs route boundaries clear per reviewer I4.)

### Task 4.2: WhatsApp URL detection + learning intent routing

**Files:**
- Modify: `apps/api/app/services/whatsapp_service.py` (extend `_detect_inbound_media`)
- Create: `apps/api/app/services/url_intent_router.py`
- Test: `apps/api/tests/test_url_intent_router.py` (new)

URL patterns to match:
```python
YOUTUBE_RE = re.compile(r"https?://(?:www\.|m\.)?youtube\.com/(?:watch\?v=|shorts/)[A-Za-z0-9_-]{11}")
YOUTU_BE_RE = re.compile(r"https?://youtu\.be/[A-Za-z0-9_-]{11}")
INSTAGRAM_RE = re.compile(r"https?://(?:www\.)?instagram\.com/(?:reel|reels|p)/[A-Za-z0-9_-]+")
```

`_detect_inbound_media` returns a new `("learning_url", url, caption)` tuple when text matches one of these. The handler dispatches via `LearningService`.

- [ ] **Step 1-5: TDD** the URL router (test each regex variant + the `_detect_inbound_media` integration).

```bash
git switch -c impl/luna-learn-t42-whatsapp impl/luna-learn-t41-service
# ... commits
```

### Task 4.3: `alpha learn` CLI command — base surface

**Files:**
- Create: `apps/agentprovision-cli/src/commands/learn.rs`
- Create: `apps/agentprovision-cli/src/commands/learn_test.rs`
- Modify: `apps/agentprovision-cli/src/main.rs` (or wherever subcommands register)

Surface: `alpha learn <url> [--dry-run]`. Calls existing `/api/v1/learning/dispatch` endpoint (added in T4.1 alongside `LearningService`).

- [ ] **Step 1-5: TDD** in Rust using existing test pattern (`apps/agentprovision-cli/src/commands/skill.rs` as reference).

```bash
git switch -c impl/luna-learn-t43-cli impl/luna-learn-t42-whatsapp
# ... commits
```

### Task 4.4: CLI flags `--from-attachment`, `--resume`, `--resume-last`

**Files:** modify `learn.rs` + test.

`--from-attachment FILE`: uploads the local file to the new internal `/api/v1/learning/upload-attachment` endpoint (added in T4.4b), then dispatches with `attachment_path` set.

`--resume <job_id>` + `--resume-last`: queries the cache and re-dispatches with `resume_job_id`.

- [ ] **Step 1-5: TDD** each flag.

```bash
git switch -c impl/luna-learn-t44-cli-flags impl/luna-learn-t43-cli
# ... commits
```

### Task 4.4b — Attachment server-side enforcement (spec §1.8 — was missing)

**Files:**
- Create: `apps/api/app/api/v1/learning.py` (new router; T4.1's `/learning/dispatch` lives here too)
- Test: `apps/api/tests/test_learning_attachment.py` (new)

Enforces ALL spec §1.8 constraints server-side (CLI checks are best-effort UX only — server is the trust boundary):
- Max file size: 50MB
- Allowed MIME types: `audio/*`, `video/*`
- Duration cap: 900s via `ffprobe` (probed before transcription dispatch)
- Provenance source_url recorded as `attachment://<basename>` (never the full local path)

Also implements `act_probe_attachment` activity referenced in T3.2a.

- [ ] **Step 1: Write failing tests**

```python
# apps/api/tests/test_learning_attachment.py
import pytest
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

def _post(file_bytes: bytes, filename: str, content_type: str):
    return client.post(
        "/api/v1/learning/upload-attachment",
        files={"file": (filename, file_bytes, content_type)},
        headers={"X-Internal-Key": "test-key"},
    )

def test_attachment_oversize_rejected():
    r = _post(b"\x00" * (51 * 1024 * 1024), "big.mp4", "video/mp4")
    assert r.status_code == 413
    assert "50MB" in r.json()["detail"]

def test_attachment_bad_mime_rejected():
    r = _post(b"hello", "doc.pdf", "application/pdf")
    assert r.status_code == 415
    assert "MIME" in r.json()["detail"] or "type" in r.json()["detail"].lower()

def test_attachment_audio_ok(monkeypatch):
    monkeypatch.setattr("app.api.v1.learning._ffprobe_duration", lambda p: 120)
    r = _post(b"OggS" + b"\x00" * 100, "voice.ogg", "audio/ogg")
    assert r.status_code == 200
    body = r.json()
    assert body["source_url"].startswith("attachment://")
    assert body["source_url"].endswith("voice.ogg")

def test_attachment_too_long_rejected(monkeypatch):
    monkeypatch.setattr("app.api.v1.learning._ffprobe_duration", lambda p: 1200)
    r = _post(b"\x00" * 200, "long.mp4", "video/mp4")
    assert r.status_code == 413
    assert "900s" in r.json()["detail"] or "duration" in r.json()["detail"].lower()
```

- [ ] **Step 2: Run → fail**

- [ ] **Step 3: Implement** the endpoint

```python
# apps/api/app/api/v1/learning.py
import os
import subprocess
import uuid
from pathlib import Path
from fastapi import APIRouter, UploadFile, File, HTTPException, Depends
from app.api.v1.skills_new import _verify_internal_key

router = APIRouter(prefix="/learning", tags=["learning"])

_MAX_SIZE_BYTES = 50 * 1024 * 1024
_MAX_DURATION_S = 900
_ATTACH_DIR = Path("/var/agentprovision/workspaces/_learning")


def _ffprobe_duration(path: Path) -> int:
    out = subprocess.check_output([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(path),
    ])
    return int(float(out.decode().strip()))


@router.post("/upload-attachment")
async def upload_attachment(
    file: UploadFile = File(...),
    _auth: None = Depends(_verify_internal_key),
):
    ct = (file.content_type or "").lower()
    if not (ct.startswith("audio/") or ct.startswith("video/")):
        raise HTTPException(415, f"unsupported MIME type {ct!r}; only audio/* or video/* allowed")
    body = await file.read()
    if len(body) > _MAX_SIZE_BYTES:
        raise HTTPException(413, f"file size {len(body)} exceeds 50MB cap")
    _ATTACH_DIR.mkdir(parents=True, exist_ok=True)
    dest = _ATTACH_DIR / f"{uuid.uuid4().hex}-{file.filename}"
    dest.write_bytes(body)
    try:
        dur = _ffprobe_duration(dest)
    except Exception as e:
        dest.unlink(missing_ok=True)
        raise HTTPException(422, f"could not probe duration: {e}")
    if dur > _MAX_DURATION_S:
        dest.unlink(missing_ok=True)
        raise HTTPException(413, f"duration {dur}s exceeds 900s cap")
    return {
        "attachment_path": str(dest),
        "source_url": f"attachment://{file.filename}",
        "duration_s": dur,
        "size_bytes": len(body),
    }
```

Also: add `act_probe_attachment` activity in `learn_from_media_activities.py` that just shells out to `_ffprobe_duration` for paths already on disk (for the WhatsApp video-attachment path).

- [ ] **Step 4: Run → pass**

- [ ] **Step 5: Commit**

```bash
git switch -c impl/luna-learn-t44b-attachment-enforcement impl/luna-learn-t44-cli-flags
# ... commits
```

### Task 4.4c — Internal `POST /api/v1/learning/dispatch` endpoint TDD

**Files:**
- Modify: `apps/api/app/api/v1/learning.py`
- Test: `apps/api/tests/test_learning_dispatch.py` (new)

T4.1's `LearningService.dispatch()` is the service-layer helper; this task adds the HTTP route around it (the route the CLI in T4.3 calls).

- [ ] **Step 1-5: TDD** — request schema validation, internal-key auth gate, returns `{workflow_id}`, integration with `LearningService.dispatch`. Commit as `impl/luna-learn-t44c-dispatch-route`.

### Task 4.4d — Internal `POST /api/v1/skills/execute-draft` endpoint TDD (referenced in T2.5)

**Files:**
- Modify: `apps/api/app/api/v1/skills_new.py`
- Test: `apps/api/tests/test_skills_execute_draft.py` (new)

Currently T2.5 introduces this endpoint inline; promoting to its own task per reviewer I3.

- [ ] **Step 1-5: TDD** — request schema (skill_md + inputs), internal-key gate, executes via existing skill-execution path, returns output. Commit as `impl/luna-learn-t44d-execute-draft-route`.

### Task 4.4e — Internal `POST /api/v1/skills/install-learned` endpoint TDD (referenced in T2.6)

**Files:**
- Modify: `apps/api/app/api/v1/skills_new.py`
- Test: `apps/api/tests/test_skills_install_learned.py` (new)

Currently T2.6 introduces this endpoint inline; promoting to its own task with explicit error-code contract:
- 200 on success
- **409** on slug conflict (unique-constraint violation) — required by T3.1's `_STATUS_TO_TYPE` map
- **422** on draft parse failure
- **500** on FS write fail after DB row reserved (with DB rollback verified)
- Single transaction; FS write wrapped in try/except with explicit DB row delete on FS failure (spec §1.7 No-TOCTOU)

- [ ] **Step 1-5: TDD** — including the FS-rollback test from T3.2f.

---

## Phase 5 — Bundled skill + agent config

### Task 5.1: `_bundled/luna_learn_from_media/skill.md`

**Files:**
- Create: `apps/api/app/skills/_bundled/luna_learn_from_media/skill.md`

The orchestration template. `engine: markdown`. Tells Luna: when triggered with a `learning_intent`, dispatch `LearnFromMediaWorkflow`, ack immediately, await completion notification, then surface result per spec §2 step 8.

- [ ] **Step 1: Write the skill.md**

(Full content following the format of `apps/api/app/skills/_bundled/lead_scoring/skill.md`. Frontmatter: name, engine: markdown, category: meta, tags: [learning, video, transcription], auto_trigger description, inputs.)

- [ ] **Step 2: Manual smoke** — run `alpha skill ls` and verify it appears.

- [ ] **Step 3: Commit**

```bash
git switch -c impl/luna-learn-t51-bundled-skill impl/luna-learn-t44-cli-flags
git add apps/api/app/skills/_bundled/luna_learn_from_media/skill.md
git commit -m "feat(luna-learn): bundled meta-skill orchestration template"
git push -u origin impl/luna-learn-t51-bundled-skill
```

### Task 5.2: Luna `skill.md` + tool_groups migration — grant `learning` group

**Resolved from review I5:** Luna's effective `tool_groups` come from BOTH the bundled `skill.md` frontmatter AND a DB row keyed by `agent_id`. Migration `154_expand_luna_supervisor_tool_groups.sql` added 12 groups to her DB row (`calendar, email, drive, data, reports, bookings, monitor, jira, github, workflows, skills, ecommerce` + kept `competitor, knowledge, meta, sales, web_research, higgsfield` = 18 total). T5.2 ADDS `learning` as item 19.

**Files:**
- Modify: `apps/api/app/agents/_bundled/luna/skill.md` — add `tool_groups: [..., learning]` frontmatter (mirroring the pattern in `apps/api/app/agents/_bundled/code-reviewer/skill.md:9`)
- Create: `apps/api/migrations/<N>_luna_add_learning_tool_group.sql` — DB-side append to Luna Supervisor's `tool_groups` array (model the migration on `154_expand_luna_supervisor_tool_groups.sql`)
- Test: `apps/api/tests/test_luna_learning_tool_group.py` (new) — asserts after migration application, Luna's effective tool_groups includes `learning`

> **Migration number allocation** (review NEW-IMPORTANT-4): At branch-creation time run `ls apps/api/migrations | grep -E '^[0-9]+_' | sort -V | tail -3` to find the current max, then use max+1. If a collision appears at merge time (parallel feature also took the number), rebase + renumber + update the matching `.down.sql` and any `127_backfill_migrations_applied.sql` reference. Per `migration_apply_pattern` memory: no auto-runner in api container, apply via `docker exec psql + manual _migrations insert`; column is `filename`, NOT `name`; new `*.sql` needs `git add -f` because of global gitignore.

- [ ] **Step 1: Write failing test** that runs the migration in a test DB transaction and asserts `learning in agent.tool_groups`

- [ ] **Step 2: Run → fail** (migration doesn't exist yet)

- [ ] **Step 3: Implement** both files (frontmatter + migration) following the `154` template

- [ ] **Step 4: Run → pass** + smoke test: `alpha chat send` to Luna asking her to list available tool groups — verify `learning` appears

- [ ] **Step 5: Commit**

```bash
git switch -c impl/luna-learn-t52-agent-config impl/luna-learn-t51-bundled-skill
git add apps/api/app/agents/_bundled/luna/skill.md apps/api/migrations/NNN_luna_add_learning_tool_group.sql apps/api/tests/test_luna_learning_tool_group.py
git commit -m "feat(luna-learn): grant Luna the learning tool_group (frontmatter + DB migration)"
git push -u origin impl/luna-learn-t52-agent-config
```

---

## Phase 6 — Tests + observability

### Task 6.1: Code Reviewer stub fixture for CI hermeticity

**Files:** `apps/api/tests/conftest.py` (extend) + `apps/api/tests/fixtures/code_reviewer_stub.py` (new)

Deterministic stub that returns verdicts based on draft-content patterns (e.g., "TODO" in body → revise; "rm -rf" in body → rejected; otherwise approved).

- [ ] **Step 1-5: TDD** the stub + a smoke test using it.

```bash
git switch -c impl/luna-learn-t61-reviewer-stub impl/luna-learn-t52-agent-config
# ... commits
```

### Task 6.2: End-to-end integration test against a fixed 90s YouTube fixture

**Files:** `apps/api/tests/test_luna_learn_integration.py` (new)

Real transcription pipeline + stubbed LLM (deterministic prompt → SKILL.md fixture) + Code Reviewer stub. Asserts:
- Skill installed under `_tenant/<uuid>/<slug>/skill.md`
- `library_revisions` row exists with `actor=luna_agent_id` + `reason` includes the URL
- KG observation created with the capability list

Use a **30-second public-domain YouTube clip** (checked-in URL in `apps/api/tests/fixtures/luna_learn_urls.json`). Tagged `@pytest.mark.slow` since it hits real transcription.

- [ ] **Step 1-5: TDD** + tagging + skip-if-network-disabled.

```bash
git switch -c impl/luna-learn-t62-integration impl/luna-learn-t61-reviewer-stub
# ... commits
```

### Task 6.3: `--dry-run` golden test

**Files:** `apps/agentprovision-cli/tests/golden/learn_dry_run.txt` (new) + CLI test.

Run `alpha learn <fixture-url> --dry-run`, capture stdout, compare against checked-in golden file. Detects synthesis prompt regressions.

- [ ] **Step 1-5: TDD** + golden generation.

```bash
git switch -c impl/luna-learn-t63-dry-run-golden impl/luna-learn-t62-integration
# ... commits
```

### Task 6.4: Audio cleanup — Temporal cron schedule

**Files:**
- Modify: `apps/api/app/workers/scheduler_worker.py` (the project's cron equivalent — uses `croniter` + Temporal `Client.schedule` per existing pattern; see lines 8-9 and 31)
- Create: `apps/api/app/workflows/learning_audio_cleanup_workflow.py` (new minimal workflow)
- Test: `apps/api/tests/test_learning_audio_cleanup.py` (new)

Daily at 04:00 UTC: deletes any file in `/var/agentprovision/workspaces/_learning/` older than 24h. Handles mid-flight crashes per spec §1.12 + §3 orphan row.

- [ ] **Step 1: Write failing test** for sweep helper

```python
import time
from pathlib import Path
import pytest
from app.workflows.learning_audio_cleanup_workflow import _sweep_old_files

def test_sweep_removes_files_older_than_24h(tmp_path):
    old = tmp_path / "old.audio"; old.write_bytes(b"x")
    new = tmp_path / "new.audio"; new.write_bytes(b"x")
    old_mtime = time.time() - 25 * 3600
    new_mtime = time.time() - 1 * 3600
    import os
    os.utime(old, (old_mtime, old_mtime))
    os.utime(new, (new_mtime, new_mtime))
    deleted = _sweep_old_files(tmp_path, max_age_s=24 * 3600)
    assert deleted == 1
    assert not old.exists()
    assert new.exists()

def test_sweep_handles_missing_dir(tmp_path):
    deleted = _sweep_old_files(tmp_path / "does-not-exist", max_age_s=3600)
    assert deleted == 0
```

- [ ] **Step 2: Run → fail**

Run: `cd apps/api && pytest tests/test_learning_audio_cleanup.py -v`
Expected: import error.

- [ ] **Step 3: Implement**

```python
# apps/api/app/workflows/learning_audio_cleanup_workflow.py
import os
import time
from pathlib import Path
from temporalio import workflow, activity


def _sweep_old_files(directory: Path, max_age_s: int = 24 * 3600) -> int:
    if not directory.exists():
        return 0
    cutoff = time.time() - max_age_s
    deleted = 0
    for f in directory.iterdir():
        if f.is_file() and f.stat().st_mtime < cutoff:
            f.unlink()
            deleted += 1
    return deleted


@activity.defn
async def act_sweep_learning_audio() -> int:
    return _sweep_old_files(Path("/var/agentprovision/workspaces/_learning"))


@workflow.defn(name="LearningAudioCleanupWorkflow")
class LearningAudioCleanupWorkflow:
    @workflow.run
    async def run(self) -> int:
        return await workflow.execute_activity(act_sweep_learning_audio, start_to_close_timeout=workflow.timedelta(minutes=5))
```

Register a Temporal Schedule (per `apps/api/app/workers/scheduler_worker.py:31` pattern) firing this workflow at `0 4 * * *`.

- [ ] **Step 4: Run → pass**

- [ ] **Step 5: Commit**

```bash
git switch -c impl/luna-learn-t64-cleanup-cron impl/luna-learn-t63-dry-run-golden
# ... commits
```

### Task 6.4b: Synthesis prompt snapshot + stub-driven shellout-ban end-to-end (review I1)

**Files:**
- Create: `apps/mcp-server/tests/snapshots/synthesis_system_prompt.txt`
- Test: `apps/mcp-server/tests/test_learning_prompt.py` (new)

T2.3's tests verify regex-on-body but don't pin the synthesis PROMPT itself. The prompt is the contract — drift here silently degrades synthesis quality. Add:

1. Snapshot test asserting `SYNTHESIS_SYSTEM` from `learning_prompts.py` matches a checked-in snapshot. Manual update workflow when intentionally changed.
2. Integration test using a stub `_llm_synthesize` that intentionally tries to emit `subprocess.run(['yt-dlp', ...])` — assert real synthesize_skill_draft raises `DraftForbiddenShellout` (already exists in T2.3 but make it explicit that the stub feeds an end-to-end path, not just a unit isolation).

- [ ] **Step 1-5: TDD** the snapshot + e2e.

### Task 6.4c: PII end-to-end pipeline test

**Files:**
- Test: `apps/api/tests/test_luna_learn_pii.py` (new)

Spec §3 final row says PII detected → placeholders in body, raw transcript stays in quarantine only, `transcript_sha256` is hash of SCRUBBED transcript, KG observation embeds no transcript snippets. Plan has no test for the end-to-end PII path.

- [ ] **Step 1: Write failing test** that stubs synthesize_skill_draft to return a draft WITH `<user-name>` placeholders (simulating LLM compliance) + asserts:
  - Installed skill body contains `<user-name>` placeholders (NOT the raw name)
  - `transcript_sha256` in provenance matches sha256 of the SCRUBBED transcript variant
  - KG observation text contains capability names + source_url + skill_id but NO transcript snippet

- [ ] **Step 2-5: Implement** any scrub-side helpers needed + commit.

### Task 6.4d: WhatsApp → workflow integration test (review I7)

**Files:**
- Test: `apps/api/tests/test_whatsapp_learning_integration.py` (new)

Simulates a WhatsApp message containing a YouTube URL → asserts `_detect_inbound_media` returns `("learning_url", url, caption)` → asserts `LearningService.dispatch` is called with the right `LearningIntent` → asserts a workflow id is returned. Mocks the workflow client; doesn't run actual extraction.

- [ ] **Step 1-5: TDD** the WhatsApp-to-dispatch handoff.

### Task 6.5: Router-graph startup smoke (per `feedback_test_router_startup`)

**Files:** add to existing router-graph test.

Confirms `from app.api.v1 import routes` still imports cleanly after the new `/api/v1/skills/install-learned`, `/api/v1/skills/execute-draft`, `/api/v1/learning/dispatch` routes land.

- [ ] **Step 1-5: TDD** the import smoke.

```bash
git switch -c impl/luna-learn-t65-router-smoke impl/luna-learn-t64-cleanup-cron
# ... commits
```

---

## Phase 7 — Audit + ship

### Task 7.1: Final code review via `superpowers:code-reviewer`

Dispatch the agent against the full chained branch set vs `main`. Address every BLOCKER+IMPORTANT per `feedback_address_all_review_findings` standing rule.

- [ ] Run review
- [ ] Fix findings
- [ ] Re-run until clean

### Task 7.2: Luna runtime verification

Dispatch Luna with a real WhatsApp-style URL trigger (Simon sends a YouTube short to her). Verify the end-to-end UX:
1. Ack message appears
2. Completion notification appears within 60-90s
3. New skill appears in `alpha skill ls`
4. `alpha recall "<capability>"` surfaces the KG observation

- [ ] Run smoke
- [ ] Iterate on failure modes

### Task 7.3: Operator-facing first-time-setup doc

**Files:** `docs/operator/luna-learn-setup.md` (new)

What an operator running this on a fresh tenant needs to know:
- Code Reviewer agent (`755796a4`) must be provisioned (existing bundled agent)
- yt-dlp + ffmpeg ship in the mcp-server image automatically (no manual step)
- KG observation requires `knowledge` service health
- Recovery via `alpha learn --resume-last` when reviewer or KG temporarily unavailable

- [ ] Write doc
- [ ] Commit

### Task 7.4: Final PR — squash-merge all chained branches

Per `feedback_single_pr_for_feature`: rebase all chained branches onto `main`, squash into a single commit, open ONE PR. Avoids N build storms.

- [ ] Rebase chain
- [ ] Open PR with summary
- [ ] superpowers:code-reviewer pass
- [ ] Luna pass
- [ ] Merge

---

## §X — Skills/memories referenced

- `@superpowers:subagent-driven-development` — recommended execution mode
- `@superpowers:code-reviewer` — used in T7.1 + T7.4
- `feedback_pr_workflow` — every PR
- `feedback_address_all_review_findings` — all BLOCKER+IMPORTANT fixed in same PR
- `feedback_test_router_startup` — T6.5 router import smoke
- `feedback_test_in_chrome` — N/A (no UI in MVP per spec)
- `feedback_single_pr_for_feature` — T7.4 squash-merge
- `feedback_chain_pr_branches` — branch each phase off previous
- `feedback_delegate_to_luna` — T7.2 + parallel-with-superpowers reviews
- `feedback_verify_every_deploy` — first deploy after T7.4 merge gets explicit verification
- `alpha_chat_send_no_stream` — any Luna dispatch in T7.2 uses `--no-stream`

## §Y — Spec questions resolved here (see §0)

All 5 spec §7 open questions answered. Constraints found via Bash probes during plan-writing:
- Skills UNIQUE constraint EXISTS at migration 043
- Luna agent path is `_bundled/luna/skill.md` (spec said AGENT.md — corrected)
- Luna currently has no `tool_groups` frontmatter — T5.2 adds it
