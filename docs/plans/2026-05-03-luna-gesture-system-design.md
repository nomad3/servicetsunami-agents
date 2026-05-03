# Luna Gesture System — Design

**Status:** Draft
**Date:** 2026-05-03
**Owner:** Luna native client
**Branch:** `feat/luna-gesture-system`

## Goal

Make hand gestures the **primary** interaction modality for the Luna Tauri client — replacing mouse/trackpad for the things users do dozens of times a day (navigate, scroll, click, switch agents, recall memory, run workflows). Mirror Apple's trackpad grammar (**finger-count + motion**) for familiarity, then extend with Luna-specific gestures (5-finger grab, two-handed frame, hand-rotation knob, hold-pose modifiers).

Gestures must work **globally**, not only when Luna's WebView is focused. That requires a Rust-native sidecar process owning camera + MediaPipe, communicating with the React UI over Tauri's event channel.

The system is **wake-gesture activated** (open palm held 500ms) so always-on camera doesn't burn battery and doesn't fire on every random hand wave during a Zoom call.

## Non-Goals (v1)

- iOS / Android (camera & MediaPipe stack differ; defer to v2).
- Eye tracking or face-pose.
- ML-trained custom user gestures (v1 = geometric rules only; v2 may add per-user ONNX classifier).
- Replacing keyboard input — typing stays on keyboard.
- Voice (already covered by `useVoice`).

## Constraints

- Must run in the existing `apps/luna-client` Tauri 2 / React + Vite project.
- Must coexist with the existing webcam-based `GestureController.jsx` in the Spatial HUD without conflict (replace it cleanly).
- macOS ARM64 is the only target shipped via the existing CI pipeline (`.github/workflows/luna-client-build.yaml`). Linux/Windows builds may follow later but are not required to land.
- Builds happen in CI, never locally on the user's machine (per project memory `feedback_use_pipeline.md` / `feedback_no_local_builds.md`).
- Always use feature branch + PR (per `feedback_pr_workflow.md`).
- Replicate any infra changes into Helm/Terraform if they touch the API or shared services (per global CLAUDE.md).

## Architecture

```
┌──────────────────────────────────────────────────────────────────────────┐
│                         Luna Tauri 2 application                         │
│                                                                          │
│  ┌────────────────────────┐         ┌──────────────────────────────────┐ │
│  │  React WebView (UI)    │ ◀──IPC──│  Rust core (lib.rs)              │ │
│  │                        │         │   ├─ spawn/manage sidecar         │ │
│  │  GestureContext        │         │   ├─ system tray + global shortcut│ │
│  │   ├─ GestureOverlay    │         │   └─ persist bindings to disk     │ │
│  │   ├─ GestureBindings   │         └──────────┬───────────────────────┘ │
│  │   ├─ GestureCalibration│                    │ stdin/stdout JSON-lines │
│  │   └─ LunaCursor        │                    ▼                         │
│  │                        │         ┌──────────────────────────────────┐ │
│  └────────────────────────┘         │  luna-gesture-engine (sidecar)   │ │
│                                     │   ├─ camera.rs   (nokhwa)        │ │
│                                     │   ├─ mediapipe.rs (landmarks)    │ │
│                                     │   ├─ pose.rs     (geometric)     │ │
│                                     │   ├─ motion.rs   (ring buffer)   │ │
│                                     │   ├─ wake.rs     (state machine) │ │
│                                     │   ├─ recognizer.rs               │ │
│                                     │   └─ ipc.rs      (event emitter) │ │
│                                     └──────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────────────┘
                                              │
                                              ▼
                                   ┌──────────────────────┐
                                   │  Webcam (AVFoundation)│
                                   └──────────────────────┘
```

### Why a sidecar process?

1. **Survives WebView reloads.** The recognizer keeps its state (calibration, wake state) when the React app hot-reloads or navigates between Luna's main window and the Spatial HUD window.
2. **Background-capable.** Camera + landmark extraction continues when Luna is not the foreground app, which is required for "raise your palm anywhere on your desk" wake-gesture UX.
3. **Process isolation.** A native MediaPipe binding crash doesn't take down the Tauri main process or the WebView.
4. **CPU governance.** Sidecar can be paused/killed independently for the kill-switch without restarting Luna.

The sidecar binary is bundled with Luna via `tauri.conf.json` `bundle.externalBin`. Tauri spawns it via `tauri-plugin-shell` (already a dep). Communication is JSON-line over stdin/stdout; results re-emitted as Tauri events to the WebView.

### Why Rust + MediaPipe via FFI (not WebView MediaPipe)?

