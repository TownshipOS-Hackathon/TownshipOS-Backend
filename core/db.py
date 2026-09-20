"""SQLite connection + schema. One file, zero setup."""
import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects(
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  state TEXT NOT NULL,
  district TEXT,
  manager TEXT,
  status TEXT NOT NULL DEFAULT 'active',
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS buildings(
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id),
  block TEXT NOT NULL,
  name TEXT NOT NULL,
  type TEXT NOT NULL,
  units INTEGER NOT NULL DEFAULT 0,
  floors INTEGER NOT NULL DEFAULT 0,
  latitude REAL, longitude REAL,
  handover_at TEXT,
  UNIQUE(project_id, block)
);
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY,
  staff_no TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  role TEXT NOT NULL CHECK(role IN ('admin','fm')),
  project_id TEXT REFERENCES projects(id),   -- NULL = whole portfolio
  phone TEXT, email TEXT,
  active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS contractors(
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  trade TEXT NOT NULL,
  contact_person TEXT, phone TEXT, email TEXT,
  sla_hours INTEGER NOT NULL DEFAULT 24,
  rating REAL NOT NULL DEFAULT 4.0,
  status TEXT NOT NULL DEFAULT 'active',
  contract_ref TEXT,
  notes TEXT,
  last_update TEXT
);
CREATE TABLE IF NOT EXISTS tickets(
  id INTEGER PRIMARY KEY,
  created_at TEXT NOT NULL,
  raw_text TEXT NOT NULL,
  image_path TEXT,
  building_id TEXT REFERENCES buildings(id),
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
  building_id TEXT REFERENCES buildings(id),
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
  building_id TEXT REFERENCES buildings(id),
  block TEXT NOT NULL, month TEXT NOT NULL,
  kwh REAL NOT NULL, m3 REAL NOT NULL,
  UNIQUE(building_id, month)
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


# Columns added when the single-township build grew a portfolio. CREATE TABLE IF NOT EXISTS
# cannot add them to a database that already exists, so they are patched in on connect.
MIGRATIONS = [
    ("tickets", "building_id", "TEXT"),
    ("assets", "building_id", "TEXT"),
    ("utility_readings", "building_id", "TEXT"),
]


def connect(path="townshipos.db") -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), check_same_thread=False)  # Streamlit reruns on threads
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    for table, column, decl in MIGRATIONS:
        cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        if column not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
    conn.commit()
    return conn
