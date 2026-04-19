import logging
import os
import tempfile
import time

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status

from app.api import deps
from app.models.user import User
from app.services import media_utils

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("/transcribe")
async def transcribe_audio(
    file: UploadFile = File(...),
    current_user: User = Depends(deps.get_current_active_user),
):
    """Transcribe an uploaded audio file using local Whisper.

    The file is streamed to a disk-backed temp file and passed by PATH to the
    transcription routine — soundfile reads from disk directly, so we never
    hold the whole payload in RAM. Oversized uploads are rejected mid-stream.
    """
    if not file.content_type.startswith("audio/") and file.content_type not in media_utils.AUDIO_MIMES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported media type: {file.content_type}. Must be audio.",
        )

    tmp_path: str | None = None
    try:
        # delete=False so we control cleanup and can pass the path to whisper
        with tempfile.NamedTemporaryFile(delete=False, suffix=".audio") as tmp:
            tmp_path = tmp.name
            size = 0
            while chunk := await file.read(1024 * 1024):  # 1 MB chunks
                size += len(chunk)
                if size > media_utils.MAX_AUDIO_SIZE:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"Audio file too large. Max size is {media_utils.MAX_AUDIO_SIZE // (1024 * 1024)}MB.",
                    )
                tmp.write(chunk)

        start_time = time.time()
        transcript = media_utils.transcribe_audio_path(tmp_path)
        duration_ms = int((time.time() - start_time) * 1000)

        try:
            import whisper  # noqa: F401
            engine = "whisper-local"
        except ImportError:
            engine = "unavailable"

        if transcript is None and engine == "unavailable":
            return {
                "transcript": None,
                "engine": "unavailable",
                "reason": "whisper_not_installed",
                "duration_ms": duration_ms,
            }

        return {
            "transcript": transcript,
            "engine": engine,
            "duration_ms": duration_ms,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Transcription endpoint failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e),
        )
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                logger.debug("Temp file cleanup skipped", exc_info=True)
