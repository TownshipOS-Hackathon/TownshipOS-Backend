"""Sustainability intel: rolling z-score anomalies, CO2e, and a Claude-written ESG summary."""
import json

import pandas as pd

from core.llm import API_ERRORS, MODEL, cache_key, cached, get_client, unavailable

GRID_FACTOR_KG_PER_KWH = 0.74   # Energy Commission Malaysia, Peninsular grid emission factor (2024)
UTILITIES = {"kwh": "electricity", "m3": "water"}

SYSTEM = """You write the monthly sustainability section of a Malaysian township management report.
Audience: property manager and JMB committee. Use the numbers given; do not invent any.
Structure (markdown): a 2-sentence headline, a table of blocks (kWh, m3, tCO2e), anomalies with the probable cause and
the recommended action, and one ESG note linking to net-zero reporting. Keep it under 250 words."""


def detect_anomalies(readings: pd.DataFrame, window: int = 12, z: float = 2.5) -> pd.DataFrame:
    """Per block x utility: compare each month with the rolling mean/std of the previous `window` months."""
    cols = ["block", "utility", "month", "value", "baseline", "z", "pct_vs_baseline"]
    out = []
    for block, grp in readings.sort_values("month").groupby("block"):
        if len(grp) < window + 1:
            continue  # ponytail: silently skip; UI notes blocks with short history
        for util in UTILITIES:
            v = grp[util].astype(float).reset_index(drop=True)
            prior = v.shift(1).rolling(window)
            mean, std = prior.mean(), prior.std(ddof=0).replace(0, float("nan"))
            zs = (v - mean) / std
            for i in zs[zs.abs() >= z].index:
                out.append({"block": block, "utility": util, "month": grp["month"].iloc[i], "value": v[i],
                            "baseline": round(mean[i], 1), "z": round(zs[i], 2),
                            "pct_vs_baseline": round(100 * (v[i] - mean[i]) / mean[i], 1)})
    return pd.DataFrame(out, columns=cols)


def monthly_summary(readings: pd.DataFrame, month: str) -> dict:
    months = sorted(readings["month"].unique())
    cur = readings[readings["month"] == month]
    prev_month = months[months.index(month) - 1] if month in months and months.index(month) > 0 else None
    prev = readings[readings["month"] == prev_month] if prev_month else cur.iloc[0:0]
    pct = lambda a, b: round(100 * (a - b) / b, 1) if b else None
    blocks = {r.block: {"kwh": float(r.kwh), "m3": float(r.m3),
                        "co2e_tonnes": round(r.kwh * GRID_FACTOR_KG_PER_KWH / 1000, 2)}
              for r in cur.itertuples(index=False)}
    total_kwh, total_m3 = float(cur["kwh"].sum()), float(cur["m3"].sum())
    return {
        "month": month, "blocks": blocks,
        "total_kwh": total_kwh, "total_m3": total_m3,
        "co2e_tonnes": round(total_kwh * GRID_FACTOR_KG_PER_KWH / 1000, 2),
        "mom_kwh_pct": pct(total_kwh, float(prev["kwh"].sum())),
        "mom_m3_pct": pct(total_m3, float(prev["m3"].sum())),
        "grid_factor": GRID_FACTOR_KG_PER_KWH,
    }


def _call(prompt: str, client) -> dict:
    try:
        r = client.chat.completions.create(
            model=MODEL, max_tokens=2048,
            messages=[{"role": "system", "content": SYSTEM},
                      {"role": "user", "content": prompt}],
        )
        text = r.choices[0].message.content or ""
    except API_ERRORS as e:
        raise unavailable(e) from e
    return {"text": text}


def esg_narrative(summary: dict, anomalies: pd.DataFrame, *, client=None) -> str:
    payload = {"summary": summary, "anomalies": anomalies.to_dict(orient="records")}
    prompt = "Write this month's sustainability section from the data below.\n\n" + json.dumps(payload, ensure_ascii=False, default=str)
    key = cache_key("esg", payload)
    return cached(key, lambda: _call(prompt, client or get_client()))["text"]
