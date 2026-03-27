# Luna Presence System — Implementation Plan

**Date**: 2026-03-27
**Master Spec**: User-provided Luna Presence System brief
**Design Docs**: `2026-03-27-luna-multiplatform-client-design.md`, `luna-necklace-design.md`, `luna-desk-robot-design.md`
**Status**: Ready to implement

---

## Reuse Audit — What We Already Have

The existing stack has **far more reusable infrastructure than expected**. ~40% of the work is connecting existing pieces, 60% is new UI/visualization.

### SSE Streaming (ready)
- `chat.py:141-193` has working SSE with `StreamingResponse` — add `presence_state` event type
- `apps/web/src/services/chat.js` has SSE parser — extend for presence events
- No WebSocket needed for M1-M2

### State Management (template exists)
- `ThemeContext.js` is the exact pattern to copy for `LunaPresenceContext`
- `NotificationBell.js` has polling + badge UI — becomes Luna state indicator
- `ChatPage.js` has streaming state management — extend for presence

### Scoring → Emotion (already running)
- `auto_quality_scorer.py` scores every response on 6 dimensions — map to mood:
  - accuracy > 0.85 → `confident`, < 0.65 → `uncertain`
  - response_time < 1s → `quick`, > 5s → `thinking`
- `consensus_reviewer.py` has 3 reviewers including "Persona Reviewer" — sentiment already evaluated
- `local_inference.py` has Qwen ready for cheap emotion classification

### Animation/CSS (ready)
- Glassmorphic sidebar: `backdrop-filter: blur(20px)` — Luna avatar container
- `LoadingSpinner.css` has `pulse` and `shimmer` keyframes — adapt for Luna breathing
- `animate.css` v4.1 installed — `fadeIn`, `slideUp` ready
- Full dark/light CSS variable system in `index.css`

### Device/Shell Model (partial)
- `channel_accounts` already tracks connection lifecycle + status — device registry seed
- `channel_events` already logs inbound/outbound events — shell event log
- WhatsApp `ChatPresence` enum from neonize — `composing` / `paused` states
- `memory_activities` event log — add Luna mood events

### Backend Models to Extend (not create)
- `ChatSession.memory_context` (JSON) — add `luna_mood`, `luna_emotion`
- `ChannelAccount.status` — already has connection states
- `Notification.source` — add `luna_state` source type
- `MemoryActivity.event_type` — add `luna_mood_inferred`, `luna_emotion_detected`

### What does NOT exist (must build new)
- Luna face SVG/ASCII renderers
- LunaAvatar React component
- Presence API endpoint
- Emotion classification prompt
- State machine transition logic
- Device bridge microservice (M4)

---

## Stack Reality Check

The current AgentProvision stack uses **JavaScript (not TypeScript)**, React 18, Bootstrap 5, and FastAPI. No WebSocket support exists — only SSE streaming. The master spec assumes TypeScript throughout, but the existing codebase is 100% JS. Plan adapts accordingly:

- **Phase 1**: Pure JS with JSDoc types (matches existing codebase)
- **Phase 2+**: Migrate to TS when luna-client (Vite PWA) is created as a new app

### Key constraints
- Web app is Create React App (JS), no TS compilation
- No WebSocket — using SSE for real-time
- No centralized state management (Context API only)
- Single Docker Compose on MacBook = production
- Luna already communicates via WhatsApp + web chat
- 81 MCP tools available

---

## Milestone 1 — Luna Core + Avatar in Web App (3-4 days)

Ship Luna's identity into the existing web UI with a presence state model.

### Task 1.1 — Presence state model + API endpoint
- **Option A (lean)**: Skip new table — store presence in `ChatSession.memory_context` JSON + `ChannelAccount` status fields. Presence is ephemeral state, not historical data.
- **Option B (full)**: New `luna_presence` table if we want historical presence tracking.
- **Recommended**: Option A for M1, Option B for M2 if needed.
- **New file**: `apps/api/app/schemas/luna_presence.py`
  - `LunaPresenceSnapshot` Pydantic model matching the master spec
