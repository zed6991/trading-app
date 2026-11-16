"""Level 14 swing desk: setup maths, the Time Machine, the risk gate, end-of-day order handling, the journal,
the routine, and routing to a (fake) Alpaca."""
import asyncio
import datetime as dt
import json
import time

import httpx
import numpy as np
import pytest

from app import swing as swing_mod
from app import swingmath as sm
from app.live import GoLive
from tests.test_levels_8_13 import _engine
from tests.test_live import FakeAlpaca


# ------------------------------------------------------------------------------------------ synthetic bars
def _dates(n, start=dt.date(2024, 1, 1)):
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += dt.timedelta(days=1)
    return out


def _bar(day, o, h, lo, c, v=2_000_000.0, closed=True):
    t = int((dt.datetime(day.year, day.month, day.day) - dt.datetime(1970, 1, 1)).total_seconds())
    return {"time": t, "open": float(o), "high": float(h), "low": float(lo), "close": float(c), "volume": v,
            "closed": closed}


def _trend(n=300, start=50.0, end=100.0, seed=1):
    rng = np.random.default_rng(seed)
    closes = np.linspace(start, end, n) + rng.normal(0, 0.6, n)
    days = _dates(n + 40)
    bars, prev = [], closes[0]
    for i in range(n):
        c = closes[i]
        o = prev
        bars.append(_bar(days[i], o, max(o, c) + 1.0, min(o, c) - 1.0, c, 1_000_000.0))
        prev = c
    return bars, days[n:]


# ------------------------------------------------------------------------------------------ maths
def test_simulate_trade_rules():
    days = _dates(10)
    base = [_bar(days[0], 100, 101, 99, 100)]
    a = lambda bars: sm.arrays(base + bars)  # noqa: E731
    # no fill: never reaches the buy-stop in 3 sessions
    r = sm.simulate_trade(a([_bar(days[i], 100, 100.5, 99, 100) for i in (1, 2, 3)]), 1, 101, 98, 107, 3, 10)
    assert r["outcome"] == "no_fill"
    # gaps above the trigger: fills at the open, then hits the target
    r = sm.simulate_trade(a([_bar(days[1], 102, 103, 101.5, 102.5), _bar(days[2], 103, 108, 102.6, 107)]),
                          1, 101, 98, 107, 3, 10)
    assert r["outcome"] == "target" and r["fill"] == 102 and r["r"] == pytest.approx((107 - 102) / 3)
    # gap down through the stop: sells at the open, worse than -1R
    r = sm.simulate_trade(a([_bar(days[1], 100.5, 101.5, 100.2, 101.2), _bar(days[2], 95, 96, 94, 95.5)]),
                          1, 101, 98, 107, 3, 10)
    assert r["outcome"] == "gap_stop" and r["r"] == pytest.approx((95 - 101) / 3) and r["r"] < -1
    # a day that touches both counts as the stop
    r = sm.simulate_trade(a([_bar(days[1], 100.5, 101.5, 100.2, 101.2), _bar(days[2], 101, 108, 97, 104)]),
                          1, 101, 98, 107, 3, 10)
    assert r["outcome"] == "stop" and r["r"] == pytest.approx(-1)
    # time stop: sells at the close after max_days sessions
    flat = [_bar(days[1], 100.5, 101.5, 100.2, 101.2)] + [_bar(days[i], 101, 102, 100, 101.5) for i in range(2, 5)]
    r = sm.simulate_trade(a(flat), 1, 101, 98, 107, 3, 3)
    assert r["outcome"] == "time" and r["exit_i"] == 4 and r["r"] == pytest.approx(0.5 / 3)


def test_plan_levels_and_validation():
    bars, _ = _trend()
    a = sm.arrays(bars)
    ind = sm.indicators(a)
    p = sm.plan_levels(a, ind, len(bars) - 1, 2.0)
    assert p["entry"] > a["h"][-1] and p["stop"] < p["entry"] - p["atr"] * 0.99
    assert p["target"] == pytest.approx(p["entry"] + 2 * (p["entry"] - p["stop"]), abs=0.011)
    assert sm.validate_plan(100, 101, 110) and sm.validate_plan(100, 99.8, 110)  # wrong side; too tight
    assert sm.validate_plan(100, 99, 110, atr_value=4.0)  # under half an ATR
    assert sm.validate_plan(100, 96, 110, atr_value=4.0) == []


