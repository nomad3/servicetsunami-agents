# Luna OS — The Conductor's Podium

**Status:** Draft → executing
**Date:** 2026-05-04
**Target:** Promote the existing Spatial HUD from a `Cmd+Shift+L` subwindow into the **default startup window** of the Luna desktop client, becoming the visual cockpit for orchestrating the agent fleet — a conductor's podium for a symphony of agents.
**Branches (chained):**
- `feat/luna-os-phase-a` — The Podium (this PR)
- `feat/luna-os-phase-b` — The Score (off A)
- `feat/luna-os-phase-d` — Movements (off B)
- `feat/luna-os-gesture-extensions` — sweep-arm + two-handed (off D)
- `feat/luna-os-phase-c` — Section cues (off gesture extensions)
- `feat/luna-os-phase-e` — Two-handed conducting (off C)

## Vision: the conductor metaphor

| Concept | Maps to |
|---|---|
| Conductor | The user |
| Orchestra | Agent fleet (Luna, Sales, Code, HealthPets, Marketing, Memory) |
| Sections | Teams (existing `agent_groups`) |
| Score | A running workflow (existing `dynamic_workflows.WorkflowRun`) |
| Ensemble | An A2A coalition (existing `CoalitionWorkflow`) |
| Soloist | One agent in the spotlight |
| Repertoire | Installed skills + workflow templates (existing) |
| Movements of the day | Morning overture (briefing) → focused work → finale (review) |

The user does **not** compose new music here (no authoring on the podium — `/workflows/builder` keeps that role). The user does **not** play instruments (agents do). The user **shapes the performance in real-time**: brings sections in, pushes tempo, spotlights a soloist, cuts a passage.

## Conducting grammar (gesture verbs)

Building on the gesture engine shipped in PRs #276/#279/#281. Most verbs already work; sweep-arm + two-handed land in `feat/luna-os-gesture-extensions`.

| Gesture | Conducting verb | Effect | Status |
|---|---|---|---|
| Open palm hold (500ms) | Orchestra to attention | Wake / arm; scene fades sleeping → armed | shipped |
| Point + voice | "You" | Cursor lands on agent; voice becomes their task | shipped |
| Pinch (thumb+index) | Tap baton | Click / commit | shipped |
| 3-finger swipe | Scrub through movements | Navigate score history | shipped |
| 4-finger pinch | Open comms panel | Summon chat overlay | shipped |
| Fist | Cut | Cancel / dismiss active passage | shipped |
| Hand rotate cw/ccw | Tempo | Adjust workflow priority knob | shipped |
| Sweep arm toward section | Bring section in | Dispatch to a team | new (Phase 4 gesture ext) |
| Both hands rising | Crescendo | More agents / raise priority | new (two-handed) |
| Both hands falling | Diminuendo | Defer / lower priority | new (two-handed) |
| Two hands framing | Region encompass | Multi-select agents / workflows | new (two-handed) |

## Visible surface (Phase A — what ships first)

```
           ◐  Inbox melody — flowing strip of overnight outputs
   ╔════════════════════════════════════════════════════════╗
   ║   ★  Knowledge Nebula (existing, drifting in background) ║
   ║                                                          ║
   ║       ◯ Sales      ◯ Code       ◯ HealthPets          ║
   ║      (3 agents)   (1 agent)     (2 agents)             ║
   ║                                                          ║
   ║          ◯ Memory       ◯ Marketing                    ║
   ║         (1 agent)        (2 agents)                     ║
   ║                                                          ║
   ║                    ◉ Luna (you)                          ║
   ╚════════════════════════════════════════════════════════╝
              (your podium — first-person view)
```

## Phase A scope — The Podium

### What ships

