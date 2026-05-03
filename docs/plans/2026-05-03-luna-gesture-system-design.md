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
┌─────────────────────────────────────────────────────────────────────────┐
│                       Luna Tauri 2 application (single process)         │
│                                                                         │
│  ┌────────────────────────┐         ┌─────────────────────────────────┐ │
│  │  React WebView (UI)    │ ◀──IPC──│  Rust main (lib.rs)             │ │
│  │                        │  Tauri  │                                 │ │
│  │  GestureContext        │  events │   gesture-engine module         │ │
│  │   ├─ GestureOverlay    │         │     ├─ camera.rs    (owner)     │ │
│  │   ├─ GestureBindings   │         │     ├─ landmark.rs  (Vision/MP) │ │
│  │   ├─ GestureCalibration│         │     ├─ pose.rs                  │ │
│  │   ├─ GestureRecorder   │         │     ├─ motion.rs                │ │
│  │   └─ LunaCursor        │         │     ├─ wake.rs                  │ │
│  │                        │         │     ├─ recognizer.rs            │ │
│  │                        │         │     ├─ supervisor.rs (restart)  │ │
│  │                        │         │     └─ cursor.rs (enigo, gated) │ │
│  └────────────────────────┘         │   tray + global shortcuts       │ │
│                                     │   bindings persistence          │ │
│                                     │   spatial-frame consumer feed   │ │
│                                     └────────────────┬────────────────┘ │
└──────────────────────────────────────────────────────┼──────────────────┘
                                                       │
                                                       ▼
                                          ┌────────────────────────┐
                                          │  Webcam (AVFoundation) │
                                          └────────────────────────┘