def test_time_machine_is_repeatable_and_honest():
    bars, _ = _trend()
    a = sm.arrays(bars)
    t1 = sm.time_machine(a, 100, 97, 106, 10, 2000, seed=19)
    t2 = sm.time_machine(a, 100, 97, 106, 10, 2000, seed=19)
    assert t1 == t2  # same seed, same data, same numbers
    assert t1["target_pct"] + t1["stop_pct"] + t1["time_pct"] == pytest.approx(100, abs=0.2)
    assert t1["seed"] == 19 and len(t1["fan"]) == 10 and "33%" in t1["verdict"]  # 2R needs 1 / (1 + 2)
    assert t1["r_target"] == pytest.approx(2.0)
    assert t1 != sm.time_machine(a, 100, 97, 106, 10, 2000, seed=20)


def test_verdict_weighs_the_target():
    assert "break-even" in sm._verdict(240, 505, 2.0)  # 32% target-first vs 33% needed at 2R
    assert sm._verdict(600, 200, 1.0).startswith("Strongly toward the target")
    assert sm._verdict(0, 0, 2.0).startswith("Almost every replay ran out of time")


def test_setup_history_compares_with_random_days():
    bars, _ = _trend(900, 20, 200, seed=3)
    h = sm.setup_history(bars, "pullback", 2.0, 3, 10)
    assert h["fired"] >= h["taken"] and h["random_stats"]["trades"] > 100
    assert set(h["counts"]) == {"target", "stop", "gap_stop", "time", "no_fill"}
    assert h["verdict"]


def test_edge_stats():
    trades = [{"r": 2.0, "pnl": 200, "followed_plan": True}] * 4 + [{"r": -1.0, "pnl": -100, "followed_plan": True}] * 6
    st = sm.edge_stats(trades)
    assert st["win_rate"] == 40 and st["expectancy_r"] == pytest.approx(0.2) and st["breakeven_win_rate"] == pytest.approx(33.3)
    assert st["followed_pct"] == 100 and st["pnl"] == 200 and 0 <= st["positive_in_resamples_pct"] <= 100


# ------------------------------------------------------------------------------------------ the desk on the paper account
@pytest.fixture()
def desk(tmp_path, monkeypatch):
    eng = _engine(tmp_path, monkeypatch)
    hist, future = _trend()
    feed = {"bars": list(hist)}

    async def daily(symbol, bars=1260):
        return list(feed["bars"])[-bars:]

    async def fresh(symbol):
        return list(feed["bars"])[-40:]

    async def no_earnings(symbol, hold_days):
        return {"status": "none", "detail": "test"}

    async def regime():
        return {"label": "uptrend", "detail": "test uptrend"}

    monkeypatch.setattr(eng.swing, "_daily", daily)
    monkeypatch.setattr(eng.swing, "_fresh", fresh)
    monkeypatch.setattr(eng.swing, "regime", regime)
    monkeypatch.setattr(swing_mod, "earnings_estimate", no_earnings)
    monkeypatch.setattr(swing_mod, "market_status", lambda now=None: {"open": False, "opens_at": time.time() + 3600,
                                                                      "closes_at": None, "label": "closed"})
    return eng, feed, future


def _plan(eng, **kw):
    return asyncio.run(eng.swing.new_plan("AAPL", "pullback", "Uptrend dip to the 20-day; wrong below the 5-day low.", **kw))


def test_full_paper_trade_to_target_then_journal(desk):
    eng, feed, future = desk
    t = _plan(eng)
    assert t["status"] == "planned" and t["qty"] >= 1 and t["max_loss"] <= 500 * 1.01
    with pytest.raises(ValueError):
        asyncio.run(eng.swing.approve(t["id"]))  # no risk check yet
    t = asyncio.run(eng.swing.replay(t["id"]))
    assert t["replay"]["replays"] == 10_000 and t["history"]["setup"] == "pullback"
    t = asyncio.run(eng.swing.check(t["id"]))
    assert t["check"]["verdict"] == "PASS", [c for c in t["check"]["checks"] if not c["ok"]]
    t = asyncio.run(eng.swing.approve(t["id"]))
    assert t["status"] == "working" and eng.learn.p["counts"]["swing_approve"] == 1
    entry, stop, target = t["entry"], t["stop"], t["target"]
    # day 1: trades up through the buy-stop -> fills at the trigger
    feed["bars"].append(_bar(future[0], entry - 0.5, entry + 0.4, entry - 1.0, entry + 0.2))
    asyncio.run(eng.swing.update())
    t = eng.swing._ticket(eng.swing.data(), t["id"])
    assert t["status"] == "open" and "AAPL" in eng.broker.positions
    assert eng.broker.positions["AAPL"].stop_loss == stop and eng.broker.positions["AAPL"].take_profit == target
    # day 2: reaches the target
    feed["bars"].append(_bar(future[1], entry + 0.3, target + 0.5, entry, target))
    asyncio.run(eng.swing.update())
    t = eng.swing._ticket(eng.swing.data(), t["id"])
    assert t["status"] == "closed" and t["exit_reason"] == "target" and "AAPL" not in eng.broker.positions
    assert 1.9 < t["r_multiple"] < 2.0  # 2R minus a little slippage on each side
    assert t["pnl"] > 0 and t["followed_plan"] is True
    sells = eng.db.query("SELECT reason FROM trades WHERE side = 'sell'")
    assert [r["reason"] for r in sells] == ["take_profit"]
    # journal it
    with pytest.raises(ValueError):
        eng.swing.review(t["id"], "ok", True)  # too short
    eng.swing.review(t["id"], "Waited for the trigger and let the target do its job.", True)
    j = eng.swing.journal()
    assert j["overall"]["trades"] == 1 and j["unreviewed"] == [] and "pullback" in j["by_setup"]
    assert eng.learn.p["sets"]["swing_review"] == [t["id"]]
    w = eng.swing.weekly()
    assert w["closed"] == 1 and w["points"]


