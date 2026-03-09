# Luna Multimedia Support Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Enable Luna to process images, audio voice notes, and PDFs from both WhatsApp and the web chat UI.

**Architecture:** Hybrid approach — images are sent as inline_data parts directly to Gemini via ADK multimodal messaging. Audio voice notes are transcribed via Gemini multimodal (proven pattern from vet_tools.py). PDFs are text-extracted server-side and sent as text context. Media bytes are stored ephemerally in memory (not persisted to disk/GCS) — only a description/summary is stored in the chat message context.

**Tech Stack:** neonize (WhatsApp media download), google-genai (Gemini multimodal), pdfplumber (PDF text extraction), base64 encoding, FastAPI UploadFile

---

## Summary of Changes

| Layer | File | Change |
|-------|------|--------|
| ADK Client | `apps/api/app/services/adk_client.py` | Add `parts` parameter to `run()` for multimodal messages |
| Chat Service | `apps/api/app/services/chat.py` | Accept + forward media parts; store attachment metadata in context |
| Chat Schema | `apps/api/app/schemas/chat.py` | Add `ChatMessageWithAttachment` schema |
| Chat Route | `apps/api/app/api/v1/chat.py` | Add file upload endpoint (multipart/form-data) |
| Media Utils | `apps/api/app/services/media_utils.py` | Media processing: PDF→text, image→base64 parts, audio→base64 parts |
| WhatsApp | `apps/api/app/services/whatsapp_service.py` | Download media from neonize, build multimodal parts |
| Web Chat UI | `apps/web/src/pages/ChatPage.js` | File upload button + attachment preview |
| Web Service | `apps/web/src/services/chat.js` | `postMessageWithFile()` using FormData |
| Dependencies | `apps/api/requirements.txt` | Add `pdfplumber` |

---

### Task 1: ADK Client Multimodal Support

**Files:**
- Modify: `apps/api/app/services/adk_client.py:57-103`

**Step 1: Add `parts` parameter to `run()` method**

The ADK `/run` endpoint already accepts `parts` array in `new_message`. We just need to pass multimodal parts through.

```python
def run(
    self,
    *,
    user_id: uuid.UUID,
    session_id: str,
    message: Optional[str] = None,
    parts: Optional[List[Dict[str, Any]]] = None,
    state_delta: Optional[Dict[str, Any]] = None,
    max_retries: int = 3,
) -> List[Dict[str, Any]]:
    # Build message parts — support both old (text-only) and new (multimodal) API
    if parts:
        message_parts = parts
    elif message:
        message_parts = [{"text": message}]
    else:
        raise ValueError("Either 'message' or 'parts' must be provided")

    body: Dict[str, Any] = {
        "app_name": self.app_name,
        "user_id": str(user_id),
        "session_id": session_id,
        "new_message": {
            "role": "user",
            "parts": message_parts,
        },
    }
    if state_delta:
        body["state_delta"] = state_delta
    # ... rest of retry logic unchanged
```

**Step 2: Commit**

```bash
git add apps/api/app/services/adk_client.py
git commit -m "feat: add multimodal parts support to ADK client run()"
```

---

### Task 2: Media Processing Utilities

**Files:**
- Create: `apps/api/app/services/media_utils.py`
- Modify: `apps/api/requirements.txt`

**Step 1: Add pdfplumber dependency**

Add to `apps/api/requirements.txt`:
```
pdfplumber>=0.10.0
```

**Step 2: Create media_utils.py**

This module converts raw media bytes into ADK-compatible message parts.

