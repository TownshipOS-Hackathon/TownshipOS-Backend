"""Complaint triage: Claude classifies (vision + structured output); Python routes."""
import base64
import io
import json
import math
from datetime import datetime
from typing import Literal

import pydantic
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, Field

from core.llm import API_ERRORS, MODEL, cache_key, cached, get_client, unavailable

Category = Literal["lift", "plumbing", "electrical", "structural", "security", "landscaping", "cleanliness", "other"]
Urgency = Literal["low", "medium", "high", "emergency"]

MAX_TEXT = 2000
MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_IMAGE_SIDE = 1568
ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP"}
MIN_CONFIDENCE = 0.6

# category -> (contractor, SLA hours). Deterministic, no LLM.
ROUTING: dict[str, tuple[str, int]] = {
    "lift": ("OTIS Malaysia", 4),
    "plumbing": ("AquaFix Plumbing Sdn Bhd", 8),
    "electrical": ("Voltra Electrical Sdn Bhd", 8),
    "structural": ("BuildCare Engineering Sdn Bhd", 48),
    "security": ("SecureGuard Services", 2),
    "landscaping": ("GreenScape Landscaping", 72),
    "cleanliness": ("CleanPro Facilities", 24),
    "other": ("Property Management Office", 24),
}


class TriageLLM(BaseModel):
    """Exactly what Claude returns. Kept flat and enum-heavy so parse() validates hard."""
    category: Category
    urgency: Urgency
    location: str | None = Field(description="Block / floor / unit if mentioned or visible, else null")
    language: Literal["ms", "en", "zh", "mixed"]
    summary_en: str = Field(description="One sentence in English for the ticket")
    reply_bm: str = Field(description="Short polite acknowledgement to the resident in Bahasa Malaysia")
    reply_en: str = Field(description="Same acknowledgement in English")
    confidence: float = Field(ge=0, le=1)


class TriageResult(TriageLLM):
    contractor: str
    sla_hours: int
    needs_human: bool
    error: str | None = None


SYSTEM = """You triage resident complaints for a Malaysian township property-management team.
Residents write in Bahasa Malaysia, English, Chinese or a mix (Manglish). A photo may be attached; use it.

Categories: lift, plumbing (leaks, pipes, drains, water supply), electrical (lights, power, DB boxes),
structural (cracks, ceilings, falling plaster), security (gates, doors, CCTV, intruders),
landscaping (trees, grass, gardens), cleanliness (rubbish, pests, smells), other.

Urgency:
- emergency: immediate danger to life or major property damage (person trapped in lift, fire, sparks/burning smell,
  burst pipe flooding, falling ceiling, gas smell, live wires). When in doubt between high and emergency, choose emergency.
- high: service loss or safety risk affecting many residents (lift out of service, gate stuck open, active leak into a unit).
- medium: fault that degrades service but is not urgent.
- low: cosmetic, requests, routine.

Replies: 1-2 sentences, warm and professional, acknowledge the issue, say the team is arranging the right contractor.
Never promise a specific time. Do not mention AI. reply_bm in Bahasa Malaysia, reply_en in English.
confidence is your confidence in category AND urgency together."""


def route(category: str, urgency: str, confidence: float,
          routing: dict[str, tuple[str, int]] | None = None) -> tuple[str, int, bool]:
    """`routing` comes from the admin's contractor directory; ROUTING is the offline fallback."""
    contractor, sla = (routing or {}).get(category) or ROUTING[category]
    if urgency == "emergency":
        return contractor, min(sla, 1), True
    return contractor, sla, confidence < MIN_CONFIDENCE


def prepare_image(data: bytes, media_type: str | None = None) -> tuple[str, str]:
    """Validate, downscale, re-encode as JPEG. Returns (base64, media_type)."""
    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError("Image larger than 5 MB")
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except (UnidentifiedImageError, OSError) as e:
        raise ValueError("File is not a readable image") from e
    if img.format not in ALLOWED_FORMATS:
        raise ValueError(f"Unsupported image format {img.format}; use JPEG, PNG or WebP")
    img = img.convert("RGB")
    img.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE))
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=85)
    return base64.b64encode(out.getvalue()).decode(), "image/jpeg"


def _fallback(error: str) -> TriageResult:
    contractor, sla, _ = route("other", "medium", 0.0)
    return TriageResult(
        category="other", urgency="medium", location=None, language="mixed",
        summary_en="Could not auto-triage; needs manual review",
        reply_bm="Terima kasih, aduan anda telah diterima dan sedang disemak oleh pasukan kami.",
        reply_en="Thank you, your complaint has been received and is being reviewed by our team.",
        confidence=0.0, contractor=contractor, sla_hours=sla, needs_human=True, error=error)


