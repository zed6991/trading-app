import numpy as np

from app import indicators as ind


def test_sma_matches_manual():
    v = np.arange(1, 11, dtype=float)
    out = ind.sma(v, 3)
    assert np.isnan(out[:2]).all()
    assert out[2] == 2.0 and out[-1] == 9.0


def test_ema_seeded_with_sma():
    v = np.arange(1, 11, dtype=float)
    out = ind.ema(v, 3)
    assert out[2] == 2.0
    assert out[3] == 0.5 * 4 + 0.5 * 2.0


def test_rsi_extremes():
    up = np.arange(1, 40, dtype=float)
    assert ind.rsi(up, 14)[-1] == 100.0
    down = up[::-1]
    assert ind.rsi(down, 14)[-1] < 1e-9


def test_bollinger_flat_series_collapses():
    v = np.full(30, 5.0)
    up, mid, low = ind.bollinger(v, 20)
    assert up[-1] == mid[-1] == low[-1] == 5.0


def test_macd_and_snapshot_shapes():
    rng = np.random.default_rng(0)
    close = 100 + np.cumsum(rng.normal(0, 1, 200))
    m, s, h = ind.macd(close)
    assert len(m) == len(s) == len(h) == 200
    assert not np.isnan(s[-1])
    candles = [{"time": i * 60, "open": c, "high": c + 1, "low": c - 1, "close": c, "volume": 1.0, "closed": True}
               for i, c in enumerate(close)]
    snap = ind.snapshot(candles)
    assert snap["rsi14"] is not None and snap["atr14"] is not None
    ser = ind.series(candles)
    assert len(ser["sma20"]) == 181 and ser["rsi14"][0]["time"] == 14 * 60
