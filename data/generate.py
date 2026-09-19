"""Seeded synthetic data for TownshipOS. Run: uv run python -m data.generate"""
import json
import math
import random
from datetime import date, datetime, timedelta
from pathlib import Path

from core.db import connect

SEED = 42
BLOCKS = ["A", "B", "C", "D"]
CATEGORIES = ["lift", "plumbing", "electrical", "structural", "security", "landscaping", "cleanliness", "other"]
URGENCIES = ["low", "medium", "high", "emergency"]
END = date(2026, 8, 31)                       # last month in history = 2026-08
START = date(2024, 9, 1)                      # 24 months

PREFIX = {"lift": "L", "pump": "P", "genset": "GS", "chiller": "CH", "gate": "GT"}
ASSET_TYPES = {  # type: (per block, contractor, sla_hours, service interval months, runtime h/month)
    "lift": (4, "OTIS Malaysia", 4, 3, 500),
    "pump": (4, "AquaFix Plumbing Sdn Bhd", 8, 6, 300),
    "genset": (2, "Voltra Electrical Sdn Bhd", 8, 6, 20),
    "chiller": (2, "CoolTech M&E Sdn Bhd", 12, 3, 600),
    "gate": (3, "SecureGuard Services", 2, 6, 720),
}


def gen_assets(rng=None):
    rng = rng or random.Random(SEED)
    out = []
    for block in BLOCKS:
        for typ, (n, contractor, sla, _, _) in ASSET_TYPES.items():
            for i in range(1, n + 1):
                age_years = rng.uniform(1, 12)
                out.append({
                    "id": f"{PREFIX[typ]}{i}-{block}",
                    "name": f"{typ.title()} {PREFIX[typ]}{i}, Block {block}",
                    "type": typ, "block": block,
                    "installed_at": (START - timedelta(days=365 * age_years)).isoformat(),
                    "contractor": contractor, "sla_hours": sla,
                })
    return out


def _month_starts():
    d = START
    while d <= END:
        yield d
        d = (d.replace(day=1) + timedelta(days=32)).replace(day=1)


# Monthly breakdown hazard. Tune here if the label base rate or model AUC drifts (see tests).
HAZARD = {"intercept": -8.0, "age": 0.25, "dss": 0.02, "vib": 4.0, "vib_knee": 0.70}


def gen_service_logs(assets, rng=None):
    """Monthly walk per asset. Breakdown hazard rises with age, days since service and current vibration.
    Each log records the vibration measured AFTER the event (post-service condition), so assets that
    reset poorly (high residual wear) are visibly riskier in the data."""
    rng = rng or random.Random(SEED + 1)
    logs = []
    for a in assets:
        _, _, _, interval, runtime = ASSET_TYPES[a["type"]]
        installed = date.fromisoformat(a["installed_at"])
        vib = rng.uniform(0.25, 0.45)
        wear = rng.uniform(0.45, 0.9)              # latent condition: residual vibration after a service
        last_service = START - timedelta(days=rng.randint(10, 120))
        for m0 in _month_starts():
            age_years = (m0 - installed).days / 365
            if (m0 - last_service).days >= interval * 30 - 5:
                d = m0 + timedelta(days=rng.randint(0, 6))
                last_service, vib = d, max(0.2, vib * wear)
                logs.append(_log(a, d, "scheduled", runtime, vib, rng))
            dss = (m0 - last_service).days
            h = HAZARD
            logit = h["intercept"] + h["age"] * age_years + h["dss"] * dss + h["vib"] * (vib - h["vib_knee"])
            if rng.random() < 1 / (1 + math.exp(-logit)):
                d = m0 + timedelta(days=rng.randint(7, 27))
                last_service, vib = d, max(0.2, vib * min(1.0, wear + 0.1))
                logs.append(_log(a, d, "breakdown", runtime, vib, rng))
            vib = min(1.5, vib + rng.uniform(0.04, 0.10) + 0.01 * age_years)
    return sorted(logs, key=lambda l: (l["asset_id"], l["date"]))


