"""Quant lab maths, walk-forward testing, US market hours and Yahoo parsing."""
import math
from datetime import datetime
from zoneinfo import ZoneInfo

import numpy as np
import pytest

from app import assets, quant
from app.backtest import walk_forward
from app.broker import PaperBroker, RiskLimits
from app.stocks import market_status, parse_chart

DAY = 86400
T0 = 1_600_000_000 // DAY * DAY


def _candles(closes, start=T0):
    return [{"time": start + i * DAY, "open": c, "high": c * 1.01, "low": c * 0.99, "close": c, "volume": 1.0,
             "closed": True} for i, c in enumerate(closes)]


def _walk(n, vol, seed, drift=0.0):
    rng = np.random.default_rng(seed)
    return list(100 * np.cumprod(1 + rng.normal(drift, vol, n)))


def test_stats_numbers():
    closes = _walk(800, 0.01, 1)
    s = quant.stats("SPY", _candles(closes))
    r = np.array(closes[1:]) / np.array(closes[:-1]) - 1
    assert s["vol_daily_pct"] == pytest.approx(r.std(ddof=1) * 100, abs=1e-3)
    assert s["vol_annual_pct"] == pytest.approx(r.std(ddof=1) * math.sqrt(252) * 100, abs=0.01)
    assert s["total_return_pct"] == pytest.approx((closes[-1] / closes[0] - 1) * 100, abs=0.01)
    assert sum(b["count"] for b in s["histogram"]) == len(r)
    assert 0 <= s["max_drawdown_pct"] <= 100 and s["periods_per_year"] == 252
    assert quant.stats("BTCUSDT", _candles(closes))["periods_per_year"] == 365
    with pytest.raises(ValueError):
        quant.stats("SPY", _candles(closes[:10]))


def test_max_drawdown():
    dd, peak, trough = quant.max_drawdown(np.array([100, 120, 60, 90, 130.0]))
    assert dd == pytest.approx(0.5) and (peak, trough) == (1, 2)


def test_correlation_and_beta():
    rng = np.random.default_rng(2)
    m = rng.normal(0, 0.01, 500)
    spy = 100 * np.cumprod(1 + m)
    lever = 100 * np.cumprod(1 + 2 * m)            # beta ~2, corr ~1
    other = 100 * np.cumprod(1 + rng.normal(0, 0.01, 500))  # unrelated
    out = quant.correlation({"SPY": _candles(spy), "NVDA": _candles(lever), "GLD": _candles(other)})
    i = out["symbols"].index
    assert out["matrix"][i("SPY")][i("NVDA")] > 0.99
    assert abs(out["matrix"][i("SPY")][i("GLD")]) < 0.2
    assert out["beta"]["NVDA"] == pytest.approx(2.0, abs=0.05) and out["beta"]["SPY"] == pytest.approx(1.0)
    assert out["lowest_pair"]["corr"] <= out["highest_pair"]["corr"]


def test_alignment_uses_shared_dates():
    stock = _candles(_walk(100, 0.01, 3))
    crypto = _candles(_walk(140, 0.03, 4), start=T0 - 20 * DAY)
    times, closes = quant.align({"SPY": stock, "BTCUSDT": crypto})
    assert len(times) == 100 and len(closes["BTCUSDT"]) == 100


def test_mix_of_uncorrelated_assets_is_calmer():
    a, b = _walk(600, 0.01, 5), _walk(600, 0.01, 6)
    out = quant.mix({"SPY": _candles(a), "TLT": _candles(b)}, {"SPY": 1, "TLT": 1})
    assert out["weights"] == {"SPY": 0.5, "TLT": 0.5}
    assert out["calmer_than_all"] and out["diversification_benefit_pct"] > 0
    assert not out["has_stock_and_crypto"]
    same = quant.mix({"SPY": _candles(a), "QQQ": _candles(a)}, {"SPY": 1, "QQQ": 1})
    assert not same["calmer_than_all"]
    with pytest.raises(ValueError):
        quant.mix({"SPY": _candles(a)}, {"SPY": 1})


