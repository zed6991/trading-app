"""Rule-based long-only trading strategies built on app.indicators.

Every strategy computes its signal series vectorised over the whole candle list (`signals`). All indicators are
causal (value at bar i depends only on bars <= i), so `signals(candles)[i]` equals `signal(candles[: i + 1])`,
which lets the backtester evaluate a strategy in one pass without look-ahead.
"""
from __future__ import annotations

from typing import Any, Literal

import numpy as np

from app.indicators import bollinger, ema, macd, rsi, sma

Signal = Literal["buy", "sell"] | None


class Strategy:
    """Base class. Subclasses set `name`, `description`, `default_params` and implement `_signals` and `min_bars`."""

    name: str = ""
    description: str = ""
    default_params: dict[str, Any] = {}

    def __init__(self, params: dict | None = None) -> None:
        merged = dict(self.default_params)
        for key, value in (params or {}).items():
            if key not in self.default_params or value is None:  # unknown keys are ignored
                continue
            try:
                number = float(value)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"parameter '{key}' must be numeric") from exc
            if not np.isfinite(number):
                raise ValueError(f"parameter '{key}' must be finite")
            if isinstance(self.default_params[key], int):
                if not number.is_integer():
                    raise ValueError(f"parameter '{key}' must be an integer")
                merged[key] = int(number)
            else:
                merged[key] = number
        self.params: dict[str, Any] = merged
        self._validate()

    def _validate(self) -> None:
        """Raise ValueError for inconsistent parameters."""

    @property
    def min_bars(self) -> int:
        """Minimum number of candles needed before a signal can be produced."""
        raise NotImplementedError

    def _signals(self, close: np.ndarray) -> list[Signal]:
        raise NotImplementedError

    def signals(self, candles: list[dict]) -> list[Signal]:
        """Signal for every bar (evaluated at that bar's close); None before `min_bars` candles are available."""
        if len(candles) < self.min_bars:
            return [None] * len(candles)
        close = np.array([c["close"] for c in candles], dtype=float)
        out = self._signals(close)
        out[: self.min_bars - 1] = [None] * (self.min_bars - 1)
        return out

    def signal(self, candles: list[dict]) -> Signal:
        """Signal on the last candle of `candles` (closed candles only), or None."""
        if len(candles) < self.min_bars:
            return None
        return self.signals(candles)[-1]