def _log(a, d, kind, runtime, vib, rng):
    return {
        "asset_id": a["id"], "date": d.isoformat(), "kind": kind,
        "runtime_hours": round(runtime * rng.uniform(0.8, 1.2), 1),
        "vibration_score": round(vib + rng.uniform(-0.05, 0.05), 3),
        "notes": "Routine service" if kind == "scheduled" else rng.choice(
            ["Motor overheating", "Bearing noise", "Trip on overload", "Sensor fault", "Door jam"]),
    }


def gen_readings(rng=None):
    rng = rng or random.Random(SEED + 2)
    base_kwh = {"A": 52000, "B": 48000, "C": 55000, "D": 45000}
    base_m3 = {"A": 3100, "B": 2900, "C": 3300, "D": 2700}
    months = list(_month_starts())
    rows = []
    for block in BLOCKS:
        for i, m0 in enumerate(months):
            season = 1 + 0.08 * math.sin(2 * math.pi * (m0.month - 4) / 12)  # hotter Apr-Jun
            kwh = base_kwh[block] * season * rng.uniform(0.97, 1.03)
            m3 = base_m3[block] * rng.uniform(0.96, 1.04)
            if block == "C" and i == len(months) - 1:
                m3 *= 1.42                       # leak in latest month
            if block == "A" and i == len(months) - 4:
                kwh *= 1.25                      # chiller fault 3 months back
            rows.append({"block": block, "month": m0.strftime("%Y-%m"), "kwh": round(kwh), "m3": round(m3)})
    return rows


# Serenia Heights, Sepang — block centroids (WGS-84). Complaints scatter ±80 m from each block.
BLOCK_COORDS = {
    "A": (2.8685, 101.7255),
    "B": (2.8675, 101.7255),
    "C": (2.8685, 101.7265),
    "D": (2.8675, 101.7265),
}

LOCS = ["Blok A tingkat 5", "Blok B tingkat 12", "Blok C aras 3", "Blok D tingkat 8", "unit 12-3",
        "unit 7-9", "parkir B2", "lobi Blok A", "kolam renang", "surau", "taman permainan"]


def _block_from_loc(loc: str) -> str | None:
    for b in BLOCKS:
        if f"Blok {b}" in loc or f"Block {b}" in loc:
            return b
    return None

# (language, category, urgency, template)
TEMPLATES = [
    ("ms", "lift", "high", "Lif rosak {loc}, bunyi pelik bila naik"),
    ("ms", "lift", "emergency", "Tolong!! ada orang tersangkut dalam lif {loc}"),
    ("ms", "plumbing", "high", "Bocor dari atas, air menitik masuk {loc}"),
    ("ms", "plumbing", "medium", "Paip sinki tersumbat {loc}, air tak lalu"),
    ("ms", "electrical", "medium", "Lampu koridor {loc} dah seminggu tak nyala"),
    ("ms", "electrical", "emergency", "Ada bau hangit dan percikan api dari DB box {loc}!"),
    ("ms", "structural", "medium", "Dinding retak panjang {loc}, makin besar"),
    ("ms", "security", "high", "Pintu pagar {loc} tak boleh tutup, sesiapa boleh masuk"),
    ("ms", "landscaping", "low", "Rumput {loc} dah tinggi sangat, tak potong"),
    ("ms", "cleanliness", "medium", "Bilik sampah {loc} busuk, penuh lalat"),
    ("ms", "other", "low", "Ada kereta parking kat lot saya {loc}"),
    ("en", "lift", "high", "Lift at {loc} keeps stopping between floors lah"),
    ("en", "plumbing", "high", "Water leaking from ceiling at {loc}, quite heavy"),
    ("en", "plumbing", "emergency", "Burst pipe at {loc}, water flooding the corridor now!"),
    ("en", "electrical", "medium", "Corridor light at {loc} flickering non stop"),
    ("en", "structural", "medium", "Big crack on the wall near {loc}, getting wider"),
    ("en", "structural", "emergency", "Ceiling plaster at {loc} fell down, more looks like dropping"),
    ("en", "security", "high", "Boom gate at {loc} stuck open since morning"),
    ("en", "landscaping", "low", "Trees at {loc} need trimming, branches touching windows"),
    ("en", "cleanliness", "low", "Rubbish not collected at {loc} for 3 days already"),
    ("en", "other", "low", "Can I get the renovation form for {loc}?"),
    ("zh", "lift", "high", "{loc}的电梯坏了，有奇怪的声音"),
    ("zh", "plumbing", "high", "{loc}天花板漏水，滴到地上"),
    ("zh", "electrical", "medium", "{loc}走廊的灯不亮"),
    ("zh", "structural", "medium", "{loc}墙壁有裂缝"),
    ("zh", "security", "high", "{loc}大门坏了，谁都能进来"),
    ("zh", "landscaping", "low", "{loc}的草太长了"),
    ("zh", "cleanliness", "medium", "{loc}垃圾房很臭"),
    ("zh", "other", "low", "{loc}我的停车位被人占用了"),
]
LANG_WEIGHTS = {"ms": 0.5, "en": 0.35, "zh": 0.15}