The existing `GestureController.jsx` runs `@mediapipe/hands` in the WebView. That's good enough for the Spatial HUD demo but has three blockers for primary input:

1. Stops when window loses focus.
2. Tied to the WebView's frame loop — drops to single-digit fps under React rerenders.
3. Cannot drive a global cursor or be the only input modality.

For the sidecar we have two viable Rust paths:

- **`mediapipe-rs`** — community Rust binding to MediaPipe Tasks. Lower risk, pure Rust, but newer / less battle-tested.
- **MediaPipe Tasks C++ via cxx FFI** — Google's official C++ runtime, bundled as a `.dylib` for macOS ARM64 in CI. Higher confidence, more setup.

We'll spike both in Phase 1 Task 1 (a half-day timebox) and pick whichever delivers stable 30fps on a Mac M4 with <12% CPU. Default to `mediapipe-rs` if it works.

## Components

### 1. Rust sidecar — `apps/luna-client/src-tauri/crates/gesture-engine/`

New cargo crate, compiled into a separate binary `luna-gesture-engine` and bundled via `tauri.conf.json`.

**`camera.rs`**
- Wraps `nokhwa` for cross-platform camera capture (uses AVFoundation on macOS).
- Exposes `CameraStream::frames() -> impl Stream<Item = Frame>`.
- Configurable resolution (default 640×480) and fps (default 30).
- Drops gracefully when sleep state turns off motion analysis (camera stays open at low fps to keep the wake-gesture detector alive).

**`mediapipe.rs`**
- Single trait `LandmarkExtractor::extract(frame) -> Vec<Hand>`.
- One impl using the runtime chosen in the spike (`mediapipe-rs` or C++ FFI).
- Returns `Hand { handedness: Left|Right, landmarks: [Landmark; 21], confidence: f32 }`.

**`pose.rs`**
- Pure-function `classify_pose(hand: &Hand) -> Pose`.
- Geometric finger-extension test: tip-to-wrist distance > pip-to-wrist distance (per finger).
- Returns `Pose` enum: `OpenPalm | Fist | Point | Peace | Three | Four | Five | ThumbUp | PinchPose | Custom(name)` plus per-finger booleans for full fidelity.

**`motion.rs`**
- 30-frame ring buffer of palm-center positions.
- Computes `Motion { kind: Swipe|Pinch|Rotate|Tap|None, direction, magnitude, velocity }` from sliding window.
- Swipe = palm-center delta exceeds threshold within 250ms with low jitter.
- Pinch = thumb-tip ↔ index-tip distance derivative.
- Rotate = palm-normal angular velocity around the wrist axis.
- Tap = pinch transition (open → closed → open) within 200ms.

**`wake.rs`**
- State machine with three states: `Sleeping`, `Arming(start_ts)`, `Armed(last_activity_ts)`.
- Transitions:
  - `Sleeping → Arming` when `OpenPalm` detected with confidence > 0.85.
  - `Arming → Armed` when `OpenPalm` held continuously for 500ms.
  - `Arming → Sleeping` if pose changes before 500ms.
  - `Armed → Sleeping` after 5s with no recognized gesture (idle timeout).
  - Force `Sleeping` on kill-switch.
- While `Sleeping`, only `pose.rs` runs (cheap). `motion.rs` + `recognizer.rs` activate on `Armed`.

**`recognizer.rs`**
- Combines `Pose` + `Motion` + `WakeState` into `GestureEvent`s.
- Emits at most 1 event per 80ms (debounce) to avoid action storms.
- Detects two-handed gestures by aligning frames where both hands are present.

**`ipc.rs`**
- JSON-lines protocol over stdout to parent Tauri process.
- Outbound events: `gesture`, `pose`, `wake_state_changed`, `engine_status` (fps, cpu, last_error).
- Inbound commands: `pause`, `resume`, `set_camera_index`, `set_calibration`, `shutdown`.

### 2. Tauri main process — `apps/luna-client/src-tauri/src/lib.rs` (extend existing)

New module `gestures.rs` with:
- `start_gesture_engine() -> Result<()>` — spawn sidecar via `tauri-plugin-shell`.
- `stop_gesture_engine()` / `pause_gesture_engine()` / `resume_gesture_engine()`.
- `gesture_engine_status() -> EngineStatus`.
- Forwards JSON-line events from sidecar stdout to Tauri events: `gesture-event`, `wake-state`, `engine-status`.
- Persists user bindings to `~/Library/Application Support/luna/gesture-bindings.json` (macOS standard path).

Tray menu additions (extending existing `setup_tray`):
- "Pause Gestures" (toggle).
- "Show Gesture Overlay".
- "Open Gesture Bindings…".
- Visible camera-active indicator dot (red while Armed, dim while Sleeping, hidden while Paused).

