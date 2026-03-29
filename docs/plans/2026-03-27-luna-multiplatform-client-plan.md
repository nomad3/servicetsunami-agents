# Luna Multi-Platform Client — Implementation Plan

**Date**: 2026-03-27
**Master Plan**: `2026-03-29-luna-native-operating-system-plan.md`
**Design Doc**: `2026-03-27-luna-multiplatform-client-design.md`
**Status**: Ready to implement

---

## Phase 1 — PWA + Avatar (Priority: ship first)

### Task 1.1 — Add `emotion` field to chat API response
- File: `apps/api/app/services/enhanced_chat.py`
- Add `emotion` detection from response content (keyword heuristics first, Ollama scoring later)
- Return `{ ..., "emotion": "happy" | "thinking" | "alert" | "speaking" | "idle" }` in chat response
- Update chat schema: `apps/api/app/schemas/chat.py`

### Task 1.2 — `LunaAvatar` React component
- File: `apps/web/src/components/LunaAvatar.jsx`
- SVG face with 7 emotion states (idle, thinking, happy, speaking, alert, sleep, listening)
- CSS transitions between states (200ms ease)
- Props: `emotion`, `size` (sm/md/lg/floating), `animated`, `onTap`
- Integrate into `ChatPage.js` as floating avatar (bottom-right, 48px)

### Task 1.3 — Bootstrap `apps/luna-client/` as Vite PWA
- Init: `npm create vite@latest luna-client -- --template react`
- Add Vite PWA plugin (`vite-plugin-pwa`)
- PWA manifest: name="Luna", icon=moon, theme_color=ocean dark
- Service worker: cache shell + API responses for offline
- Port key components from `apps/web/src`: ChatInterface, LunaAvatar, Layout
- API base URL: `VITE_API_BASE_URL` env var

### Task 1.4 — Deploy PWA via existing Cloudflare Tunnel
- Add `luna-client` build to Docker Compose as static file server (nginx)
- Route `luna.servicetsunami.com` (or subfolder) via Cloudflare Tunnel
- PWA installable from phone browser ("Add to Home Screen")

**Deliverable**: Luna PWA on phone/desktop with avatar face reacting to responses

---

## Phase 2 — Device Registry + EZVIZ Camera

### Task 2.1 — `device_registry` model + migration
- New file: `apps/api/app/models/device_registry.py` (schema in design doc)
- Migration: `apps/api/migrations/NNNN_add_device_registry.sql`
  - `CREATE TABLE device_registry (...)`
- Import in `apps/api/app/models/__init__.py`

### Task 2.2 — Device API routes
- New file: `apps/api/app/api/v1/devices.py`
  - `GET /` — list tenant devices
  - `POST /` — register device (generates + returns device token)
  - `DELETE /{device_id}` — remove device
  - `POST /{device_id}/command` — send command (relayed via device bridge)
- New file: `apps/api/app/api/v1/robot.py`
  - `POST /interact` — audio+image → STT → Luna → TTS+emotion
  - `POST /vision/analyze` — image → vision description
  - `POST /ambient/ingest` — ambient audio → STT → knowledge graph
  - `GET /ambient/history` — list ambient captures
- Mount both in `apps/api/app/api/v1/routes.py`

### Task 2.3 — Device Bridge microservice
- New directory: `apps/device-bridge/`
- `main.py`: FastAPI app + WebSocket endpoint `/ws`
- `hub.py`: connection manager — device_id → websocket mapping
- `rtsp_bridge.py`: ffmpeg subprocess + aiortc WebRTC relay
- `auth.py`: verify X-Device-Token against API `/internal/devices/verify`
- `event_bus.py`: broadcast device events to subscribed clients via SSE
- Dockerfile + requirements.txt
- Add to `docker-compose.yml` on port 8088

### Task 2.4 — `DevicePanel.jsx` + `CameraView.jsx`
- `DevicePanel.jsx`: list connected devices, online/offline status, add device flow
- `CameraView.jsx`: WebRTC player (uses browser RTCPeerConnection)
  - Connects to `wss://device-bridge/webrtc/{device_id}`
  - Shows live feed in Luna Client sidebar or fullscreen
- Add to luna-client app

### Task 2.5 — Device MCP tools
- New file: `apps/mcp-server/src/mcp_tools/devices.py`
  - `list_connected_devices`, `capture_camera_frame`, `analyze_camera_feed`
  - `send_device_command`, `get_device_status`
- Import in `apps/mcp-server/src/mcp_tools/__init__.py`

**Deliverable**: EZVIZ H6 live stream in Luna Client, Luna can describe what she sees

---

## Phase 3 — Tauri Desktop App (macOS M4)

### Task 3.1 — Init Tauri 2.0 project
- `cd apps/luna-client && cargo tauri init`
- Configure `tauri.conf.json`: app name, bundle ID, window size, system tray
- Add Tauri plugins: `tauri-plugin-notification`, `tauri-plugin-global-shortcut`

### Task 3.2 — Rust audio command
- `src-tauri/src/commands/audio.rs`
- Native mic capture via `cpal` crate (cross-platform audio)
- Stream PCM chunks to frontend via Tauri event emitter
- Push-to-talk: start on keydown, stop on keyup, send to API

### Task 3.3 — System tray
- `src-tauri/src/commands/tray.rs`
- Moon icon in menu bar (template image for macOS dark mode support)
- Tray menu: Open Luna / Voice Input / Quit
- Mini overlay window: 380×480px, always-on-top, borderless, transparent bg

### Task 3.4 — Global shortcut
- Cmd+Shift+Space → toggle Luna overlay (like Raycast)
- Hold Cmd+Shift+Space → push-to-talk voice input
- Configurable in app settings

