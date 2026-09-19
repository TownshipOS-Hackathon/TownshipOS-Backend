"""Whisper audio transcription via OpenRouter (OpenAI-compatible)."""
import io
import os

from core.llm import cache_key, cached

WHISPER_MODEL = "openai/whisper-1"
_client = None


class TranscriptionUnavailable(RuntimeError):
    pass


def _get_client():
    global _client
    if _client is None:
        try:
            from openai import OpenAI
            _client = OpenAI(
                base_url="https://openrouter.ai/api/v1",
                api_key=os.environ["OPENROUTER_API_KEY"],
            )
        except KeyError:
            raise TranscriptionUnavailable(
                "No OPENROUTER_API_KEY found. Set it to enable voice transcription."
            )
    return _client


def transcribe(audio_bytes: bytes, filename: str = "audio.mp3") -> str:
    """Transcribe audio → text via Whisper. Cached by content hash."""
    key = cache_key("whisper", audio_bytes)

    def _call():
        buf = io.BytesIO(audio_bytes)
        buf.name = filename
        result = _get_client().audio.transcriptions.create(
            model=WHISPER_MODEL, file=buf
        )
        return {"text": result.text}

    return cached(key, _call)["text"]
