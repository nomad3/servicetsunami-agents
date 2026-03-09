"""
Media processing utilities for converting raw media bytes into
ADK-compatible message parts (inline_data / text).
"""

import base64
import io
import logging
from typing import Dict, List, Tuple

logger = logging.getLogger(__name__)

# ── MIME-type sets ──────────────────────────────────────────────────────────

IMAGE_MIMES = {
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/gif",
    "image/heic",
}

AUDIO_MIMES = {
    "audio/ogg",
    "audio/mpeg",
    "audio/mp4",
    "audio/wav",
    "audio/webm",
    "audio/aac",
}

PDF_MIMES = {"application/pdf"}

# ── Size limits (bytes) ────────────────────────────────────────────────────

MAX_IMAGE_SIZE = 10 * 1024 * 1024   # 10 MB
MAX_AUDIO_SIZE = 25 * 1024 * 1024   # 25 MB
MAX_PDF_SIZE = 20 * 1024 * 1024     # 20 MB

# ── Default prompts ────────────────────────────────────────────────────────

DEFAULT_IMAGE_PROMPT = "The user sent this image. Describe what you see and respond helpfully."
DEFAULT_AUDIO_PROMPT = "The user sent this voice message. Transcribe and respond to it."
DEFAULT_PDF_PROMPT = "The user sent a PDF. Please review and respond."

# ── PDF extraction limits ──────────────────────────────────────────────────

MAX_PDF_PAGES = 30
MAX_PDF_CHARS = 50_000


# ── Public API ──────────────────────────────────────────────────────────────

def classify_media(mime_type: str) -> str:
    """Classify a MIME type into image, audio, pdf, or unsupported."""
    # Clean the mime_type (handles "audio/ogg; codecs=opus")
    clean = mime_type.split(";")[0].strip().lower()

    if clean in IMAGE_MIMES:
        return "image"
    if clean in AUDIO_MIMES or clean.startswith("audio/"):
        return "audio"
    if clean in PDF_MIMES:
        return "pdf"
    return "unsupported"


def build_media_parts(
    media_bytes: bytes,
    mime_type: str,
    caption: str = "",
    filename: str = "",
) -> Tuple[List[Dict], Dict]:
    """
    Convert raw media bytes into ADK-compatible message parts.

    Returns:
        (parts, attachment_meta)
        - parts: list of ADK message-part dicts
        - attachment_meta: metadata dict with type, mime_type, size_bytes, filename
    """
    clean_mime = mime_type.split(";")[0].strip().lower()
    media_class = classify_media(clean_mime)

    if media_class == "unsupported":
        raise ValueError(f"Unsupported media type: {mime_type}")

    size = len(media_bytes)

    # Enforce size limits
    if media_class == "image" and size > MAX_IMAGE_SIZE:
        raise ValueError(
            f"Image too large: {size} bytes (max {MAX_IMAGE_SIZE} bytes)"
        )
    if media_class == "audio" and size > MAX_AUDIO_SIZE:
        raise ValueError(
            f"Audio too large: {size} bytes (max {MAX_AUDIO_SIZE} bytes)"
        )
    if media_class == "pdf" and size > MAX_PDF_SIZE:
        raise ValueError(
            f"PDF too large: {size} bytes (max {MAX_PDF_SIZE} bytes)"
        )

    # Build parts by media class
    if media_class == "image":
        parts = _build_image_parts(media_bytes, clean_mime, caption)
    elif media_class == "audio":
        parts = _build_audio_parts(media_bytes, clean_mime, caption)
    else:
        parts = _build_pdf_parts(media_bytes, caption, filename)

    attachment_meta = {
        "type": media_class,
        "mime_type": clean_mime,
        "size_bytes": size,
        "filename": filename,
    }

    return parts, attachment_meta


# ── Internal helpers ────────────────────────────────────────────────────────

def _build_image_parts(
    image_bytes: bytes,
    mime_type: str,
    caption: str,
) -> List[Dict]:
    """Base64-encode an image and return ADK inline_data + text parts."""
    b64 = base64.b64encode(image_bytes).decode("utf-8")
    text = caption if caption else DEFAULT_IMAGE_PROMPT

    return [
        {"inline_data": {"mime_type": mime_type, "data": b64}},
        {"text": text},
    ]


def _build_audio_parts(
    audio_bytes: bytes,
    mime_type: str,
    caption: str,
) -> List[Dict]:
    """Base64-encode audio and return ADK inline_data + text parts."""
    b64 = base64.b64encode(audio_bytes).decode("utf-8")
    text = caption if caption else DEFAULT_AUDIO_PROMPT

    return [
        {"inline_data": {"mime_type": mime_type, "data": b64}},
        {"text": text},
    ]


def _build_pdf_parts(
    pdf_bytes: bytes,
    caption: str,
    filename: str,
) -> List[Dict]:
    """Extract text from a PDF with pdfplumber and return a text part."""
    import pdfplumber

    extracted_pages: List[str] = []

    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            pages_to_read = pdf.pages[:MAX_PDF_PAGES]
            for i, page in enumerate(pages_to_read):
                page_text = page.extract_text()
                if page_text:
                    extracted_pages.append(f"--- Page {i + 1} ---\n{page_text}")

            total_pages = len(pdf.pages)
    except Exception:
        logger.exception("Failed to extract text from PDF")
        extracted_pages = ["[Could not extract PDF text]"]
        total_pages = 0

    full_text = "\n\n".join(extracted_pages)

    # Truncate if too long
    truncated = False
    if len(full_text) > MAX_PDF_CHARS:
        full_text = full_text[:MAX_PDF_CHARS]
        truncated = True

    # Build header
    header_parts = []
    if filename:
        header_parts.append(f"Filename: {filename}")
    header_parts.append(f"Pages: {min(total_pages, MAX_PDF_PAGES)}/{total_pages}")
    if truncated:
        header_parts.append(f"(truncated to {MAX_PDF_CHARS} chars)")
    header = " | ".join(header_parts)

    prompt = caption if caption else DEFAULT_PDF_PROMPT

    content = f"{prompt}\n\n--- PDF Content ({header}) ---\n{full_text}"

    return [{"text": content}]
