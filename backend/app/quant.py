"""Quant lab maths: return statistics, correlation and beta, portfolio mixing, and "edge or luck?" tests.

Everything works on daily candles. Returns are simple daily returns (close to close). Stocks have ~252 trading days
a year and crypto 365; when stocks and crypto are compared, both are aligned on the dates they share, so a crypto
"day" there spans the weekend like a stock's Monday does.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone

import numpy as np

from app import assets
from app.strategies import make_strategy

DAY = 86400
EXAMPLE_DOLLARS = 10_000.0


# ------------------------------------------------------------------------------------------ helpers
def _date(t: int) -> str:
    return datetime.fromtimestamp(int(t), tz=timezone.utc).strftime("%Y-%m-%d")


def _r(x: float | None, digits: int = 4) -> float | None:
    if x is None or not math.isfinite(x):
        return None
    return round(float(x), digits)


def max_drawdown(values: np.ndarray) -> tuple[float, int, int]:
    """Largest fall from a peak, as a fraction, with the peak and trough indexes."""
    if len(values) < 2:
        return 0.0, 0, 0
    peaks = np.maximum.accumulate(values)
    dd = (peaks - values) / peaks
    trough = int(np.argmax(dd))
    peak = int(np.argmax(values[: trough + 1])) if trough > 0 else 0
    return float(dd[trough]), peak, trough


def _moments(r: np.ndarray) -> tuple[float, float]:
    sd = r.std()
    if sd <= 0 or len(r) < 3:
        return 0.0, 0.0
    z = (r - r.mean()) / sd
    return float((z ** 3).mean()), float((z ** 4).mean() - 3)


def _normal_pdf(x: np.ndarray, mu: float, sd: float) -> np.ndarray:
    return np.exp(-0.5 * ((x - mu) / sd) ** 2) / (sd * math.sqrt(2 * math.pi))


def _curve(times: list[int], values: np.ndarray, start: float = EXAMPLE_DOLLARS) -> list[dict]:
    scaled = values / values[0] * start
    return [{"time": int(t), "value": round(float(v), 2)} for t, v in zip(times, scaled)]


# ------------------------------------------------------------------------------------------ one asset
def stats(symbol: str, candles: list[dict]) -> dict:
    """Return and risk numbers for one asset from its daily candles."""
    if len(candles) < 30:
        raise ValueError(f"Not enough history for {symbol} yet.")
    ppy = assets.periods_per_year(symbol)
    times = [int(c["time"]) for c in candles]
    close = np.array([c["close"] for c in candles], dtype=float)
    r = close[1:] / close[:-1] - 1
    years = len(r) / ppy
    total = close[-1] / close[0] - 1
    cagr = (close[-1] / close[0]) ** (1 / years) - 1 if years > 0 else 0.0
    sd = float(r.std(ddof=1))
    mean = float(r.mean())
    dd, peak, trough = max_drawdown(close)
    skew, kurt = _moments(r)
    big = int(np.sum(np.abs(r - mean) > 3 * sd))
    best, worst = int(np.argmax(r)), int(np.argmin(r))

    # Histogram of daily returns (in %) with the bell curve a "normal" market would give.
    pct = r * 100
    lo, hi = np.percentile(pct, 0.5), np.percentile(pct, 99.5)
    edge = max(abs(lo), abs(hi))
    bins = np.linspace(-edge, edge, 31)
    counts, _ = np.histogram(np.clip(pct, -edge, edge), bins=bins)
    centers = (bins[:-1] + bins[1:]) / 2
    width = bins[1] - bins[0]
    normal = _normal_pdf(centers, mean * 100, sd * 100) * len(r) * width

    up_days = float(np.mean(r > 0)) * 100
    return {
        "symbol": symbol, "name": assets.name(symbol), "kind": assets.kind(symbol),
        "start": _date(times[0]), "end": _date(times[-1]), "days": len(r), "years": _r(years, 2),
        "periods_per_year": ppy,
        "total_return_pct": _r(total * 100, 2), "cagr_pct": _r(cagr * 100, 2),
        "example_start": EXAMPLE_DOLLARS, "example_end": _r(EXAMPLE_DOLLARS * (1 + total), 0),
        "mean_daily_pct": _r(mean * 100, 4), "vol_daily_pct": _r(sd * 100, 3),
        "vol_annual_pct": _r(sd * math.sqrt(ppy) * 100, 2),
        "typical_day_dollars": _r(EXAMPLE_DOLLARS * sd, 0),
        "sharpe": _r(mean / sd * math.sqrt(ppy) if sd > 0 else 0.0, 2),
        "max_drawdown_pct": _r(dd * 100, 2), "drawdown_peak": _date(times[peak]), "drawdown_trough": _date(times[trough]),
        "drawdown_example": _r(EXAMPLE_DOLLARS * (1 - dd), 0),
        "best_day_pct": _r(r[best] * 100, 2), "best_day": _date(times[best + 1]),
        "worst_day_pct": _r(r[worst] * 100, 2), "worst_day": _date(times[worst + 1]),
        "up_days_pct": _r(up_days, 1),
        "big_days": big, "big_days_expected": _r(len(r) * 0.0027, 1),
        "skew": _r(skew, 2), "excess_kurtosis": _r(kurt, 2),
        "histogram": [{"pct": _r(c, 3), "count": int(n), "normal": _r(e, 2)} for c, n, e in zip(centers, counts, normal)],
        "curve": _curve(times, close),
    }


# ------------------------------------------------------------------------------------------ many assets
def align(series: dict[str, list[dict]]) -> tuple[list[int], dict[str, np.ndarray]]:
    """Closes on the dates every series shares (dates are the UTC day of each daily candle)."""
    by_day = {s: {int(c["time"]) // DAY: float(c["close"]) for c in candles} for s, candles in series.items()}
    common = sorted(set.intersection(*(set(d) for d in by_day.values()))) if by_day else []
    if len(common) < 30:
        raise ValueError("These assets don't share enough trading days to compare.")
    return [d * DAY for d in common], {s: np.array([d[k] for k in common]) for s, d in by_day.items()}


def _returns(closes: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    return {s: c[1:] / c[:-1] - 1 for s, c in closes.items()}


def correlation(series: dict[str, list[dict]], benchmark: str = assets.BENCHMARK) -> dict:
    """Correlation matrix of daily returns, plus each asset's beta to the benchmark (SPY) and volatility."""
    symbols = list(series)
    times, closes = align(series)
    rets = _returns(closes)
    m = np.vstack([rets[s] for s in symbols])
    corr = np.corrcoef(m) if len(symbols) > 1 else np.array([[1.0]])
    out_beta = {}
    if benchmark in rets:
        b = rets[benchmark]
        var = float(np.var(b, ddof=1))
        for s in symbols:
            out_beta[s] = _r(float(np.cov(rets[s], b, ddof=1)[0, 1]) / var if var > 0 else None, 2)
    pairs = []
    for i in range(len(symbols)):
        for j in range(i + 1, len(symbols)):
            pairs.append({"a": symbols[i], "b": symbols[j], "corr": _r(corr[i, j], 2)})
    pairs.sort(key=lambda p: p["corr"])
    return {
        "symbols": symbols, "names": {s: assets.name(s) for s in symbols},
        "start": _date(times[0]), "end": _date(times[-1]), "days": len(times) - 1,
        "matrix": [[_r(corr[i, j], 2) for j in range(len(symbols))] for i in range(len(symbols))],
        "beta": out_beta, "benchmark": benchmark,
        "vol_annual_pct": {s: _r(float(rets[s].std(ddof=1)) * math.sqrt(252) * 100, 1) for s in symbols},
        "lowest_pair": pairs[0] if pairs else None, "highest_pair": pairs[-1] if pairs else None,
    }


