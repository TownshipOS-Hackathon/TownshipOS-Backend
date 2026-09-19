# TownshipOS — Backend

> AI core modules and data layer for the TownshipOS platform.  
> Built for the Sime Darby Property Hackathon.

This repository contains the business logic, AI integrations, and data infrastructure that power TownshipOS. The modules are framework-agnostic Python — no Streamlit dependency — and can be imported by any frontend or API server.

---

## What's Here

```
townshipos-backend/
├── core/
│   ├── llm.py              # LLM client, caching, error types
│   ├── triage.py           # Complaint classification pipeline
│   ├── sustainability.py   # Utility anomaly detection + ESG narrative
│   ├── assistant.py        # RAG knowledge assistant
│   ├── maintenance.py      # Predictive maintenance ML model
│   ├── db.py               # SQLite schema and connection
│   └── voice.py            # Whisper audio transcription
├── data/
│   ├── generate.py         # Synthetic data generator
│   ├── complaints.jsonl    # 100 sample resident complaint messages
│   └── docs/               # Knowledge base for the RAG assistant
│       ├── 01_Lift_Maintenance_SLA.txt
│       ├── 02_Plumbing_SLA.txt
│       ├── 03_Renovation_Guidelines.txt
│       ├── 04_Emergency_SOP.txt
│       └── 05_Strata_Management_Act_2013_Extracts.txt
└── pyproject.toml
```

---

## Module Reference

### `core/llm.py` — LLM Client

Singleton OpenRouter client with JSON-file response caching.

```python
from core.llm import get_client, cached, cache_key, MODEL, LLMUnavailable

client = get_client()          # Returns openai.OpenAI pointed at OpenRouter
response = cached(fn, key)     # Returns cached result or calls fn()
```

Key constants:
- `MODEL` — defaults to `anthropic/claude-opus-4-5`, overridden by `OPENROUTER_MODEL` env var
- `LLMUnavailable` — raised when the API key is missing or the API is unreachable
- `API_ERRORS` — tuple of exception types to catch for graceful degradation

Cache location: `data/.llm_cache/<sha256>.json`. Pre-warming the cache allows full demo runs without a live API key.

---

### `core/triage.py` — Complaint Classification

Takes a free-text resident complaint (and optional photo) → returns a fully structured triage result.

```python
from core.triage import triage, insert_ticket, apply_triage, find_duplicate

result = triage("Lif rosak tingkat 5", image_bytes=None, mime=None)
# result.category      → "lift"
# result.urgency       → "high"
# result.contractor    → "OtisElevator"
# result.sla_hours     → 4
# result.reply_bm      → bilingual reply (Bahasa Malaysia)
# result.reply_en      → bilingual reply (English)
# result.confidence    → 0.92
# result.needs_human   → False

tid = insert_ticket(db_conn, raw_text, image_path, lat, lon, location_note)
apply_triage(db_conn, tid, result)

dup = find_duplicate(db_conn, category, lat, lon)  # Haversine 200 m radius
```

**How it works:**
1. Claude is called with a structured JSON tool (`TriageLLM`) to force typed output
2. Up to 3 retries with exponential backoff on API errors
3. Falls back to safe defaults (urgency=medium, contractor=General, needs_human=True) if all retries fail
4. `find_duplicate` uses the Haversine formula to find open tickets of the same category within 200 m

**Contractor routing** (`ROUTING` dict):
| Category | Contractor | SLA (hours) |
|----------|-----------|-------------|
| lift | OtisElevator | 4 |
| plumbing | Plumbwise | 8 |
| electrical | PowerTech | 6 |
| landscaping | GreenCare | 48 |
| security | GuardPro | 2 |
| structural | BuildSafe | 24 |
| cleanliness | CleanPro | 12 |
| other | General | 24 |

---

### `core/sustainability.py` — Utility Monitoring

Anomaly detection and AI-generated ESG narratives for monthly water and electricity data.

```python
from core.sustainability import detect_anomalies, monthly_summary, esg_narrative

anomalies = detect_anomalies(readings_df)
# Returns rows where z-score > 2.5 vs. each block's rolling 3-month baseline
# Columns: block, month, utility, value, baseline, pct_vs_baseline, z_score

summary = monthly_summary(readings_df, "2025-10")
# Returns: total_kwh, total_m3, co2e_tonnes, mom_kwh_pct, mom_m3_pct, blocks dict

narrative = esg_narrative(summary, anomalies_df)
# Returns: markdown string suitable for a JMB committee report
```

**CO₂ factor:** Peninsular Malaysia grid — 0.74 kg CO₂e per kWh (Suruhanjaya Tenaga 2023).

---

### `core/assistant.py` — Knowledge Assistant (RAG)

Full-text retrieval-augmented generation over the `data/docs/` knowledge base.