def _cross_up(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """True at i where a crosses above b between i-1 and i (NaN-safe)."""
    out = np.zeros(len(a), dtype=bool)
    with np.errstate(invalid="ignore"):
        out[1:] = (a[:-1] <= b[:-1]) & (a[1:] > b[1:])
    return out


def _cross_down(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """True at i where a crosses below b between i-1 and i (NaN-safe)."""
    out = np.zeros(len(a), dtype=bool)
    with np.errstate(invalid="ignore"):
        out[1:] = (a[:-1] >= b[:-1]) & (a[1:] < b[1:])
    return out


def _combine(buy: np.ndarray, sell: np.ndarray) -> list[Signal]:
    return ["buy" if b else "sell" if s else None for b, s in zip(buy.tolist(), sell.tolist())]


class SmaCross(Strategy):
    name = "sma_cross"
    description = "Buy when the fast SMA crosses above the slow SMA; sell when it crosses below."
    default_params = {"fast": 10, "slow": 30}

    def _validate(self) -> None:
        if not 1 <= self.params["fast"] < self.params["slow"]:
            raise ValueError("require 1 <= fast < slow")

    @property
    def min_bars(self) -> int:
        return self.params["slow"] + 1

    def _signals(self, close: np.ndarray) -> list[Signal]:
        fast, slow = sma(close, self.params["fast"]), sma(close, self.params["slow"])
        return _combine(_cross_up(fast, slow), _cross_down(fast, slow))


class EmaMacd(Strategy):
    name = "ema_macd"
    description = "Buy when MACD crosses above its signal line while price is above the trend EMA; sell on the cross below."
    default_params = {"fast": 12, "slow": 26, "signal": 9, "trend": 50}

    def _validate(self) -> None:
        p = self.params
        if not 1 <= p["fast"] < p["slow"] or p["signal"] < 1 or p["trend"] < 1:
            raise ValueError("require 1 <= fast < slow, signal >= 1, trend >= 1")

    @property
    def min_bars(self) -> int:
        p = self.params
        return max(p["slow"] + p["signal"], p["trend"]) + 1

    def _signals(self, close: np.ndarray) -> list[Signal]:
        p = self.params
        line, sig, _ = macd(close, p["fast"], p["slow"], p["signal"])
        trend = ema(close, p["trend"])
        with np.errstate(invalid="ignore"):
            uptrend = close > trend
        return _combine(_cross_up(line, sig) & uptrend, _cross_down(line, sig))


class RsiReversion(Strategy):
    name = "rsi_reversion"
    description = "Mean reversion: buy when RSI drops below the oversold level, sell when it rises above overbought."
    default_params = {"period": 14, "oversold": 30.0, "overbought": 70.0}

    def _validate(self) -> None:
        p = self.params
        if p["period"] < 2 or not 0 < p["oversold"] < p["overbought"] < 100:
            raise ValueError("require period >= 2 and 0 < oversold < overbought < 100")

    @property
    def min_bars(self) -> int:
        return self.params["period"] + 1

    def _signals(self, close: np.ndarray) -> list[Signal]:
        values = rsi(close, self.params["period"])
        with np.errstate(invalid="ignore"):
            return _combine(values < self.params["oversold"], values > self.params["overbought"])


class BollingerBreakout(Strategy):
    name = "bollinger_breakout"
    description = "Breakout: buy when the close is above the upper Bollinger band, sell when it falls below the middle band."
    default_params = {"period": 20, "num_std": 2.0}

    def _validate(self) -> None:
        if self.params["period"] < 2 or self.params["num_std"] <= 0:
            raise ValueError("require period >= 2 and num_std > 0")

    @property
    def min_bars(self) -> int:
        return self.params["period"]

    def _signals(self, close: np.ndarray) -> list[Signal]:
        upper, mid, _ = bollinger(close, self.params["period"], self.params["num_std"])
        with np.errstate(invalid="ignore"):
            return _combine(close > upper, close < mid)


class Momentum(Strategy):
    name = "momentum"
    description = ("Momentum: buy when the price is up more than `threshold` over the last `lookback` bars; sell when "
                   "it's down over that period.")
    default_params = {"lookback": 63, "threshold": 0.0}

    def _validate(self) -> None:
        if self.params["lookback"] < 2 or not 0 <= self.params["threshold"] < 1:
            raise ValueError("require lookback >= 2 and 0 <= threshold < 1")

    @property
    def min_bars(self) -> int:
        return self.params["lookback"] + 1

    def _signals(self, close: np.ndarray) -> list[Signal]:
        n = self.params["lookback"]
        change = np.full(len(close), np.nan)
        change[n:] = close[n:] / close[:-n] - 1
        with np.errstate(invalid="ignore"):
            return _combine(change > self.params["threshold"], change < 0)


STRATEGIES: dict[str, type[Strategy]] = {
    cls.name: cls for cls in (SmaCross, EmaMacd, RsiReversion, BollingerBreakout, Momentum)
}

# Small settings grids used by walk-forward tests (the "tuning" step).
GRIDS: dict[str, list[dict]] = {
    "sma_cross": [{"fast": f, "slow": s} for f in (5, 10, 20) for s in (30, 50, 100)],
    "ema_macd": [{"fast": f, "slow": s, "trend": t} for f, s in ((8, 21), (12, 26)) for t in (50, 100, 200)],
    "rsi_reversion": [{"period": p, "oversold": o, "overbought": b} for p in (7, 14) for o in (20.0, 30.0)
                      for b in (60.0, 70.0)],
    "bollinger_breakout": [{"period": p, "num_std": k} for p in (10, 20, 40) for k in (1.5, 2.0, 2.5)],
    "momentum": [{"lookback": n, "threshold": t} for n in (21, 63, 126, 252) for t in (0.0, 0.05)],
}


def make_strategy(name: str, params: dict | None = None) -> Strategy:
    """Instantiate a strategy by name. KeyError if unknown, ValueError for bad params."""
    if name not in STRATEGIES:
        raise KeyError(f"unknown strategy '{name}'")
    return STRATEGIES[name](params)


def list_strategies() -> list[dict]:
    """Strategy catalogue for the API: name, description and default params."""
    return [{"name": n, "description": c.description, "params": dict(c.default_params)} for n, c in STRATEGIES.items()]