def gen_complaints(n=200, rng=None):
    rng = rng or random.Random(SEED + 3)
    by_lang = {l: [t for t in TEMPLATES if t[0] == l] for l in LANG_WEIGHTS}
    out = []
    for i in range(n):
        lang = rng.choices(list(LANG_WEIGHTS), weights=list(LANG_WEIGHTS.values()))[0]
        _, cat, urg, tpl = rng.choice(by_lang[lang])
        ts = datetime(2026, 9, 1, 6, 0) + timedelta(minutes=rng.randint(0, 60 * 24 * 14))
        loc = rng.choice(LOCS)
        block = _block_from_loc(loc) or rng.choice(BLOCKS)
        base_lat, base_lon = BLOCK_COORDS[block]
        # scatter within ~80 m (0.0007° ≈ 78 m at this latitude)
        lat = round(base_lat + rng.uniform(-0.0004, 0.0004), 6)
        lon = round(base_lon + rng.uniform(-0.0004, 0.0004), 6)
        out.append({"id": i + 1, "created_at": ts.isoformat(timespec="minutes"),
                    "text": tpl.format(loc=loc),
                    "language": lang, "category": cat, "urgency": urg,
                    "lat": lat, "lon": lon})
    return out


def main(db_path="townshipos.db", out_dir="data"):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    conn = connect(db_path)
    for t in ["work_orders", "service_logs", "utility_readings", "tickets", "assets"]:
        conn.execute(f"DELETE FROM {t}")
    assets = gen_assets()
    conn.executemany("INSERT INTO assets VALUES(:id,:name,:type,:block,:installed_at,:contractor,:sla_hours)", assets)
    conn.executemany(
        "INSERT INTO service_logs(asset_id,date,kind,runtime_hours,vibration_score,notes) "
        "VALUES(:asset_id,:date,:kind,:runtime_hours,:vibration_score,:notes)", gen_service_logs(assets))
    conn.executemany("INSERT INTO utility_readings(block,month,kwh,m3) VALUES(:block,:month,:kwh,:m3)", gen_readings())

    complaints = gen_complaints(200)
    (out_dir / "complaints.jsonl").write_text(
        "\n".join(json.dumps(c, ensure_ascii=False) for c in complaints) + "\n", encoding="utf-8")
    # 40 text items for the eval set; photo items are appended by hand later
    evalset = random.Random(SEED + 4).sample(complaints, 40)
    (out_dir / "triage_set.jsonl").write_text(
        "\n".join(json.dumps({"text": c["text"], "image": None, "category": c["category"],
                              "urgency": c["urgency"]}, ensure_ascii=False) for c in evalset) + "\n",
        encoding="utf-8")

    # Seed 23 pre-triaged tickets so the dashboard has history on day one.
    from core.triage import ROUTING
    for c in complaints[:23]:
        contractor, sla = ROUTING[c["category"]]
        conn.execute(
            "INSERT INTO tickets(created_at,raw_text,language,category,urgency,contractor,sla_hours,"
            "needs_human,confidence,status,latitude,longitude) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (c["created_at"], c["text"], c["language"], c["category"], c["urgency"], contractor,
             1 if c["urgency"] == "emergency" else sla, int(c["urgency"] == "emergency"), 0.9, "open",
             c["lat"], c["lon"]))
    conn.commit()
    return conn


if __name__ == "__main__":
    main()
    print("townshipos.db written")