```python
from core.assistant import load_docs, ask

docs = load_docs("data/docs")   # Loads all .txt files
answer = ask("What is the SLA for a burst pipe?", docs)
# answer.text       → prose answer
# answer.citations  → list of Citation(doc_title=...)
# answer.refused    → False if answered, True if out of scope
```

**How it works:**
- No vector database — full-text search by keyword overlap (fast, zero infra)
- Top-3 matching document chunks are injected into the Claude prompt as context
- Claude is instructed to cite sources and refuse out-of-scope questions
- Suitable for the ~5 document corpus in this project; swap for a vector DB at larger scale

Knowledge base covers:
- Lift Maintenance SLA
- Plumbing SLA
- Renovation Guidelines
- Emergency SOP
- Strata Management Act 2013 (extracts)

---

### `core/maintenance.py` — Predictive Maintenance

Scikit-learn Gradient Boosting model that scores physical assets by failure probability.

```python
from core.maintenance import load_or_train, build_features, score

features_df = build_features(service_logs_df, assets_df, as_of=date.today())
model, metrics = load_or_train(service_logs_df, assets_df)
scored = score(model, features_df)
# scored: DataFrame with asset_id + failure_probability columns
```

**Feature engineering** (`build_features`):
- `days_since_last_service` — recency of last maintenance visit
- `service_count_90d` — service frequency in last 90 days
- `avg_resolution_hours` — mean time to resolve past service calls
- `age_days` — asset age from installation date

**Model:** `GradientBoostingClassifier` inside a `Pipeline` with `StandardScaler`.  
**Training target:** `failure_within_30d` — binary label derived from service log history.  
**Persistence:** pickled to `models/maintenance.pkl`, retrained automatically if the file is missing.

---

### `core/db.py` — Database

SQLite schema and connection helper.

```python
from core.db import connect

conn = connect("townshipos.db")  # Creates schema on first run
```

**Schema:**

`assets` — physical infrastructure (lifts, pumps, generators)
```
id, name, type, block, installation_date, last_service_date
```

`service_logs` — maintenance history
```
id, asset_id, date, technician, issue, resolution_hours, failure
```

`tickets` — resident complaints
```
id, created_at, raw_text, image_path, lat, lon, location_note,
urgency, category, summary_en, summary_bm, contractor, sla_hours,
needs_human, language, location, confidence, reply_bm, reply_en,
status, error
```

`utility_readings` — monthly water and electricity per block
```
id, block, month, kwh, m3
```

---

### `core/voice.py` — Audio Transcription

Whisper transcription via OpenRouter.

```python
from core.voice import transcribe, TranscriptionUnavailable

try:
    text = transcribe(audio_bytes, filename="note.m4a")
except TranscriptionUnavailable as e:
    print(e)  # Shown to user; app continues without transcript
```

Supported formats: mp3, wav, m4a, ogg, webm, mp4.

---

### `data/generate.py` — Synthetic Data Generator

Generates a full realistic dataset for demo purposes.

```python
python data/generate.py
```

Creates:
- 24 assets across Blocks A–D (lifts, pumps, generators, lights)
- 2 years of service logs with realistic failure rates
- 24 months of utility readings per block (with injected anomalies)
- `data/complaints.jsonl` — 100 bilingual resident complaint samples

Run once before starting the app for the first time.

---

## Setup

### Prerequisites

- Python 3.12+
- An [OpenRouter](https://openrouter.ai) API key

### Install

```bash
git clone https://github.com/TownshipOS-Hackathon/TownshipOS-Backend.git
cd TownshipOS-Backend
pip install -e .
# or: uv sync
```

### Environment

```env
OPENROUTER_API_KEY=sk-or-...your-key-here...

# Optional
# OPENROUTER_MODEL=anthropic/claude-sonnet-4-5
```

### Generate data

```bash
python data/generate.py
```

---

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `OPENROUTER_API_KEY` | Yes | — | API key from openrouter.ai |
| `OPENROUTER_MODEL` | No | `anthropic/claude-opus-4-5` | LLM model ID to use for all AI calls |

---

## Dependencies

| Package | Purpose |
|---------|---------|
| `openai` | OpenRouter API client (OpenAI-compatible) |
| `pydantic` | Typed triage output model |
| `pandas` | Data manipulation for sustainability and maintenance |
| `scikit-learn` | Gradient Boosting predictive maintenance model |
| `pillow` | Image validation before base64 encoding |
| `python-dotenv` | `.env` file loading |

No Streamlit, no web framework — pure Python logic layer.

---

## Related

- **[TownshipOS-Frontend](https://github.com/TownshipOS-Hackathon/TownshipOS-Frontend)** — Streamlit application that imports these modules and provides the full resident + FM UI
