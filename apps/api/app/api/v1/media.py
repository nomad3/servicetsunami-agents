from fastapi import APIRouter, Depends, File, UploadFile, HTTPException, status
from app.api import deps
from app.services import media_utils
from app.models.user import User
import time
import logging

router = APIRouter()
logger = logging.getLogger(__name__)

@router.post("/transcribe")
async def transcribe_audio(
    file: UploadFile = File(...),
    current_user: User = Depends(deps.get_current_active_user)
):
    """
    Transcribe an uploaded audio file using local Whisper.
    """
    if not file.content_type.startswith("audio/"):
        # Check if it's in our allowed list even if it doesn't start with audio/
        if file.content_type not in media_utils.AUDIO_MIMES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unsupported media type: {file.content_type}. Must be audio."
            )

    try:
        # Use a temporary file to avoid loading everything into RAM
        import tempfile
        import shutil

        with tempfile.NamedTemporaryFile(delete=True) as tmp:
            # Stream the upload to the temp file
            size = 0
            while chunk := await file.read(1024 * 1024):  # 1MB chunks
                size += len(chunk)
                if size > media_utils.MAX_AUDIO_SIZE:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"Audio file too large. Max size is {media_utils.MAX_AUDIO_SIZE // (1024*1024)}MB."
                    )
                tmp.write(chunk)
            
            tmp.flush()
            tmp.seek(0)
            content = tmp.read() # Read for transcription

        start_time = time.time()
        transcript = media_utils.transcribe_audio_bytes(content)
        duration_ms = int((time.time() - start_time) * 1000)

        # Determine engine status
        try:
            import whisper
            engine = "whisper-local"
        except ImportError:
            engine = "unavailable"

        if transcript is None and engine == "unavailable":
            return {
                "transcript": None,
                "engine": "unavailable",
                "reason": "whisper_not_installed",
                "duration_ms": duration_ms
            }

        return {
            "transcript": transcript,
            "engine": engine,
            "duration_ms": duration_ms
        }

    except Exception as e:
        logger.exception("Transcription endpoint failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )
