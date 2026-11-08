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
) -> dict:
    """Simulate `strategy_name` over ascending `candles`.

    `bars_per_year` annualises the Sharpe ratio; if None it is inferred from the median candle spacing.
    `symbol` only labels the fills.
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
    for i, bar in enumerate(candles):
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

    buy_hold = (float(candles[-1]["close"]) / float(candles[0]["open"]) - 1) * 100 if n else 0.0
    sells = [t for t in trades if t["side"] == "sell"]
    return {
        "metrics": _metrics([p["value"] for p in equity_curve], sells, float(cash), buy_hold, bpy),
        "equity_curve": equity_curve,
        "trades": trades,
        "markers": markers,
    }