Global shortcut additions (extending existing `tauri-plugin-global-shortcut` block):
- `Cmd+Shift+G` — toggle pause/resume (kill-switch).
- `Cmd+Shift+B` — open Bindings page.

### 3. React frontend — `apps/luna-client/src/`

**`context/GestureContext.jsx`**
- Subscribes to Tauri `gesture-event`, `wake-state`, `engine-status` events.
- Exposes `useGesture()` hook: `{ wakeState, lastEvent, status, pause, resume }`.
- Maintains live binding registry; dispatches to handlers when `gesture-event` matches.

**`hooks/useGestureBindings.js`**
- Loads bindings from API + local file on mount.
- Provides `getBindings()`, `saveBinding(b)`, `deleteBinding(id)`, `resetToDefaults()`, `exportJson()`, `importJson(j)`.
- Conflict detection: warns if a new binding shadows an existing one at the same scope.

**`components/gestures/GestureOverlay.jsx`** (replaces existing `spatial/GestureController.jsx`)
- Translucent corner widget. Shows live skeleton (overlaid on a tiny camera ghost) + current pose name + wake state badge.
- Auto-hides after 3s of `Sleeping` to avoid screen burn.
- 160×120 by default, draggable, position persisted.

**`components/gestures/GestureBindingsPage.jsx`**
- Two-pane layout. Left = action catalog grouped by category (Memory, Navigation, Workflows, Media, Agents, Custom MCP). Right = per-binding row.
- Each row: gesture preview (animated icon) + assigned action + "Record New Gesture" button + scope toggle (global / hud-only / chat-only) + enable switch.
- Header: "Reset to Defaults", "Export JSON", "Import JSON".

**`components/gestures/GestureRecorder.jsx`** (modal opened from `GestureBindingsPage`)
- Shows live camera and skeleton.
- Prompts user "Perform the gesture now" three times. Captures pose + motion signature.
- Validates that the signature is distinguishable from existing bindings (geometric distance > threshold).
- Saves on confirm.

**`components/gestures/GestureCalibration.jsx`** (one-time onboarding)
- Step 1: Camera permission request.
- Step 2: Pose tutorial — open palm, fist, point, peace, five. Records each user's baseline landmark distances; stored as calibration JSON in `~/Library/Application Support/luna/gesture-calibration.json`.
- Step 3: Wake-gesture practice ("raise an open palm to wake Luna").
- Step 4: 5-card walkthrough of default bindings (animated previews).

**`components/luna/LunaCursor.jsx`**
- Renders virtual cursor (≈ a small luna-glow dot) when the active pose is `Point` and the engine is `Armed`.
- Cursor position derived from index-finger-tip x/y via Tauri `set_cursor_position` Rust command (uses `enigo` crate or platform API).
- Pinch (thumb+index) → simulated click via `enigo`.

**Routing & integration:**
- Add `/gestures` route to `App.jsx` rendering `GestureBindingsPage`.
- `GestureContext` provider wraps the authenticated app shell (alongside existing `VoiceProvider`).
- First-launch detection in `App.jsx` triggers `GestureCalibration` wizard.

## Data model

### `GestureEvent` (sidecar → React)

```ts
type GestureEvent = {
  id: string;            // ulid
  ts: number;            // ms epoch
  pose: Pose;
  fingers_extended: { thumb: boolean; index: boolean; middle: boolean; ring: boolean; pinky: boolean };
  motion?: {
    kind: "swipe" | "pinch" | "rotate" | "tap" | "none";
    direction?: "up" | "down" | "left" | "right" | "in" | "out" | "cw" | "ccw";
    magnitude: number;   // 0..1 normalized
    velocity: number;    // px/ms
  };
  hand: "left" | "right";
  two_handed?: { other_pose: Pose; other_hand: "left" | "right"; frame_box?: { x: number; y: number; w: number; h: number } };
  confidence: number;    // 0..1
};
```

### `Binding` (persisted)

```ts
type Binding = {
  id: string;
  gesture: {
    pose: Pose;
    motion?: { kind: string; direction?: string };
    modifier_pose?: Pose;          // optional chord (held pose modifies the primary)
  };
  action: {
    kind:
      | "memory_recall" | "memory_record" | "memory_clear"
      | "nav_chat" | "nav_hud" | "nav_command_palette" | "nav_bindings"
      | "agent_next" | "agent_prev" | "agent_open"
      | "workflow_run" | "workflow_pause" | "workflow_dismiss"
      | "approve" | "dismiss"
      | "mic_toggle" | "ptt_start" | "ptt_stop"
      | "scroll_up" | "scroll_down" | "scroll_left" | "scroll_right"
      | "zoom_in" | "zoom_out"
      | "cursor_move" | "click"
      | "mcp_tool" | "custom";
    params?: Record<string, unknown>;
  };
  scope: "global" | "hud_only" | "chat_only";
  enabled: boolean;
  user_recorded: boolean;          // true if captured via GestureRecorder
};
```

