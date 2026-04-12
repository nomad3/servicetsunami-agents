# Plan: Voice Commands for the In-App WhatsApp UI

**Date:** 2026-04-11
**Status:** Plan only — not started
**Author:** Plan agent investigation

## Goal & success criteria

Let a user on the Integrations page's WhatsApp card push a microphone button, speak a message, see their speech transcribed into the `sendMessage` textarea, and send it through the existing `POST /channels/whatsapp/send` flow — with an optional spoken playback of Luna's replies. Success = (1) talk → transcript appears → send works end-to-end in Chrome with mic permission granted; (2) a clear disabled/fallback state when mic or STT is unavailable; (3) no regression to the existing test-send form.

## Architecture decision

**Hybrid, browser-first with server fallback.** Push-to-talk (hold-to-record) on a new mic button inside `WhatsAppChannelCard.js`, wired as follows:

- **Primary STT path (fast, free):** browser `MediaRecorder` → blob → `POST /media/transcribe` (new thin endpoint) → backend calls the **existing** `media_utils.transcribe_audio_bytes` which already runs local Whisper with a graceful fallback to returning `None`. The transcript is written into the `sendMessage` state. The user reviews it, edits if needed, and clicks **Send** — no change to the send path.
- **Why not Web Speech API directly?** It's Chromium/Safari-only, streams text to Google servers implicitly, and behaves inconsistently inside HTTPS embedded iframes (the app is served via the Layout shell). `MediaRecorder` is universally supported in every modern browser and keeps STT under our control. We reuse code that's already in the repo, so the server cost is near zero.
- **TTS (bonus):** use the browser's `window.speechSynthesis` SpeechSynthesis API (also already used in `ChatPage.js`'s `speakText`). Extract that helper into a shared hook/util and reuse it for WhatsApp inbound-message playback.
- **Push-to-talk, not continuous:** simpler permissions story, fewer false triggers, matches the existing `ChatPage` 🎙️ pattern so users have one mental model.

**Main tradeoff:** server-side Whisper adds ~1–3 s latency on first use (model load) vs. the sub-second Web Speech API. We accept that in exchange for cross-browser support and reusing existing code. If latency becomes a complaint, Phase 2 can add an opt-in Web Speech API fast-path for Chrome.

**Existing 🎙️ icon:** lives only in `apps/web/src/pages/ChatPage.js`, NOT in the WhatsApp card. It's fully wired (lines 187–214): `MediaRecorder` → `audio/webm` file → attached → `chatService.postMessageWithFile` → backend `/chat/sessions/{id}/messages/upload` → `media_utils._build_audio_parts` → local Whisper → transcript. So the STT backend pipeline already exists for the chat flow — we just need to expose a lightweight variant of it for non-chat callers.

## File-by-file changes

### Backend

- **`apps/api/app/api/v1/media.py`** — **new file**. Single endpoint `POST /media/transcribe` that accepts a multipart upload (`UploadFile`), enforces `AUDIO_MIMES` and `MAX_AUDIO_SIZE` from `media_utils`, calls `transcribe_audio_bytes`, returns `{"transcript": str | null, "engine": "whisper-local" | "unavailable"}`. Auth via `deps.get_current_active_user` for tenant scoping.
- **`apps/api/app/api/v1/routes.py`** — register the new media router under `/media`.
- **`apps/api/requirements.txt`** — pin `openai-whisper`, `soundfile`, optionally `librosa`. Currently lazy-imported in `media_utils.transcribe_audio_bytes` and silently returns `None` when missing.
- **`apps/api/app/services/media_utils.py`** — no required changes for MVP. Polish: cache `whisper.load_model("base")` via `functools.lru_cache` to avoid reloading on every request (~1.5–3 s cold start).

### Frontend