### Task 3.5 — macOS M4 build + distribution
- `cargo tauri build --target aarch64-apple-darwin`
- Code sign with Apple Developer certificate
- Auto-update: Tauri updater plugin checking GitHub releases
- GitHub Action: `luna-client-deploy.yaml` — build on push to `apps/luna-client/**`

**Deliverable**: Luna in macOS menu bar, native mic, Cmd+Shift+Space summons her

---

## Phase 4 — Desk Robot Integration

### Task 4.1 — Robot API client
- New file in `luna-robot/api_client.py`
- WebSocket connect to `wss://api/api/v1/devices/ws`
- Registration handshake on boot
- Reconnect with exponential backoff

### Task 4.2 — Wire `motion_hint` + `emotion` to robot hardware
- `luna-robot/main.py`: parse `motion_hint` from API response
- `luna-robot/motion.py`: map hint to servo choreography sequence
- `luna-robot/leds.py`: map `emotion` to LED eye sprite

### Task 4.3 — Vision: face detection + head tracking
- `luna-robot/camera.py`: OpenCV face detection (Haar cascade, local)
- Detected face center coordinates → servo pan/tilt target
- Send face detection events to API: `{ "type": "event", "event": "face_detected", "payload": { "x": 0.5, "y": 0.4 } }`

### Task 4.4 — Periodic frame upload for context
- Every 60s (or on motion): capture frame → POST `/api/v1/vision/analyze`
- Vision result stored as observation in knowledge graph entity for the robot device
- Context injected into Luna reasoning: "I can see you at your desk"

**Deliverable**: Robot responds to Luna's emotion states, tracks faces, provides visual context

---

## Phase 5 — Mobile App + Necklace BLE

### Task 5.1 — Tauri iOS build
- `cargo tauri ios build`
- BLE plugin: `tauri-plugin-ble` (or custom Rust + Swift bridge)
- AVAudioSession config for background audio + earbuds routing

### Task 5.2 — BLE Manager
- `src/hooks/useBLE.js`: scan, pair, connect nRF52840
- BLE GATT services matching necklace firmware UUIDs
- Auto-reconnect on proximity
- Battery % + connection status in `AmbientBar.jsx`

### Task 5.3 — Audio relay pipeline
- Necklace PCM (16kHz BLE) → phone buffer → POST `/api/v1/robot/interact`
- Response audio (TTS from API) → AVAudioSession → earbuds
- Latency target: < 1.5s from necklace button release to first audio byte in ear

### Task 5.4 — Ambient capture pipeline
- Hold button → BLE ambient audio stream
- 30s chunks buffered → POST `/api/v1/ambient/ingest`
- `AmbientBar.jsx`: shows "Ambient: ON / 2 captures pending review"
- Privacy review screen: transcript preview + delete before confirm

### Task 5.5 — Push notifications
- APNs (iOS) + FCM (Android) via Tauri notification plugin
- Luna sends push when: new email, meeting in 10min, important entity detected
- Tapping notification → opens Luna Client to relevant context

**Deliverable**: Tap necklace → Luna answers in earbuds. Hold → meeting captured to knowledge graph.

---

## Phase 6 — Multi-Device Session Sync

### Task 6.1 — Active channel tracking
- Add `last_active_channel` field to `ChatSession` model
- Update on every message: WhatsApp / web / desktop / mobile
- Use in notification routing: prefer most recently active channel

### Task 6.2 — Session handoff UX
- When opening Luna Client on a new device after activity elsewhere: show "Continuing from [device]" header
- Last N messages pre-loaded from server

### Task 6.3 — Device presence in knowledge graph
- On device connect: `record_observation(device_entity, "came online")`
- On disconnect: `record_observation(device_entity, "went offline")`
- Luna can reference: "Your phone has been offline for 2 hours"

---

## Migration Script

```sql
-- apps/api/migrations/NNNN_add_device_registry.sql
CREATE TABLE device_registry (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    name VARCHAR(255) NOT NULL,
    device_type VARCHAR(50) NOT NULL,
    device_id VARCHAR(255) NOT NULL,
    capabilities JSONB DEFAULT '[]',
    config JSONB DEFAULT '{}',
    token_hash VARCHAR(255),
    status VARCHAR(50) DEFAULT 'offline',
    last_seen TIMESTAMP,
    meta JSONB DEFAULT '{}',
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(tenant_id, device_id)
);

CREATE INDEX idx_device_registry_tenant ON device_registry(tenant_id);
CREATE INDEX idx_device_registry_status ON device_registry(status);

CREATE TABLE ambient_captures (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    device_id VARCHAR(255),
    audio_duration_s FLOAT,
    transcript TEXT,
    entities_extracted JSONB DEFAULT '[]',
    status VARCHAR(50) DEFAULT 'pending',  -- pending | reviewed | deleted | processed
    captured_at TIMESTAMP NOT NULL,
    processed_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_ambient_captures_tenant ON ambient_captures(tenant_id);
```

---

## Estimated Effort

| Phase | Tasks | Effort |
|---|---|---|
| Phase 1 — PWA + Avatar | 4 tasks | 3-4 days |
| Phase 2 — Device Bridge + Camera | 5 tasks | 5-7 days |
| Phase 3 — Tauri Desktop | 5 tasks | 4-5 days |
| Phase 4 — Desk Robot | 4 tasks | 3-4 days |
| Phase 5 — Mobile + Necklace | 5 tasks | 5-7 days |
| Phase 6 — Multi-device Sync | 3 tasks | 2 days |
| **Total** | **26 tasks** | **~4-5 weeks** |

Start with Phase 1 — it's the highest leverage, lowest complexity, and unblocks testing Luna's avatar personality immediately on any device.