def test_gap_through_stop_loses_more_than_1r(desk):
    eng, feed, future = desk
    t = _plan(eng)
    asyncio.run(eng.swing.check(t["id"]))
    t = asyncio.run(eng.swing.approve(t["id"]))
    feed["bars"].append(_bar(future[0], t["entry"], t["entry"] + 0.3, t["entry"] - 0.2, t["entry"] + 0.1))
    gap_open = t["stop"] - 2.0
    feed["bars"].append(_bar(future[1], gap_open, gap_open + 0.5, gap_open - 0.5, gap_open))
    asyncio.run(eng.swing.update())
    t = eng.swing._ticket(eng.swing.data(), t["id"])
    assert t["status"] == "closed" and t["exit_reason"] == "gap_stop" and t["r_multiple"] < -1


def test_time_stop_and_expiry(desk):
    eng, feed, future = desk
    eng.swing.update_settings({"time_stop_days": 3, "max_positions": 5})
    t = _plan(eng)
    asyncio.run(eng.swing.check(t["id"]))
    t = asyncio.run(eng.swing.approve(t["id"]))
    e = t["entry"]
    feed["bars"].append(_bar(future[0], e, e + 0.2, e - 0.3, e + 0.1))
    for k in (1, 2, 3):
        feed["bars"].append(_bar(future[k], e + 0.1, e + 0.4, e - 0.2, e + 0.2))
    asyncio.run(eng.swing.update())
    t = eng.swing._ticket(eng.swing.data(), t["id"])
    assert t["status"] == "closed" and t["exit_reason"] == "time" and t["held_sessions"] == 3
    # a second plan whose buy-stop never triggers expires after its valid days, and nothing is bought
    t2 = asyncio.run(eng.swing.new_plan("MSFT", "breakout", "Breakout on volume; wrong back under the range.",
                                        entry=e + 30, stop=e + 24, target=e + 45))
    asyncio.run(eng.swing.check(t2["id"]))
    t2 = asyncio.run(eng.swing.approve(t2["id"]))
    for k in (4, 5, 6):
        feed["bars"].append(_bar(future[k], e, e + 1, e - 1, e))
    asyncio.run(eng.swing.update())
    t2 = eng.swing._ticket(eng.swing.data(), t2["id"])
    assert t2["status"] == "expired" and "MSFT" not in eng.broker.positions


def test_risk_gate_blocks_and_fingerprint(desk):
    eng, feed, future = desk
    t = _plan(eng)
    # too many shares: over the max loss per trade
    t = asyncio.run(eng.swing.edit_plan(t["id"], {"qty": t["qty"] * 3}))
    t = asyncio.run(eng.swing.check(t["id"]))
    assert t["check"]["verdict"] == "BLOCK"
    assert {c["id"] for c in t["check"]["checks"] if not c["ok"] and c["level"] == "block"} >= {"loss"}
    # back to a passing plan, then change it after the check: approval must refuse
    t = asyncio.run(eng.swing.edit_plan(t["id"], {"stop": t["stop"]}))  # re-sizes from the risk
    t = asyncio.run(eng.swing.check(t["id"]))
    assert t["check"]["verdict"] == "PASS"
    d = eng.swing.data()
    eng.swing._ticket(d, t["id"])["qty"] -= 1  # sneak an edit past the fingerprint
    eng.swing._save(d)
    with pytest.raises(ValueError, match="changed"):
        asyncio.run(eng.swing.approve(t["id"]))
    # chasing: price already ran more than half an ATR above the entry
    t = asyncio.run(eng.swing.edit_plan(t["id"], {"stop": t["stop"]}))
    last = feed["bars"][-1]
    feed["bars"][-1] = dict(last, close=t["entry"] + t["atr"])
    t = asyncio.run(eng.swing.check(t["id"]))
    assert any(c["id"] == "chase" and not c["ok"] and c["level"] == "block" for c in t["check"]["checks"])
    with pytest.raises(ValueError, match="thesis"):
        asyncio.run(eng.swing.new_plan("AAPL", "pullback", "because"))


def test_risk_gate_limits_sector_duplicates_and_earnings(desk, monkeypatch):
    eng, feed, future = desk
    eng.swing.update_settings({"max_per_sector": 1})
    a = _plan(eng)
    asyncio.run(eng.swing.check(a["id"]))
    asyncio.run(eng.swing.approve(a["id"]))
    dup = asyncio.run(eng.swing.check(_plan(eng)["id"]))
    blocked = {c["id"] for c in dup["check"]["checks"] if not c["ok"] and c["level"] == "block"}
    assert {"duplicate", "sector"} <= blocked

    async def earnings_soon(symbol, hold_days):
        return {"status": "estimate", "overlap": True, "detail": "likely inside the hold"}
    monkeypatch.setattr(swing_mod, "earnings_estimate", earnings_soon)
    e = asyncio.run(eng.swing.new_plan("XOM", "pullback", "Energy pullback; wrong under the 5-day low."))
    e = asyncio.run(eng.swing.check(e["id"]))
    assert any(c["id"] == "earnings" and not c["ok"] and c["level"] == "block" for c in e["check"]["checks"])
    eng.swing.update_settings({"block_earnings": False})
    e = asyncio.run(eng.swing.check(e["id"]))
    assert any(c["id"] == "earnings" and c["level"] == "warn" for c in e["check"]["checks"])


def test_stops_only_move_up(desk):
    eng, feed, future = desk
    t = _plan(eng)
    asyncio.run(eng.swing.check(t["id"]))
    t = asyncio.run(eng.swing.approve(t["id"]))
    feed["bars"].append(_bar(future[0], t["entry"], t["entry"] + 0.4, t["entry"] - 0.2, t["entry"] + 0.2))
    asyncio.run(eng.swing.update())
    with pytest.raises(ValueError, match="only move up"):
        asyncio.run(eng.swing.raise_stop(t["id"], t["stop"] - 1))
    eng.market.last_price["AAPL"] = t["entry"] + 1
    t2 = asyncio.run(eng.swing.raise_stop(t["id"], t["stop"] + 0.5))
    assert t2["stop"] == t["stop"] + 0.5 and eng.broker.positions["AAPL"].stop_loss == t2["stop"]


def test_routine_completes_once_per_session(desk):
    eng, feed, future = desk
    r = eng.swing.routine()
    assert not r["done"] and len(r["items"]) == 5
    asyncio.run(eng.swing.scan())  # the test universe loads the same synthetic chart for every symbol
    eng.swing.tick_routine("market")
    eng.swing.tick_routine("review")
    r = eng.swing.tick_routine("no_trade")
    assert r["done"] and eng.learn.p["sets"]["swing_routine"] == [r["session"]]
    eng.swing.routine()
    assert eng.learn.p["counts"]["swing_routine"] == 1
    with pytest.raises(ValueError):
        eng.swing.tick_routine("scan")  # automatic steps can't be ticked by hand


def test_track_record_opens_the_evidence_gate(desk):
    eng, feed, future = desk
    d = eng.swing.data()
    for i in range(20):
        d["tickets"].append({"id": 1000 + i, "status": "closed", "setup": "breakout", "route": "sim", "created": i,
                             "exit_ts": time.time() - i, "r_multiple": 1.0 if i % 2 else -0.5, "pnl": 10,
                             "followed_plan": True, "symbol": "AAPL"})
    eng.swing._save(d)
    rec = eng.swing.track_record()["breakout"]
    assert rec["eligible"] and rec["trades"] == 20
    ev = next(c for c in eng.live.readiness()["checks"] if c["id"] == "evidence")
    assert ev["ok"] and "breakout" in ev["detail"]


