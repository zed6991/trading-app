"""Swing trading maths on daily bars (Level 14). Pure functions: no network, no state, so everything is testable.

Ideas adapted for swing trading from the MIT-licensed day-trader-skill (github.com/jakeschincariol/day-trader-skill):
a scan is a list of charts that matched a rule (never "a buy"); the stop and target are set before the entry and
measured in R; a Time Machine replays the plan thousands of times from the stock's own bars with a fixed seed, plus the
real record of the same rule; and its verdict weighs the target's R (break-even win rate = 1 / (1 + R)).

Conventions
- Candles are the app's candle dicts, ascending, closed bars only (the forming bar is handled by the desk).
- A plan is long-only: buy-stop entry above the signal bar's high, stop below, target = entry + R x risk,
  `valid_days` sessions for the entry to trigger and a time stop of `max_days` sessions after the fill.
- R = (exit - fill) / (planned entry - stop), after slippage on both sides. A gap through the stop is counted at the
  open, so it can be worse than -1R: that's what really happens to a stop order overnight.
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np

from app.indicators import atr as _atr
from app.indicators import rsi as _rsi
from app.indicators import sma as _sma

SETUPS: dict[str, dict[str, str]] = {
    "pullback": {"name": "Pullback in an uptrend",
                 "rule": "Price is above its 200-day average with the 50-day above the 200-day, ran up, then dipped to "
                         "its 20-day average and closed in the top half of the day's range."},
    "breakout": {"name": "20-day breakout",
                 "rule": "Closed above the highest high of the previous 20 days, on at least 1.5x the average volume, "
                         "above its 50-day average."},
    "bull_flag": {"name": "Bull flag",
                  "rule": "Rose more than 10% over 10 days, then rested: the last 5 closes sit inside a 5% range, above "
                          "the 50-day average."},
    "oversold_bounce": {"name": "Oversold bounce in an uptrend",
                        "rule": "Above its 200-day average, RSI was under 30 yesterday, and today closed higher."},
    "ma_reclaim": {"name": "Reclaimed the 20-day average",
                   "rule": "Closed back above the 20-day average after closing below it, while the 50-day is above the "
                           "200-day."},
}
MIN_BARS = 220
DEFAULTS = {"r_target": 2.0, "valid_days": 3, "max_days": 10}


# --------------------------------------------------------------------------------------------- helpers
def arrays(candles: list[dict]) -> dict[str, np.ndarray]:
    o = np.array([c["open"] for c in candles], dtype=float)
    h = np.array([c["high"] for c in candles], dtype=float)
    lo = np.array([c["low"] for c in candles], dtype=float)
    c = np.array([c["close"] for c in candles], dtype=float)
    v = np.array([x.get("volume", 0.0) for x in candles], dtype=float)
    return {"o": o, "h": h, "l": lo, "c": c, "v": v, "t": np.array([x["time"] for x in candles], dtype=np.int64)}


def _shift(a: np.ndarray, k: int) -> np.ndarray:
    """a[i - k] at position i (NaN where it doesn't exist)."""
    out = np.full(len(a), np.nan)
    if k < len(a):
        out[k:] = a[:len(a) - k]
    return out


def _rolling(a: np.ndarray, n: int, fn) -> np.ndarray:
    out = np.full(len(a), np.nan)
    if len(a) >= n:
        out[n - 1:] = fn(np.lib.stride_tricks.sliding_window_view(a, n), axis=1)
    return out


def round_down(x: float) -> float:
    return math.floor(x * 100 + 1e-9) / 100


def round_up(x: float) -> float:
    return math.ceil(x * 100 - 1e-9) / 100


def indicators(a: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    c, h, lo, v = a["c"], a["h"], a["l"], a["v"]
    return {
        "sma20": _sma(c, 20), "sma50": _sma(c, 50), "sma200": _sma(c, 200), "atr": _atr(h, lo, c, 14),
        "rsi": _rsi(c, 14), "avgvol50": _sma(v, 50), "dollar_vol20": _sma(c * v, 20),
        "hh20_prev": _shift(_rolling(h, 20, np.max), 1), "low5": _rolling(lo, 5, np.min),
        "maxc15_prev": _shift(_rolling(c, 15, np.max), 1),
        "c5_max": _rolling(c, 5, np.max), "c5_min": _rolling(c, 5, np.min),
    }


def setup_masks(a: dict[str, np.ndarray], ind: dict[str, np.ndarray] | None = None) -> dict[str, np.ndarray]:
    """For each setup, a boolean array: did the setup fire on the close of bar i? Uses only bars <= i."""
    ind = ind or indicators(a)
    o, h, lo, c, v = a["o"], a["h"], a["l"], a["c"], a["v"]
    s20, s50, s200, at = ind["sma20"], ind["sma50"], ind["sma200"], ind["atr"]
    with np.errstate(invalid="ignore", divide="ignore"):
        uptrend = (c > s200) & (s50 > s200)
        rng = h - lo
        upper_half = np.where(rng > 0, (c - lo) >= 0.5 * rng, False)
        masks = {
            "pullback": uptrend & (lo <= s20 * 1.01) & (c >= s50) & (ind["maxc15_prev"] >= s20 + 1.5 * at) & upper_half,
            "breakout": (c > ind["hh20_prev"]) & (v >= 1.5 * _shift(ind["avgvol50"], 1)) & (c > s50),
            "bull_flag": ((_shift(c, 5) / _shift(c, 15) - 1) > 0.10) & ((ind["c5_max"] / ind["c5_min"] - 1) <= 0.05)
                         & (c > s50),
            "oversold_bounce": (c > s200) & (_shift(ind["rsi"], 1) < 30) & (c > _shift(c, 1)),
            "ma_reclaim": (_shift(c, 1) < _shift(s20, 1)) & (c > s20) & (s50 > s200),
        }
    valid = ~np.isnan(s200) & ~np.isnan(at) & ~np.isnan(ind["avgvol50"])
    valid[:MIN_BARS - 1] = False
    return {k: np.nan_to_num(m.astype(float)).astype(bool) & valid for k, m in masks.items()}


# --------------------------------------------------------------------------------------------- the plan
def plan_levels(a: dict[str, np.ndarray], ind: dict[str, np.ndarray], i: int, r_target: float = 2.0,
                stop_rule: str = "swing") -> dict[str, Any]:
    """Default entry, stop and target for a setup that fired on bar i.

    Entry: a buy-stop one cent (or 0.1%) above bar i's high, so the stock has to prove itself by trading higher.
    Stop ("swing"): just under the lowest low of the last 5 days, but at least 1 ATR (a normal day's move) under the
    entry so ordinary wiggle doesn't stop you out, and at most 3 ATR (else 2 ATR). ("atr"): 2 ATR under the entry.
    Target: entry + R x risk.
    """
    h, at = float(a["h"][i]), float(ind["atr"][i])
    entry = round_up(h + max(0.01, 0.001 * h))
    note = ""
    if stop_rule == "atr":
        stop = entry - 2 * at
        note = "2 ATR under the entry"
    else:
        stop = min(float(ind["low5"][i]) - 0.1 * at, entry - at)
        note = "just under the 5-day low (at least 1 ATR away)"
        if entry - stop > 3 * at:
            stop = entry - 2 * at
            note = "the 5-day low was more than 3 ATR away, so 2 ATR under the entry"
    stop = round_down(stop)
    risk = entry - stop
    target = round_up(entry + r_target * risk)
    return {"entry": entry, "stop": stop, "target": target, "risk_per_share": round(risk, 4), "atr": round(at, 4),
            "stop_note": note, "r_target": r_target}


def validate_plan(entry: float, stop: float, target: float, atr_value: float | None = None) -> list[str]:
    """Reasons a plan can't be used (empty list = fine)."""
    problems = []
    if not (entry > 0 and stop > 0 and target > 0):
        problems.append("Entry, stop and target must all be above zero.")
        return problems
    if stop >= entry:
        problems.append("The stop must be below the entry for a buy.")
    if target <= entry:
        problems.append("The target must be above the entry.")
    if stop < entry and (entry - stop) < 0.005 * entry:
        problems.append(f"The stop is only ${entry - stop:.2f} away ({(entry - stop) / entry * 100:.2f}%): too tight to "
                        "survive a normal day. Widen it and buy fewer shares.")
    if atr_value and stop < entry and (entry - stop) < 0.5 * atr_value:
        problems.append(f"The stop is ${entry - stop:.2f} away but this stock moves about ${atr_value:.2f} on a normal "
                        "day (its ATR). Put the stop at least half an ATR away.")
    return problems


# --------------------------------------------------------------------------------------------- one trade on real bars
def simulate_trade(a: dict[str, np.ndarray], start: int, entry: float, stop: float, target: float,
                   valid_days: int = 3, max_days: int = 10, slip: float = 0.0) -> dict[str, Any]:
    """Walk the plan forward from bar `start` (the first session after the signal). Daily bars only, so:
    - the entry fills at the trigger, or at the open if the stock gaps above it;
    - on the fill day the stop counts if the low reaches it, the target only if the close is above it (we can't
      know which came first inside the day, so we assume the worse);
    - after that, a gap through the stop exits at the open (worse than -1R), a gap over the target at the open;
      a day that touches both counts as the stop;
    - after `max_days` sessions it sells at the close (the time stop).
    Returns {"outcome": "target"|"stop"|"gap_stop"|"time"|"no_fill"|"open", "r", "fill_i", "exit_i", "fill", "exit"}.
    """
    o, h, lo, c = a["o"], a["h"], a["l"], a["c"]
    n = len(c)
    risk = entry - stop
    fill_i = None
    for j in range(start, min(start + valid_days, n)):
        if h[j] >= entry:
            fill_i = j
            break
    if fill_i is None:
        return {"outcome": "no_fill" if start + valid_days <= n else "open", "r": None, "fill_i": None, "exit_i": None}
    fill = max(entry, o[fill_i]) * (1 + slip)

    def done(outcome: str, px: float, k: int) -> dict:
        px = px * (1 - slip)
        return {"outcome": outcome, "r": (px - fill) / risk, "fill_i": fill_i, "exit_i": k, "fill": fill, "exit": px}

    if lo[fill_i] <= stop:
        return done("stop", stop, fill_i)
    if c[fill_i] >= target:
        return done("target", target, fill_i)
    for k in range(fill_i + 1, min(fill_i + max_days + 1, n)):
        if o[k] <= stop:
            return done("gap_stop", o[k], k)
        if o[k] >= target:
            return done("target", o[k], k)
        if lo[k] <= stop:
            return done("stop", stop, k)
        if h[k] >= target:
            return done("target", target, k)
        if k - fill_i >= max_days:
            return done("time", c[k], k)
    return {"outcome": "open", "r": None, "fill_i": fill_i, "exit_i": None}


# --------------------------------------------------------------------------------------------- the Time Machine
def _verdict(target_first: int, stop_first: int, r_target: float) -> str:
    hits = target_first + stop_first
    if hits == 0:
        return "Almost every replay ran out of time: the time stop decides this trade, not the stop or the target."
    share = 100 * target_first / hits
    need = 100 / (1 + r_target)
    diff = share - need
    side = "target" if diff > 0 else "stop"
    if abs(diff) < 5:
        return (f"Close to a coin flip: of the replays that hit a side, {share:.0f}% hit the target first; a 1R target "
                f"needs {need:.0f}% to break even." if abs(r_target - 1) < 0.05 else
                f"Close to break-even: of the replays that hit a side, {share:.0f}% hit the target first; a "
                f"{r_target:g}R target needs {need:.0f}%.")
    word = "Leans" if abs(diff) < 15 else "Strongly"
    return (f"{word} toward the {side}: of the replays that hit a side, {share:.0f}% hit the target first; a "
            f"{r_target:g}R target needs {need:.0f}% to break even.")


def time_machine(a: dict[str, np.ndarray], entry: float, stop: float, target: float, max_days: int = 10,
                 replays: int = 10_000, seed: int = 19, pool_days: int = 500, slip: float = 0.0) -> dict[str, Any]:
    """Replay the trade `replays` times, starting from a fill at the entry. Each day is a real day from the stock's
    last `pool_days` sessions, picked at random and scaled to the current price (open, high, low and close as ratios
    of the previous close), so gaps, wide days and quiet days all show up as often as they really did.

    Shuffling days keeps the stock's normal size of moves and its drift but forgets your setup: it shows what your
    stop, target and time stop do with this stock's ordinary days. The history (`setup_history`) is the evidence
    for the setup itself.
    """
    o, h, lo, c = a["o"], a["h"], a["l"], a["c"]
    m = min(pool_days, len(c) - 1)
    if m < 60:
        raise ValueError("Need at least 60 days of history to replay this trade.")
    prev = c[-m - 1:-1]
    ratios = np.stack([o[-m:] / prev, h[-m:] / prev, lo[-m:] / prev, c[-m:] / prev], axis=1)
    rng = np.random.default_rng(seed)
    picks = rng.integers(0, m, size=(replays, max_days))
    risk = entry - stop
    price = np.full(replays, entry * (1 + slip))
    fill = price.copy()
    exit_px = np.full(replays, np.nan)
    outcome = np.zeros(replays, dtype=np.int8)  # 0 open, 1 target, 2 stop, 3 gap stop, 4 time
    days = np.full(replays, max_days)
    alive = np.ones(replays, dtype=bool)
    fan = np.zeros((max_days, 5))
    for d in range(max_days):
        r = ratios[picks[:, d]]
        O, H, L, C = price * r[:, 0], price * r[:, 1], price * r[:, 2], price * r[:, 3]
        gap_stop = alive & (O <= stop)
        gap_tgt = alive & ~gap_stop & (O >= target)
        hit_stop = alive & ~gap_stop & ~gap_tgt & (L <= stop)
        hit_tgt = alive & ~gap_stop & ~gap_tgt & ~hit_stop & (H >= target)
        for mask, px, code in ((gap_stop, O, 3), (gap_tgt, O, 1), (hit_stop, np.full(replays, stop), 2),
                               (hit_tgt, np.full(replays, target), 1)):
            exit_px[mask] = px[mask]
            outcome[mask] = code
            days[mask] = d + 1
            alive &= ~mask
        price = np.where(alive, C, price)
        fan[d] = np.percentile(np.where(alive, C, exit_px), [5, 25, 50, 75, 95])
    exit_px[alive] = price[alive]
    outcome[alive] = 4
    R = (exit_px * (1 - slip) - fill) / risk
    n = replays
    tgt, stp, gap, tim = (int((outcome == k).sum()) for k in (1, 2, 3, 4))
    hist_edges = np.linspace(-2.0, max(3.0, float(np.ceil((target - entry) / risk)) + 0.5), 14)
    counts, _ = np.histogram(np.clip(R, hist_edges[0], hist_edges[-1]), bins=hist_edges)
    r_target = (target - entry) / risk
    notes = []
    if tim / n > 0.4:
        notes.append(f"{100 * tim / n:.0f}% of the replays hit neither within {max_days} days; the time stop sold those "
                     f"(average {float(R[outcome == 4].mean()):+.2f}R). A closer target or a longer time stop changes this.")
    if gap / n > 0.03:
        notes.append(f"{100 * gap / n:.1f}% of the replays gapped through the stop overnight; the worst lost "
                     f"{-float(R.min()):.1f}R, not 1R. Size for that.")
    return {
        "notes": notes,
        "replays": n, "seed": seed, "pool_days": m, "max_days": max_days,
        "target_pct": round(100 * tgt / n, 1), "stop_pct": round(100 * (stp + gap) / n, 1),
        "gap_stop_pct": round(100 * gap / n, 1), "time_pct": round(100 * tim / n, 1),
        "avg_r": round(float(R.mean()), 3), "median_r": round(float(np.median(R)), 3),
        "r_p5": round(float(np.percentile(R, 5)), 2), "r_p95": round(float(np.percentile(R, 95)), 2),
        "worst_r": round(float(R.min()), 2), "median_days": float(np.median(days)),
        "fan": [{"day": d + 1, "p5": round(float(f[0]), 2), "p25": round(float(f[1]), 2), "p50": round(float(f[2]), 2),
                 "p75": round(float(f[3]), 2), "p95": round(float(f[4]), 2)} for d, f in enumerate(fan)],
        "hist": [{"from": round(float(hist_edges[i]), 2), "to": round(float(hist_edges[i + 1]), 2), "count": int(counts[i])}
                 for i in range(len(counts))],
        "verdict": _verdict(tgt, stp + gap, r_target),
        "r_target": round(r_target, 2),
    }


# --------------------------------------------------------------------------------------------- the setup's real record
def setup_history(candles: list[dict], setup: str, r_target: float = 2.0, valid_days: int = 3, max_days: int = 10,
                  stop_rule: str = "swing", slip: float = 0.0, seed: int = 19, random_trials: int = 1500) -> dict:
    """Run the same setup and plan rules on every past day it fired (one trade at a time, like you would), and the
    same plan rules on random days. Answers: did this setup do better than picking days at random?"""
    if setup not in SETUPS:
        raise KeyError(f"Unknown setup '{setup}'")
    a = arrays(candles)
    ind = indicators(a)
    fires = setup_masks(a, ind)[setup]
    n = len(a["c"])

    def run(i: int) -> dict | None:
        p = plan_levels(a, ind, i, r_target, stop_rule)
        if p["entry"] <= p["stop"]:
            return None
        return simulate_trade(a, i + 1, p["entry"], p["stop"], p["target"], valid_days, max_days, slip) | {"i": i}

    trades, busy_until, fired = [], -1, 0
    for i in np.flatnonzero(fires[:-1]):
        fired += 1
        if i <= busy_until:
            continue
        t = run(int(i))
        if t is None:
            continue
        if t["outcome"] in ("no_fill",):
            busy_until = i + valid_days
            trades.append(t)
            continue
        busy_until = t["exit_i"] if t["exit_i"] is not None else n
        trades.append(t)
    closed = [t for t in trades if t["r"] is not None]
    rs = np.array([t["r"] for t in closed]) if closed else np.array([])

    valid_idx = np.flatnonzero(~np.isnan(ind["sma200"]) & ~np.isnan(ind["atr"]))
    valid_idx = valid_idx[valid_idx < n - 1]
    rng = np.random.default_rng(seed)
    rand_r: list[float] = []
    if len(valid_idx):
        for i in rng.choice(valid_idx, size=min(random_trials, len(valid_idx) * 3), replace=True):
            t = run(int(i))
            if t and t["r"] is not None:
                rand_r.append(t["r"])
    rand = np.array(rand_r)
    beat = None
    if len(rs) >= 3 and len(rand) >= 50:
        sims = rng.choice(rand, size=(2000, len(rs)), replace=True).mean(axis=1)
        beat = round(100 * float((sims < rs.mean()).mean()), 1)

    def summary(r: np.ndarray) -> dict:
        if not len(r):
            return {"trades": 0, "win_rate": None, "avg_r": None, "total_r": None}
        return {"trades": int(len(r)), "win_rate": round(100 * float((r > 0).mean()), 1),
                "avg_r": round(float(r.mean()), 3), "total_r": round(float(r.sum()), 2)}

    counts = {k: sum(1 for t in trades if t["outcome"] == k) for k in ("target", "stop", "gap_stop", "time", "no_fill")}
    recent = [{"date": _iso(a["t"][t["i"]]), "outcome": t["outcome"], "r": None if t["r"] is None else round(t["r"], 2)}
              for t in trades[-12:]]
    s, rnd = summary(rs), summary(rand)
    years = (a["t"][-1] - a["t"][0]) / (365.25 * 86400) if n > 1 else 0
    if s["trades"] < 10:
        verdict = (f"Only {s['trades']} finished trades in {years:.1f} years: too few to tell skill from luck. Treat any "
                   "number here as a rough hint.")
    elif beat is not None and beat >= 95:
        verdict = (f"The setup's average ({s['avg_r']:+.2f}R) beat {beat:.0f}% of same-sized groups of random-day "
                   "trades with the same stop and target. On this stock's history it looks like more than luck.")
    elif beat is not None and beat >= 80:
        verdict = (f"The setup did better than random days ({s['avg_r']:+.2f}R vs {rnd['avg_r']:+.2f}R), beating "
                   f"{beat:.0f}% of random groups: promising, but luck can still explain it.")
    else:
        verdict = (f"The setup ({s['avg_r']:+.2f}R a trade) wasn't clearly better than random days with the same stop and "
                   f"target ({rnd['avg_r'] if rnd['avg_r'] is not None else 0:+.2f}R): on this stock, it looks like luck.")
    return {"setup": setup, "years": round(years, 1), "fired": fired, "taken": len(trades), "counts": counts,
            "setup_stats": s, "random_stats": rnd, "beat_random_pct": beat, "recent": recent, "verdict": verdict}


def _iso(t: int) -> str:
    import datetime as _dt

    return _dt.datetime.fromtimestamp(int(t), _dt.timezone.utc).date().isoformat()


# --------------------------------------------------------------------------------------------- journal statistics
def edge_stats(trades: list[dict], seed: int = 19) -> dict:
    """Stats for a list of closed trades ({"r", "pnl", "followed_plan"}): win rate, average win/loss in R, expectancy,
    and a luck check: resample the trades 2,000 times and count how often the average R stays above zero."""
    rs = np.array([t["r"] for t in trades if t.get("r") is not None], dtype=float)
    pnl = float(sum(t.get("pnl") or 0 for t in trades))
    followed = [t.get("followed_plan") for t in trades if t.get("followed_plan") is not None]
    if not len(rs):
        return {"trades": 0, "pnl": round(pnl, 2)}
    wins, losses = rs[rs > 0], rs[rs <= 0]
    win_rate = float((rs > 0).mean())
    avg_win = float(wins.mean()) if len(wins) else 0.0
    avg_loss = float(losses.mean()) if len(losses) else 0.0
    gross_loss = -float(losses.sum())
    rng = np.random.default_rng(seed)
    luck = None
    if len(rs) >= 5:
        means = rng.choice(rs, size=(2000, len(rs)), replace=True).mean(axis=1)
        luck = round(100 * float((means > 0).mean()), 1)
    breakeven = (-avg_loss / (avg_win - avg_loss)) if avg_win > 0 and avg_loss < 0 else None
    return {
        "trades": int(len(rs)), "wins": int(len(wins)), "losses": int(len(losses)),
        "win_rate": round(100 * win_rate, 1), "avg_win_r": round(avg_win, 2), "avg_loss_r": round(avg_loss, 2),
        "expectancy_r": round(float(rs.mean()), 3), "total_r": round(float(rs.sum()), 2), "pnl": round(pnl, 2),
        "profit_factor": round(float(wins.sum()) / gross_loss, 2) if gross_loss > 0 else None,
        "best_r": round(float(rs.max()), 2), "worst_r": round(float(rs.min()), 2),
        "breakeven_win_rate": round(100 * breakeven, 1) if breakeven is not None else None,
        "followed_pct": round(100 * sum(1 for f in followed if f) / len(followed), 1) if followed else None,
        "positive_in_resamples_pct": luck,
    }


def regime(candles: list[dict]) -> dict:
    """The market's mood from SPY's daily bars: above or below the 200-day average, and the 50-day's direction."""
    a = arrays(candles)
    c = a["c"]
    s50, s200 = _sma(c, 50), _sma(c, 200)
    if len(c) < 210 or np.isnan(s200[-1]):
        return {"label": "unknown", "detail": "Not enough SPY history."}
    above = c[-1] > s200[-1]
    rising = s50[-1] > s50[-11]
    if above and rising:
        label, detail = "uptrend", ("SPY is above its 200-day average and its 50-day average is rising. Most stocks "
                                    "move with the market, so long setups have the tide behind them.")
    elif not above and not rising:
        label, detail = "downtrend", ("SPY is below its 200-day average and its 50-day average is falling. Long setups "
                                      "fight the tide: trade smaller, fewer, or not at all.")
    else:
        label, detail = "mixed", ("SPY's trend is mixed (it's " + ("above" if above else "below") + " its 200-day average "
                                  "but the 50-day is " + ("rising" if rising else "falling") + "). Be pickier than usual.")
    return {"label": label, "detail": detail, "close": round(float(c[-1]), 2), "sma200": round(float(s200[-1]), 2),
            "sma50": round(float(s50[-1]), 2), "pct_vs_200": round(float(100 * (c[-1] / s200[-1] - 1)), 2),
            "as_of": _iso(a["t"][-1])}