```python
"""Media processing utilities for multimodal agent messaging.

Converts images, audio, and PDFs into ADK-compatible message parts
(inline_data for Gemini vision/audio, extracted text for PDFs).
"""
import base64
import io
import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Supported MIME types by category
IMAGE_MIMES = {"image/jpeg", "image/png", "image/webp", "image/gif", "image/heic"}
AUDIO_MIMES = {"audio/ogg", "audio/mpeg", "audio/mp4", "audio/wav", "audio/webm",
               "audio/ogg; codecs=opus", "audio/aac"}
PDF_MIMES = {"application/pdf"}

# Max sizes (bytes)
MAX_IMAGE_SIZE = 10 * 1024 * 1024   # 10MB
MAX_AUDIO_SIZE = 25 * 1024 * 1024   # 25MB
MAX_PDF_SIZE = 20 * 1024 * 1024     # 20MB


def classify_media(mime_type: str) -> str:
    """Return 'image', 'audio', 'pdf', or 'unsupported'."""
    mime_clean = mime_type.split(";")[0].strip().lower()
    if mime_clean in IMAGE_MIMES:
        return "image"
    if mime_clean in AUDIO_MIMES or mime_clean.startswith("audio/"):
        return "audio"
    if mime_clean in PDF_MIMES:
        return "pdf"
    return "unsupported"


def build_media_parts(
    media_bytes: bytes,
    mime_type: str,
    caption: str = "",
    filename: str = "",
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Convert media bytes into ADK message parts + attachment metadata.

    Returns:
        (parts, attachment_meta) where parts is a list of ADK message part dicts
        and attachment_meta is metadata to store in the chat message context.
    """
    media_type = classify_media(mime_type)
    mime_clean = mime_type.split(";")[0].strip()

    if media_type == "image":
        if len(media_bytes) > MAX_IMAGE_SIZE:
            raise ValueError(f"Image too large ({len(media_bytes)} bytes, max {MAX_IMAGE_SIZE})")
        parts = _build_image_parts(media_bytes, mime_clean, caption)
        meta = {
            "type": "image",
            "mime_type": mime_clean,
            "size_bytes": len(media_bytes),
            "filename": filename,
        }

    elif media_type == "audio":
        if len(media_bytes) > MAX_AUDIO_SIZE:
            raise ValueError(f"Audio too large ({len(media_bytes)} bytes, max {MAX_AUDIO_SIZE})")
        parts = _build_audio_parts(media_bytes, mime_clean, caption)
        meta = {
            "type": "audio",
            "mime_type": mime_clean,
            "size_bytes": len(media_bytes),
            "filename": filename,
        }

    elif media_type == "pdf":
        if len(media_bytes) > MAX_PDF_SIZE:
            raise ValueError(f"PDF too large ({len(media_bytes)} bytes, max {MAX_PDF_SIZE})")
        parts = _build_pdf_parts(media_bytes, caption, filename)
        meta = {
            "type": "pdf",
            "mime_type": mime_clean,
            "size_bytes": len(media_bytes),
            "filename": filename,
        }

    else:
        raise ValueError(f"Unsupported media type: {mime_type}")

    return parts, meta


def _build_image_parts(
    image_bytes: bytes, mime_type: str, caption: str,
) -> List[Dict[str, Any]]:
    """Build inline_data part for Gemini vision."""
    b64 = base64.b64encode(image_bytes).decode("ascii")
    parts = [
        {"inline_data": {"mime_type": mime_type, "data": b64}},
    ]
    text = caption or "The user sent this image. Describe what you see and respond helpfully."
    parts.append({"text": text})
    return parts


def _build_audio_parts(
    audio_bytes: bytes, mime_type: str, caption: str,
) -> List[Dict[str, Any]]:
    """Build inline_data part for Gemini audio understanding."""
    b64 = base64.b64encode(audio_bytes).decode("ascii")
    parts = [
        {"inline_data": {"mime_type": mime_type, "data": b64}},
    ]
    text = caption or "The user sent this voice message. Transcribe and respond to it."
    parts.append({"text": text})
    return parts


def _build_pdf_parts(
    pdf_bytes: bytes, caption: str, filename: str,
) -> List[Dict[str, Any]]:
    """Extract text from PDF and send as text context."""
    try:
        import pdfplumber
        text_pages = []
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            for i, page in enumerate(pdf.pages[:30]):  # Cap at 30 pages
                page_text = page.extract_text()
                if page_text:
                    text_pages.append(f"--- Page {i+1} ---\n{page_text}")
        extracted = "\n\n".join(text_pages)
    except Exception as e:
        logger.warning("PDF text extraction failed: %s", e)
        extracted = "[Could not extract text from this PDF]"

    # Truncate to ~50K chars to stay within context limits
    if len(extracted) > 50000:
        extracted = extracted[:50000] + "\n\n[... truncated, document too long ...]"

    label = f"PDF document: {filename}" if filename else "PDF document"
    user_text = caption or f"The user sent a PDF. Please review and respond."
    context = f"[{label}]\n\n{extracted}\n\n---\n{user_text}"
    return [{"text": context}]
```