def _summary(r: np.ndarray, ppy: int = 252) -> dict:
    value = np.concatenate([[1.0], np.cumprod(1 + r)])
    sd = float(r.std(ddof=1))
    years = len(r) / ppy
    dd, _, _ = max_drawdown(value)
    return {
        "total_return_pct": _r((value[-1] - 1) * 100, 2),
        "cagr_pct": _r((value[-1] ** (1 / years) - 1) * 100 if years > 0 else 0.0, 2),
        "vol_annual_pct": _r(sd * math.sqrt(ppy) * 100, 2),
        "sharpe": _r(float(r.mean()) / sd * math.sqrt(ppy) if sd > 0 else 0.0, 2),
        "max_drawdown_pct": _r(dd * 100, 2),
        "worst_day_pct": _r(float(r.min()) * 100, 2),
    }


def mix(series: dict[str, list[dict]], weights: dict[str, float]) -> dict:
    """A portfolio rebalanced back to `weights` every day, compared with each asset on its own."""
    symbols = [s for s in series if weights.get(s, 0) > 0]
    if len(symbols) < 2:
        raise ValueError("Pick at least two assets with a weight above zero.")
    total_w = sum(float(weights[s]) for s in symbols)
    w = np.array([float(weights[s]) / total_w for s in symbols])
    times, closes = align({s: series[s] for s in symbols})
    rets = _returns(closes)
    m = np.vstack([rets[s] for s in symbols])
    port = w @ m
    each = {s: _summary(rets[s]) for s in symbols}
    p = _summary(port)
    min_vol = min(e["vol_annual_pct"] for e in each.values())
    port_value = np.concatenate([[1.0], np.cumprod(1 + port)])
    curves = {"mix": _curve(times, port_value)}
    for s in symbols:
        curves[s] = _curve(times, closes[s])
    weighted_vol = float(sum(w[i] * each[s]["vol_annual_pct"] for i, s in enumerate(symbols)))
    return {
        "symbols": symbols, "names": {s: assets.name(s) for s in symbols},
        "weights": {s: _r(float(w[i]), 4) for i, s in enumerate(symbols)},
        "start": _date(times[0]), "end": _date(times[-1]), "days": len(times) - 1,
        "mix": p, "assets": each,
        "weighted_avg_vol_pct": _r(weighted_vol, 2),
        "diversification_benefit_pct": _r(weighted_vol - p["vol_annual_pct"], 2),
        "calmer_than_all": p["vol_annual_pct"] < min_vol,
        "has_stock_and_crypto": any(assets.is_crypto(s) for s in symbols) and any(not assets.is_crypto(s) for s in symbols),
        "curves": curves,
    }


