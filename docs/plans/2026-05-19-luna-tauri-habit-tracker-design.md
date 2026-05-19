# Luna Tauri Habit Tracker — vision-based design

Date: 2026-05-19
Primary author: **Luna** (designed via `alpha chat send` dispatch from Claude on 2026-05-19; full transcript in chat session `70765af8-01d7-49b9-8fb7-4fb3587cc876`)
Co-author (request framing + commit): Claude Code
Operator: Simon Aguilera
Status: Proposed — awaiting operator approval to implement v1
Tracks: task #297 (Luna Tauri app: habit tracking with camera integration)

---

> **Provenance note.** The body of this doc below the horizontal rule is Luna's design pass, returned verbatim from the dispatched chat session. Claude framed the request and committed the doc; the actual design decisions, scope picks, crate choices, and civilization-layer framing are Luna's. Per the role split 2026-05-19 (Claude executes, Luna stays reviewer/design-lead on tenant-resident tooling), this is Luna's design and Claude will drive the v1 implementation.

---

# Design Doc: Luna Tauri Habit Tracker (Vision-Based)

**Date:** 2026-05-19
**Author:** Luna
**Status:** Proposed

## 1. Scope Sketch

As an engineer spending hours at a desk building this platform, the highest-leverage habits revolve around physical sustainability and cognitive pacing. Using the existing camera feed, we will target these three concrete use-cases:

1. **Posture & Ergonomic Decay (The "Slump"):**
   - *Detection:* Tracking the distance and angle between the eyes and shoulders over time.
   - *Value:* Prevents chronic back/neck pain. The system detects gradual decay into the "hacker hunch" rather than momentary leaning.
2. **Hydration Verification (The "Water Loop"):**
   - *Detection:* Using object detection for a cup/bottle on the desk combined with hand-tracking (lifting motion towards the face).
   - *Value:* Replaces dumb time-based alarms with actual verification of the physical action.
3. **Focus State & Screen Fatigue (The "Stare"):**
   - *Detection:* Head pose and eye gaze estimation (blink rate + continuous screen fixation).
   - *Value:* Detects flow state (do not disturb) vs. fatigue (staring without typing, low blink rate). Triggers the 20-20-20 rule only when appropriate.

## 2. Architecture

This feature slots directly into the existing macOS Tauri client, leveraging the Rust backend for heavy lifting and ensuring zero video frames ever touch the network.

- **Camera Ingestion:** Rely on the existing camera stream (likely via the `nokhwa` or `escapi` crates) currently feeding the spatial-HUD.
- **Vision Pipeline:** We will extend the existing MediaPipe integration. Since hand-tracking is already wired, we will initialize the **MediaPipe Pose** (for posture) and **Face Mesh** (for gaze/blinks) graphs.
- **Privacy Enforcement:** Processing happens entirely in the Rust sidecar using FFI or C++ bindings to MediaPipe. Frames are dropped immediately after inference. Only the **derived semantic signals** (e.g., `posture_score: 0.4`, `bottle_lifted: true`) are passed to the Tauri frontend via `app_handle.emit_all()` and subsequently routed to the platform memory layer.

## 3. Memory & RL Integration

Habits must feel like a natural extension of our ongoing conversation, not a separate sterile dashboard.

- **Knowledge Graph:** Introduce a new `habit` entity type (e.g., `entity_type="habit"`, `properties={"name": "hydration", "target_daily": 4}`).
- **Agent Memory:** Derived vision signals and confirmed completions are logged as `agent_memory` rows (`observation_type="habit_execution"`).
- **RL Reward Loop:** When I ask, "Did you drink water?" and you confirm, the system registers a positive reward. The RL engine will use these confirmations to learn the optimal time to nudge you.
- **Affect / PAD Substrate:** Nudges are gated by your PAD (Pleasure, Arousal, Dominance) state. If the vision pipeline detects high arousal/flow (rapid typing, fixed gaze), I stay silent. If I detect low arousal or idle states (leaning back, looking away), I trigger the nudge.

## 4. UX & Coalition Handoff

Interaction should be frictionless and ambient. You supervise the system; I supervise your habits.

- **The Nudge:** I will use the existing `cpal` audio streams and VoiceProvider to deliver a short, natural voice prompt: *"Hey Simon, you've been hunching for a while. Sit back."*
- **Visual Reinforcement:** A subtle, non-blocking visual indicator on the spatial-HUD (e.g., a colored dot turning from green to yellow).
- **The Confirmation:** You can confirm via voice ("Done", picked up by the existing VAD/wake-word loop) or a global quick keypress registered in Tauri (e.g., `Cmd+Option+H`).