**Step 3: Commit**

```bash
git add apps/api/app/services/media_utils.py apps/api/requirements.txt
git commit -m "feat: add media processing utils for image/audio/PDF"
```

---

### Task 3: Chat Service — Accept & Forward Media Parts

**Files:**
- Modify: `apps/api/app/services/chat.py:169-316`

**Step 1: Update `post_user_message()` to accept media parts**

```python
def post_user_message(
    db: Session,
    *,
    session: ChatSessionModel,
    user_id: uuid.UUID,
    content: str,
    sender_phone: str | None = None,
    media_parts: list | None = None,
    attachment_meta: dict | None = None,
) -> Tuple[ChatMessage, ChatMessage]:
    # Store attachment metadata in the user message context
    user_context = {"attachment": attachment_meta} if attachment_meta else None
    user_message = _append_message(
        db, session=session, role="user", content=content, context=user_context,
    )
    assistant_message = _generate_agentic_response(
        db,
        session=session,
        user_id=user_id,
        user_message=content,
        sender_phone=sender_phone,
        media_parts=media_parts,
    )
    return user_message, assistant_message
```

**Step 2: Update `_generate_agentic_response()` to forward media parts**

Add `media_parts: list | None = None` parameter. In the ADK client call:

```python
# Around line 310-316, replace the client.run() call:
if media_parts:
    # Multimodal: merge text + media parts
    all_parts = media_parts  # media_parts already includes text part
    events = client.run(
        user_id=user_id,
        session_id=str(adk_session_id),
        parts=all_parts,
        state_delta=state_delta,
    )
else:
    events = client.run(
        user_id=user_id,
        session_id=str(adk_session_id),
        message=user_message,
        state_delta=state_delta,
    )
```

And in the retry path (after session re-creation, around line 380):

```python
if media_parts:
    events = client.run(
        user_id=user_id,
        session_id=str(new_adk_session_id),
        parts=media_parts,
        state_delta=state_delta,
    )
else:
    events = client.run(
        user_id=user_id,
        session_id=str(new_adk_session_id),
        message=user_message,
        state_delta=state_delta,
    )
```

**Step 3: Commit**

```bash
git add apps/api/app/services/chat.py
git commit -m "feat: pass multimodal media parts through chat service to ADK"
```

---

### Task 4: Chat API — File Upload Endpoint

**Files:**
- Modify: `apps/api/app/api/v1/chat.py:110-135`
- Modify: `apps/api/app/schemas/chat.py`

**Step 1: Add upload endpoint to chat route**

Add a new endpoint that accepts multipart/form-data (text + file):

```python
from fastapi import File, Form, UploadFile

@router.post(
    "/sessions/{session_id}/messages/upload",
    response_model=chat_schema.ChatTurn,
    status_code=status.HTTP_201_CREATED,
)
async def post_message_with_file(
    session_id: uuid.UUID,
    content: str = Form(""),
    file: UploadFile = File(...),
    *,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    """Post a message with a file attachment (image, audio, or PDF)."""
    session = chat_service.get_session(
        db, session_id=session_id, tenant_id=current_user.tenant_id,
    )
    if not session:
        raise HTTPException(status_code=404, detail="Chat session not found")

    from app.services.media_utils import build_media_parts, classify_media

    file_bytes = await file.read()
    mime_type = file.content_type or "application/octet-stream"

    media_type = classify_media(mime_type)
    if media_type == "unsupported":
        raise HTTPException(status_code=400, detail=f"Unsupported file type: {mime_type}")

    try:
        parts, attachment_meta = build_media_parts(
            media_bytes=file_bytes,
            mime_type=mime_type,
            caption=content,
            filename=file.filename or "",
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    user_msg, assistant_msg = chat_service.post_user_message(
        db,
        session=session,
        user_id=current_user.id,
        content=content or f"[Sent {media_type}: {file.filename}]",
        media_parts=parts,
        attachment_meta=attachment_meta,
    )
    return chat_schema.ChatTurn(
        user_message=chat_schema.ChatMessage.model_validate(user_msg),
        assistant_message=chat_schema.ChatMessage.model_validate(assistant_msg),
    )
```

