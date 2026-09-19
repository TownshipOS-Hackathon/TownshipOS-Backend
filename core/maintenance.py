"""Predictive maintenance: features from service logs -> gradient boosting -> 30-day failure risk."""
import pickle
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.model_selection import cross_val_score
from sklearn.pipeline import Pipeline, make_pipeline
from sklearn.preprocessing import StandardScaler

FEATURES = ["age_months", "days_since_service", "breakdowns_last_12m", "avg_runtime_hours", "vibration_score"]
LABEL = "failed_within_30d"
NO_SERVICE_DAYS = 365          # sentinel when an asset has no log before as_of
MODEL_PATH = Path("models/maintenance.pkl")


def _months_between(a: date, b: date) -> int:
    return (b.year - a.year) * 12 + b.month - a.month


def build_features(logs: pd.DataFrame, assets: pd.DataFrame, as_of: date) -> pd.DataFrame:
    """One row per asset as seen on `as_of`. Label = any breakdown in (as_of, as_of + 30d]."""
    logs = logs.assign(date=pd.to_datetime(logs["date"]).dt.date)
    horizon = as_of + timedelta(days=30)
    year_ago = as_of - timedelta(days=365)
    rows = []
    for a in assets.itertuples(index=False):
        al = logs[logs["asset_id"] == a.id]
        past = al[al["date"] <= as_of]
        recent = past[past["date"] > year_ago]
        last = past.sort_values("date").tail(1)
        rows.append({
            "asset_id": a.id,
            "age_months": _months_between(date.fromisoformat(str(a.installed_at)[:10]), as_of),
            "days_since_service": (as_of - last["date"].iloc[0]).days if len(last) else NO_SERVICE_DAYS,
            "breakdowns_last_12m": int((recent["kind"] == "breakdown").sum()),
            "avg_runtime_hours": float(recent["runtime_hours"].mean()) if len(recent) else 0.0,
            "vibration_score": float(last["vibration_score"].iloc[0]) if len(last) else 0.0,
            LABEL: int(((al["date"] > as_of) & (al["date"] <= horizon) & (al["kind"] == "breakdown")).any()),
        })
    return pd.DataFrame(rows)


def training_frame(logs: pd.DataFrame, assets: pd.DataFrame) -> pd.DataFrame:
    """Slide as_of over month starts that have 12 months of history and 30 days of lookahead."""
    dates = pd.to_datetime(logs["date"])
    first = dates.min().date().replace(day=1)
    last = dates.max().date() - timedelta(days=31)
    cursor = pd.Timestamp(first) + pd.DateOffset(months=12)
    frames = []
    while cursor.date() <= last:
        frames.append(build_features(logs, assets, cursor.date()))
        cursor = cursor + pd.DateOffset(months=1)
    return pd.concat(frames, ignore_index=True)


def train(frame: pd.DataFrame) -> tuple[Pipeline, dict]:
    X, y = frame[FEATURES], frame[LABEL]
    model = make_pipeline(StandardScaler(), GradientBoostingClassifier(random_state=0, max_depth=3, n_estimators=150))
    auc = float(cross_val_score(model, X, y, cv=5, scoring="roc_auc").mean())
    model.fit(X, y)
    importances = dict(zip(FEATURES, map(float, model[-1].feature_importances_)))
    return model, {"auc": auc, "importances": importances, "n_rows": int(len(frame)), "base_rate": float(y.mean())}


def score(model: Pipeline, features: pd.DataFrame) -> pd.DataFrame:
    X = features[FEATURES]
    risk = model.predict_proba(X)[:, 1]
    z = (X - X.mean()) / X.std(ddof=0).replace(0, 1)
    drivers = [list(z.loc[i].sort_values(ascending=False).index[:2]) for i in X.index]
    out = pd.DataFrame({"asset_id": features["asset_id"].values, "risk": risk, "top_drivers": drivers})
    return out.sort_values("risk", ascending=False, ignore_index=True)


def save(model: Pipeline, path=MODEL_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(pickle.dumps(model))


def load_or_train(logs: pd.DataFrame, assets: pd.DataFrame, path=MODEL_PATH) -> tuple[Pipeline, dict | None]:
    """Returns (model, metrics). metrics is None when loaded from disk."""
    if path.exists():
        return pickle.loads(path.read_bytes()), None
    model, metrics = train(training_frame(logs, assets))
    save(model, path)
    return model, metrics


def create_work_order(conn, asset_id: str, risk: float, reason: str) -> int:
    cur = conn.execute("INSERT INTO work_orders(asset_id, created_at, risk, reason) VALUES(?,?,?,?)",
                       (asset_id, datetime.now().isoformat(timespec="minutes"), risk, reason))
    conn.commit()
    return cur.lastrowid