### Persistence
- Local: `~/Library/Application Support/luna/gesture-bindings.json` (canonical local copy).
- Sync: `GET/PUT /api/v1/users/me/gesture-bindings` (new endpoints, `apps/api/app/api/v1/users.py`).
- Calibration: `~/Library/Application Support/luna/gesture-calibration.json` (per-user landmark baselines, never synced).

## Default bindings

| Gesture | Action | Scope |
|---|---|---|
| Open palm hold 500ms | Wake / arm | global |
| 1-finger point + motion | `cursor_move` | global |
| Pinch (thumb+index) | `click` | global |
| 2-finger swipe ↕ | `scroll_up` / `scroll_down` | global |
| 2-finger swipe ↔ | nav prev/next message | chat_only |
| 2-finger pinch in/out | `zoom_in` / `zoom_out` | chat_only |
| 3-finger swipe ↑ | `nav_hud` | global |
| 3-finger swipe ↓ | minimize / close HUD | global |
| 3-finger swipe ←/→ | `agent_prev` / `agent_next` | global |
| 4-finger pinch in | `nav_command_palette` | global |
| 4-finger spread | hide-all (show desktop) | global |
| 5-finger grab → release | `memory_record` (current selection) | global |
| Fist | `dismiss` | global |
| Hand rotate cw / ccw | continuous knob (context-sensitive) | global |
| Two-handed frame | region-select → `summarize` MCP tool | global |
| Held fist 800ms after primary | two-step destructive confirm | global |

## Wake-gesture state machine

```
                ┌─ open_palm conf>0.85 ─┐
                ▼                        │
           ┌─────────┐                   │
           │ Sleeping│                   │
           └────┬────┘                   │
                │ (start_ts = now)       │
                ▼                        │
           ┌─────────┐ pose changes      │
           │ Arming  │── before 500ms ──▶│
           └────┬────┘                   │
                │ held 500ms             │
                ▼                        │
           ┌─────────┐                   │
           │  Armed  │                   │
           └────┬────┘                   │
                │ 5s no gesture          │
                └────── Idle timer ─────▶│
                                         ▼
                                    [Sleeping]
```

While **Sleeping** the engine runs at reduced cost: 30fps capture, pose classifier only, no motion analysis, no recognizer. CPU target <3%. The menubar dot is dim. While **Armed** the full pipeline runs and the dot is solid red.

The kill-switch (`Cmd+Shift+G` or tray) forces `Sleeping` and additionally tells `camera.rs` to release the camera handle entirely. Returning from kill-switch reopens the camera.

## Privacy & safety

- Camera frames never leave the device. Only landmark coordinates (21 floats × 3 axes × ≤2 hands) cross the IPC boundary.
- Optional anonymized landmark sync to API for recognizer improvement is **opt-in, off by default** (config flag `gesture.share_landmarks`).
- Camera-active menubar dot is always visible while the engine is running; cannot be hidden via API.
- Hard kill-switch always available via global shortcut and tray menu.
- Destructive actions (`memory_clear`, `workflow_dismiss`, `dismiss-all-notifications`) require **two-step confirm**: primary gesture + held fist 800ms even when armed. The bindings UI marks these actions with a warning badge.
- Calibration data is local-only and never synced.

## Performance targets

| Metric | Sleeping | Armed |
|---|---|---|
| CPU (Mac M4) | < 3% | < 12% |
| RAM | < 200 MB | < 350 MB |
| Landmark fps | 5 | 30 |
| Gesture → action latency p95 | n/a | < 80 ms (excluding 500 ms wake) |
| Battery overhead (8 h typical use) | — | < 8 % |

Latency budget for Armed:
- Capture frame → landmark: 18 ms (MediaPipe).
- Landmark → pose+motion: 2 ms.
- IPC + dispatch: 5 ms.
- Action handler: 5 ms.
- React rerender: ≤ 50 ms.

## Integration with existing code