```

### In-process, not separate sidecar binary

Earlier drafts proposed a separate `luna-gesture-engine` binary spawned via `tauri-plugin-shell` and bundled as `externalBin`. We **reject** that for v1 because:

- macOS code-signing and notarization cost: every external binary needs its own signature, hardened runtime, and entitlements; `.github/workflows/luna-client-build.yaml` already signs only the main app bundle.
- The "background-capable" requirement is satisfied by any code that runs outside the WebView — a Rust thread inside the main Tauri process already runs continuously, regardless of WebView focus.
- Process isolation buys little: a MediaPipe panic that takes down the engine thread can be recovered by the supervisor (`supervisor.rs`); a panic that takes down the whole process is recoverable by the user re-launching Luna, which is acceptable.

The gesture engine is a **Rust module inside `apps/luna-client/src-tauri/`** running on a dedicated Tokio task. It emits `gesture-event`, `wake-state`, and `engine-status` directly via Tauri's event channel to the WebView. No stdin/stdout, no extra binary, no extra signing.

### Camera ownership: gesture engine is the sole owner

The existing `lib.rs` `start_spatial_capture` (lines 127–164) is a **synthetic placeholder** — it emits fake `SpatialFrame` events on a 60Hz timer and does **not** open the camera today. Multi-owner camera access is a non-issue for now.

In v1 we redefine ownership cleanly:

- `gesture-engine/camera.rs` is the sole AVFoundation client.
- `start_spatial_capture` is rewritten to be a **consumer**: it subscribes to the same frame stream the engine processes, downsamples to 30Hz, and emits `spatial-frame` for the HUD scene. No second camera handle is opened.
- A single Tauri command `set_camera_index(i)` controls which device the engine binds to; both the engine and `start_spatial_capture` share that selection.

This way the green camera light never flickers from ownership churn, and removing/replacing `start_spatial_capture`'s placeholder code is part of Phase 1.

### Landmark runtime — Apple Vision first, MediaPipe second

The existing `GestureController.jsx` uses `@mediapipe/hands` in the WebView. It's fine for HUD demos but has three blockers for primary input:

1. Stops when the window loses focus.
2. Tied to the WebView's frame loop — drops to single-digit fps under React rerenders.
3. Cannot drive a system cursor or be the only input modality.

For the native engine we have **three** candidate runtimes, evaluated in a 2-day spike (Phase 1 days 1–2):

- **a. Apple Vision (`VNDetectHumanHandPoseRequest`)** via Swift FFI — *recommended primary*. Native to macOS, hardware-accelerated on M-series, no model files to ship, no additional notarization, lowest CPU. Returns 21 landmarks per hand. **Risk:** Swift↔Rust FFI plumbing.
- **b. `mediapipe-rs`** — pure-Rust community binding. **Risk:** hand-landmarker task on macOS ARM64 has not been validated at 30fps with the CPU budget.
- **c. MediaPipe Tasks C++ via `cxx` FFI** — Google's official runtime, ships a `.dylib` (universal2/arm64) plus `.task` model files (~12 MB). **Risk:** binary size, signing, app-bundle layout, model load time (~300ms cold).

**Spike pass criteria** (one runtime must satisfy all):
- Sustained 30 fps landmark extraction on a Mac M4 with both hands visible.
- ≤ 12% CPU averaged over a 60-second armed session.
- ≤ 50 ms p95 frame-to-landmarks latency.
- App bundle size growth ≤ 25 MB.
- Clean shutdown (no leaked threads, camera released within 200 ms of `pause`).

**Decision tree:**
1. If (a) passes → use Apple Vision; (b) and (c) are not pursued in v1.
2. If (a) fails on FFI complexity but the recognition works → still use (a), invest the FFI time.
3. If (a) fails on accuracy → fall back to (c) MediaPipe C++ (more proven than (b)).
4. If (a) and (c) both fail → escalate to the user; do not silently fall back to WebView MediaPipe (defeats the purpose).

## Components

### 1. Rust gesture-engine module — `apps/luna-client/src-tauri/src/gesture/`

Module inside the existing `luna_lib` crate. Owns one Tokio task pool. No separate binary.

**`camera.rs`**
- Wraps `nokhwa` for AVFoundation camera capture on macOS (Linux/Windows deferred).
- Exposes `CameraStream::frames() -> impl Stream<Item = Frame>` and `list_cameras() -> Vec<CameraInfo>`.
- Configurable resolution (default 640×480) and fps (default 30, drops to 5 while Sleeping).
- Hot-plug recovery: if the bound camera disappears, the stream emits `CameraEvent::Disconnected`; supervisor re-binds to default device or surfaces an `engine-status` error.
- The same `Frame` stream is fanned out to two consumers: the landmark extractor and the HUD's `spatial-frame` emitter (downsampled).

**`landmark.rs`**
- Single trait `LandmarkExtractor::extract(frame) -> Vec<Hand>`.
- v1 impl is whichever runtime won the spike (Apple Vision via Swift FFI, MediaPipe C++ FFI, or `mediapipe-rs` — see "Landmark runtime" section).
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
- **Confirm-window timer extension:** when a binding requires a two-step confirm (destructive actions), the recognizer arms a `ConfirmPending` substate within `Armed`. While `ConfirmPending`, the wake-state idle timer is suspended (idle countdown does not advance) until either the confirm fist completes (800ms hold) or the user releases the pose. This prevents the disarm-mid-confirm race called out in the review.

**`supervisor.rs`**
- Owns engine lifecycle: spawn the Tokio task, watch for panics, restart with bounded budget.
- **Restart policy:** at most **3 restarts per Luna session** (not per minute). After exhaustion, surface a persistent error in the menubar dot (cross-out icon) and emit a `engine-status: { state: "fatal", reason }` event. The user must manually re-enable from the tray menu.
- Each restart releases and re-acquires the camera handle; the menubar dot dims briefly during this window.

**Engine API (Tauri commands + events)**
- Outbound Tauri events: `gesture-event`, `pose-changed`, `wake-state-changed`, `engine-status` (fps, cpu, last_error).
- Inbound Tauri commands: `gesture_pause`, `gesture_resume`, `gesture_set_camera_index`, `gesture_list_cameras`, `gesture_set_calibration`, `gesture_shutdown`.

**`cursor.rs`**
- Owns `enigo` for cursor moves and synthetic clicks, **gated** behind:
  1. macOS Accessibility permission (`AXIsProcessTrusted` check at startup).
  2. A user-controlled `cursor_global_mode` flag (default **off**).
- When `cursor_global_mode` is **off**, cursor/click events are no-ops if Luna or the Spatial HUD is not the frontmost app (checked via `NSWorkspace.frontmostApplication`).
- When `cursor_global_mode` is **on**, cursor/click drive the system cursor over any frontmost app — explicit user opt-in only, with an in-app warning shown the first time the toggle is enabled ("Pinch click will fire in whatever app is in front of you").
- If Accessibility permission is denied, all `cursor_move`/`click` bindings are disabled in `useGestureBindings` and shown with a "permission required" badge in `GestureBindingsPage`. The rest of the system continues to work.

### 2. Tauri main wiring — `apps/luna-client/src-tauri/src/lib.rs` (extend existing)

The gesture engine runs as a Tokio task started during the existing `tauri::Builder::default()...setup` closure (alongside `setup_tray` and audio capture). New Tauri commands:

- `gesture_start()` / `gesture_stop()` — spawn or join the engine task.
- `gesture_pause()` / `gesture_resume()` — soft-disable (kill-switch); `pause` releases the camera handle.
- `gesture_status() -> EngineStatus`.
- `gesture_list_cameras() -> Vec<CameraInfo>`.
- `gesture_set_camera_index(i: usize)`.

Persistence:
- Bindings: `~/Library/Application Support/luna/gesture-bindings.json` (canonical local copy, written atomically via `tempfile::persist`).
- Calibration: `~/Library/Application Support/luna/gesture-calibration.json` (local-only, never synced).

**Migration of existing `start_spatial_capture`:** the synthetic-frame placeholder in `lib.rs` lines 127–164 is rewritten to subscribe to `gesture-engine`'s frame fan-out instead of running its own 60 Hz timer. Dependency direction: `lib.rs` → `gesture` module owns camera; `start_spatial_capture` becomes a thin consumer that emits `spatial-frame` from real frames.

Tray menu additions (extending existing `setup_tray`):
- "Pause Gestures" (toggle, `Cmd+Shift+G` mirror).
- "Show Gesture Overlay".
- "Open Gesture Bindings…".

**Camera indicator dot — three states (always visible while engine is not Paused):**

| State | Visual | Meaning |
|---|---|---|
| Paused | hidden | engine off, camera released |
| Sleeping | dim outline (no fill) | camera open at 5fps, only wake detector active |
| Armed | solid red | camera open at 30fps, full recognition active |
| Fatal | red cross-out | supervisor exhausted restart budget; click to re-enable |

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
- Step 1: Camera permission request, with explanation copy before the OS prompt fires.
- Step 2: Camera selection (built-in FaceTime by default; `gesture_list_cameras` for alternates).
- Step 3: **Accessibility permission** — only requested if the user enables a cursor binding now or later. Wizard shows a "Skip" option so non-cursor users aren't blocked. Cursor bindings are gated by an `AXIsProcessTrusted` runtime check; when denied, the bindings UI marks them "permission required" with a button to open System Settings → Privacy & Security → Accessibility.
- Step 4: Pose tutorial — open palm, fist, point, peace, five. Records each user's baseline landmark distances; stored as calibration JSON in `~/Library/Application Support/luna/gesture-calibration.json`.
- Step 5: Wake-gesture practice ("raise an open palm to wake Luna").
- Step 6: 5-card walkthrough of default bindings (animated previews).

**`components/luna/LunaCursor.jsx`**
- Renders an in-app virtual cursor overlay (luna-glow dot) when the active pose is `Point` and the engine is `Armed` — used as visual feedback regardless of the cursor permission state.
- The system cursor itself is moved by Rust `cursor.rs` via `enigo`, **not** by React. Cursor frames bypass `GestureContext` and React rerenders entirely; landmark → `set_cursor_position` happens inside the engine's recognizer task to hit the <16ms tip-to-cursor target.
- Pinch (thumb+index) → simulated click via `enigo`, gated by the same Accessibility + frontmost-app rules described in `cursor.rs`.

**Required Info.plist entries** (added to `tauri.conf.json` macOS bundle config):
- `NSCameraUsageDescription` (already required) — "Luna uses your camera to recognize hand gestures."
- `NSAccessibilityUsageDescription` — "Luna uses Accessibility access to move the cursor and click via hand gestures."

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
  scope: "global" | "luna_only" | "hud_only" | "chat_only";
  enabled: boolean;
  user_recorded: boolean;          // true if captured via GestureRecorder
};
```

