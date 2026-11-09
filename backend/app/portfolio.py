"""Portfolio construction and factor analysis (Level 10).

Weighting schemes are estimated on the first part of history and judged on the rest, so the learner sees which ideas
survive out-of-sample. Portfolios are rebalanced on a schedule and pay costs on the amount traded (turnover).
Factor exposures use ETF proxies: each factor's daily return is its ETF minus SPY (a rough long-short), plus SPY itself
as the market factor.
"""
from __future__ import annotations

import math

import numpy as np

from app import assets
from app.quant import _date, _r, align, max_drawdown

SCHEMES = {
    "equal": "Equal weight",
    "inverse_vol": "Inverse volatility",
    "risk_parity": "Risk parity",
    "min_variance": "Minimum variance",
    "max_sharpe": "Max Sharpe (mean-variance)",
}
FACTORS = {  # factor -> (ETF, plain-English meaning)
    "market": ("SPY", "the whole stock market"),
    "size": ("IWM", "small companies vs big ones"),
    "momentum": ("MTUM", "recent winners vs the market"),
    "value": ("VLUE", "cheap-looking companies vs the market"),
    "quality": ("QUAL", "profitable, steady companies vs the market"),
    "low_vol": ("USMV", "calm, low-volatility stocks vs the market"),
}
FACTOR_ETFS = [etf for etf, _ in FACTORS.values()]
REBALANCE_DAYS = {"daily": 1, "weekly": 5, "monthly": 21, "quarterly": 63, "never": 10**9}
PPY = 252


# ------------------------------------------------------------------------------------------ weights
def _project_simplex(v: np.ndarray) -> np.ndarray:
    """Closest long-only weights (>= 0, summing to 1) to v."""
    u = np.sort(v)[::-1]
    css = np.cumsum(u)
    rho = np.nonzero(u * np.arange(1, len(v) + 1) > (css - 1))[0][-1]
    theta = (css[rho] - 1) / (rho + 1.0)
    return np.maximum(v - theta, 0)


def weights_for(scheme: str, rets: np.ndarray) -> np.ndarray:
    """Long-only weights for returns matrix `rets` (days × assets)."""
    n = rets.shape[1]
    cov = np.cov(rets, rowvar=False) + np.eye(n) * 1e-10
    vol = np.sqrt(np.diag(cov))
    if scheme == "equal":
        return np.full(n, 1 / n)
    if scheme == "inverse_vol":
        w = 1 / vol
        return w / w.sum()
    if scheme == "risk_parity":
        w = 1 / vol
        w /= w.sum()
        for _ in range(500):  # equal risk contribution: w_i * (cov w)_i the same for all i
            mrc = cov @ w
            rc = w * mrc
            target = rc.sum() / n
            w_new = w * (target / np.maximum(rc, 1e-18)) ** 0.5
            w_new /= w_new.sum()
            if np.max(np.abs(w_new - w)) < 1e-10:
                w = w_new
                break
            w = w_new
        return w
    if scheme == "min_variance":
        w = np.full(n, 1 / n)
        step = 0.5 / max(np.linalg.eigvalsh(cov).max(), 1e-12)
        for _ in range(3000):
            w = _project_simplex(w - step * (cov @ w))
        return w
    if scheme == "max_sharpe":
        mu = rets.mean(axis=0)
        w = np.full(n, 1 / n)
        step = 0.05 / max(np.linalg.eigvalsh(cov).max(), 1e-12)
        for _ in range(4000):  # maximise mu.w - (lambda/2) w'Cw with lambda chosen so it's scale-free
            lam = max(float(w @ mu) / max(float(w @ cov @ w), 1e-18), 1e-6)
            w = _project_simplex(w + step * (mu - lam * (cov @ w)))
        return w
    raise KeyError(f"Unknown weighting scheme '{scheme}'")


def risk_contributions(w: np.ndarray, rets: np.ndarray) -> np.ndarray:
    cov = np.cov(rets, rowvar=False)
    rc = w * (cov @ w)
    total = rc.sum()
    return rc / total if total > 0 else rc


# ------------------------------------------------------------------------------------------ simulation
def simulate(rets: np.ndarray, w: np.ndarray, rebalance: str = "monthly", cost: float = 0.0005) -> tuple[np.ndarray, float]:
    """Daily portfolio returns holding target weights `w`, rebalanced every N days, paying `cost` per $ traded.
    Returns (daily returns, average yearly turnover)."""
    every = REBALANCE_DAYS[rebalance]
    hold = w.copy()
    out = np.zeros(len(rets))
    traded = 0.0
    for t in range(len(rets)):
        if t % every == 0:
            turnover = float(np.abs(w - hold).sum()) if t else float(np.abs(w).sum())
            traded += turnover
            out[t] -= turnover * cost
            hold = w.copy()
        gross = float(hold @ rets[t])
        out[t] += gross
        hold = hold * (1 + rets[t]) / (1 + gross) if 1 + gross > 0 else hold
    years = max(len(rets) / PPY, 1e-9)
    return out, traded / years