- **New file**: `apps/api/app/api/v1/presence.py`
  - `GET /presence` — current snapshot (reads from in-memory cache or session)
  - `PUT /presence` — update state (internal, from agent pipeline)
  - `GET /presence/stream` — SSE endpoint (reuse pattern from `chat.py:141-193`)
- Mount in `apps/api/app/api/v1/routes.py`

### Task 1.2 — Presence service (backend)
- **New file**: `apps/api/app/services/luna_presence_service.py`
  - `get_presence(db, tenant_id)` → snapshot
  - `update_state(db, tenant_id, state, mood?, privacy?)` → snapshot
  - `register_shell(db, tenant_id, shell_name)` → add to connected_shells
  - `deregister_shell(db, tenant_id, shell_name)` → remove
  - `set_tool_status(db, tenant_id, status)` → update tool_status
- **Integrate into chat pipeline**:
  - Set `thinking` when CLI dispatch starts (in `cli_session_manager.py`)
  - Set `responding` when response arrives
  - Set `idle` after response delivered
  - Set `listening` on WhatsApp inbound message

### Task 1.3 — ASCII renderer (JS module)
- **New file**: `apps/web/src/components/luna/asciiRenderer.js`
  - Pure function: `renderAsciiface(state, mood, privacy)` → string[]
  - All 7 canonical faces from master spec (idle, listening, thinking, responding, alert, sleep, private)
  - Data-driven: face parts map keyed by state
  - Half-moon eyes (`◜ ◝`) as identity primitive

### Task 1.4 — SVG renderer (JS module)
- **New file**: `apps/web/src/components/luna/svgRenderer.js`
  - Pure function: `renderSvgFace(state, mood, privacy, size)` → React SVG element
  - Half-moon eyes as SVG crescents
  - Mouth, brow, halo as animated SVG paths
  - CSS transitions (200ms ease) between states
  - Supports sizes: xs (24px), sm (32px), md (48px), lg (80px), xl (128px)

### Task 1.5 — LunaAvatar component
- **New file**: `apps/web/src/components/luna/LunaAvatar.js`
  - Props: `state, mood, privacy, size, mode (icon|svg|ascii), animated`
  - Delegates to svgRenderer or asciiRenderer based on mode
  - Default: SVG with animation
  - Subtle animations: slow blink (2.4s), pulse halo on listening, shimmer on thinking

### Task 1.6 — LunaStateBadge component
- **New file**: `apps/web/src/components/luna/LunaStateBadge.js`
  - Tiny chip: state name + colored dot
  - States: Listening (blue), Thinking (amber), Responding (green), Private (red), Alert (orange)

### Task 1.7 — LunaAsciiPanel component
- **New file**: `apps/web/src/components/luna/LunaAsciiPanel.js`
  - Monospace panel showing the ASCII face
  - Below: state name, mood, privacy mode, active shell
  - Useful for debug/terminal view

### Task 1.8 — Integrate into Layout + ChatPage
- **Edit**: `apps/web/src/components/Layout.js`
  - Add `LunaAvatar` (sm, svg) in sidebar header, above navigation
  - Reuse glassmorphic container from `.sidebar-brand` CSS
  - Add `LunaStateBadge` next to it
- **Edit**: `apps/web/src/pages/ChatPage.js`
  - Replace `LoadingSpinner` typing indicator with `LunaAvatar` (md) showing `thinking` state
  - Show `responding` while streaming (hook into existing `isStreaming` state)
  - Show `idle` after response complete
- **New file**: `apps/web/src/context/LunaPresenceContext.js`
  - Copy `ThemeContext.js` pattern exactly
  - Start with polling (follow `NotificationBell.js` pattern, 5s interval)
  - Later upgrade to SSE via `chat.js` streaming pattern
  - Provides `useLunaPresence()` hook to all components

