"""Bar-by-bar backtester that reuses PaperBroker for all accounting.

Timing (no look-ahead): the strategy signal is computed on the close of bar i and filled at the OPEN of bar i+1.
Stop-loss / take-profit / trailing exits are checked against each bar's high/low. Any open position is closed at the
final bar's close.
"""
from __future__ import annotations

import math
import re

import numpy as np

from app.broker import PaperBroker, RiskError, RiskLimits
from app.strategies import make_strategy

_UNIT_SECONDS = {"m": 60, "h": 3600, "d": 86400, "w": 604800}
_YEAR_SECONDS = 365 * 86400


def bars_per_year(interval: str) -> float:
    """Number of bars per (365-day) year for a Binance-style interval such as '1m', '4h', '1d', '1w'."""
    match = re.fullmatch(r"\s*(\d+)\s*([mhdw])\s*", interval or "")
    if not match or int(match.group(1)) <= 0:
        raise ValueError(f"invalid interval '{interval}'")
    return _YEAR_SECONDS / (int(match.group(1)) * _UNIT_SECONDS[match.group(2)])



def _infer_bars_per_year(candles: list[dict]) -> float:
    if len(candles) < 2:
        return 365.0
    step = float(np.median(np.diff([c["time"] for c in candles])))
    return _YEAR_SECONDS / step if step > 0 else 365.0


def _marker(fill: dict, time: int) -> dict:
    if fill["side"] == "buy":
        return {"time": time, "position": "belowBar", "color": "#26a69a", "shape": "arrowUp", "text": "BUY"}
    labels = {"stop_loss": "SL", "trailing_stop": "TS", "take_profit": "TP", "end_of_backtest": "END"}
    text = labels.get(fill["reason"], "SELL")
    pnl = fill.get("pnl")
    if pnl is not None:
        text += f" {pnl:+.2f}"
    return {"time": time, "position": "aboveBar", "color": "#ef5350", "shape": "arrowDown", "text": text}


def _metrics(equity: list[float], sells: list[dict], cash: float, buy_hold: float, bpy: float) -> dict:
    values = np.array(equity, dtype=float)
    final = float(values[-1]) if len(values) else cash
    if len(values):
        peaks = np.maximum.accumulate(values)
        max_dd = float(np.max((peaks - values) / peaks)) * 100
    else:
        max_dd = 0.0
    sharpe = 0.0
    if len(values) > 2:
        rets = values[1:] / values[:-1] - 1
        sd = float(rets.std(ddof=1))
        if sd > 1e-12:
            sharpe = float(rets.mean()) / sd * math.sqrt(bpy)
    pnls = [float(f["pnl"]) for f in sells]
    gains = sum(p for p in pnls if p > 0)
    losses = -sum(p for p in pnls if p < 0)
    return {
        "total_return_pct": round((final / cash - 1) * 100, 4),
        "buy_hold_return_pct": round(buy_hold, 4),
        "max_drawdown_pct": round(max_dd, 4),
        "sharpe": round(sharpe, 4),
        "win_rate_pct": round(sum(p > 0 for p in pnls) / len(pnls) * 100, 2) if pnls else 0.0,
        "profit_factor": round(gains / losses, 4) if losses > 0 else None,
        "num_trades": len(pnls),
        "final_equity": round(final, 2),
    }


