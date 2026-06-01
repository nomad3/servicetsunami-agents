# Reel Advice Platform Adoption Plan

**Date:** 2026-05-30
**Status:** Draft
**Source:** Instagram reel URL supplied by Simon: `https://www.instagram.com/reel/DY4olq-R_2E/`
**Related:** `docs/superpowers/specs/2026-05-25-luna-learn-from-media-design.md`, `docs/superpowers/plans/2026-05-25-luna-learn-from-media-plan.md`

## 1. Context

Simon asked how AgentProvision should follow the advice from an Instagram reel
and requested a repo-native plan plus PR.

Important constraint: the reel content was not accessible from this worker
session, so this plan does **not** claim the specific advice from the clip.
The first implementation gate is to extract or receive the transcript, then
turn only transcript-backed advice into platform behavior. This keeps Luna's
anti-hallucination contract intact: no product change should be justified by a
video we have not actually read.

## 2. Goal

Create a repeatable platform pattern for turning short-form advice into
operational improvements:

1. Capture the source and transcript.
2. Extract the concrete advice as structured claims.
3. Classify each claim by platform area.
4. Convert approved claims into product, agent, or workflow changes.
5. Track adoption and outcome metrics.

For this reel, the desired output is not just a summary. The desired output is a
decision-ready implementation backlog for AgentProvision/Luna.

## 3. Product Principle

Short-form advice should become a **reviewable operating card**, not an
unverified instruction.

Each advice item must carry:

- `source_url`
- `transcript_sha256`
- exact transcript excerpt or timestamp range
- interpreted advice
- affected platform surface
- proposed change
- risk level
- owner
- acceptance criteria
- verification path

This gives Luna a way to learn aggressively without weakening evidence,
provenance, or review discipline.

## 4. Use Existing Substrate

The platform already has the right foundation in Luna Learn from Media:

- WhatsApp or CLI media/link trigger
- `LearnFromMediaWorkflow`
- extract/transcribe/synthesize/review/test/install flow
- tenant library install path
- knowledge-graph diffusion
- cache/quarantine behavior for failed or incomplete learning jobs

For this request, do **not** build a separate Instagram ingestion path. Extend
the existing learning flow with an "advice adoption" output mode.

## 5. Proposed Flow

### Phase 0 — Source Capture

Input:

```text
source_url = https://www.instagram.com/reel/DY4olq-R_2E/
intent = advice_adoption
```

Behavior:

- Try the existing media extraction path.
- If Instagram blocks extraction, ask Simon to upload the reel file or paste the
  transcript.
- Cache the job under the normal learning cache key.
- Do not synthesize any platform change until transcript exists.

Acceptance criteria:

- Failed extraction returns a clear recovery instruction.
- A transcript or uploaded file can resume the same job.
- The source URL and transcript hash are persisted in the learning job state.

### Phase 1 — Advice Extraction

Add an advice-specific synthesis pass after transcription:

```json
{
  "source_url": "...",
  "transcript_sha256": "...",
  "advice_items": [
    {
      "id": "advice-001",
      "excerpt": "...",
      "interpretation": "...",
      "confidence": 0.0,
      "platform_area": "agent_response_latency | visibility | onboarding | sales | safety | ux | unknown",
      "risk": "low | medium | high",
      "recommended_action": "..."
    }
  ]
}
```

Rules:

- Each item must cite a transcript excerpt or timestamp.
- If the clip is motivational but not operational, produce a `no_action`
  recommendation instead of forcing a feature.
- If an item requires a business assumption, mark it `needs_operator_decision`.

Acceptance criteria:

- No advice item can be emitted without source evidence.
- Items are classified into platform areas.
- Medium/high-risk items require review before implementation.

### Phase 2 — Adoption Review

Route extracted advice through a lightweight review gate:

- Luna Supervisor checks whether the interpretation is faithful to the source.
- Code Reviewer checks whether proposed implementation touches risky code paths.
- Simon remains the final approver for roadmap tradeoffs.

Recommended review outcomes:

- `adopt_now`: small docs/config/workflow change
- `backlog`: useful but not urgent
- `reject`: not applicable to AgentProvision
- `needs_more_context`: requires transcript clarification or business decision

Acceptance criteria:

- The PR body lists every advice item and its review outcome.
- Rejected items are explicitly explained.
- Adopted items include verification steps.

### Phase 3 — Platform Surfaces

Map advice into one of these existing surfaces before adding new ones:

| Advice type | Preferred platform surface |
|---|---|
| Faster replies | `apps/code-worker`, `alpha run`, Temporal async patterns, streaming/status UX |
| Better visibility | dashboard events, audit trails, task status cards, operator summaries |
| Better user trust | Luna response rules, provenance, failure transparency, review gates |
| Better onboarding | bundled skills, `docs/operator/`, CLI help, WhatsApp first-run flow |
| Better execution quality | code-reviewer routing, synthetic tests, metacognition traces |
| Better learning | `luna_learn_from_media`, skill registry, knowledge diffusion |

This prevents the platform from accumulating one-off features that bypass the
architecture already built.

### Phase 4 — Implementation Backlog

Once the reel transcript is available, create a follow-up implementation PR with
only transcript-backed items. Likely task shapes:

- Add `intent = "advice_adoption"` to the learning workflow input schema.
- Add an `AdviceAdoptionCard` schema in the API layer.
- Add a synthesis prompt section that emits advice cards instead of a SKILL.md
  when the user asks "how do we follow this advice?".
- Persist advice cards in the learning cache/quarantine path.
- Add CLI surface:

```bash
alpha learn <url> --advice-plan
```

- Add WhatsApp behavior:

```text
User sends reel + "how can we follow this?"
Luna replies immediately: "I need the transcript first; I will extract it or ask for upload if IG blocks it."
```

- Add tests for:
  - inaccessible Instagram URL recovery
  - transcript-required gate
  - no-evidence/no-action behavior
  - advice item classification
  - review outcome serialization

## 6. Metrics

Track whether advice adoption is actually improving the platform:

- Time from URL received to transcript ready.
- Time from transcript ready to adoption plan.
- Count of advice items by outcome: adopted, backlogged, rejected.
- Count of advice items later converted into PRs.
- User satisfaction signal after adopted advice is shipped.
- Regression rate for advice-driven PRs.

## 7. Risks

| Risk | Mitigation |
|---|---|
| Instagram anti-scrape blocks extraction | Support direct upload and pasted transcript resume path |
| Advice is vague or motivational | Emit `no_action` instead of inventing implementation work |
| Advice conflicts with existing architecture | Route through review and map to existing platform surfaces first |
| Advice causes scope creep | Require owner, acceptance criteria, and verification for every item |
| Hallucinated interpretation | Require transcript excerpt or timestamp for every advice item |

## 8. Immediate Next Step

Run the reel through Luna Learn from Media once the production extractor or a
direct upload is available. If extraction fails, Simon should send the reel file
or transcript in the same thread. After transcript capture, open a second PR
containing the concrete implementation tasks tied to the actual advice.

Until then, this PR intentionally ships only the adoption framework.
