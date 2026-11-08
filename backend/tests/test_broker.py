"""Tests for app.broker."""
import json

import pytest

from app.broker import PaperBroker, RiskError, RiskLimits

T0 = 1_760_000_000.0  # 2025-10-09 UTC


def limits(**kw):
    base = dict(max_position_pct=0.5, max_open_positions=2, stop_loss_pct=0.0, take_profit_pct=0.0,
                trailing_stop_pct=0.0, daily_loss_limit_pct=0.0)
    base.update(kw)
    return RiskLimits(**base)


def broker(cash=10_000.0, fee=0.001, slip=0.0, **kw):
    return PaperBroker(cash, fee, slip, limits(**kw))


def test_buy_sell_accounting_exact():
    b = broker(slip=10.0)  # 10 bps
    buy = b.open_long("BTC", 100.0, T0, strategy="x", qty=10)
    assert buy["price"] == pytest.approx(100.1)
    assert buy["fee"] == pytest.approx(10 * 100.1 * 0.001)
    assert buy["pnl"] is None and buy["side"] == "buy" and buy["strategy"] == "x"
    assert b.cash == pytest.approx(10_000 - 1001.0 - 1.001)
    sell = b.close("BTC", 110.0, T0 + 60)
    exit_fill = 110.0 * (1 - 0.001)
    exit_notional = 10 * exit_fill
    exit_fee = exit_notional * 0.001
    expected_pnl = (exit_notional - exit_fee) - (1001.0 + 1.001)
    assert sell["price"] == pytest.approx(exit_fill)
    assert sell["pnl"] == pytest.approx(expected_pnl)
    assert b.cash == pytest.approx(10_000 + expected_pnl)
    assert b.realized_pnl == pytest.approx(expected_pnl)
    assert b.fees_paid == pytest.approx(1.001 + exit_fee)
    assert b.positions == {}
    with pytest.raises(RiskError):
        b.close("BTC", 100.0, T0)


def test_equity_identity_and_snapshot():
    b = broker()
    b.open_long("BTC", 100.0, T0, qty=20)
    snap = b.snapshot({"BTC": 105.0})
    assert snap["equity"] == pytest.approx(b.equity({"BTC": 105.0}))
    assert snap["equity"] == pytest.approx(snap["starting_cash"] + snap["realized_pnl"] + snap["unrealized_pnl"])
    pos = snap["positions"][0]
    assert pos["mark"] == 105.0 and pos["market_value"] == pytest.approx(2100.0)
    assert set(pos) == {"symbol", "qty", "avg_price", "mark", "market_value", "unrealized_pnl", "pnl_pct",
                        "stop_loss", "take_profit", "strategy", "opened_at"}
    assert snap["total_return_pct"] == pytest.approx((snap["equity"] / 10_000 - 1) * 100)


def test_position_sizing_default_and_caps():
    b = broker(max_position_pct=0.25)
    fill = b.open_long("BTC", 200.0, T0)
    assert fill["qty"] * 200.0 == pytest.approx(2500.0)
    with pytest.raises(RiskError, match="already open"):
        b.open_long("BTC", 200.0, T0)
    with pytest.raises(RiskError, match="exceeds max position"):
        b.open_long("ETH", 100.0, T0, qty=30)  # 3000 > 25% of ~9997.5
    # cash cap: position pct 100% sizes by cash including fee and never goes negative
    b2 = broker(max_position_pct=1.0)
    f2 = b2.open_long("BTC", 100.0, T0)
    assert b2.cash >= 0 and b2.cash == pytest.approx(0.0, abs=1e-6)
    assert f2["qty"] * f2["price"] + f2["fee"] == pytest.approx(10_000.0)


def test_insufficient_cash():
    b = broker(max_position_pct=1.0, max_open_positions=3)
    b.open_long("A", 100.0, T0, qty=60)
    with pytest.raises(RiskError, match="insufficient cash"):
        b.open_long("B", 100.0, T0, qty=45)


def test_max_open_positions():
    b = broker(max_position_pct=0.1, max_open_positions=2)
    b.open_long("A", 10.0, T0)
    b.open_long("B", 10.0, T0)
    with pytest.raises(RiskError, match="max open positions"):
        b.open_long("C", 10.0, T0)
    b.close("A", 10.0, T0)
    b.open_long("C", 10.0, T0)
    assert set(b.positions) == {"B", "C"}


def test_stop_loss_exit_priority_and_gap():
    b = broker(stop_loss_pct=0.02, take_profit_pct=0.04)
    b.open_long("BTC", 100.0, T0, qty=10)
    pos = b.positions["BTC"]
    assert pos.stop_loss == pytest.approx(98.0) and pos.take_profit == pytest.approx(104.0)
    assert b.check_exits("BTC", 103.0, 99.0, 101.0, T0) == []
    # both levels touched in one bar -> stop has priority
    fills = b.check_exits("BTC", 105.0, 97.0, 100.0, T0)
    assert len(fills) == 1 and fills[0]["reason"] == "stop_loss" and fills[0]["price"] == pytest.approx(98.0)
    # gap below the stop fills at the open
    b.open_long("BTC", 100.0, T0, qty=10)
    fills = b.check_exits("BTC", 96.0, 94.0, 95.0, T0, open_price=95.5)
    assert fills[0]["price"] == pytest.approx(95.5)
    # live tick beyond the stop fills at the tick price
    b.open_long("BTC", 100.0, T0, qty=10)
    assert b.check_exits("BTC", 97.0, 97.0, 97.0, T0)[0]["price"] == pytest.approx(97.0)