### Task 1.9a — Emotion detection from scoring (zero new inference cost)
- **Edit**: `apps/api/app/services/auto_quality_scorer.py`
  - After scoring, derive mood from existing rubric dimensions:
    - `accuracy >= 22/25` → mood `confident`
    - `helpfulness >= 18/20` → mood `warm`
    - `tool_usage >= 18/20` → mood `focused`
    - `overall < 50` → mood `uncertain`
    - Default → `calm`
  - Store derived mood in `reward_components["luna_mood"]`
  - Update presence state via `luna_presence_service.update_state()`
- No additional Ollama call needed — piggybacks on existing scoring

### Task 1.9 — Luna design tokens + theme
- **New file**: `apps/web/src/components/luna/lunaTokens.js`
  - Shape, motion, glow constants from master spec
  - Dark/light mode support
  - Colors that work in monochrome (shape carries identity, not color)

**Deliverable**: Luna's half-moon face appears in the web UI, reacts to chat state in real-time.

---

## Milestone 2 — Presence Protocol + Shell Registry (2-3 days)

### Task 2.1 — Shell registry
- Extend `luna_presence_service.py`:
  - Track connected shells with heartbeat (30s timeout)
  - Shell types: `whatsapp`, `web`, `desktop`, `mobile`, `necklace`, `glasses`, `camera`
  - Auto-deregister on missed heartbeat

### Task 2.2 — Presence events (SSE)
- Implement `GET /presence/stream` as SSE endpoint
  - Events: `STATE_CHANGED`, `PRIVACY_CHANGED`, `SHELL_CONNECTED`, `SHELL_DISCONNECTED`, `HANDOFF_STARTED`, `HANDOFF_COMPLETED`
  - Replace polling in `LunaPresenceContext.js` with EventSource

### Task 2.3 — WhatsApp shell integration
- Edit `apps/api/app/services/whatsapp_service.py`:
  - Register WhatsApp as shell on connection
  - Set `listening` on inbound message
  - Set `responding` while agent processes
  - Deregister on disconnect
  - State-to-text fallback for WhatsApp status

### Task 2.4 — LunaPresenceCard component
- **New file**: `apps/web/src/components/luna/LunaPresenceCard.js`
  - Shows: avatar (lg), current state, mood, active device, privacy mode, session summary
  - Handoff target if in handoff state

### Task 2.5 — LunaDeviceStatus component
- **New file**: `apps/web/src/components/luna/LunaDeviceStatus.js`
  - Grid of connected shells with online/offline indicators
  - Shell icons: WhatsApp, Web, Desktop, Necklace, Glasses, Camera

### Task 2.6 — Handoff behavior
- When user switches devices mid-conversation:
  - Mark previous shell as inactive
  - New shell becomes active
  - Show "Continuing from [device]" in chat header
  - Same session, same memory context

**Deliverable**: Real-time presence state synced across web + WhatsApp. Device panel shows all shells.

---

## Milestone 3 — Luna Debug Page + PWA Bootstrap (2-3 days)

### Task 3.1 — LunaDebugPage
- **New file**: `apps/web/src/pages/LunaDebugPage.js`
  - Grid of all states × moods × privacy modes
  - Toggle controls for each dimension
  - Side-by-side: SVG, ASCII, icon renderers
  - Live state from API
  - Connected shells panel
  - Event log (last 50 presence events)
- Add route in `App.js` at `/luna`

### Task 3.2 — Emotion detection from agent responses
- Edit `apps/api/app/services/cli_session_manager.py`:
  - After response, detect mood from content (keyword heuristics):
    - Exclamation/enthusiasm → `warm`/`playful`
    - Questions/uncertainty → `empathetic`
    - Technical/code → `serious`
    - Error/warning → `alert`
    - Default → `calm`
  - Update presence with detected mood