### Persistence
- Local: `~/Library/Application Support/luna/gesture-bindings.json` (canonical local copy, atomic write).
- Sync: `GET/PUT /api/v1/users/me/gesture-bindings` (new endpoints, `apps/api/app/api/v1/users.py`).
- Calibration: `~/Library/Application Support/luna/gesture-calibration.json` (per-user landmark baselines, never synced).

### Server-side schema & migration

Verified `apps/api/app/models/user.py` has **no** `preferences` column today (fields: id, full_name, email, hashed_password, is_active, is_superuser, password_reset_token, password_reset_expires, tenant_id). Latest migration is `113`.

Decision: add a **dedicated table** `user_gesture_bindings` rather than overload `User` with a JSONB grab-bag. This keeps the model focused and avoids a future "what else lives in `preferences`?" debate.

Migration `apps/api/migrations/114_user_gesture_bindings.sql`:

```sql
CREATE TABLE user_gesture_bindings (
  user_id    UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  bindings   JSONB NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT bindings_size_cap CHECK (octet_length(bindings::text) <= 65536)
);
```

Down migration `114_user_gesture_bindings.down.sql` drops the table.

### API endpoints (`apps/api/app/api/v1/users.py`)

- `GET /api/v1/users/me/gesture-bindings` → `{ bindings: Binding[] }`.
- `PUT /api/v1/users/me/gesture-bindings` body `{ bindings: Binding[] }` → 204.

