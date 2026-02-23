"""
speech.py – Custom Speech-to-Text (STT) and Text-to-Speech (TTS) endpoints.

STT: POST /speech/stt
  - Accepts an audio file upload (webm, wav, mp3, ogg, etc.)
  - Transcribes using faster-whisper (local OpenAI Whisper, CPU-friendly)
  - Returns: { "transcript": "..." }

TTS: POST /speech/tts
  - Accepts JSON: { "text": "...", "voice": "en-US-AriaNeural" }
  - Synthesises using edge-tts (Microsoft Edge TTS, free, no API key)
  - Returns: audio/mpeg stream

Available voices (en-US, high quality):
  en-US-AriaNeural       – friendly female (default)
  en-US-GuyNeural        – friendly male
  en-US-JennyNeural      – warm female
  en-US-DavisNeural      – expressive male
  en-GB-SoniaNeural      – British female
  en-IN-NeerjaNeural     – Indian female

List all voices:  python -m edge_tts --list-voices
"""

import io
import os
import tempfile

try:
    import edge_tts
    _EDGE_TTS_AVAILABLE = True
except Exception:
    edge_tts = None
    _EDGE_TTS_AVAILABLE = False
from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from faster_whisper import WhisperModel
from pydantic import BaseModel

router = APIRouter(prefix="/speech", tags=["speech"])

# ---------------------------------------------------------------------------
# Whisper model (loaded once at import time)
# Using small.en for fast CPU inference; change to "base" for even lighter.
# ---------------------------------------------------------------------------
_WHISPER_MODEL_SIZE = os.getenv("WHISPER_MODEL", "small.en")
_whisper: WhisperModel | None = None


def _get_whisper() -> WhisperModel:
    global _whisper
    if _whisper is None:
        print(f"🧠 Loading Whisper model: {_WHISPER_MODEL_SIZE}")
        _whisper = WhisperModel(
            _WHISPER_MODEL_SIZE,
            device="cpu",
            compute_type="int8",   # fastest safe option on CPU
        )
        print("✅ Whisper model ready")
    return _whisper


# ---------------------------------------------------------------------------
# STT endpoint
# ---------------------------------------------------------------------------
@router.post("/stt")
async def speech_to_text(file: UploadFile = File(...)):
    """
    Transcribe an audio file.
    Accepts: webm, wav, mp3, ogg, m4a, flac (any format ffmpeg can decode)
    Returns: { "transcript": "...", "language": "en", "duration_s": 4.2 }
    """
    audio_bytes = await file.read()
    if not audio_bytes:
        raise HTTPException(status_code=400, detail="Empty audio file")

    # Write to a temp file so faster-whisper can read it
    suffix = os.path.splitext(file.filename or "audio.webm")[1] or ".webm"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(audio_bytes)
        tmp_path = tmp.name

    try:
        model = _get_whisper()
        segments, info = model.transcribe(
            tmp_path,
            beam_size=3,
            vad_filter=True,           # skip silence automatically
            vad_parameters={"min_silence_duration_ms": 500},
        )
        transcript = " ".join(seg.text.strip() for seg in segments)
    finally:
        os.unlink(tmp_path)

    return {
        "transcript": transcript.strip(),
        "language": info.language,
        "duration_s": round(info.duration, 2),
    }


# ---------------------------------------------------------------------------
# TTS endpoint
# ---------------------------------------------------------------------------
class TTSRequest(BaseModel):
    text: str
    voice: str = "en-US-AriaNeural"
    rate: str = "+0%"    # e.g. "+10%", "-10%"
    pitch: str = "+0Hz"  # e.g. "+5Hz"


@router.post("/tts")
async def text_to_speech(req: TTSRequest):
    """
    Synthesise speech from text.
    Returns an audio/mpeg stream (MP3).
    """
    if not req.text.strip():
        raise HTTPException(status_code=400, detail="Text cannot be empty")

    if not _EDGE_TTS_AVAILABLE:
        raise HTTPException(
            status_code=503,
            detail=("TTS backend not available: missing optional dependency 'edge-tts'. "
                    "Install it in your environment (pip install edge-tts)."),
        )

    try:
        communicate = edge_tts.Communicate(req.text, req.voice, rate=req.rate, pitch=req.pitch)
        audio_buf = io.BytesIO()
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio_buf.write(chunk["data"])
        audio_buf.seek(0)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"TTS failed: {e}")

    return StreamingResponse(
        audio_buf,
        media_type="audio/mpeg",
        headers={"Content-Disposition": "inline; filename=speech.mp3"},
    )


# ---------------------------------------------------------------------------
# List available voices (helper / debugging)
# ---------------------------------------------------------------------------
@router.get("/tts/voices")
async def list_voices(locale: str = "en"):
    """Return all available edge-tts voices, optionally filtered by locale prefix."""
    if not _EDGE_TTS_AVAILABLE:
        raise HTTPException(
            status_code=503,
            detail=("TTS backend not available: missing optional dependency 'edge-tts'. "
                    "Install it in your environment (pip install edge-tts)."),
        )

    voices = await edge_tts.list_voices()
    if locale:
        voices = [v for v in voices if v["Locale"].startswith(locale)]
    return voices