### Task 3.3 — Bootstrap `apps/luna-client/` as Vite PWA
- Init Vite + React project
- PWA manifest: name="Luna", crescent moon icon, ocean dark theme
- Service worker for offline shell caching
- Port: LunaAvatar, ChatInterface, LunaPresenceCard
- API base: `VITE_API_BASE_URL` env var
- Add to Docker Compose as nginx static server

### Task 3.4 — Deploy PWA via Cloudflare Tunnel
- Route `luna.agentprovision.com` or `/luna` subfolder
- PWA installable from phone browser

**Deliverable**: Full debug page showing all Luna states. PWA installable on phone.

---

## Milestone 4 — Device Bridge + Camera (1 week)

### Task 4.1 — Device registry model + API
- Model: `device_registry` (from multiplatform-client plan Task 2.1)
- Routes: list, register, delete, command (from Task 2.2)

### Task 4.2 — Device Bridge microservice
- `apps/device-bridge/`: FastAPI + WebSocket hub
- RTSP bridge for camera feeds
- Auth via device tokens
- Add to Docker Compose on port 8088

### Task 4.3 — Robot API endpoints
- `/api/v1/robot/interact` — audio+image → Luna → TTS+emotion
- `/api/v1/robot/vision/analyze` — image → description
- `/api/v1/ambient/ingest` — ambient audio → knowledge graph

### Task 4.4 — Device MCP tools
- `list_connected_devices`, `capture_camera_frame`, `analyze_camera_feed`
- `send_device_command`, `get_device_status`

**Deliverable**: EZVIZ camera live in Luna Client. Luna can describe what she sees.

---

## Milestone 5 — Tauri Desktop App (1 week)

Per multiplatform-client plan Phase 3:
- Tauri 2.0 project wrapping luna-client
- System tray with moon icon
- Cmd+Shift+Space global shortcut
- Native mic via cpal crate
- Push-to-talk
- macOS aarch64 build

---

## Milestone 6 — Necklace BLE + Mobile (2 weeks)

Per necklace design doc:
- BLE Manager in Tauri iOS
- Audio relay pipeline: necklace → phone → API → earbuds
- Ambient capture pipeline
- Push notifications (APNs/FCM)

---

## File Structure (adapted to existing monorepo)

```
apps/web/src/components/luna/       ← Luna UI components (JS, in existing web app)
  LunaAvatar.js
  LunaPresenceCard.js
  LunaDeviceStatus.js
  LunaAsciiPanel.js
  LunaStateBadge.js
  asciiRenderer.js
  svgRenderer.js
  lunaTokens.js
  LunaAvatar.css

apps/web/src/context/
  LunaPresenceContext.js            ← Presence state hook

apps/web/src/pages/
  LunaDebugPage.js                  ← Debug/preview page

apps/api/app/models/
  luna_presence.py                  ← Presence DB model

apps/api/app/schemas/
  luna_presence.py                  ← Pydantic schemas

apps/api/app/services/
  luna_presence_service.py          ← Presence business logic

apps/api/app/api/v1/
  presence.py                       ← Presence API routes

apps/luna-client/                   ← Vite PWA (Milestone 3+)
  src/
  public/
  vite.config.js

apps/device-bridge/                 ← Device bridge (Milestone 4+)
  main.py
  hub.py
  rtsp_bridge.py
```

---

## Priority Order

| Milestone | Duration | What ships |
|-----------|----------|------------|
| **M1** | 3-4 days | Luna avatar in web UI, presence API, state machine |
| **M2** | 2-3 days | SSE events, shell registry, WhatsApp integration, handoff |
| **M3** | 2-3 days | Debug page, emotion detection, PWA bootstrap |
| **M4** | 1 week | Device bridge, camera integration, robot API |
| **M5** | 1 week | Tauri desktop app with system tray |
| **M6** | 2 weeks | Necklace BLE, mobile app, ambient capture |

Start with M1. Ship Luna's face into the web app first.
