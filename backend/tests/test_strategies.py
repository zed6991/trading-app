"""Tests for app.strategies."""
import numpy as np
import pytest

from app.strategies import STRATEGIES, list_strategies, make_strategy


def candles_from(closes, start=1_700_000_000, step=60):
    out, prev = [], closes[0]
    for i, c in enumerate(closes):
        o = prev
        out.append({"time": start + i * step, "open": o, "high": max(o, c) * 1.001, "low": min(o, c) * 0.999,
                    "close": float(c), "volume": 1.0, "closed": True})
        prev = c
    return out


def test_registry_and_listing():
    assert set(STRATEGIES) == {"sma_cross", "ema_macd", "rsi_reversion", "bollinger_breakout"}
    listed = {s["name"]: s for s in list_strategies()}
    assert listed["sma_cross"]["params"] == {"fast": 10, "slow": 30}
    assert all(s["description"] for s in listed.values())
    with pytest.raises(KeyError):
        make_strategy("nope")


def test_params_merge_and_validation():
    s = make_strategy("sma_cross", {"fast": 5, "unknown": 1})
    assert s.params == {"fast": 5, "slow": 30} and s.min_bars == 31
    assert isinstance(make_strategy("sma_cross", {"fast": 4.0}).params["fast"], int)
    with pytest.raises(ValueError):
        make_strategy("sma_cross", {"fast": 40, "slow": 30})
    with pytest.raises(ValueError):
        make_strategy("sma_cross", {"fast": 2.5})
    with pytest.raises(ValueError):
        make_strategy("rsi_reversion", {"oversold": 80})


@pytest.mark.parametrize("name", list(STRATEGIES))
def test_too_few_bars_returns_none(name):
    s = make_strategy(name)
    assert s.signal([]) is None
    assert s.signal(candles_from([100.0] * (s.min_bars - 1))) is None
    assert s.signals(candles_from([100.0] * 3)) == [None] * 3


def test_sma_cross_signals():
    s = make_strategy("sma_cross", {"fast": 3, "slow": 5})
    down = [110, 108, 106, 104, 102, 100, 98, 96]
    c = candles_from(down + [110])
    assert s.signal(c) == "buy"
    assert s.signal(c[:-1]) is None
    up = [90, 92, 94, 96, 98, 100, 102, 104]
    assert s.signal(candles_from(up + [85])) == "sell"


def test_rsi_reversion_signals():
    s = make_strategy("rsi_reversion", {"period": 5})
    assert s.signal(candles_from(list(np.linspace(100, 80, 12)))) == "buy"
    assert s.signal(candles_from(list(np.linspace(80, 100, 12)))) == "sell"


def test_bollinger_breakout_signals():
    s = make_strategy("bollinger_breakout", {"period": 10})
    flat = [100.0, 100.5] * 10
    assert s.signal(candles_from(flat + [110.0])) == "buy"
    assert s.signal(candles_from(flat + [95.0])) == "sell"
    assert s.signal(candles_from(flat + [100.4])) is None


def test_ema_macd_signals():
    s = make_strategy("ema_macd", {"fast": 3, "slow": 6, "signal": 3, "trend": 10})
    # long uptrend with a dip, then resumption -> MACD crosses back above signal while above trend EMA
    closes = list(np.linspace(100, 130, 30)) + [127, 124, 122, 121, 121.5, 124, 128]
    sigs = make_strategy("ema_macd", s.params).signals(candles_from(closes))
    assert "buy" in sigs[-4:]
    # down move after uptrend -> MACD crosses below signal
    closes2 = list(np.linspace(100, 130, 30)) + [126, 121, 115]
    assert "sell" in s.signals(candles_from(closes2))[-3:]


@pytest.mark.parametrize("name", list(STRATEGIES))
def test_vectorised_signals_match_prefix_evaluation(name):
    rng = np.random.default_rng(7)
    closes = list(100 * np.exp(np.cumsum(rng.normal(0, 0.01, 160))))
    c = candles_from(closes)
    s = make_strategy(name)
    full = s.signals(c)
    assert len(full) == len(c)
    for i in range(len(c)):
        assert full[i] == s.signal(c[: i + 1]), i
    assert any(x is not None for x in full)