def vol_target(port: np.ndarray, target: float = 0.10, window: int = 20, max_lev: float = 1.0) -> np.ndarray:
    """Scale exposure each day by target ÷ last `window` days' volatility (capped), using only past data."""
    out = np.zeros(len(port))
    for t in range(len(port)):
        if t < window:
            lev = 1.0
        else:
            realized = float(port[t - window:t].std(ddof=1)) * math.sqrt(PPY)
            lev = min(max_lev, target / realized) if realized > 0 else max_lev
        out[t] = lev * port[t]
    return out


def summary(r: np.ndarray) -> dict:
    value = np.concatenate([[1.0], np.cumprod(1 + r)])
    sd = float(r.std(ddof=1)) if len(r) > 2 else 0.0
    years = len(r) / PPY
    dd, _, _ = max_drawdown(value)
    return {
        "total_return_pct": _r((value[-1] - 1) * 100, 2),
        "cagr_pct": _r((value[-1] ** (1 / years) - 1) * 100 if years > 0 and value[-1] > 0 else -100.0, 2),
        "vol_annual_pct": _r(sd * math.sqrt(PPY) * 100, 2),
        "sharpe": _r(float(r.mean()) / sd * math.sqrt(PPY) if sd > 0 else 0.0, 2),
        "max_drawdown_pct": _r(dd * 100, 2),
    }