# ------------------------------------------------------------------------------------------ Alpaca routing
class FakeAlpacaSwing(FakeAlpaca):
    """Adds stop-limit entries that rest until `trigger()`, OCO exits with a stop leg, and order replace."""

    def handler(self, req):
        if req.url.path == "/v2/orders" and req.method == "POST":
            body = json.loads(req.content)
            if body.get("order_class") == "oco" or body["type"] == "stop_limit":
                self.submitted.append(body)
                oid = f"ord-{len(self.submitted)}"
                o = {"id": oid, "client_order_id": body.get("client_order_id"), "symbol": body["symbol"],
                     "side": body["side"], "qty": body["qty"], "filled_qty": "0", "filled_avg_price": None,
                     "status": "accepted", "type": body["type"]}
                if body.get("order_class") == "oco":
                    leg = dict(o, id=oid + "-sl", type="stop")
                    self.orders[leg["id"]] = leg
                    o["legs"] = [leg]
                self.orders[oid] = o
                return httpx.Response(200, json=o)
        if req.url.path.startswith("/v2/orders/") and req.method == "PATCH":
            old = self.orders[req.url.path.rsplit("/", 1)[1]]
            old["status"] = "replaced"
            new = dict(old, id=old["id"] + "r", status="accepted")
            self.orders[new["id"]] = new
            return httpx.Response(200, json=new)
        return super().handler(req)

    def fill(self, oid, price):
        o = self.orders[oid]
        o.update({"status": "filled", "filled_qty": o["qty"], "filled_avg_price": str(price)})
        q = float(o["qty"])
        self.positions[o["symbol"]] = self.positions.get(o["symbol"], 0) + (q if o["side"] == "buy" else -q)


def test_alpaca_paper_swing_bracket_lifecycle(desk, monkeypatch):
    eng, feed, future = desk
    fake = FakeAlpacaSwing(equity=20_000, price=100.0)
    eng.live = GoLive(eng, transport=httpx.MockTransport(fake.handler))
    d = eng.live.data()
    d["route"] = "alpaca_paper"
    eng.live._save(d)
    t = asyncio.run(eng.swing.new_plan("BRK-B", "pullback", "Quality dip in an uptrend; wrong under the low."))
    assert t["max_loss"] <= 0.005 * 20_000 * 1.01  # sized from the broker account
    asyncio.run(eng.swing.check(t["id"]))
    t = asyncio.run(eng.swing.approve(t["id"]))
    entry = fake.submitted[0]
    assert entry["symbol"] == "BRK.B" and entry["type"] == "stop_limit" and entry["time_in_force"] == "gtc"
    assert float(entry["limit_price"]) == pytest.approx(t["entry"] * 1.005, abs=0.011)
    fake.fill(t["entry_order"], t["entry"])
    asyncio.run(eng.live.sync())
    asyncio.run(eng.swing.update())
    t = eng.swing._ticket(eng.swing.data(), t["id"])
    assert t["status"] == "open" and t["fill_price"] == t["entry"]
    oco = fake.submitted[1]
    assert oco["order_class"] == "oco" and float(oco["stop_loss"]["stop_price"]) == t["stop"]
    assert float(oco["take_profit"]["limit_price"]) == t["target"]
    # raise the stop at the broker
    eng.market.last_price["BRK-B"] = t["entry"] + 2
    t = asyncio.run(eng.swing.raise_stop(t["id"], t["stop"] + 0.5))
    assert t["sl_order"].endswith("r")
    # the stop leg fills -> the ticket closes from the broker's fill price
    fake.fill(t["sl_order"], t["stop"])
    asyncio.run(eng.live.sync())
    asyncio.run(eng.swing.update())
    t = eng.swing._ticket(eng.swing.data(), t["id"])
    assert t["status"] == "closed" and t["exit_reason"] == "stop"
    assert t["r_multiple"] == pytest.approx((t["stop"] - t["entry"]) / (t["entry"] - t["stop_planned"]), abs=1e-3)
    assert eng.live.data()["paper_fills"] >= 2


def test_alpaca_live_swing_needs_confirmation_and_track_record(desk, monkeypatch):
    eng, feed, future = desk
    fake = FakeAlpacaSwing(equity=20_000, price=100.0)
    eng.live = GoLive(eng, transport=httpx.MockTransport(fake.handler))
    d = eng.live.data()
    d["route"] = "alpaca_live"
    eng.live._save(d)
    t = _plan(eng)
    t = asyncio.run(eng.swing.check(t["id"]))
    track = next(c for c in t["check"]["checks"] if c["id"] == "track")
    assert not track["ok"] and t["check"]["verdict"] == "BLOCK"
    assert not fake.submitted