1. **Default window flip** — `spatial_hud` becomes the visible-on-launch window. The existing narrow chat-panel `main` window becomes a secondary callable surface (tray menu / `Cmd+1` / `gesture_action: nav_chat`).
2. **Section clusters** — agents arranged in soft rings by their `agent_group`. Each agent rendered as an avatar with a status halo.
3. **Live status halos** — pulse intensity = activity over the last 5 minutes. Driven by `agent_performance_snapshots` (hourly) + Temporal worker queue depth (live).
4. **A2A comms beams** — when a coalition is running (existing `CoalitionWorkflow` SSE feed at `/collaborations/stream`), beams light up between participating agents.
5. **Inbox melody** — horizontal strip at the top of the scene flowing recent notifications + commitments. Items glow when new.
6. **Knowledge Nebula in background** — already shipped; lightly dimmed so it sits behind the orchestra rather than dominating.
7. **Comms panel summon** — 4-finger pinch (existing) opens chat overlay; fist (existing) dismisses.
8. **Wake animation** — open palm hold transitions scene from dim/distant to close/lit. Auto-dims back to "sleeping" after the existing 5s idle.
9. **Point + voice dispatch** — gesture cursor on an agent → voice → task spawns through existing `agent_tasks` POST.

### Reuse map

| Concern | Reuse |
|---|---|
| Window | Existing `spatial_hud` Tauri window — promote to default visible |
| Scene | Existing Three.js + `@react-three/fiber` + `@react-three/postprocessing` bloom + KnowledgeNebula |
| Gestures | Existing GestureProvider / useGesture / Rust engine (PR #276, #279) |
| Auth | Token refresh shipped (PR #281) — the conductor stays logged in indefinitely |
| Live A2A | Existing `/collaborations/stream` SSE |
| Live agent status | Existing `agent_performance_snapshots` table + `/agents/discover` |
| Notifications + commitments | Existing `/notifications` + `/commitments` endpoints |
| Task dispatch | Existing `agent_tasks` POST + `TaskExecutionWorkflow` |
| Section grouping | Existing `agent_groups` model |
| Voice | Existing `useVoice` + `/media/transcribe` |

### New for Phase A

**Frontend (`apps/luna-client/src/`):**
- `components/spatial/Podium.jsx` — scene root that arranges sections in a fan around the user's viewpoint
- `components/spatial/SectionCluster.jsx` — one team's avatars in a soft ring
- `components/spatial/AgentAvatar.jsx` — single agent dot with halo, name label, status color
- `components/spatial/InboxMelody.jsx` — flowing strip at the top of the scene
- `components/spatial/CommsBeam.jsx` — animated beam between two agent positions during A2A
- `components/spatial/PodiumScene.jsx` — composes the above into a Three.js scene
- `hooks/useFleetSnapshot.js` — initial fleet load from `/fleet/snapshot`
- `hooks/useFleetStream.js` — subscribes to live SSE updates (collab events + status snapshots)
- `hooks/useDispatchOnPoint.js` — handles point + voice → spawn task on the targeted agent

**API (`apps/api/`):**
- `app/api/v1/fleet.py` — new `GET /fleet/snapshot` aggregator returning `{ agents, groups, recent_collaborations, recent_notifications, recent_commitments }` in one shot for fast (<1s) podium boot
- `app/services/fleet_snapshot_service.py` — assembles the snapshot from existing models (no new tables)

**Tauri wiring (`apps/luna-client/src-tauri/`):**
- `tauri.conf.json` — flip `spatial_hud.visible` to `true`, demote `main.visible` to `false` (still openable via tray + `gesture_action: nav_chat`)
- `src/lib.rs` — expose `open_main_window` Tauri command for the comms-panel summon path

### Boot performance target

Podium first paint < 1.5s on a Mac M4:
- DNS / TLS / API auth: ~150ms (Cloudflare tunnel + cached token from refresh)
- `GET /fleet/snapshot`: ≤ 300ms (single query joining agents + groups + last hour of collab + notifications + commitments)
- Three.js scene mount + initial layout: ≤ 500ms
- Gesture engine warm-up (already running before user wakes): n/a — runs in background
- First halo render: ≤ 100ms after data lands

### Privacy & safety

- Camera: same gates that already shipped — Apple Vision FFI runs in the existing engine, gated by wake state, killable via `Cmd+Shift+G`. The promotion to default window does NOT change camera lifecycle.
- Always-visible menubar dot already shipped; users always know when the camera is armed.
- The narrow `main` window is reachable via tray + voice + gesture so users without a working camera have a fallback.

### Non-goals (explicit)

- Authoring new agents or workflows on the podium. Use `/workflows/builder` and `/agents`.
- Replacing macOS shell. Luna OS is a fullscreen surface, not a desktop environment.
- Mobile. Tauri 2 mobile build is parked.
- Linux / Windows. macOS ARM64 only for v1; gesture engine is Apple Vision-bound.

## Phasing summary

| Phase | Adds | Effort | Branch off |
|---|---|---|---|
| **A — The Podium** | Default-window flip, sections, halos, beams, melody, dispatch | ~1 week | `main` |
| **B — The Score** | Workflow runs as flowing graphs across the floor; pinch to inspect a step | ~5 days | `feat/luna-os-phase-a` |
| **D — Movements** | Morning overture briefing animation, evening finale review | ~3 days | `feat/luna-os-phase-b` |
| **Gesture extensions** | Sweep-arm motion classifier; two-handed gesture support; `tip_xy` already shipped | ~5 days | `feat/luna-os-phase-d` |
| **C — Section cues** | Sweep-arm-toward-section gesture dispatches to a team | ~3 days | gesture extensions |
| **E — Two-handed conducting** | Crescendo / diminuendo / region-encompass | ~5 days | `feat/luna-os-phase-c` |

Each phase ships independently as a PR. Branches are chained per the project memory `feedback_chain_pr_branches.md` to avoid merge cascades on the shared scene file.

## Risks & mitigations

| Risk | Mitigation |
|---|---|
| First boot blank if `/fleet/snapshot` is slow or fails | Render the scene immediately with empty sections; populate as data arrives |
| Camera permission prompt fires on first launch and dominates the screen | Calibration wizard (already shipped) handles this on day 1; Phase A inherits |
| User without working camera can't reach functionality | Tray menu + `Cmd+1` summons the chat-panel `main` window |
| Spatial scene blocks chat keyboard focus when comms panel summoned | Existing 4-finger pinch already toggles; ensure typing focus lands in the panel |
| Performance on older Macs | Bloom postprocessing already gated by perf checks in KnowledgeNebula; reuse |

## Success criteria for Phase A

- Luna boots into the podium within 1.5s on a Mac M4
- All tenant agents appear in their correct section clusters (driven by `agent_groups`)
- An agent halo pulses with intensity proportional to last-24h activity (`agent_performance_snapshots.invocation_count`)
- A2A comms beams render for active blackboards (status='active', updated within 1h) with participants derived from distinct `BlackboardEntry.author_agent_slug` values
- Inbox melody surfaces high-priority notifications first, then medium, then low (CASE-ranked, not alphabetic)
- Wake gesture transitions scene from dim to lit (CSS opacity); idle 5s reverses
- Comms panel summons cleanly with 4-finger pinch; fist dismisses
- Original chat-panel `main` window still reachable via tray + `Cmd+Shift+L`; nothing in the broader app workflow is broken
- Live updates: 60-second polling refresh of `/fleet/snapshot` (Phase B replaces with tenant-wide SSE)

## Phase A deferrals (called out so we don't pretend they ship)

- **Voice dispatch on point** — the `useDispatchOnPoint` hook is wired to fire on `luna-podium-target-agent` + `luna-podium-voice-text` events, but no voice hook in the current Luna client emits the second one. Voice dispatch ships in Phase B alongside the Score zone, when we wire `useVoice` against the existing `/media/transcribe` endpoint per `luna_client_voice_pattern.md`.
- **Tenant-wide live SSE** — Phase A polls `/fleet/snapshot` every 60s. The existing `/collaborations/{session_id}/stream` SSE is per-session and Bearer-only (EventSource can't pre-flight headers). A tenant-scoped feed lands in Phase B.
- **Knowledge Nebula behind the orchestra** — the existing nebula owns its own Canvas; refactoring it to nest inside another scene is Phase B+ work. Phase A uses `<Stars/>` from drei as the background.