- **`apps/web/src/services/mediaService.js`** — **new file**. Export `transcribeAudio(file)` → `POST /media/transcribe` with multipart form-data; 90 s timeout; returns `{ transcript, engine }`.
- **`apps/web/src/hooks/useVoiceInput.js`** — **new file**. Reusable custom hook wrapping the `ChatPage.js` 187–214 pattern: `MediaRecorder` + chunk ref + `startRecording/stopRecording` + `isRecording` state + auto-stop on unmount. Returns `{ isRecording, supported, error, start, stop, recordedBlob }`. Hook reuse on both ChatPage and WhatsAppChannelCard.
- **`apps/web/src/hooks/useSpeechSynthesis.js`** — **new file**. Wraps `window.speechSynthesis` + the `stripMarkdown` helper from ChatPage.js (lines 153–185). Returns `{ supported, speak, cancel, speaking }`.
- **`apps/web/src/components/WhatsAppChannelCard.js`** — **primary change**:
  1. Import `FaMicrophone`, `FaStop`, `FaVolumeUp`.
  2. `useVoiceInput()` and `useSpeechSynthesis()`.
  3. New `transcribing` state.
  4. New mic button next to Send (around lines 630–644). Hold semantics: `onMouseDown/onTouchStart → start`, `onMouseUp/onTouchEnd/onMouseLeave → stop`. Red + pulse while held. On release: `mediaService.transcribeAudio(blob)`, `setSendMessage(prev => prev ? prev + ' ' + transcript : transcript)`.
  5. Disable state when `!supported` with tooltip.
  6. **Bonus TTS toggle:** `FaVolumeUp` icon, persists `speakReplies` (localStorage). Phase 3 wires inbound-message playback (requires new inbound subscription — see Phasing).
- **`apps/web/src/services/channelService.js`** — no change; existing `sendWhatsApp({to, message})` reused as-is.

### Optional cleanup (polish)

- **`apps/web/src/pages/ChatPage.js`** — refactor lines 151–214 to consume `useVoiceInput` and `useSpeechSynthesis` (deduplication).

## API endpoints needed

### `POST /media/transcribe` (new)

- **Auth:** bearer token (same `deps.get_current_active_user` as `/chat/.../upload`).
- **Request:** `multipart/form-data` with single `file` field. MIME ∈ `media_utils.AUDIO_MIMES` (`audio/ogg`, `audio/mpeg`, `audio/mp4`, `audio/wav`, `audio/webm`, `audio/aac`). Size ≤ `MAX_AUDIO_SIZE` (25 MB).
- **Response 200:**
  ```json
  { "transcript": "send a message to John saying I will be late", "engine": "whisper-local", "duration_ms": 1740 }
  ```
- **Response 200 — STT unavailable but audio valid:**
  ```json
  { "transcript": null, "engine": "unavailable", "reason": "whisper_not_installed" }
  ```
- **Response 400:** unsupported MIME / file too large / empty body.
- **Response 401:** unauthenticated.

> Intentionally **not** reusing `/chat/sessions/{id}/messages/upload` — that creates a chat message, runs embeddings, and calls the LLM. Voice → WhatsApp wants raw transcription only.

## UX flow

1. User opens Integrations → WhatsApp card. Channel already linked (QR scanned).
2. "Test Send" form visible with `to`, `message`, and new **mic button** next to Send.
3. User types phone number into `sendTo`.
4. User **presses and holds** mic button. Turns red, pulses, shows "Recording… release to stop". Browser prompts for mic permission first time.
5. User speaks, releases. Button disables + spinner "Transcribing…".
6. Transcript lands in `sendMessage` textarea. User can edit/correct.
7. User clicks **Send** — existing flow handles the rest.
8. On error (no transcript, mic denied, unsupported browser), `Alert` appears in same slot.
9. **Bonus playback:** speaker toggle next to mic. When on + a new Luna reply arrives (Phase 3), browser speaks it via `useSpeechSynthesis.speak`.

## Permissions / browser concerns

