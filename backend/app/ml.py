"""Machine-learning signals (Level 11).

Features are computed only from data up to each day. The label is whether the price is higher `horizon` days later.
The honest split trains on the past and tests on the future, with a gap of `horizon` days so overlapping labels can't
leak. Two deliberate traps can be switched on to show what goes wrong: a shuffled split, and a "leaky" feature that
contains the future.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.inspection import permutation_importance
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from app import assets
from app.quant import _date, _r, max_drawdown

MODELS = {"logistic": "Logistic regression (simple, linear)", "boosting": "Gradient boosting (trees, flexible)"}
FEATURES = {
    "ret_1": "Return over the last day", "ret_5": "Return over the last week", "ret_20": "Return over the last month",
    "ret_60": "Return over the last 3 months", "vol_20": "Volatility over the last month",
    "rsi_14": "RSI (14 days)", "dist_sma50": "Distance from the 50-day average", "dist_sma200": "Distance from the 200-day average",
    "volume_z": "Volume vs its usual level", "weekday": "Day of the week",
}
LEAK = "future_return"


def build_features(candles: list[dict], horizon: int, leak: bool = False) -> pd.DataFrame:
    df = pd.DataFrame(candles)
    df["date"] = pd.to_datetime(df["time"], unit="s")
    df = df.set_index("date")
    c = df["close"]
    r = c.pct_change()
    out = pd.DataFrame(index=df.index)
    for n in (1, 5, 20, 60):
        out[f"ret_{n}"] = c.pct_change(n)
    out["vol_20"] = r.rolling(20).std()
    delta = c.diff()
    up = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    down = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    out["rsi_14"] = 100 - 100 / (1 + up / down.replace(0, np.nan))
    out["dist_sma50"] = c / c.rolling(50).mean() - 1
    out["dist_sma200"] = c / c.rolling(200).mean() - 1
    vol = df["volume"].replace(0, np.nan)
    out["volume_z"] = (vol - vol.rolling(20).mean()) / vol.rolling(20).std()
    out["weekday"] = out.index.dayofweek.astype(float)
    future = c.shift(-horizon) / c - 1
    if leak:
        out[LEAK] = future  # the trap: this is the very thing we're trying to predict
    out["next_day_return"] = r.shift(-1)  # used to score the strategy, never a feature
    out["label"] = (future > 0).astype(float)
    out.loc[future.isna(), "label"] = np.nan
    return out.replace([np.inf, -np.inf], np.nan)


def _model(kind: str, seed: int = 0):
    if kind == "logistic":
        return make_pipeline(StandardScaler(), LogisticRegression(max_iter=2000, C=1.0))
    if kind == "boosting":
        return HistGradientBoostingClassifier(max_iter=200, learning_rate=0.05, max_depth=3, random_state=seed)
    raise KeyError(f"Unknown model '{kind}'")


def _summary(r: np.ndarray, ppy: int) -> dict:
    value = np.concatenate([[1.0], np.cumprod(1 + r)])
    sd = float(r.std(ddof=1)) if len(r) > 2 else 0.0
    dd, _, _ = max_drawdown(value)
    return {"total_return_pct": _r((value[-1] - 1) * 100, 2),
            "sharpe": _r(float(r.mean()) / sd * math.sqrt(ppy) if sd > 0 else 0.0, 2),
            "max_drawdown_pct": _r(dd * 100, 2)}


def train(candles: list[dict], symbol: str, model: str = "boosting", horizon: int = 5, split: float = 0.7,
          shuffle: bool = False, leak: bool = False, cost: float = 0.0005, threshold: float = 0.5,
          seed: int = 0) -> dict:
    if horizon not in (1, 5, 20):
        raise ValueError("horizon must be 1, 5 or 20 days")
    if model not in MODELS:
        raise KeyError(f"Unknown model '{model}'")
    data = build_features(candles, horizon, leak)
    feats = list(FEATURES) + ([LEAK] if leak else [])
    data = data.dropna(subset=feats + ["label"])
    if len(data) < 400:
        raise ValueError("Not enough history to train on. Pick more years.")
    X, y = data[feats].to_numpy(), data["label"].to_numpy().astype(int)
    n = len(data)
    cut = int(n * split)
    if shuffle:
        rng = np.random.default_rng(seed)
        idx = rng.permutation(n)
        tr, te = np.sort(idx[:cut]), np.sort(idx[cut:])
    else:
        tr = np.arange(0, cut - horizon)  # purge: drop training rows whose label overlaps the test period
        te = np.arange(cut, n)
    clf = _model(model, seed)
    clf.fit(X[tr], y[tr])
    p_te = clf.predict_proba(X[te])[:, 1]
    p_tr = clf.predict_proba(X[tr])[:, 1]
    pred = (p_te >= threshold).astype(int)
    majority = int(round(y[tr].mean()))
    acc_test = accuracy_score(y[te], pred)
    acc_train = accuracy_score(y[tr], (p_tr >= threshold).astype(int))
    base = accuracy_score(y[te], np.full(len(te), majority))
    try:
        auc = roc_auc_score(y[te], p_te)
    except ValueError:
        auc = 0.5
    imp = permutation_importance(clf, X[te], y[te], n_repeats=5, random_state=seed, scoring="accuracy")
    importance = sorted(({"feature": f, "label": FEATURES.get(f, "Tomorrow's actual result (a leak!)"),
                          "importance": _r(float(m) * 100, 2)} for f, m in zip(feats, imp.importances_mean)),
                        key=lambda x: -(x["importance"] or 0))
    ppy = assets.periods_per_year(symbol)
    result = {
        "symbol": symbol, "name": assets.name(symbol), "model": model, "model_name": MODELS[model],
        "horizon": horizon, "shuffle": shuffle, "leak": leak, "rows": n, "train_rows": len(tr), "test_rows": len(te),
        "accuracy_train_pct": _r(acc_train * 100, 1), "accuracy_test_pct": _r(acc_test * 100, 1),
        "baseline_pct": _r(base * 100, 1), "baseline_says": "up" if majority else "down", "auc": _r(auc, 3),
        "up_share_test_pct": _r(float(y[te].mean()) * 100, 1), "importance": importance,
        "overfit_gap_pct": _r((acc_train - acc_test) * 100, 1),
    }
    if not shuffle:
        # Trade it: hold for the next day when the model says "up" (decided at today's close).
        nxt = data["next_day_return"].to_numpy()[te]
        ok = ~np.isnan(nxt)
        pos = pred[ok].astype(float)
        nxt = nxt[ok]
        changes = np.abs(np.diff(np.concatenate([[0.0], pos])))
        strat = pos * nxt - changes * cost
        dates = data.index[te][ok]
        times = [int(t.timestamp()) for t in dates]
        step = max(1, len(times) // 600)
        sv = 10_000 * np.cumprod(1 + strat)
        hv = 10_000 * np.cumprod(1 + nxt)
        result.update({
            "test_period": f"{_date(times[0])} to {_date(times[-1])}",
            "strategy": {**_summary(strat, ppy), "trades": int(changes.sum()), "time_in_market_pct": _r(float(pos.mean()) * 100, 1)},
            "buy_hold": _summary(nxt, ppy),
            "curves": {"strategy": [{"time": times[i], "value": round(float(sv[i]), 2)} for i in range(0, len(times), step)],
                       "buy_hold": [{"time": times[i], "value": round(float(hv[i]), 2)} for i in range(0, len(times), step)]},
        })
        result["beat_hold"] = (result["strategy"]["total_return_pct"] or 0) > (result["buy_hold"]["total_return_pct"] or 0)
    edge = acc_test - base
    if leak:
        verdict = (f"{acc_test * 100:.0f}% accuracy looks amazing, and that's the warning sign. The 'future_return' feature "
                   "contains the answer, so the model is reading tomorrow's newspaper. Real signals almost never get far "
                   "above 55%. Turn the leak off.")
    elif shuffle:
        verdict = (f"Shuffled split: {acc_test * 100:.1f}% accuracy. Training and test days are mixed together, so the model "
                   f"has seen days right next to the ones it's tested on (and with a {horizon}-day label their answers "
                   "overlap). Compare with the honest time split.")
    elif edge > 0.02:
        verdict = (f"{acc_test * 100:.1f}% accuracy vs {base * 100:.1f}% for always guessing '{result['baseline_says']}'. "
                   "A small edge on paper. Check whether it survives costs in the strategy result, and be suspicious: "
                   "small accuracy edges often vanish on the next period.")
    else:
        verdict = (f"{acc_test * 100:.1f}% accuracy vs {base * 100:.1f}% for always guessing '{result['baseline_says']}'. "
                   "No real edge over the simple baseline, which is the most common honest result in finance.")
    result["verdict"] = verdict
    return result