**Step 2: Commit**

```bash
git add apps/api/app/api/v1/chat.py apps/api/app/schemas/chat.py
git commit -m "feat: add file upload endpoint for chat messages"
```

---

### Task 5: WhatsApp Media Reception

**Files:**
- Modify: `apps/api/app/services/whatsapp_service.py:390-530`

**Step 1: Detect and download media messages**

Replace the early return on empty text (lines 408-409) with media detection:

```python
# After extracting text (line 403):
text = msg.conversation or (msg.extendedTextMessage.text if msg.extendedTextMessage else "")

# Detect media messages
media_bytes = None
media_mime = None
media_type = None
media_caption = text  # text may come from caption on media messages

if msg.imageMessage and msg.imageMessage.url:
    media_mime = msg.imageMessage.mimetype or "image/jpeg"
    media_caption = msg.imageMessage.caption or text
    media_type = "image"
elif msg.audioMessage and msg.audioMessage.url:
    media_mime = msg.audioMessage.mimetype or "audio/ogg"
    media_type = "audio"
elif msg.documentMessage and msg.documentMessage.url:
    media_mime = msg.documentMessage.mimetype or "application/pdf"
    media_caption = msg.documentMessage.title or msg.documentMessage.fileName or text
    media_type = "document"

# Download media if present
if media_type:
    try:
        media_bytes = await asyncio.wait_for(
            client.download_any(event.Message), timeout=30,
        )
        logger.info(f"Downloaded {media_type} ({len(media_bytes)} bytes) from {sender_phone}")
    except Exception as e:
        logger.warning(f"Failed to download {media_type} from {sender_phone}: {e}")
        media_bytes = None

# Skip if no text AND no media
if not text and not media_bytes:
    return
```

**Step 2: Build media parts and pass to agent**

Replace the `_process_through_agent` call (line 489) with media-aware version:

```python
# Process through agent — use phone number (not LID) as session key
if media_bytes:
    from app.services.media_utils import build_media_parts
    try:
        parts, _ = build_media_parts(
            media_bytes=media_bytes,
            mime_type=media_mime,
            caption=media_caption or "",
            filename=media_caption or "",
        )
        response_text = await self._process_through_agent(
            tenant_id, sender_phone,
            media_caption or f"[Sent {media_type}]",
            media_parts=parts,
        )
    except ValueError as e:
        logger.warning(f"Media processing failed for {sender_phone}: {e}")
        response_text = await self._process_through_agent(
            tenant_id, sender_phone, text or "[Unsupported media]",
        )
else:
    response_text = await self._process_through_agent(tenant_id, sender_phone, text)
```

**Step 3: Update `_process_through_agent` to accept media_parts**

```python
async def _process_through_agent(
    self, tenant_id: str, sender_id: str, message: str,
    media_parts: list | None = None,
) -> Optional[str]:
```

And pass `media_parts` through to `chat_service.post_user_message()`:

```python
user_msg, assistant_msg = chat_service.post_user_message(
    db,
    session=session,
    user_id=owner.id,
    content=message,
    sender_phone=sender_id,
    media_parts=media_parts,
)
```

**Step 4: Commit**

```bash
git add apps/api/app/services/whatsapp_service.py
git commit -m "feat: download and process WhatsApp media messages (images, audio, PDFs)"
```

---

### Task 6: Web Chat UI — File Upload

**Files:**
- Modify: `apps/web/src/services/chat.js`
- Modify: `apps/web/src/pages/ChatPage.js`

**Step 1: Add `postMessageWithFile` to chat service**

```javascript
// In apps/web/src/services/chat.js
const postMessageWithFile = (sessionId, content, file) => {
  const formData = new FormData();
  formData.append('content', content || '');
  formData.append('file', file);
  return api.post(`/chat/sessions/${sessionId}/messages/upload`, formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
    timeout: 120000,  // 2 min for large files
  });
};
```

**Step 2: Add file upload UI to ChatPage**

Add state for file attachment:
```javascript
const [attachedFile, setAttachedFile] = useState(null);
const fileInputRef = useRef(null);
```