def _finalize(llm: TriageLLM) -> TriageResult:
    contractor, sla, needs_human = route(llm.category, llm.urgency, llm.confidence)
    return TriageResult(**llm.model_dump(), contractor=contractor, sla_hours=sla, needs_human=needs_human)


_TOOL = {
    "type": "function",
    "function": {
        "name": "triage",
        "description": "Classify this resident complaint",
        "parameters": None,  # set at module load after TriageLLM is defined
    },
}


def _call(text: str, image_bytes: bytes | None, media_type: str | None, client) -> dict:
    content = []
    if image_bytes is not None:
        b64, mt = prepare_image(image_bytes, media_type)
        content.append({"type": "image_url", "image_url": {"url": f"data:{mt};base64,{b64}"}})
    content.append({"type": "text", "text": text or "(no text, photo only)"})
    tool = {**_TOOL, "function": {**_TOOL["function"], "parameters": TriageLLM.model_json_schema()}}
    last_error = "unknown"
    for _ in range(2):  # one retry on incomplete/invalid output
        try:
            r = client.chat.completions.create(
                model=MODEL, max_tokens=2048,
                messages=[{"role": "system", "content": SYSTEM},
                          {"role": "user", "content": content}],
                tools=[tool],
                tool_choice={"type": "function", "function": {"name": "triage"}},
            )
        except API_ERRORS as e:
            raise unavailable(e) from e
        choice = r.choices[0]
        if choice.finish_reason == "length":
            last_error = "incomplete output (finish_reason=length)"
            continue
        tool_calls = getattr(choice.message, "tool_calls", None)
        if not tool_calls:
            last_error = getattr(choice.message, "content", None) or "no tool call in response"
            return _fallback(last_error).model_dump()
        try:
            llm_data = json.loads(tool_calls[0].function.arguments)
            parsed_llm = TriageLLM(**llm_data)
        except (json.JSONDecodeError, pydantic.ValidationError) as e:
            last_error = f"invalid output: {e}"
            continue
        return _finalize(parsed_llm).model_dump()
    return _fallback(last_error).model_dump()


def triage(text: str, image_bytes: bytes | None = None, media_type: str | None = None, *, client=None) -> TriageResult:
    text = (text or "").strip()[:MAX_TEXT]
    if not text and image_bytes is None:
        raise ValueError("Provide a message or a photo")
    if image_bytes is not None:
        prepare_image(image_bytes, media_type)  # fail fast on bad input before touching the cache
    key = cache_key("triage", text, image_bytes)
    data = cached(key, lambda: _call(text, image_bytes, media_type, client or get_client()))
    return TriageResult(**data)


def insert_ticket(conn, raw_text: str, image_path: str | None,
                  lat: float | None = None, lon: float | None = None,
                  location_note: str | None = None, building_id: str | None = None) -> int:
    """Insert the raw complaint BEFORE calling the model, so nothing is ever lost."""
    cur = conn.execute(
        "INSERT INTO tickets(created_at, raw_text, image_path, latitude, longitude, location_note, "
        "building_id) VALUES(?,?,?,?,?,?,?)",
        (datetime.now().isoformat(timespec="minutes"), raw_text, image_path, lat, lon,
         location_note, building_id))
    conn.commit()
    return cur.lastrowid


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6_371_000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    a = math.sin(math.radians(lat2 - lat1) / 2) ** 2 + \
        math.cos(phi1) * math.cos(phi2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def find_duplicate(conn, category: str, lat: float | None, lon: float | None,
                   radius_m: float = 100):
    """Return an existing open ticket of the same category within radius_m metres, or None."""
    if lat is None or lon is None:
        return None
    rows = conn.execute(
        "SELECT * FROM tickets WHERE category=? AND status IN ('open','assigned') "
        "AND latitude IS NOT NULL AND longitude IS NOT NULL ORDER BY created_at DESC LIMIT 50",
        (category,)).fetchall()
    for row in rows:
        if _haversine_m(lat, lon, row["latitude"], row["longitude"]) <= radius_m:
            return row
    return None


def apply_triage(conn, ticket_id: int, r: TriageResult) -> None:
    """Dispatch is re-resolved against the admin's contractor directory before the write,
    so suspending a contractor immediately redirects new tickets."""
    from core.org import routing_table
    contractor, sla, needs_human = route(r.category, r.urgency, r.confidence, routing_table(conn))
    r.contractor, r.sla_hours, r.needs_human = contractor, sla, needs_human or r.needs_human
    conn.execute(
        "UPDATE tickets SET language=?, category=?, urgency=?, location=?, summary_en=?, contractor=?, "
        "sla_hours=?, reply_bm=?, reply_en=?, needs_human=?, confidence=?, status='open' WHERE id=?",
        (r.language, r.category, r.urgency, r.location, r.summary_en, r.contractor, r.sla_hours,
         r.reply_bm, r.reply_en, int(r.needs_human), r.confidence, ticket_id))
    conn.commit()
