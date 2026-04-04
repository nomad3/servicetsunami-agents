# Luna Multi-Platform Client — Design Document

**Date**: 2026-03-27
**Status**: Design / Pre-implementation
**Project**: Luna as a universal interface — desktop, mobile, necklace, desk robot, IoT

---

## Vision

Luna lives in WhatsApp and a chat UI today. That's too small. She should be on your desk, in your pocket, around your neck, watching through the camera, and listening through the necklace — all connected to the same brain: the MacBook M4 running ServiceTsunami as the production server.

This design creates **Luna Client**: a single, unified application shell that runs natively on macOS (M4), iOS, Android, and as a PWA in any browser. It also introduces the **Device Bridge** — a lightweight microservice that connects IoT devices (EZVIZ H6 camera, desk robot, future glasses) to Luna via WebSocket and WebRTC. All platforms share one codebase, one backend, one knowledge graph.

The MacBook M4 is the brain. Every device is a sense organ or a voice.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                     Luna Clients (UI Layer)                      │
│                                                                  │
│  ┌──────────────┐  ┌──────────────┐  ┌────────────────────────┐ │
│  │  Luna Desktop│  │  Luna Mobile │  │  Luna PWA (browser)    │ │
│  │  Tauri 2.0   │  │  Tauri 2.0   │  │  React + Service Worker│ │
│  │  macOS M4    │  │  iOS/Android │  │  Any device            │ │
│  └──────┬───────┘  └──────┬───────┘  └──────────┬─────────────┘ │
│         │                 │                      │               │
│         └─────────────────┴──────────────────────┘               │
│                           │                                      │
│                    React Frontend (shared)                       │
│                    - Chat UI (existing)                          │
│                    - LunaAvatar component                        │
│                    - DevicePanel component                       │
│                    - CameraView component                        │
└───────────────────────────┬─────────────────────────────────────┘
                            │  HTTPS + WSS
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│              MacBook M4 — Production Server                      │
│                                                                  │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │              ServiceTsunami API (port 8001)              │    │
│  │  + /api/v1/devices/       — device registry CRUD        │    │
│  │  + /api/v1/robot/interact — voice+vision interaction    │    │
│  │  + /api/v1/vision/analyze — camera frame analysis       │    │
│  │  + /api/v1/ambient/ingest — necklace ambient audio      │    │
│  │  + /api/v1/devices/ws     — WebSocket device hub        │    │
│  └─────────────────────────────────────────────────────────┘    │
│                                                                  │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │              Device Bridge (port 8088)                   │   │
│  │  - WebSocket hub: IoT devices connect + authenticate     │   │
│  │  - RTSP → WebRTC: EZVIZ camera → browser stream         │   │
│  │  - Event bus: device events → SSE to Luna clients        │   │
│  │  - ffmpeg pipeline: RTSP pull → HLS/WebRTC relay        │   │
│  └──────────────────────────────────────────────────────────┘   │
│                                                                  │
│  ┌──────────────────┐  ┌──────────────┐  ┌───────────────────┐  │
│  │  Ollama (11434)  │  │ Temporal(7233│  │  Postgres (8003)  │  │
│  │  Gemma 4 vision     │  │  workflows   │  │  + pgvector       │  │
│  └──────────────────┘  └──────────────┘  └───────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
                            │
          ┌─────────────────┴──────────────────┐
          │          IoT Devices               │
          │                                    │
│  ┌──────▼──────┐  ┌──────────────┐  ┌──────▼──────┐  │
│  │ EZVIZ H6   │  │ Luna Desk    │  │ Luna        │  │
│  │ Camera     │  │ Robot        │  │ Necklace    │  │
│  │ RTSP:8554  │  │ Pi Zero 2W   │  │ nRF52840   │  │
│  │ WiFi       │  │ WiFi+WS      │  │ BLE→Phone   │  │
│  └────────────┘  └──────────────┘  └─────────────┘  │
```

---

## Technology Stack

### Client App — Tauri 2.0

**Why Tauri over Electron**:
- Native ARM64 binary on macOS M4 (no Rosetta, ~5x faster startup than Electron)
- Single codebase for macOS + iOS + Android + Linux/Windows
- Rust backend with system API access (camera, mic, BLE, notifications)
- ~10MB installer vs ~150MB Electron bundle
- Uses native WebView (WKWebView on macOS/iOS, Android WebView) — no bundled Chromium
- Same React frontend as `apps/web` — reuse all components

**Why not React Native**:
- We already have a React web app with Ocean Theme components
- Tauri 2.0 mobile ships the web bundle in a native shell — zero component rewrite
- Tauri handles native features via Rust plugins (camera, mic, BLE)

### Device Bridge — Python + FastAPI + aiortc

Same tech stack as ServiceTsunami API. Minimal new service. Key libraries:
- `websockets` — IoT device WebSocket server
- `aiortc` — WebRTC for camera stream relay
- `asyncio` subprocess — ffmpeg for RTSP pull
- Runs inside existing Docker Compose stack

### IoT Protocol — Luna Device Protocol (LDP)

Simple JSON-over-WebSocket protocol for all devices:

```json
// Device → Bridge: register
{"type": "register", "device_id": "ezviz-h6-kitchen", "device_type": "camera",
 "tenant_id": "...", "capabilities": ["video", "audio", "ptz"], "token": "..."}

// Device → Bridge: event
{"type": "event", "device_id": "...", "event": "motion_detected",
 "payload": {"confidence": 0.92, "zone": "entry"}}

// Bridge → Device: command
{"type": "command", "device_id": "...", "command": "capture_frame",
 "params": {"quality": "high"}}

// Bridge → Client: device update (SSE)
{"type": "device_update", "device_id": "...", "status": "online",
 "last_event": {...}}
```

---

## Luna Client App (`apps/luna-client/`)

### Directory Structure

```
apps/luna-client/
├── src-tauri/                    # Rust/Tauri backend
│   ├── src/
│   │   ├── main.rs               # App entry, system tray
│   │   ├── commands/
│   │   │   ├── audio.rs          # Mic capture (native)
│   │   │   ├── camera.rs         # Local webcam access
│   │   │   ├── ble.rs            # BLE scan/connect (necklace)
│   │   │   ├── notifications.rs  # Native push notifications
│   │   │   └── tray.rs           # System tray menu
│   │   └── lib.rs
│   ├── Cargo.toml
│   └── tauri.conf.json
│
├── src/                          # React frontend (shared with apps/web)
│   ├── App.jsx
│   ├── components/
│   │   ├── LunaAvatar.jsx        # ASCII/SVG avatar with emotion states
│   │   ├── ChatInterface.jsx     # Main chat (port from apps/web ChatPage)
│   │   ├── DevicePanel.jsx       # Connected IoT devices sidebar
│   │   ├── CameraView.jsx        # WebRTC camera stream viewer
│   │   ├── VoiceInput.jsx        # Push-to-talk + wake word UI
│   │   └── AmbientBar.jsx        # Necklace ambient status indicator
│   ├── hooks/
│   │   ├── useDeviceBridge.js    # WebSocket connection to device bridge
│   │   ├── useVoice.js           # Mic capture → STT → Luna API
│   │   ├── useBLE.js             # BLE device management (necklace)
│   │   └── useLunaStream.js      # SSE response streaming
│   ├── store/
│   │   └── devices.js            # Zustand store for device state
│   └── index.html
│
├── package.json
└── vite.config.js
```

### Platform Targets

| Target | How | When |
|---|---|---|
| macOS (M4 native) | `cargo tauri build --target aarch64-apple-darwin` | Primary dev machine |
| iOS | `cargo tauri ios build` | Luna Mobile (necklace relay) |
| Android | `cargo tauri android build` | Luna Mobile (necklace relay) |
| PWA (browser) | `npm run build` → serves via Cloudflare Tunnel | Any device, zero install |
| Desk Robot display | Chromium kiosk mode (headless Pi) | Embedded display |

### System Tray Mode (macOS)

Luna lives in the menu bar when minimized:
- Click moon icon → expand chat overlay
- Always-on-top mini chat window option
- Notification badge for unread messages
- Quick voice input from tray (click + hold)

---

## Luna Avatar Component

The avatar gives Luna a visual presence across all platforms — expressive but not heavy. Two rendering modes:

### Mode 1 — ASCII Avatar (terminal / desk robot LED / low-bandwidth)

```
IDLE:
    ◉ ◉
   (   )
    ───

THINKING:
    ◉ ○
   (   )
    ~~~

HAPPY:
    ◕ ◕
   (   )
    ───
    ~~~

SPEAKING:
    ◉ ◉
   ( ○ )
    ───

ALERT:
    ◎ ◎
   (   )
    ▲▲▲

SLEEP:
    − −
   (   )
    zzz
```

### Mode 2 — React Component (app / web)

```jsx
// components/LunaAvatar.jsx
// SVG-based, smooth CSS transitions between emotion states
// Emotion states: idle | thinking | happy | speaking | alert | sleep | listening

<LunaAvatar
  emotion="thinking"
  size="sm"          // sm | md | lg | floating
  animated={true}
  onTap={() => openChat()}
/>
```

Emotion is driven by the `emotion` field returned in API responses (already in desk robot design: `motion_hint`). The same field drives:
- Avatar face expression (React component)
- Desk robot LED eyes + servo choreography
- Necklace privacy LED color

### Emotion State Machine

```
API response metadata: { emotion: "happy" | "thinking" | "alert" | "speaking" | "idle" | "sleep" }

→ Luna Client: LunaAvatar emotion state
→ Desk Robot: motion_hint → servo + LED
→ Necklace: LED color via phone BLE
```

---

## Device Bridge (`apps/device-bridge/`)

New microservice. Minimal. ~400 lines total.

```
apps/device-bridge/
├── main.py               # FastAPI + WebSocket server entry
├── hub.py                # WebSocket connection manager (device registry)
├── rtsp_bridge.py        # RTSP → WebRTC via aiortc + ffmpeg
├── event_bus.py          # Device events → SSE to clients
├── auth.py               # Device token validation (X-Device-Token header)
├── models.py             # Device registration dataclass
└── requirements.txt      # fastapi, websockets, aiortc, aiohttp
```

### Device Registration Flow

```
1. New EZVIZ camera added to local WiFi
2. User opens Luna Client → DevicePanel → "Add Device"
3. Luna Client calls POST /api/v1/devices/ with {name, type, rtsp_url, token}
4. API creates device_registry record (tenant_id FK)
5. Device bridge polls API for registered devices
6. EZVIZ connects: rtsp://192.168.x.x:554/stream1
7. Bridge starts ffmpeg RTSP pull → WebRTC relay
8. Luna Client receives WebRTC offer → renders live feed in CameraView
```

### EZVIZ H6 Integration (specific)

The EZVIZ H6 exposes:
- RTSP stream: `rtsp://admin:<password>@<ip>:554/h264/ch01/main/av_stream`
- Local HTTP API for PTZ, snapshot, config
- No cloud dependency needed for local LAN access

Device Bridge handles:
```python
# rtsp_bridge.py
async def start_rtsp_relay(device_id: str, rtsp_url: str):
    # Pull RTSP via ffmpeg, relay as WebRTC to browser
    # ffmpeg -i rtsp://... -f rtp rtp://127.0.0.1:5004
    # aiortc RTCPeerConnection → SDP offer → client
```

Luna Vision Integration:
```
Camera frame capture → POST /api/v1/vision/analyze
→ Ollama gemma4-vision (vision model, if available) or Claude
→ { description, persons, objects, sentiment }
→ Injected into Luna chat context as visual awareness
```

---

## API Extensions (ServiceTsunami)

### New Model: `apps/api/app/models/device_registry.py`

```python
class DeviceRegistry(Base):
    __tablename__ = "device_registry"
    id = Column(UUID, primary_key=True, default=uuid4)
    tenant_id = Column(UUID, ForeignKey("tenants.id"), nullable=False)
    name = Column(String, nullable=False)          # "Kitchen Camera"
    device_type = Column(String, nullable=False)   # camera | robot | necklace | glasses
    device_id = Column(String, nullable=False)     # unique per tenant
    capabilities = Column(JSONB, default=[])       # ["video", "audio", "ptz"]
    config = Column(JSONB, default={})             # rtsp_url, ip, etc.
    token_hash = Column(String)                    # bcrypt hash of device token
    status = Column(String, default="offline")     # online | offline | error
    last_seen = Column(DateTime)
    meta = Column(JSONB, default={})
```

### New Routes: `apps/api/app/api/v1/devices.py`

```
GET    /api/v1/devices/                        — list tenant devices
POST   /api/v1/devices/                        — register new device
GET    /api/v1/devices/{device_id}             — get device status
DELETE /api/v1/devices/{device_id}             — remove device
POST   /api/v1/devices/{device_id}/command     — send command to device

POST   /api/v1/robot/interact                  — voice+vision (desk robot)
POST   /api/v1/vision/analyze                  — camera frame analysis
POST   /api/v1/ambient/ingest                  — necklace ambient audio
GET    /api/v1/ambient/history                 — ambient capture history

WS     /api/v1/devices/ws                      — device hub WebSocket (auth: X-Device-Token)
```

All new routes use standard tenant JWT auth except `/api/v1/devices/ws` which uses `X-Device-Token` (device-specific token, same pattern as `X-Internal-Key`).

### New MCP Tools

New file: `apps/mcp-server/src/mcp_tools/devices.py`

```python
# Tools:
# list_connected_devices(tenant_id) → list of online devices
# capture_camera_frame(device_id, tenant_id) → base64 image
# analyze_camera_feed(device_id, context, tenant_id) → vision description
# send_device_command(device_id, command, params, tenant_id) → ack
# get_device_status(device_id, tenant_id) → status dict
```

These tools let Luna proactively use the camera:
- "I can see you're on a call, I'll wait"
- "Motion detected at the front door"
- "You left your coffee on the desk"

---

## Docker Compose Extension

Add to `docker-compose.yml`:

```yaml
device-bridge:
  build: ./apps/device-bridge
  ports:
    - "8088:8088"
  environment:
    - API_BASE_URL=http://api:8000
    - API_INTERNAL_KEY=${API_INTERNAL_KEY}
  depends_on:
    - api
  volumes:
    - /tmp/luna-rtsp:/tmp/luna-rtsp   # ffmpeg temp files
```

Add to root `.env`:
```
DEVICE_BRIDGE_PORT=8088
```

---

## Multi-Platform Communication Channels

```
Channel          | Transport    | Auth              | Realtime
─────────────────┼─────────────┼───────────────────┼─────────
WhatsApp         | WhatsApp API | Phone number      | ✓ (push)
Chat UI (web)    | SSE + REST   | JWT               | ✓ (SSE)
Luna Desktop     | SSE + REST   | JWT + Tauri token | ✓ (SSE)
Luna Mobile      | SSE + REST   | JWT + push notif  | ✓ (SSE)
Desk Robot       | WebSocket    | X-Device-Token    | ✓ (WS)
Necklace         | BLE → phone  | BLE pairing       | ✓ (BLE)
EZVIZ Camera     | RTSP + WS    | X-Device-Token    | ✓ (WS)
```

All channels share the same Luna backend, knowledge graph, and RL system. A conversation started on WhatsApp continues seamlessly on the desktop client. Vision from the camera enriches context for voice queries from the necklace.

---

## Luna Avatar — Phases

### Phase 1 — Emotion Field in API (no hardware yet)
- Add `emotion` field to all Luna API responses
- Values: `idle | thinking | happy | speaking | alert | sleep | listening`
- Derived from response content (keywords, question vs statement, urgency)
- `LunaAvatar` React component with SVG faces + CSS transitions
- Integrate into existing `ChatPage.js` — small floating avatar

### Phase 2 — ASCII Mode
- Terminal-renderable ASCII art for each emotion
- Used by: desk robot OLED display, necklace companion CLI, low-bandwidth clients
- Exportable via `GET /api/v1/avatar/ascii?emotion=happy`

### Phase 3 — Animated Avatar (future)
- Lottie or Rive animation for smooth face transitions
- Optional: 3D rendered head (Three.js) for full desktop client
- Lip sync with TTS audio waveform

---

## Implementation Phases

### Phase 1 — PWA + Avatar (2 weeks)
**Goal**: Luna accessible from any browser with emotion-aware avatar

Tasks:
1. Add `emotion` field to chat response API
2. Build `LunaAvatar` React component (SVG, 7 emotions, animated)
3. Create `apps/luna-client/` with Vite + React (base PWA config)
4. Port `ChatPage.js` components into luna-client
5. PWA manifest + service worker (offline support, installable)
6. Deploy PWA via Cloudflare Tunnel (same as existing web)

Deliverable: Luna installable as PWA on phone/desktop from browser

### Phase 2 — Device Registry + Camera (2 weeks)
**Goal**: EZVIZ H6 camera live in Luna Client

Tasks:
1. `device_registry.py` model + migration
2. `devices.py` API routes + mount in routes.py
3. `apps/device-bridge/` service (WebSocket hub + RTSP bridge)
4. Docker Compose entry for device-bridge
5. `DevicePanel.jsx` + `CameraView.jsx` components
6. MCP tools: `devices.py` (5 tools) + register in `__init__.py`
7. Vision analysis: `POST /api/v1/vision/analyze` → Ollama vision model

Deliverable: Luna can see through the EZVIZ camera and describe what she sees

### Phase 3 — Tauri Desktop App (2 weeks)
**Goal**: Native macOS M4 app with system tray + local mic

Tasks:
1. Init Tauri 2.0 project wrapping luna-client React app
2. Rust commands: audio capture (native mic), camera access, notifications
3. System tray: moon icon, mini chat overlay, voice hold button
4. Native mic → STT → Luna API (bypass browser mic permission UX)
5. macOS M4 arm64 build + code signing
6. Auto-update via GitHub releases

Deliverable: Luna lives in the macOS menu bar, always one click away

### Phase 4 — Desk Robot Integration (3 weeks)
**Goal**: Robot connects to Device Bridge, gets emotion states

Tasks:
1. `luna-robot/api_client.py` — WebSocket client connecting to device bridge
2. Device registration handshake on boot
3. API returns `emotion` + `motion_hint` in `/api/v1/robot/interact`
4. Desk robot executes servo + LED choreography from hints
5. Face detection: camera frame → `/api/v1/vision/analyze` for person detection
6. Head tracking: pan/tilt servos follow detected face coordinates

Deliverable: Desk robot is fully connected to Luna, expressive and aware

### Phase 5 — Mobile App + Necklace BLE (3 weeks)
**Goal**: Tauri iOS/Android app as necklace relay

Tasks:
1. `cargo tauri ios build` + `cargo tauri android build`
2. Rust BLE plugin: scan, pair, GATT connect to nRF52840
3. BLE audio relay: necklace PCM chunks → phone buffer → `/api/v1/robot/interact`
4. TTS relay: API audio → earbuds via AVAudioSession (iOS)
5. `AmbientBar.jsx` — ambient capture status + privacy review UI
6. Push notifications from Luna (via APNs/FCM)
7. Background BLE + periodic ambient upload

Deliverable: Necklace + phone = always-on Luna in your pocket

### Phase 6 — Multi-Device Sync (1 week)
**Goal**: Seamless session continuity across all platforms

Tasks:
1. `channel_preference` field on chat session — remembers last active client
2. Session handoff: "You were on mobile, continuing on desktop"
3. Notification routing: prefer most recently active channel
4. Device presence in knowledge graph (entity per device, last_seen observation)

---

## Security Model

- **Device tokens**: bcrypt-hashed, scoped per device per tenant. Rotatable from Luna Client.
- **RTSP credentials**: stored in credential vault (Fernet-encrypted), never exposed to frontend
- **Camera frames**: processed server-side, never stored unless user explicitly saves a clip
- **BLE pairing**: nRF52840 uses LE Secure Connections (LESC) pairing
- **Device commands**: only tenant-owned devices can receive commands (enforced by device bridge)
- **RTSP stream in browser**: served via WebRTC (no direct RTSP exposure to client)

---

## New Files Summary

| File | Purpose |
|---|---|
| `apps/luna-client/` | Tauri 2.0 + React multi-platform client |
| `apps/luna-client/src/components/LunaAvatar.jsx` | Avatar with emotion states |
| `apps/luna-client/src/components/DevicePanel.jsx` | IoT device management |
| `apps/luna-client/src/components/CameraView.jsx` | WebRTC camera viewer |
| `apps/luna-client/src/components/VoiceInput.jsx` | PTT voice input |
| `apps/luna-client/src/hooks/useDeviceBridge.js` | Device bridge WS hook |
| `apps/device-bridge/` | RTSP bridge + IoT WebSocket hub |
| `apps/api/app/models/device_registry.py` | Device registry model |
| `apps/api/app/api/v1/devices.py` | Device + interaction routes |
| `apps/api/app/api/v1/robot.py` | Robot/ambient interaction routes |
| `apps/mcp-server/src/mcp_tools/devices.py` | 5 device MCP tools |
| `apps/api/migrations/XXX_add_device_registry.sql` | DB migration |

### Files to Modify

| File | Change |
|---|---|
| `apps/api/app/api/v1/routes.py` | Mount `devices`, `robot` routers |
| `apps/api/app/models/__init__.py` | Import `DeviceRegistry` |
| `apps/mcp-server/src/mcp_tools/__init__.py` | Import devices tools |
| `docker-compose.yml` | Add `device-bridge` service |
| `.env` | Add `DEVICE_BRIDGE_PORT=8088` |

---

## Open Questions

- **Tauri vs PWA-only for Phase 1**: Could ship Phase 1 as pure PWA (no Tauri) and add Tauri in Phase 3 — reduces complexity upfront
- **Ollama vision model**: `gemma4` is available but needs ~5GB VRAM. M4 Mac has 16-32GB unified memory so it fits. Need to confirm it's pulled.
- **EZVIZ H6 RTSP credentials**: EZVIZ default RTSP path + credentials vary by firmware. Need to verify `rtsp://admin:<password>@<ip>:554/h264/ch01/main/av_stream` works with this unit.
- **Device bridge as separate service vs integrated into API**: Could add WebSocket hub directly to FastAPI API (avoids new Docker service). Trade-off: simpler stack vs cleaner separation.
- **Luna avatar voice**: Should the avatar lip-sync with TTS audio? Phase 3+ — requires waveform analysis.
- **Glasses form factor**: Same BLE + phone relay pattern as necklace. Ready when hardware exists.
