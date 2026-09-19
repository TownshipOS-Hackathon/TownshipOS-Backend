"""Shared OpenRouter plumbing: client, on-disk response cache."""
import hashlib
import json
import os
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI, OpenAIError

load_dotenv()

MODEL = os.environ.get("OPENROUTER_MODEL", "anthropic/claude-opus-4-5")
CACHE_DIR = Path(os.environ.get("TOWNSHIPOS_CACHE", "cache"))
_client = None


class LLMUnavailable(RuntimeError):
    """Raised after retries exhausted. Callers fall back to cache or show an error."""


def get_client():
    global _client
    if _client is None:
        try:
            api_key = os.environ["OPENROUTER_API_KEY"]
        except KeyError:
            raise LLMUnavailable("No OPENROUTER_API_KEY found. Set it (cached demo responses still work).")
        _client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=api_key, timeout=60.0, max_retries=2)
    return _client


def cache_key(*parts) -> str:
    h = hashlib.sha256()
    for p in parts:
        if isinstance(p, (bytes, bytearray)):
            h.update(b"<bytes>" + hashlib.sha256(p).digest())
        else:
            h.update(json.dumps(p, sort_keys=True, ensure_ascii=False, default=str).encode())
        h.update(b"\x1f")
    return h.hexdigest()[:24]


def cached(key: str, fn):
    """Return the JSON-able dict stored under key, or call fn(), store it, return it."""
    p = CACHE_DIR / f"{key}.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    value = fn()
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(value, ensure_ascii=False, indent=1), encoding="utf-8")
    return value


API_ERRORS = (OpenAIError, TypeError)


def unavailable(e: Exception) -> LLMUnavailable:
    """Wrap an SDK failure in LLMUnavailable with a message a demo audience can read."""
    msg = str(e).lower()
    if "api_key" in msg or "authentication" in msg or "api key" in msg:
        return LLMUnavailable("No OPENROUTER_API_KEY found. Set it (cached demo responses still work).")
    return LLMUnavailable(f"{type(e).__name__}: {e}")