- **Replaces** `apps/luna-client/src/components/spatial/GestureController.jsx`. The Spatial HUD will instead read from the same `GestureContext` so HUD and main window share one engine.
- **Coexists** with `VoiceProvider` and `useVoice`: voice and gesture are independent. Push-to-talk gesture (e.g. open-palm hold) can call `voiceStart()`.
- **Reuses** existing API endpoints for the action targets: memory (`apps/api/app/memory/recall.py`, `record.py`), workflows (`POST /workflows/{id}/run`), MCP (`POST /mcp/tools/{name}/invoke`), notifications (`PUT /notifications/{id}/read`).
- **New API endpoints** (`apps/api/app/api/v1/users.py`):
  - `GET /users/me/gesture-bindings` — fetch user's binding set.
  - `PUT /users/me/gesture-bindings` — replace user's binding set (validates schema).
- **No new database tables.** Bindings are stored as a JSON column on `users.preferences` (existing JSONB field). If `users.preferences` doesn't exist, add it via migration.

## Testing strategy

### Unit tests (Rust, `cargo test`)
- `pose.rs`: classify against fixture landmark JSON files (one per pose, recorded from real hands).
- `motion.rs`: synthetic landmark sequences for each motion kind.
- `wake.rs`: time-mocked transitions for every state edge.
- `recognizer.rs`: end-to-end against scripted landmark sequences.

### Integration tests (Rust)
- Sidecar process lifecycle: spawn, send command, receive event, shutdown.
- Camera mock: feed pre-recorded frames, assert event sequence.

### Frontend tests (Vitest)
- `GestureContext` event dispatch.
- `useGestureBindings` conflict detection.
- `GestureRecorder` signature distinguishability.

### Manual smoke tests
- Per-finger pose detection with screen recording.
- Default-bindings checklist run-through.
- Battery/CPU profile under typical 1h chat session.
- Privacy: confirm camera light off when killed; confirm menubar dot accuracy.

## Phases & milestones

### Phase 1 — Sidecar + grammar (week 1)
- **Day 1:** spike `mediapipe-rs` vs C++ FFI; pick winner.
- **Days 2–3:** `camera.rs`, `mediapipe.rs`, `pose.rs`, `motion.rs`, `wake.rs`, `recognizer.rs`, `ipc.rs`.
- **Day 4:** Tauri side `gestures.rs`, sidecar lifecycle, tray + shortcut wiring.
- **Day 5:** `GestureContext`, `GestureOverlay` (replacing existing controller). Hard-coded default bindings. End-to-end test: wake → 3-finger swipe up opens HUD.

**Exit criteria:** all default bindings work end-to-end. Sleeping <3% CPU. Armed <12% CPU. Latency <80ms p95.

### Phase 2 — Bindings UI (week 2)
- `GestureBindingsPage`, `GestureRecorder`, `useGestureBindings`.
- API endpoints + migration if needed.
- Conflict detection, scope toggles, export/import.

**Exit criteria:** user can record a custom gesture and bind it to any action; conflict warnings work; bindings sync to API.

### Phase 3 — Extensions (week 3)
- `LunaCursor` (point-pose virtual cursor + pinch click).
- Hand-rotation knob (continuous parameter binding).
- Two-handed frame for region-select → summarize MCP tool.
- `GestureCalibration` onboarding wizard.

**Exit criteria:** virtual cursor reliably tracks across full screen; rotation knob drives chat zoom and model temperature smoothly; calibration wizard reduces false-detection rate.

## Risks & mitigations

| Risk | Mitigation |
|---|---|
| MediaPipe Rust binding immature | Phase 1 day-1 spike with C++ FFI fallback |
| Camera permission UX ugly on macOS | Calibration wizard explains the prompt before triggering it |
| False wake (open palm during conversation) | 500 ms hold + confidence > 0.85 threshold; user can tune in bindings page |
| Battery drain | Sleeping at 5fps + only pose classifier; auto-disarm after 5s |
| Action storms from misclassification | 80 ms event debounce + two-step confirm for destructive actions |
| Sidecar crash | Tauri main supervises and restarts up to 3× per minute, then surfaces error toast |
| User locks themselves out of the bindings UI | Settings remain reachable via keyboard nav (`Cmd+,` → Gestures); gestures can always be paused via `Cmd+Shift+G` |

## Open questions

1. Should rotation-knob direction be inverted for left-handed users, or auto-detected from hand handedness in the Hand object? (Default: auto-detect.)
2. Should we support a "spectator mode" where gestures only highlight elements (cursor follows index-finger) without firing actions, useful for screen-share demos? (Phase 3 candidate.)
3. Should `cursor_move` use absolute mapping (full screen = full camera frame) or relative (small wrist deltas → larger cursor deltas)? (Default: absolute, with sensitivity slider.)

## Out of scope (recap)
iOS/Android, eye tracking, ML-trained classifier, voice replacement, keyboard replacement.