def test_luck_test_and_best_of_many():
    candles = _candles(_walk(700, 0.015, 7))
    out = quant.luck_test(candles, "sma_cross", {"fast": 5, "slow": 20}, cost=0.001, trials=100, seed=1)
    assert 0 <= out["beat_pct"] <= 100 and out["trades"] > 0 and out["verdict"]
    assert sum(b["count"] for b in out["histogram"]) == 100
    many = quant.many_random(candles, cost=0.001, count=20, seed=1)
    assert len(many["rows"]) == 20
    best = max(many["rows"], key=lambda r: r["first_pct"])
    assert many["best_id"] == best["id"] and many["best_first_pct"] == best["first_pct"]


def test_random_positions_cost_model():
    r = np.array([0.0, 0.1, 0.1])
    assert quant._simple_returns(r, np.array([0, 1, 1.0]), 0.0) == pytest.approx(0.21)
    assert quant._simple_returns(r, np.array([0, 1, 1.0]), 0.01) == pytest.approx(1.21 * 0.99 - 1)


def test_walk_forward_splits_and_reports():
    candles = _candles(_walk(600, 0.015, 8, drift=0.0005))
    limits = RiskLimits(max_position_pct=1.0, stop_loss_pct=0, take_profit_pct=0, daily_loss_limit_pct=0)
    out = walk_forward(candles, "sma_cross", limits, 10_000, 0.001, 2, 252, "SPY", split=0.7)
    assert out["test_period"]["start"] == candles[420]["time"]
    assert out["out_sample"]["equity_curve"][0]["time"] == candles[420]["time"]
    assert len(out["tried"]) == 9 and out["best_params"] in [t["params"] for t in out["tried"]]
    assert isinstance(out["fell_apart"], bool)
    with pytest.raises(ValueError):
        walk_forward(candles[:80], "sma_cross", limits, 10_000, 0.001, 2, 252, "SPY")


def test_market_hours():
    ny = ZoneInfo("America/New_York")
    assert market_status(datetime(2026, 11, 10, 10, 0, tzinfo=ny).timestamp())["open"] is True   # Tuesday
    assert market_status(datetime(2026, 11, 10, 16, 1, tzinfo=ny).timestamp())["open"] is False
    sat = market_status(datetime(2026, 11, 7, 12, 0, tzinfo=ny).timestamp())
    assert sat["open"] is False and sat["opens_at"] == datetime(2026, 11, 9, 9, 30, tzinfo=ny).timestamp()
    assert market_status(datetime(2026, 11, 26, 11, 0, tzinfo=ny).timestamp())["open"] is False  # Thanksgiving
    assert market_status(datetime(2026, 11, 27, 13, 30, tzinfo=ny).timestamp())["open"] is False  # early close


def test_parse_yahoo_chart_adjusts_daily_bars():
    payload = {"chart": {"result": [{
        "timestamp": [1793971800, 1794058200],
        "indicators": {"quote": [{"open": [10, 11], "high": [12, 12], "low": [9, 10], "close": [10, None],
                                  "volume": [5, 6]}],
                       "adjclose": [{"adjclose": [5, None]}]},
    }], "error": None}}
    bars = parse_chart(payload, "1d", now=1794500000)
    assert len(bars) == 1 and bars[0]["close"] == 5 and bars[0]["open"] == 5 and bars[0]["time"] % DAY == 0
    with pytest.raises(ValueError):
        parse_chart({"chart": {"result": None, "error": {"description": "No data found"}}}, "1d")


def test_broker_fee_per_asset():
    b = PaperBroker(100_000, 0.001, 0, RiskLimits(max_position_pct=0.5),
                    fee_rate_for=lambda s: assets.fee_rate(s, 0.001))
    assert b.open_long("AAPL", 100.0, 0, qty=10)["fee"] == 0
    assert b.open_long("BTCUSDT", 50_000.0, 0, qty=0.1)["fee"] == pytest.approx(5.0)
    assert assets.name("KO") == "Coca-Cola" and assets.is_crypto("ETHUSDT") and assets.kind("SPY") == "etf"