# ------------------------------------------------------------------------------------------ edge or luck
def _positions_from_signals(signals: list) -> np.ndarray:
    """1 while holding. A signal on bar i's close changes the position from bar i+1 on."""
    pos = np.zeros(len(signals))
    holding = 0
    for i, s in enumerate(signals):
        pos[i] = holding
        if s == "buy":
            holding = 1
        elif s == "sell":
            holding = 0
    return pos


def _simple_returns(r: np.ndarray, pos: np.ndarray, cost: float) -> float:
    """Total return of holding when pos==1 (fully invested), paying `cost` (fraction) on every buy and sell."""
    trades = np.abs(np.diff(np.concatenate([[0.0], pos])))
    growth = np.prod(1 + r * pos) * np.prod((1 - cost) ** trades)
    return float(growth - 1)


def _random_positions(rng: np.random.Generator, n: int, p_in: float, p_out: float) -> np.ndarray:
    """A coin-flip trader: each day, if out, buy with chance p_in; if in, sell with chance p_out."""
    flips = rng.random(n)
    pos = np.zeros(n)
    holding = 0
    for i in range(n):
        pos[i] = holding
        if holding == 0 and flips[i] < p_in:
            holding = 1
        elif holding == 1 and flips[i] < p_out:
            holding = 0
    return pos


def _round_trips(pos: np.ndarray) -> tuple[int, float]:
    entries = int(np.sum(np.diff(np.concatenate([[0.0], pos])) > 0))
    held = float(pos.sum())
    return entries, (held / entries if entries else 0.0)


def luck_test(candles: list[dict], strategy: str, params: dict | None, cost: float, trials: int = 500,
              seed: int | None = None) -> dict:
    """Compare a strategy with `trials` random traders who trade about as often and hold about as long."""
    if len(candles) < 60:
        raise ValueError("Not enough history for a luck test.")
    strat = make_strategy(strategy, params)
    pos = _positions_from_signals(strat.signals(candles))
    out = luck_from_positions(candles, pos, cost, trials, seed)
    return {"strategy": strategy, "params": dict(strat.params), **out}