Hardening (consistent with the 2026-04-18 security posture):
- **Pydantic schema validation** mirroring the TS `Binding` type. Reject unknown action kinds, unknown poses, and out-of-range `magnitude`/`velocity`.
- **Payload cap 64 KB** (also enforced by the DB CHECK constraint).
- **Rate limit via `slowapi`:** PUT 10/min per user, GET 60/min per user — consistent with existing route limits.
- **Authentication:** standard `deps.get_current_user`. No tenant-cross access (user-scoped only).

## Default bindings

| Gesture | Action | Scope |
|---|---|---|
| Open palm hold 500ms | Wake / arm | global |
| 1-finger point + motion | `cursor_move` | luna_only by default; `global` requires opt-in to "global cursor mode" + Accessibility |
| Pinch (thumb+index) | `click` | luna_only by default; same opt-in for global |
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
| Battery overhead (8 h typical use) | — | < 8 % |

**Two distinct latency budgets**, because they have different paths through the system:

### Discrete gesture → action (with React in the loop)

For one-shot gestures like 3-finger swipe, pinch-to-zoom, 5-finger grab — these route through Tauri events, `GestureContext`, and a React handler that calls into the API or navigates. **Target: < 80 ms p95 (excluding the 500 ms wake hold).**

| Stage | Budget |
|---|---|
| Capture frame → landmark | 18 ms |
| Landmark → pose+motion classify | 2 ms |
| Recognizer debounce + dispatch (Tauri event) | 5 ms |
| `GestureContext` lookup + handler call | 5 ms |
| Action handler (API or nav) | ≤ 50 ms |

### Continuous tracking → cursor (Rust direct, **bypasses React**)

`cursor_move` is driven from `cursor.rs` inside the engine task, calling `enigo` directly on every armed frame. Tauri events and `GestureContext` are **not** in this path; React only renders the on-screen luna-glow overlay as decorative feedback. **Target: < 16 ms p95 tip-to-system-cursor (60 fps perceptual).**

| Stage | Budget |
|---|---|
| Capture frame → landmark | 12 ms |
| Index-tip x/y → smoothed cursor coords | 1 ms |
| `enigo::set_cursor_position` | 3 ms |

## Integration with existing code

- **Replaces** `apps/luna-client/src/components/spatial/GestureController.jsx`. The Spatial HUD will instead read from the same `GestureContext` so HUD and main window share one engine.
- **Existing `luna-gesture-move` consumers must migrate.** Audit (run during Phase 1 day 5) found:
  - `apps/luna-client/src/components/spatial/GestureController.jsx` — the producer; deleted.
  - `apps/luna-client/src/components/spatial/KnowledgeNebula.jsx` — listens to `luna-gesture-move` (lines 123/128). Migrated to `useGesture()` consuming the same dx/dy/dz from `GestureEvent.motion` when pose=`Point` and engine is `Armed`.
  - No other consumers in the repo (verified via `grep -rln "luna-gesture-move" apps/luna-client/src`).
- **Coexists** with `VoiceProvider` and `useVoice`: voice and gesture are independent. Push-to-talk gesture (e.g. open-palm hold) can call `voiceStart()`.
- **Reuses** existing API endpoints for the action targets: memory (`apps/api/app/memory/recall.py`, `record.py`), workflows (`POST /workflows/{id}/run`), MCP (`POST /mcp/tools/{name}/invoke`), notifications (`PUT /notifications/{id}/read`).
- **New API endpoints** (`apps/api/app/api/v1/users.py`):
  - `GET /users/me/gesture-bindings` — fetch user's binding set.
  - `PUT /users/me/gesture-bindings` — replace user's binding set (validates schema, 64KB cap, rate-limited 10/min).
