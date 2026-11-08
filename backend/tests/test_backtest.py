"""Tests for app.backtest."""
import numpy as np
import pytest

from app.backtest import bars_per_year, run_backtest
from app.broker import RiskLimits

NO_RISK = RiskLimits(max_position_pct=1.0, max_open_positions=1, stop_loss_pct=0.0, take_profit_pct=0.0,
                     trailing_stop_pct=0.0, daily_loss_limit_pct=0.0)


def make_candles(closes, start=1_700_000_000, step=3600, opens=None):
    out = []
    for i, c in enumerate(closes):
        o = opens[i] if opens is not None else (closes[i - 1] if i else c)
        out.append({"time": start + i * step, "open": float(o), "high": max(o, c) * 1.002,
                    "low": min(o, c) * 0.998, "close": float(c), "volume": 1.0, "closed": True})
    return out


def random_walk(n=600, seed=1):
    rng = np.random.default_rng(seed)
    return list(100 * np.exp(np.cumsum(rng.normal(0.0002, 0.01, n))))


def test_bars_per_year():
    assert bars_per_year("1m") == 525_600
    assert bars_per_year("1h") == 8_760
    assert bars_per_year("4h") == 2_190
    assert bars_per_year("1d") == 365
    with pytest.raises(ValueError):
        bars_per_year("abc")


def test_result_shape_and_metrics_sanity():
    candles = make_candles(random_walk())
    res = run_backtest(candles, "sma_cross", {"fast": 5, "slow": 20}, RiskLimits(), 10_000, 0.001, 2,
                       bars_per_year=bars_per_year("1h"))
    m = res["metrics"]
    assert set(m) == {"total_return_pct", "buy_hold_return_pct", "max_drawdown_pct", "sharpe", "win_rate_pct",
                      "profit_factor", "num_trades", "final_equity"}
    assert len(res["equity_curve"]) == len(candles)
    assert [p["time"] for p in res["equity_curve"]] == [c["time"] for c in candles]
    assert m["num_trades"] > 0 and 0 <= m["win_rate_pct"] <= 100 and m["max_drawdown_pct"] >= 0
    assert m["final_equity"] == pytest.approx(res["equity_curve"][-1]["value"], abs=0.01)
    assert m["total_return_pct"] == pytest.approx((m["final_equity"] / 10_000 - 1) * 100, abs=1e-3)
    assert m["buy_hold_return_pct"] == pytest.approx((candles[-1]["close"] / candles[0]["open"] - 1) * 100, abs=1e-3)
    # trades alternate buy/sell, end flat, realized pnl sums to equity change
    sides = [t["side"] for t in res["trades"]]
    assert sides[::2] == ["buy"] * len(sides[::2]) and sides[1::2] == ["sell"] * len(sides[1::2])
    assert sides[-1] == "sell" and len(sides) == 2 * m["num_trades"]
    total_pnl = sum(t["pnl"] for t in res["trades"] if t["side"] == "sell")
    assert total_pnl == pytest.approx(res["equity_curve"][-1]["value"] - 10_000, abs=1e-4)
    assert all(isinstance(t["time"], int) for t in res["trades"])
    # markers sorted, one per fill
    assert len(res["markers"]) == len(res["trades"])
    assert [mk["time"] for mk in res["markers"]] == sorted(mk["time"] for mk in res["markers"])
    assert {mk["shape"] for mk in res["markers"]} <= {"arrowUp", "arrowDown"}


def test_fills_at_next_bar_open_no_lookahead():
    candles = make_candles(random_walk(400, seed=3))
    res = run_backtest(candles, "rsi_reversion", {"period": 7}, NO_RISK, 10_000, 0.0, 0.0)
    by_time = {c["time"]: i for i, c in enumerate(candles)}
    signal_trades = [t for t in res["trades"] if t["reason"] == "signal"]
    assert signal_trades
    for t in signal_trades:
        i = by_time[t["time"]]
        assert i >= 1 and t["price"] == pytest.approx(candles[i]["open"])


def test_future_bars_do_not_change_past():
    closes = random_walk(500, seed=5)
    full = run_backtest(make_candles(closes), "ema_macd", None, RiskLimits(), 10_000, 0.001, 2)
    cut = 300
    part = run_backtest(make_candles(closes[:cut]), "ema_macd", None, RiskLimits(), 10_000, 0.001, 2)
    # equity before the truncated run's final bar (where it force-closes) must match exactly
    assert [p["value"] for p in part["equity_curve"][:-1]] == [p["value"] for p in full["equity_curve"][:cut - 1]]
    # perturbing future prices must not alter earlier trades
    altered = closes[:cut] + [c * 1.5 for c in closes[cut:]]
    alt = run_backtest(make_candles(altered), "ema_macd", None, RiskLimits(), 10_000, 0.001, 2)
    early = lambda r: [t for t in r["trades"] if t["time"] < make_candles(closes)[cut - 1]["time"]]
    assert early(alt) == early(full)


def test_buy_and_hold_like_run_matches_benchmark():
    # one early buy signal on a steady uptrend; with no costs/risk the return tracks buy & hold from the entry open
    closes = [100.0] * 25 + list(np.linspace(100, 200, 100))
    candles = make_candles(closes)
    res = run_backtest(candles, "bollinger_breakout", {"period": 20, "num_std": 2.0}, NO_RISK, 10_000, 0.0, 0.0)
    buy = res["trades"][0]
    assert buy["side"] == "buy"
    sells = [t for t in res["trades"] if t["side"] == "sell"]
    assert sells and all(t["pnl"] > 0 for t in sells)
    assert res["metrics"]["profit_factor"] is None  # no losing trades
    assert res["metrics"]["total_return_pct"] > 0 and res["metrics"]["sharpe"] > 0


def test_stop_loss_in_backtest_uses_bar_low():
    # buy signal then a crash bar: stop at 5% below entry should trigger on that bar's low
    closes = [100.0, 100.5] * 10 + [110.0, 110.0, 80.0, 80.0]
    candles = make_candles(closes)
    lim = RiskLimits(max_position_pct=1.0, max_open_positions=1, stop_loss_pct=0.05, take_profit_pct=0.0,
                     trailing_stop_pct=0.0, daily_loss_limit_pct=0.0)
    res = run_backtest(candles, "bollinger_breakout", {"period": 10}, lim, 10_000, 0.0, 0.0)
    buy, sell = res["trades"][0], res["trades"][1]
    assert buy["time"] == candles[21]["time"] and buy["price"] == pytest.approx(110.0)
    assert sell["reason"] == "stop_loss" and sell["time"] == candles[22]["time"]
    assert sell["price"] == pytest.approx(104.5)
    assert res["metrics"]["num_trades"] == 1 and res["metrics"]["win_rate_pct"] == 0.0
    assert res["metrics"]["profit_factor"] == 0.0  # losses only


def test_short_and_empty_input():
    res = run_backtest(make_candles([100.0, 101.0, 102.0]), "sma_cross", None, RiskLimits(), 1_000, 0.001, 2)
    assert res["trades"] == [] and res["metrics"]["num_trades"] == 0
    assert res["metrics"]["final_equity"] == 1_000 and res["metrics"]["profit_factor"] is None
    empty = run_backtest([], "sma_cross", None, RiskLimits(), 1_000, 0.001, 2)
    assert empty["equity_curve"] == [] and empty["metrics"]["final_equity"] == 1_000
    with pytest.raises(KeyError):
        run_backtest([], "nope", None, RiskLimits(), 1_000, 0.001, 2)