def run_backtest(
    candles: list[dict],
    strategy_name: str,
    params: dict | None,
    limits: RiskLimits,
    cash: float,
    fee_rate: float,
    slippage_bps: float,
    bars_per_year: float | None = None,
    symbol: str = "BACKTEST",
    trade_from: int = 0,
) -> dict:
    """Simulate `strategy_name` over ascending `candles`.

    `bars_per_year` annualises the Sharpe ratio; if None it is inferred from the median candle spacing.
    `symbol` only labels the fills. Bars before `trade_from` only warm up the indicators: no trades, no equity.
    Raises KeyError for an unknown strategy and ValueError for invalid params/cash.
    """
    strategy = make_strategy(strategy_name, params)
    broker = PaperBroker(cash, fee_rate, slippage_bps, limits)
    bpy = bars_per_year if bars_per_year else _infer_bars_per_year(candles)
    signals = strategy.signals(candles)
    n = len(candles)

    equity_curve: list[dict] = []
    trades: list[dict] = []
    markers: list[dict] = []

    def record(fills: list[dict], time: int) -> None:
        for f in fills:
            trades.append({**f, "time": time})
            markers.append(_marker(f, time))

    pending: str | None = None
    trade_from = max(0, min(int(trade_from), n - 1)) if n else 0
    for i, bar in enumerate(candles):
        if i < trade_from:
            continue
        t = int(bar["time"])
        o, h, lo, c = float(bar["open"]), float(bar["high"]), float(bar["low"]), float(bar["close"])
        broker.update_day(t, {symbol: o})

        # Execute the previous bar's signal at this bar's open.
        if pending == "buy" and symbol not in broker.positions and not broker.halted:
            try:
                record([broker.open_long(symbol, o, t, strategy=strategy_name)], t)
            except RiskError:
                pass
        elif pending == "sell" and symbol in broker.positions:
            record([broker.close(symbol, o, t, reason="signal")], t)
        pending = None

        if symbol in broker.positions:
            record(broker.check_exits(symbol, h, lo, c, t, open_price=o), t)

        if i == n - 1 and symbol in broker.positions:
            record([broker.close(symbol, c, t, reason="end_of_backtest")], t)

        broker.update_day(t, {symbol: c})
        equity_curve.append({"time": t, "value": round(broker.equity({symbol: c}), 6)})

        sig = signals[i]
        if (sig == "buy" and symbol not in broker.positions) or (sig == "sell" and symbol in broker.positions):
            pending = sig

    buy_hold = (float(candles[-1]["close"]) / float(candles[trade_from]["open"]) - 1) * 100 if n else 0.0
    sells = [t for t in trades if t["side"] == "sell"]
    return {
        "metrics": _metrics([p["value"] for p in equity_curve], sells, float(cash), buy_hold, bpy),
        "equity_curve": equity_curve,
        "trades": trades,
        "markers": markers,
    }


def walk_forward(
    candles: list[dict],
    strategy_name: str,
    limits: RiskLimits,
    cash: float,
    fee_rate: float,
    slippage_bps: float,
    bars_per_year: float | None = None,
    symbol: str = "BACKTEST",
    split: float = 0.7,
    grid: list[dict] | None = None,
) -> dict:
    """Tune on the first `split` of history (try every setting in the grid, keep the best total return), then run
    the winner once on the rest, which it has never seen. Returns both results and every tried setting."""
    from app.strategies import GRIDS, STRATEGIES

    if strategy_name not in STRATEGIES:
        raise KeyError(f"unknown strategy '{strategy_name}'")
    if not 0.3 <= split <= 0.9:
        raise ValueError("split must be between 0.3 and 0.9")
    grid = grid or GRIDS.get(strategy_name) or [{}]
    n = len(candles)
    cut = int(n * split)
    if cut < 60 or n - cut < 30:
        raise ValueError("Not enough history to split into a tuning part and a test part. Use more bars.")
    tuning = candles[:cut]
    tried = []
    for params in grid:
        try:
            res = run_backtest(tuning, strategy_name, params, limits, cash, fee_rate, slippage_bps, bars_per_year, symbol)
        except ValueError:
            continue
        tried.append({"params": make_strategy(strategy_name, params).params, "metrics": res["metrics"]})
    if not tried:
        raise ValueError("None of the settings could run on this history.")
    best = max(tried, key=lambda x: (x["metrics"]["total_return_pct"], x["metrics"]["sharpe"]))
    in_sample = run_backtest(tuning, strategy_name, best["params"], limits, cash, fee_rate, slippage_bps,
                             bars_per_year, symbol)
    # The test run sees earlier bars only to warm up its indicators; it trades from the cut onwards.
    warm = max(0, cut - make_strategy(strategy_name, best["params"]).min_bars - 1)
    out_sample = run_backtest(candles[warm:], strategy_name, best["params"], limits, cash, fee_rate, slippage_bps,
                              bars_per_year, symbol, trade_from=cut - warm)
    ins, outs = in_sample["metrics"], out_sample["metrics"]
    drop = ins["total_return_pct"] - outs["total_return_pct"]
    held_up = outs["total_return_pct"] > 0 and outs["total_return_pct"] >= outs["buy_hold_return_pct"]
    return {
        "best_params": best["params"], "tried": tried, "split": split,
        "tuning_period": {"start": int(tuning[0]["time"]), "end": int(tuning[-1]["time"])},
        "test_period": {"start": int(candles[cut]["time"]), "end": int(candles[-1]["time"])},
        "in_sample": in_sample, "out_sample": out_sample,
        "fell_apart": outs["total_return_pct"] < ins["total_return_pct"] / 2 or outs["total_return_pct"] < 0,
        "held_up": held_up, "drop_pct": round(drop, 2),
    }