- **New table** `user_gesture_bindings` via migration `114_user_gesture_bindings.sql` (see "Server-side schema & migration" above). Replicate to Helm values if any API env-var tuning is required (not anticipated).

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

### Phase 1 — Engine + grammar (week 1)
- **Days 1–2:** landmark-runtime spike (Apple Vision → MediaPipe C++ → `mediapipe-rs`), pick winner against the spike pass criteria above.
- **Days 3–4:** `camera.rs`, `landmark.rs`, `pose.rs`, `motion.rs`, `wake.rs`, `recognizer.rs`, `supervisor.rs`. Tauri command + event wiring in `lib.rs`. Rewrite `start_spatial_capture` as a frame-fan-out consumer.
- **Day 5:** `GestureContext`, `GestureOverlay` (replacing existing controller). Migrate `KnowledgeNebula` consumer to `useGesture`. Hard-coded default bindings. End-to-end smoke: wake → 3-finger swipe up opens HUD.

**Exit criteria:** all default bindings work end-to-end. Sleeping <3% CPU. Armed <12% CPU. Discrete-gesture latency <80ms p95. No `luna-gesture-move` listeners remain.

### Phase 2 — Bindings UI (week 2)
- `GestureBindingsPage`, `GestureRecorder`, `useGestureBindings`.
- Migration `114_user_gesture_bindings.sql` + API endpoints (validation, rate limit, 64KB cap).
- Conflict detection, scope toggles (`global`/`luna_only`/`hud_only`/`chat_only`), export/import.

**Exit criteria:** user can record a custom gesture and bind it to any action; conflict warnings work; bindings round-trip through API and DB; payload size + rate limit enforced.

### Phase 3 — Extensions (week 3)
- `cursor.rs` (point-pose system cursor + pinch click) gated by Accessibility + frontmost-app rules. `LunaCursor.jsx` overlay.
- Hand-rotation knob (continuous parameter binding).
- Two-handed frame for region-select → summarize MCP tool.
- `GestureCalibration` onboarding wizard with Accessibility step.
- "Global cursor mode" opt-in toggle in `GestureBindingsPage` with first-time warning copy.

**Exit criteria:** in-app cursor tracking <16ms p95; system cursor only fires when Luna or HUD is frontmost (or when the user has explicitly opted in to global cursor mode); calibration wizard reduces false-detection rate; rotation knob drives chat zoom and model temperature smoothly.

## Risks & mitigations

| Risk | Mitigation |
|---|---|
| MediaPipe Rust binding immature | Phase 1 day-1 spike with C++ FFI fallback |
| Camera permission UX ugly on macOS | Calibration wizard explains the prompt before triggering it |
| False wake (open palm during conversation) | 500 ms hold + confidence > 0.85 threshold; user can tune in bindings page |
| Battery drain | Sleeping at 5fps + only pose classifier; auto-disarm after 5s |
| Action storms from misclassification | 80 ms event debounce + two-step confirm for destructive actions |
| Engine task panic | `supervisor.rs` restarts up to **3× per session total**. After exhaustion, menubar dot shows red cross-out and the engine stays off until the user re-enables from the tray. Each restart releases and re-acquires the camera handle (brief green-light flash). |
| Camera unplugged mid-session | `camera.rs` emits `Disconnected`; supervisor falls back to default device, or surfaces `engine-status: { state: "no_camera" }` if none available. |
| User locks themselves out of the bindings UI | Settings remain reachable via keyboard nav (`Cmd+,` → Gestures); gestures can always be paused via `Cmd+Shift+G` |

## Open questions

1. Should rotation-knob direction be inverted for left-handed users, or auto-detected from hand handedness in the Hand object? (Default: auto-detect.)
2. Should we support a "spectator mode" where gestures only highlight elements (cursor follows index-finger) without firing actions, useful for screen-share demos? (Phase 3 candidate.)
3. Should `cursor_move` use absolute mapping (full screen = full camera frame) or relative (small wrist deltas → larger cursor deltas)? (Default: absolute, with sensitivity slider.)

## Out of scope (recap)
iOS/Android, eye tracking, ML-trained classifier, voice replacement, keyboard replacement.
