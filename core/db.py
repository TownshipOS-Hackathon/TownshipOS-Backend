"""SQLite connection + schema. One file, zero setup."""
import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS tickets(
  id INTEGER PRIMARY KEY,
  created_at TEXT NOT NULL,
  raw_text TEXT NOT NULL,
  image_path TEXT,
  language TEXT, category TEXT, urgency TEXT, location TEXT, summary_en TEXT,
  contractor TEXT, sla_hours INTEGER, reply_bm TEXT, reply_en TEXT,
  needs_human INTEGER NOT NULL DEFAULT 0,
  confidence REAL,
  status TEXT NOT NULL DEFAULT 'untriaged',
  latitude REAL,
  longitude REAL,
  location_note TEXT
);
CREATE TABLE IF NOT EXISTS assets(
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL, type TEXT NOT NULL, block TEXT NOT NULL,
  installed_at TEXT NOT NULL, contractor TEXT NOT NULL, sla_hours INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS service_logs(
  id INTEGER PRIMARY KEY,
  asset_id TEXT NOT NULL REFERENCES assets(id),
  date TEXT NOT NULL,
  kind TEXT NOT NULL CHECK(kind IN ('scheduled','breakdown')),
  runtime_hours REAL NOT NULL,
  vibration_score REAL NOT NULL,
  notes TEXT
);
CREATE TABLE IF NOT EXISTS utility_readings(
  id INTEGER PRIMARY KEY,
  block TEXT NOT NULL, month TEXT NOT NULL,
  kwh REAL NOT NULL, m3 REAL NOT NULL,
  UNIQUE(block, month)
);
CREATE TABLE IF NOT EXISTS work_orders(
  id INTEGER PRIMARY KEY,
  asset_id TEXT NOT NULL REFERENCES assets(id),
  created_at TEXT NOT NULL,
  risk REAL NOT NULL,
  reason TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'open'
);
"""


def connect(path="townshipos.db") -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), check_same_thread=False)  # Streamlit reruns on threads
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    for col in ("latitude REAL", "longitude REAL", "location_note TEXT"):
        try:
            conn.execute(f"ALTER TABLE tickets ADD COLUMN {col}")
            conn.commit()
        except sqlite3.OperationalError:
            pass  # column already exists
    return conn