def luck_from_positions(candles: list[dict], pos: np.ndarray, cost: float, trials: int = 500,
                        seed: int | None = None) -> dict:
    """Luck test for any holding pattern: pos[i] = 1 while holding during bar i (already shifted, no look-ahead)."""
    pos = np.asarray(pos, dtype=float)
    close = np.array([c["close"] for c in candles], dtype=float)
    r = np.concatenate([[0.0], close[1:] / close[:-1] - 1])
    real = _simple_returns(r, pos, cost)
    trips, hold = _round_trips(pos)
    if trips == 0:
        raise ValueError("This strategy made no trades on this history, so there's nothing to test. Try other settings.")
    n = len(r)
    p_out = 1 / max(hold, 1.0)
    out_days = max(n - pos.sum(), 1.0)
    p_in = min(trips / out_days, 1.0)
    rng = np.random.default_rng(seed)
    randoms = np.array([_simple_returns(r, _random_positions(rng, n, p_in, p_out), cost) for _ in range(trials)])
    beat = float(np.mean(randoms < real)) * 100
    hold_return = float(close[-1] / close[0] - 1)
    pct = randoms * 100
    edges = np.linspace(min(pct.min(), real * 100), max(pct.max(), real * 100), 26)
    counts, _ = np.histogram(pct, bins=edges)
    if beat >= 95:
        verdict = (f"Better than {beat:.0f}% of random traders. That's unusual for luck alone, so it might be a real "
                   "edge. It still needs testing on data it hasn't seen (Level 7).")
    elif beat >= 75:
        verdict = (f"Better than {beat:.0f}% of random traders. That sounds good, but about 1 random trader in "
                   f"{max(round(100 / (100 - beat)), 2)} did as well, so luck is a perfectly good explanation.")
    else:
        verdict = (f"Only better than {beat:.0f}% of random traders. Coin flips did as well or better "
                   f"{100 - beat:.0f}% of the time, so there's no sign of an edge here.")
    return {
        "trials": trials,
        "start": _date(candles[0]["time"]), "end": _date(candles[-1]["time"]),
        "strategy_return_pct": _r(real * 100, 2), "buy_hold_return_pct": _r(hold_return * 100, 2),
        "trades": trips, "avg_days_held": _r(hold, 1),
        "random_median_pct": _r(float(np.median(pct)), 2), "random_best_pct": _r(float(pct.max()), 2),
        "random_worst_pct": _r(float(pct.min()), 2),
        "beat_pct": _r(beat, 1), "verdict": verdict,
        "histogram": [{"from": _r(edges[i], 2), "to": _r(edges[i + 1], 2), "count": int(counts[i])}
                      for i in range(len(counts))],
    }


def many_random(candles: list[dict], cost: float, count: int = 20, split: float = 0.7,
                seed: int | None = None) -> dict:
    """Try `count` random strategies on the first part of history, crown the best, then see how it does next."""
    if len(candles) < 120:
        raise ValueError("Not enough history for this test.")
    close = np.array([c["close"] for c in candles], dtype=float)
    r = np.concatenate([[0.0], close[1:] / close[:-1] - 1])
    cut = int(len(r) * split)
    rng = np.random.default_rng(seed)
    rows = []
    for i in range(count):
        hold = float(rng.uniform(3, 40))
        p_in = float(rng.uniform(0.02, 0.2))
        pos = _random_positions(rng, len(r), p_in, 1 / hold)
        pos_out = pos.copy()
        pos_out[cut] = 0  # start the second part flat
        rows.append({
            "id": i + 1, "name": f"Random strategy #{i + 1}",
            "first_pct": _r(_simple_returns(r[:cut], pos[:cut], cost) * 100, 2),
            "second_pct": _r(_simple_returns(r[cut:], pos_out[cut:], cost) * 100, 2),
        })
    best = max(rows, key=lambda x: x["first_pct"])
    hold_first = float(close[cut - 1] / close[0] - 1) * 100
    hold_second = float(close[-1] / close[cut - 1] - 1) * 100
    beat_first = sum(x["first_pct"] > hold_first for x in rows)
    return {
        "count": count, "rows": rows, "best_id": best["id"],
        "first_period": f"{_date(candles[0]['time'])} to {_date(candles[cut - 1]['time'])}",
        "second_period": f"{_date(candles[cut - 1]['time'])} to {_date(candles[-1]['time'])}",
        "buy_hold_first_pct": _r(hold_first, 2), "buy_hold_second_pct": _r(hold_second, 2),
        "best_first_pct": best["first_pct"], "best_second_pct": best["second_pct"],
        "beat_hold_first": beat_first,
        "verdict": (f"The best of {count} coin-flip strategies made {best['first_pct']:+.1f}% in the first period. "
                    f"Afterwards it made {best['second_pct']:+.1f}%. Picking the best of many tries finds luck, not "
                    "skill, and luck doesn't repeat."),
    }