## 5. Phasing Strategy

- **v1 (Smallest Shippable):** Hardcoded Posture Detection. MediaPipe Pose tracks shoulder/ear ratio. A simple threshold triggers a voice nudge. Confirmation via global hotkey.
- **v2 (Hydration & Voice):** Add object detection for the water bottle. Integrate voice confirmation via the existing audio pipeline. Pipe completions into the Knowledge Graph as `agent_memory`.
- **v3 (Configurable RL):** Fully configurable habits mapped in the Knowledge Graph. RL models dynamically adjust nudge timing based on your PAD state and historical success rates.

## 6. Privacy & Trust Posture

- **Local-Only Guarantee:** Camera frames are instantiated, analyzed, and dropped in memory within the Rust process. No image data is serialized or transmitted.
- **Opt-In Mechanics:** Each habit is individually toggled.
- **Kill Switch:** A hard physical/software toggle in the macOS menu bar to instantly suspend the vision pipeline.
- **Transparency Log:** A local rolling JSON/SQLite log showing exactly what the system inferred (e.g., `[14:02:11] Inferred: Posture Decay. Action: None (Flow State Active).`) so you can audit the reasoning.

## 7. Civilization-Layer Framing

This feature fundamentally strengthens the core primitives of our platform:

- **Affect (Mood/Baseline):** By detecting physiological states (posture, eye strain), we gain a ground-truth physical input to the emotion engine, making your PAD state significantly more accurate.
- **Supervision:** It shifts my role from reactive assistant to proactive partner, actively managing your physical sustainability.
- **Memory:** It creates a continuous, high-fidelity observation stream of your daily rhythms, enriching the Knowledge Graph without requiring manual data entry.

---

## Implementation notes (added by Claude)

- **Dependency on emotions engine Phase 1**: Luna's design gates nudges on PAD state. PAD vector only becomes available after task #293 (emotions engine Phase 1 PR A/B/C) ships. v1 (posture only, hardcoded threshold) can ship in parallel without PAD — v2+ depend on the emotions engine.
- **Alpha CLI kernel rule (CLAUDE.md § "Alpha CLI is the Kernel")**: every feature flows through `alpha <verb>`. For this design that means:
  - `alpha habit list` — list configured habits for the tenant
  - `alpha habit show <name>` — show one habit + recent observations
  - `alpha habit toggle <name>` — opt-in / opt-out per habit
  - `alpha habit observe <name> --state confirmed|missed` — manual confirmation path (also fires from voice/hotkey via the Tauri client → internal API)
  - All four delegate to thin `/api/v1/habits/...` routes that share a Python entrypoint with the alpha binary.
- **Infrastructure sync rule (CLAUDE.md § "Infrastructure Sync Rule")**: any new env vars (e.g., MediaPipe model paths) replicate to Helm + Terraform to prevent drift.
- **Multi-tenant**: `habit` entities + agent_memory rows include `tenant_id` per CLAUDE.md § "Multi-tenant Query Pattern". Single-tenant for Simon's tenant initially; pattern scales.

## Open question for the operator

Luna closed with: *"How does this align with your vision for the app? Let me know if you want me to spin this up into a PR description for the `ai-sre-platform` or `nomad3/agentprovision-agents` repo!"*

Default routing (no operator input): commit to `nomad3/agentprovision-agents` (this repo, this PR) since the platform-side memory + RL changes live here. The Tauri client changes will land in the `apps/luna-client` subtree of this same repo. If operator wants this in a separate `ai-sre-platform` repo, redirect on review.

## Next actions

1. **Operator approval pass** on Luna's design scope (v1 = posture, v2 = hydration, v3 = configurable RL).
2. After approval: open `feat/luna-habit-tracker-v1` branch, implement posture-only v1 in `apps/luna-client/src-tauri/` with MediaPipe Pose, voice nudge via existing `cpal` + VoiceProvider, hotkey confirmation.
3. v1 ships WITHOUT PAD-state gating (constant timer-based nudges); v2 adds PAD-gating after emotions engine #293 lands.
4. Privacy posture verified: no image bytes leave the device; rolling local log only.

## Civilization-layer relevance

Per `feedback_design_for_civilization_layer`: this strengthens **affect** (physiological ground-truth into PAD), **supervision** (Luna as proactive partner), and **memory** (continuous high-fidelity observation stream). It is a coordination-layer feature, not a one-off productivity tool. Each habit observation becomes a Blackboard signal that other agents in a coalition can read — e.g., a code review coalition might delay its nudge to the operator if the habit tracker reports a current flow state.