- **Mic permission:** `navigator.mediaDevices.getUserMedia({audio: true})` triggers native prompt once; granted state persists per origin. Use a Bootstrap `Alert` (not the `alert()` ChatPage uses).
- **HTTPS requirement:** `getUserMedia` requires secure context. Dev `localhost` counts. Prod served over cloudflared HTTPS — fine.
- **Browser support:** `MediaRecorder` + `getUserMedia` in Chrome, Edge, Firefox, Safari 14.1+. `SpeechSynthesis` universal. Feature-detect via `typeof MediaRecorder !== 'undefined' && navigator.mediaDevices?.getUserMedia` — if false, hide mic button entirely.
- **MIME negotiation:** prefer `audio/webm`, fall back to `audio/ogg`. Backend `AUDIO_MIMES` accepts both.
- **iOS Safari:** may emit `audio/mp4` — already in `AUDIO_MIMES`.
- **Recording indicator:** small red `FaCircle` animated dot during capture (privacy-respectful, visible).

## Testing approach

### Manual, Chrome on macOS (MVP)

1. Open `https://localhost/integrations`, scroll to WhatsApp card, link phone via QR.
2. Fill `sendTo` with own WhatsApp number.
3. Click-hold mic button, grant permission, speak "hello from voice command", release.
4. Assert: transcript appears within ~3 s.
5. Click Send → assert message received on phone.
6. **Fallback tests:**
   - Deny mic → expect red Alert.
   - Browser w/o `MediaRecorder` → mic button hidden.
   - Silent/garbage audio → `transcript: null`, user-facing "Couldn't understand that".
   - Backend whisper missing → endpoint returns `engine: "unavailable"` → same graceful fallback.
7. **TTS bonus:** toggle speaker, confirm `window.speechSynthesis` speaks a known text.

### Automated

- **Backend** `apps/api/tests/test_media_transcribe.py` — TestClient posts a small `audio/wav` fixture (1 s sine wave); asserts 200 with either transcript or `engine: "unavailable"` (parametrized so CI without whisper passes).
- **Backend reject test:** post `image/png` → expect 400.
- **Frontend:** RTL test for `useVoiceInput` stubbing `navigator.mediaDevices.getUserMedia` and `MediaRecorder`; assert state transitions.

## Phasing

### MVP (~1–1.5 days)

1. Backend `/media/transcribe` + router + whisper pin.
2. Frontend `useVoiceInput` hook + `mediaService.transcribeAudio`.
3. Wire mic button into `WhatsAppChannelCard.js` Test Send.
4. Feature-detection + denial fallbacks.
5. Backend unit test + manual checklist.

### Phase 2 (polish)

6. Extract `useSpeechSynthesis` and have ChatPage consume it.
7. LRU-cache the whisper model.
8. Recording timer + waveform indicator (AnalyserNode).
9. Persist `speakReplies` in localStorage.
10. Optional Chrome fast-path: `webkitSpeechRecognition` opt-in to skip server round-trip.

### Phase 3 (requires new infra — bonus inbound TTS)

11. WhatsApp card today is send-only. To speak inbound Luna replies, need either (a) a WebSocket from `whatsapp_service.py` or (b) a polling endpoint `GET /channels/whatsapp/recent?after=<ts>`. Recommend (b) for MVP — 20-line backend change, no WebSockets just for this.

## Anticipated challenges

- **Whisper cold start** — 1.5–3 s on first request because `whisper.load_model("base")` is called per invocation. Phase 2 caching solves it; MVP ships with a spinner.
- **Sample-rate quirks** — iOS/Android WebView may record at 44.1 or 48 kHz; `librosa` resample handles it but must be installed.
- **Whisper missing in prod** — currently lazy-imported with no requirement pin → STT probably already returns `None` in prod. New endpoint will expose this. Pin `openai-whisper` + `soundfile` as part of MVP.
- **Don't add `webkitSpeechRecognition` blindly** — silently sends audio to Google.
- **Permission revoke UX** — `getUserMedia` rejects with `NotAllowedError`; surface remediation hint ("Click the lock icon → Site settings → Microphone → Allow").

## Critical files for implementation

- `apps/web/src/components/WhatsAppChannelCard.js`
- `apps/api/app/services/media_utils.py`
- `apps/api/app/api/v1/channels.py` (router-registration reference; new media router at `apps/api/app/api/v1/media.py`)
- `apps/web/src/pages/ChatPage.js` (reference implementation of MediaRecorder + SpeechSynthesis)
- `apps/api/requirements.txt` (whisper/soundfile pins)