def _curve(times: list[int], r: np.ndarray, start: float = 10_000.0) -> list[dict]:
    v = start * np.concatenate([[1.0], np.cumprod(1 + r)])
    step = max(1, len(v) // 800)
    return [{"time": int(times[i]), "value": round(float(v[i]), 2)} for i in range(0, len(v), step)]


def compare(series: dict[str, list[dict]], schemes: list[str] | None = None, split: float = 0.5,
            rebalance: str = "monthly", cost: float = 0.0005, target_vol: float | None = None) -> dict:
    """Estimate each scheme's weights on the first `split` of history; report in-sample and out-of-sample results."""
    symbols = list(series)
    if len(symbols) < 2:
        raise ValueError("Pick at least two assets.")
    if rebalance not in REBALANCE_DAYS:
        raise ValueError(f"rebalance must be one of {', '.join(REBALANCE_DAYS)}")
    schemes = schemes or list(SCHEMES)
    times, closes = align(series)
    m = np.vstack([closes[s][1:] / closes[s][:-1] - 1 for s in symbols]).T
    t_ret = times[1:]
    cut = int(len(m) * split)
    if cut < 120 or len(m) - cut < 60:
        raise ValueError("Not enough shared history for a fair test. Use more years or fewer recent assets.")
    train, test = m[:cut], m[cut:]
    rows = []
    for scheme in schemes:
        w = weights_for(scheme, train)
        ins, _ = simulate(train, w, rebalance, cost)
        outs, turnover = simulate(test, w, rebalance, cost)
        row = {"scheme": scheme, "name": SCHEMES[scheme],
               "weights": {s: _r(float(w[i]), 4) for i, s in enumerate(symbols)},
               "risk_share": {s: _r(float(x), 4) for s, x in zip(symbols, risk_contributions(w, train))},
               "in_sample": summary(ins), "out_sample": summary(outs), "turnover": _r(turnover, 2),
               "curve": _curve(t_ret[cut - 1:], outs)}
        if target_vol:
            vt = vol_target(np.concatenate([ins, outs]), target_vol)[cut:]
            row["vol_target"] = summary(vt)
            row["vol_target_curve"] = _curve(t_ret[cut - 1:], vt)
        rows.append(row)
    best_in = max(rows, key=lambda x: x["in_sample"]["sharpe"] or -99)
    best_out = max(rows, key=lambda x: x["out_sample"]["sharpe"] or -99)
    return {
        "symbols": symbols, "names": {s: assets.name(s) for s in symbols}, "rebalance": rebalance, "cost_pct": cost * 100,
        "train": f"{_date(t_ret[0])} to {_date(t_ret[cut - 1])}", "test": f"{_date(t_ret[cut])} to {_date(t_ret[-1])}",
        "rows": rows, "best_in_sample": best_in["scheme"], "best_out_sample": best_out["scheme"],
        "target_vol_pct": target_vol * 100 if target_vol else None,
    }


def kelly(candles: list[dict], symbol: str, max_f: float = 4.0) -> dict:
    """Kelly fraction for one asset from its history (in hindsight), and how full / half / 1x would have grown."""
    close = np.array([c["close"] for c in candles], dtype=float)
    r = close[1:] / close[:-1] - 1
    ppy = assets.periods_per_year(symbol)
    mu, var = float(r.mean()), float(r.var(ddof=1))
    f = max(min(mu / var if var > 0 else 0.0, max_f), 0.0)
    times = [int(c["time"]) for c in candles]
    out = {"symbol": symbol, "name": assets.name(symbol), "full_kelly": _r(f, 2), "half_kelly": _r(f / 2, 2),
           "mean_annual_pct": _r(mu * ppy * 100, 2), "vol_annual_pct": _r(math.sqrt(var * ppy) * 100, 2), "paths": {}}
    for label, frac in (("1x (no leverage)", 1.0), ("half Kelly", f / 2), ("full Kelly", f)):
        g = np.clip(frac * r, -0.999, None)
        value = np.concatenate([[1.0], np.cumprod(1 + g)])
        dd, _, _ = max_drawdown(value)
        out["paths"][label] = {"fraction": _r(frac, 2), "final_multiple": _r(float(value[-1]), 2),
                               "max_drawdown_pct": _r(dd * 100, 1), "curve": _curve(times, g)}
    return out


# ------------------------------------------------------------------------------------------ factors
def factor_returns(series: dict[str, list[dict]], target: dict[str, list[dict]]) -> tuple[list[int], dict, np.ndarray]:
    """Aligned daily factor returns (market = SPY; others = ETF - SPY) and the target's returns."""
    times, closes = align({**series, **target})
    rets = {s: c[1:] / c[:-1] - 1 for s, c in closes.items()}
    spy = rets["SPY"]
    f = {}
    for name, (etf, _) in FACTORS.items():
        f[name] = spy if name == "market" else rets[etf] - spy
    (tname,) = target.keys()
    return times[1:], f, rets[tname]


def regress(y: np.ndarray, factors: dict[str, np.ndarray]) -> dict:
    """OLS of daily returns on factor returns, with alpha, t-stats and R²."""
    names = list(factors)
    X = np.column_stack([np.ones(len(y))] + [factors[k] for k in names])
    coef, *_ = np.linalg.lstsq(X, y, rcond=None)
    resid = y - X @ coef
    dof = max(len(y) - X.shape[1], 1)
    s2 = float(resid @ resid) / dof
    try:
        se = np.sqrt(np.diag(s2 * np.linalg.inv(X.T @ X)))
    except np.linalg.LinAlgError:
        se = np.full(X.shape[1], np.nan)
    ss_tot = float(((y - y.mean()) ** 2).sum())
    r2 = 1 - float(resid @ resid) / ss_tot if ss_tot > 0 else 0.0
    loadings = [{"factor": k, "etf": FACTORS[k][0], "meaning": FACTORS[k][1], "beta": _r(float(coef[i + 1]), 3),
                 "t": _r(float(coef[i + 1] / se[i + 1]) if se[i + 1] > 0 else None, 2)} for i, k in enumerate(names)]
    alpha_t = float(coef[0] / se[0]) if se[0] > 0 else 0.0
    return {"alpha_annual_pct": _r(float(coef[0]) * PPY * 100, 2), "alpha_t": _r(alpha_t, 2), "r2": _r(r2, 3),
            "loadings": loadings, "days": len(y)}


def factor_breakdown(series: dict[str, list[dict]], target_name: str, target: list[dict]) -> dict:
    times, f, y = factor_returns(series, {target_name: target})
    out = regress(y, f)
    sig = [l for l in out["loadings"] if l["t"] is not None and abs(l["t"]) >= 2 and l["factor"] != "market"]
    mkt = next(l for l in out["loadings"] if l["factor"] == "market")
    parts = [f"{out['r2'] * 100:.0f}% of the daily ups and downs are explained by these factors"]
    parts.append(f"market beta {mkt['beta']:.2f}")
    if sig:
        parts.append("clear tilts: " + ", ".join(f"{l['factor'].replace('_', ' ')} ({l['beta']:+.2f})" for l in sig))
    alpha_ok = abs(out["alpha_t"] or 0) >= 2
    verdict = "; ".join(parts) + ". " + (
        f"The leftover return (alpha) of {out['alpha_annual_pct']:+.1f}% a year is statistically clear (t = {out['alpha_t']:.1f})."
        if alpha_ok else
        f"The leftover return (alpha) of {out['alpha_annual_pct']:+.1f}% a year is not clearly different from zero "
        f"(t = {out['alpha_t']:.1f}; you'd want about 2 or more), so it could be luck.")
    return {**out, "target": target_name, "start": _date(times[0]), "end": _date(times[-1]), "verdict": verdict}