Add hidden file input + attachment button next to the send button:
```jsx
<input
  type="file"
  ref={fileInputRef}
  style={{ display: 'none' }}
  accept="image/*,audio/*,.pdf"
  onChange={(e) => {
    if (e.target.files[0]) setAttachedFile(e.target.files[0]);
  }}
/>
```

Add a paperclip/attachment icon button that triggers `fileInputRef.current.click()`.

Show a small preview chip when a file is attached (filename + X to remove).

**Step 3: Update `handleMessageSubmit` to handle file uploads**

```javascript
const handleMessageSubmit = async (event) => {
  event.preventDefault();
  if ((!messageDraft.trim() && !attachedFile) || !selectedSession) return;

  setPostingMessage(true);
  setGlobalError('');
  try {
    let response;
    if (attachedFile) {
      response = await chatService.postMessageWithFile(
        selectedSession.id, messageDraft.trim(), attachedFile,
      );
      setAttachedFile(null);
      if (fileInputRef.current) fileInputRef.current.value = '';
    } else {
      response = await chatService.postMessage(selectedSession.id, messageDraft.trim());
    }
    setMessages((prev) => [...prev, response.data.user_message, response.data.assistant_message]);
    setMessageDraft('');
  } catch (err) {
    console.error(err);
    setGlobalError('Failed to send message to agent.');
  } finally {
    setPostingMessage(false);
  }
};
```

**Step 4: Show attachment indicator on user messages**

In the message rendering, check `msg.context?.attachment` and show a small badge/icon (e.g., image icon, document icon) next to the message content.

**Step 5: Commit**

```bash
git add apps/web/src/services/chat.js apps/web/src/pages/ChatPage.js
git commit -m "feat: add file upload UI for chat messages"
```

---

### Task 7: Luna Agent Instructions Update

**Files:**
- Modify: `apps/adk-server/servicetsunami_supervisor/personal_assistant.py`
- Modify: `apps/adk-server/servicetsunami_supervisor/agent.py`

**Step 1: Add multimedia awareness to Luna's instructions**

Add a section to Luna's instruction text:

```
## Multimedia Messages
You can receive images, audio voice notes, and PDF documents from users.
- **Images**: You can see images directly. Describe what you see, answer questions about the image, or extract information as needed.
- **Audio**: Voice notes are transcribed for you. Respond to the content of what the user said.
- **PDFs**: Document text is extracted and provided to you. Summarize, answer questions, or extract data as requested.

When receiving media, acknowledge the type of content ("I can see your image", "I heard your voice note", "I've reviewed the document") before responding to the content.
```

**Step 2: Commit**

```bash
git add apps/adk-server/servicetsunami_supervisor/personal_assistant.py apps/adk-server/servicetsunami_supervisor/agent.py
git commit -m "feat: add multimedia message instructions to Luna"
```

---

### Task 8: Deploy & Verify

**Step 1: Build and push API image**

The API Dockerfile should already include the new files. Verify `pdfplumber` is in requirements.txt.

**Step 2: Deploy via GitHub Actions**

```bash
git push origin main
# API, Web, and ADK workflows trigger automatically on path changes
```

**Step 3: Verify end-to-end**

1. **Web UI**: Open chat, click attachment button, upload an image → verify Luna describes the image
2. **Web UI**: Upload a PDF → verify Luna summarizes the content
3. **WhatsApp**: Send a photo to the bot → verify Luna responds about the image
4. **WhatsApp**: Send a voice note → verify Luna transcribes and responds
5. **WhatsApp**: Send a PDF document → verify Luna processes and responds

---

## Implementation Notes

- **No database migration needed**: Attachment metadata is stored in the existing `context` JSON column on `ChatMessage`
- **No GCS/cloud storage needed**: Media bytes are processed in-memory and sent directly to Gemini as base64. Only metadata (type, size, filename) is persisted
- **Backward compatible**: The `run()` method still accepts `message: str` — existing callers don't need changes
- **Size limits**: Images 10MB, audio 25MB, PDFs 20MB. These are generous for WhatsApp (which compresses media) and reasonable for web uploads
- **Gemini model**: Uses whatever model is configured in ADK settings. Gemini 2.5 Flash and Pro both support multimodal input