def test_take_profit_exit():
    b = broker(stop_loss_pct=0.02, take_profit_pct=0.04)
    b.open_long("BTC", 100.0, T0, qty=10)
    fills = b.check_exits("BTC", 106.0, 101.0, 105.0, T0)
    assert fills[0]["reason"] == "take_profit" and fills[0]["price"] == pytest.approx(104.0)
    assert fills[0]["pnl"] > 0 and "BTC" not in b.positions


def test_trailing_stop():
    b = broker(stop_loss_pct=0.05, trailing_stop_pct=0.03)
    b.open_long("BTC", 100.0, T0, qty=10)
    assert b.check_exits("BTC", 110.0, 108.0, 109.0, T0) == []
    pos = b.positions["BTC"]
    assert pos.high_water == 110.0 and pos.stop_loss == pytest.approx(106.7)
    assert b.check_exits("BTC", 108.0, 107.0, 107.5, T0) == []
    assert pos.stop_loss == pytest.approx(106.7)  # never lowered
    fills = b.check_exits("BTC", 107.0, 106.0, 106.5, T0)
    assert fills[0]["reason"] == "trailing_stop" and fills[0]["price"] == pytest.approx(106.7)
    assert fills[0]["pnl"] > 0


def test_set_brackets():
    b = broker()
    with pytest.raises(RiskError):
        b.set_brackets("BTC", 90.0, 110.0)
    b.open_long("BTC", 100.0, T0, qty=1)
    pos = b.set_brackets("BTC", 95.0, None)
    assert pos["stop_loss"] == 95.0 and pos["take_profit"] is None
    with pytest.raises(RiskError):
        b.set_brackets("BTC", 120.0, 110.0)
    assert b.check_exits("BTC", 96.0, 94.0, 95.0, T0)[0]["reason"] == "stop_loss"


def test_daily_loss_halt_and_rollover():
    b = broker(max_position_pct=1.0, daily_loss_limit_pct=0.05)
    b.update_day(T0, {})
    b.open_long("BTC", 100.0, T0, qty=90)
    b.update_day(T0 + 60, {"BTC": 97.0})
    assert not b.halted
    b.update_day(T0 + 120, {"BTC": 93.0})
    assert b.halted and "daily loss" in b.halt_reason
    with pytest.raises(RiskError, match="halted"):
        b.open_long("ETH", 10.0, T0, qty=1)
    # exits still work while halted
    assert b.close("BTC", 93.0, T0 + 180)["side"] == "sell"
    # next UTC day clears the daily halt and rebases
    b.update_day(T0 + 86_400, {})
    assert not b.halted and b.day_start_equity == pytest.approx(b.cash)
    # manual resume
    b.halted, b.halt_reason = True, "daily loss test"
    b.resume()
    assert not b.halted and b.halt_reason is None


def test_reset():
    b = broker()
    b.open_long("BTC", 100.0, T0, qty=1)
    b.reset(5_000.0)
    assert b.cash == b.starting_cash == 5_000.0 and not b.positions and b.realized_pnl == 0 and b.fees_paid == 0
    b.reset()
    assert b.cash == 5_000.0


def test_dump_load_roundtrip():
    b = broker(stop_loss_pct=0.02, trailing_stop_pct=0.01)
    b.open_long("BTC", 100.0, T0, strategy="sma_cross", qty=10)
    b.open_long("ETH", 50.0, T0, qty=5)
    b.close("ETH", 55.0, T0 + 60)
    assert b.check_exits("BTC", 104.0, 103.5, 103.8, T0 + 60) == []
    state = json.loads(json.dumps(b.dump()))
    b2 = PaperBroker(1.0, 0.001, 0.0, RiskLimits())
    b2.load(state)
    assert b2.dump() == b.dump()
    assert b2.snapshot({"BTC": 103.0}) == b.snapshot({"BTC": 103.0})
    assert b2.limits == b.limits
    s1, s2 = b.close("BTC", 102.0, T0 + 120), b2.close("BTC", 102.0, T0 + 120)
    assert s1 == s2


def test_risk_limits_update_and_from_settings():
    base = RiskLimits.from_settings()
    assert set(base.to_dict()) == {"max_position_pct", "max_open_positions", "stop_loss_pct", "take_profit_pct",
                                   "trailing_stop_pct", "daily_loss_limit_pct"}
    new = base.update({"stop_loss_pct": 0.03, "max_open_positions": 5, "take_profit_pct": None})
    assert new.stop_loss_pct == 0.03 and new.max_open_positions == 5
    assert new.take_profit_pct == base.take_profit_pct and new is not base
    for bad in ({"max_position_pct": 0}, {"max_position_pct": 1.5}, {"stop_loss_pct": -0.1},
                {"max_open_positions": 0}, {"bogus": 1}, {"stop_loss_pct": "x"}):
        with pytest.raises(ValueError):
            base.update(bad)
