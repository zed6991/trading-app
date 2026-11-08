"""Technical indicators on numpy arrays. Every function returns an array aligned to the input, NaN-padded."""
import numpy as np


def sma(values: np.ndarray, period: int) -> np.ndarray:
    out = np.full(len(values), np.nan)
    if len(values) < period:
        return out
    csum = np.cumsum(np.insert(values.astype(float), 0, 0.0))
    out[period - 1:] = (csum[period:] - csum[:-period]) / period
    return out


def ema(values: np.ndarray, period: int) -> np.ndarray:
    out = np.full(len(values), np.nan)
    if len(values) < period:
        return out
    alpha = 2.0 / (period + 1)
    out[period - 1] = values[:period].mean()
    for i in range(period, len(values)):
        out[i] = alpha * values[i] + (1 - alpha) * out[i - 1]
    return out


def rsi(values: np.ndarray, period: int = 14) -> np.ndarray:
    """Wilder's RSI."""
    out = np.full(len(values), np.nan)
    if len(values) <= period:
        return out
    deltas = np.diff(values.astype(float))
    gains = np.clip(deltas, 0, None)
    losses = np.clip(-deltas, 0, None)
    avg_gain = gains[:period].mean()
    avg_loss = losses[:period].mean()
    out[period] = 100.0 if avg_loss == 0 else 100 - 100 / (1 + avg_gain / avg_loss)
    for i in range(period + 1, len(values)):
        avg_gain = (avg_gain * (period - 1) + gains[i - 1]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i - 1]) / period
        out[i] = 100.0 if avg_loss == 0 else 100 - 100 / (1 + avg_gain / avg_loss)
    return out


def macd(values: np.ndarray, fast: int = 12, slow: int = 26, signal: int = 9):
    macd_line = ema(values, fast) - ema(values, slow)
    signal_line = np.full(len(values), np.nan)
    valid = ~np.isnan(macd_line)
    if valid.sum() >= signal:
        first = int(np.argmax(valid))
        signal_line[first:] = ema(macd_line[first:], signal)
    return macd_line, signal_line, macd_line - signal_line


def bollinger(values: np.ndarray, period: int = 20, num_std: float = 2.0):
    mid = sma(values, period)
    std = np.full(len(values), np.nan)
    if len(values) >= period:
        windows = np.lib.stride_tricks.sliding_window_view(values.astype(float), period)
        std[period - 1:] = windows.std(axis=1)
    return mid + num_std * std, mid, mid - num_std * std


def atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 14) -> np.ndarray:
    out = np.full(len(close), np.nan)
    if len(close) <= period:
        return out
    prev_close = np.insert(close[:-1], 0, close[0])
    tr = np.maximum.reduce([high - low, np.abs(high - prev_close), np.abs(low - prev_close)])
    out[period] = tr[1:period + 1].mean()
    for i in range(period + 1, len(close)):
        out[i] = (out[i - 1] * (period - 1) + tr[i]) / period
    return out


def _last(arr: np.ndarray) -> float | None:
    v = arr[-1] if len(arr) else np.nan
    return None if np.isnan(v) else round(float(v), 6)


def snapshot(candles: list[dict]) -> dict:
    """Latest indicator readings for a candle list, used by the dashboard and the AI module."""
    if not candles:
        return {}
    close = np.array([c["close"] for c in candles], dtype=float)
    high = np.array([c["high"] for c in candles], dtype=float)
    low = np.array([c["low"] for c in candles], dtype=float)
    macd_line, signal_line, hist = macd(close)
    bb_up, bb_mid, bb_low = bollinger(close)
    return {
        "price": float(close[-1]),
        "sma20": _last(sma(close, 20)),
        "sma50": _last(sma(close, 50)),
        "ema12": _last(ema(close, 12)),
        "ema26": _last(ema(close, 26)),
        "rsi14": _last(rsi(close, 14)),
        "macd": _last(macd_line),
        "macd_signal": _last(signal_line),
        "macd_hist": _last(hist),
        "bb_upper": _last(bb_up),
        "bb_mid": _last(bb_mid),
        "bb_lower": _last(bb_low),
        "atr14": _last(atr(high, low, close, 14)),
        "change_pct_window": round(float((close[-1] / close[0] - 1) * 100), 3),
    }


def series(candles: list[dict]) -> dict:
    """Full indicator series for charting: {name: [{time, value}]}."""
    if not candles:
        return {}
    close = np.array([c["close"] for c in candles], dtype=float)
    times = [c["time"] for c in candles]
    bb_up, _, bb_low = bollinger(close)
    macd_line, signal_line, hist = macd(close)

    def pack(arr):
        return [{"time": t, "value": round(float(v), 6)} for t, v in zip(times, arr) if not np.isnan(v)]

    return {
        "sma20": pack(sma(close, 20)),
        "sma50": pack(sma(close, 50)),
        "bb_upper": pack(bb_up),
        "bb_lower": pack(bb_low),
        "rsi14": pack(rsi(close, 14)),
        "macd": pack(macd_line),
        "macd_signal": pack(signal_line),
        "macd_hist": pack(hist),
    }
