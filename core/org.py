"""Portfolio org model: projects, buildings, staff and contractors, plus sign-in lookup."""
import re
import sqlite3
from datetime import datetime

STAFF_RE = re.compile(r"^\d{7}$")
UNIT_RE = re.compile(r"^([A-Z])(\d{3,4})(\d{4})$")


class AuthError(Exception):
    """Raised when a code is well-formed but not registered."""


def _now() -> str:
    return datetime.now().isoformat(timespec="minutes")


def authenticate(conn: sqlite3.Connection, code: str) -> dict:
    """Resolve an access code to a session. Raises AuthError when it is not registered.

    Staff enter a 7-digit number that must exist in `users`.
    Residents enter block letter + unit digits + 4-digit PIN; the block must be a
    registered building.
    """
    raw = (code or "").strip().upper()
    if not raw:
        raise AuthError("Enter your access code.")

    if STAFF_RE.match(raw):
        row = conn.execute(
            "SELECT staff_no, name, role, project_id, active FROM users WHERE staff_no = ?",
            (raw,)).fetchone()
        if row is None:
            raise AuthError(f"Staff number {raw} is not registered. Ask your administrator to add it.")
        if not row["active"]:
            raise AuthError(f"Staff number {raw} has been deactivated.")
        return {"role": row["role"], "name": row["name"], "staff_no": row["staff_no"],
                "project_id": row["project_id"], "building_id": None, "unit": None}

    m = UNIT_RE.match(raw)
    if not m:
        raise AuthError("Residents: unit + 4-digit PIN e.g. A15121223 · Staff: 7-digit badge ID")
    block, unit_no = m.group(1), m.group(2)
    row = conn.execute(
        "SELECT id, project_id, name FROM buildings WHERE block = ? ORDER BY project_id LIMIT 1",
        (block,)).fetchone()
    if row is None:
        raise AuthError(f"Block {block} is not a registered building in any township.")
    return {"role": "resident", "name": f"Unit {block}{unit_no}", "staff_no": None,
            "project_id": row["project_id"], "building_id": row["id"], "unit": f"{block}{unit_no}"}


# ── Reads ─────────────────────────────────────────────────────────────────────
def projects(conn) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM projects ORDER BY name").fetchall()


def buildings(conn, project_id: str | None = None) -> list[sqlite3.Row]:
    if project_id:
        return conn.execute("SELECT * FROM buildings WHERE project_id = ? ORDER BY block",
                            (project_id,)).fetchall()
    return conn.execute("SELECT * FROM buildings ORDER BY project_id, block").fetchall()


def users(conn) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT u.*, p.name AS project_name FROM users u "
        "LEFT JOIN projects p ON p.id = u.project_id ORDER BY u.role, u.name").fetchall()


def contractors(conn, trade: str | None = None) -> list[sqlite3.Row]:
    if trade:
        return conn.execute("SELECT * FROM contractors WHERE trade = ? ORDER BY name",
                            (trade,)).fetchall()
    return conn.execute("SELECT * FROM contractors ORDER BY trade, name").fetchall()


def routing_table(conn) -> dict[str, tuple[str, int]]:
    """category -> (contractor name, sla_hours), sourced from the admin's directory."""
    rows = conn.execute(
        "SELECT trade, name, sla_hours FROM contractors WHERE status = 'active' "
        "ORDER BY trade, rating DESC").fetchall()
    out: dict[str, tuple[str, int]] = {}
    for r in rows:
        out.setdefault(r["trade"], (r["name"], r["sla_hours"]))
    return out


# ── Writes ────────────────────────────────────────────────────────────────────
def add_project(conn, pid: str, name: str, state: str, district: str, manager: str) -> None:
    conn.execute(
        "INSERT INTO projects(id, name, state, district, manager, created_at) VALUES(?,?,?,?,?,?)",
        (pid.strip().upper(), name.strip(), state.strip(), district.strip(), manager.strip(), _now()))
    conn.commit()


def add_building(conn, project_id: str, block: str, name: str, btype: str,
                 units: int, floors: int, lat: float | None, lon: float | None) -> None:
    block = block.strip().upper()
    conn.execute(
        "INSERT INTO buildings(id, project_id, block, name, type, units, floors, latitude, longitude) "
        "VALUES(?,?,?,?,?,?,?,?,?)",
        (f"{project_id}-{block}", project_id, block, name.strip(), btype, units, floors, lat, lon))
    conn.commit()


def add_user(conn, staff_no: str, name: str, role: str, project_id: str | None,
             phone: str, email: str) -> None:
    staff_no = staff_no.strip()
    if not STAFF_RE.match(staff_no):
        raise ValueError("Staff number must be exactly 7 digits.")
    conn.execute(
        "INSERT INTO users(staff_no, name, role, project_id, phone, email, created_at) "
        "VALUES(?,?,?,?,?,?,?)",
        (staff_no, name.strip(), role, project_id, phone.strip(), email.strip(), _now()))
    conn.commit()


def set_user_active(conn, staff_no: str, active: bool) -> None:
    conn.execute("UPDATE users SET active = ? WHERE staff_no = ?", (int(active), staff_no))
    conn.commit()


def add_contractor(conn, name: str, trade: str, contact_person: str, phone: str, email: str,
                   sla_hours: int, rating: float, contract_ref: str, notes: str) -> None:
    conn.execute(
        "INSERT INTO contractors(name, trade, contact_person, phone, email, sla_hours, rating, "
        "contract_ref, notes, last_update) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (name.strip(), trade, contact_person.strip(), phone.strip(), email.strip(),
         sla_hours, rating, contract_ref.strip(), notes.strip(), _now()))
    conn.commit()


def update_contractor(conn, cid: int, **fields) -> None:
    allowed = {"contact_person", "phone", "email", "sla_hours", "rating", "status", "notes", "contract_ref"}
    sets = {k: v for k, v in fields.items() if k in allowed}
    if not sets:
        return
    clause = ", ".join(f"{k} = ?" for k in sets)
    conn.execute(f"UPDATE contractors SET {clause}, last_update = ? WHERE id = ?",
                 (*sets.values(), _now(), cid))
    conn.commit()
