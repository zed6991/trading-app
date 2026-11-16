"""Level 14 go-live: gates, routing to a fake Alpaca, whole shares, stop-loss after fill, caps, staleness, kill switch."""
import asyncio
import json
import time

import httpx
import pytest

from app import live as live_mod
from app.live import GoLive, LiveError
from tests.test_levels_8_13 import _engine, fake_prices  # noqa: F401 - pytest fixture


class FakeAlpaca:
    """Just enough of Alpaca's v2 API: account, assets, positions, orders (market fills instantly, stops rest)."""

    def __init__(self, equity=10_000.0, last_equity=10_000.0, price=500.0):
        self.acct = {"equity": str(equity), "last_equity": str(last_equity), "cash": str(equity),
                     "buying_power": str(equity), "currency": "USD", "trading_blocked": False, "account_blocked": False}
        self.price = price
        self.orders: dict[str, dict] = {}
        self.positions: dict[str, float] = {}
        self.submitted: list[dict] = []
        self.cancelled: list[str] = []

    def handler(self, req: httpx.Request) -> httpx.Response:
        path, m = req.url.path, req.method
        if path == "/v2/account":
            return httpx.Response(200, json=self.acct)
        if path.startswith("/v2/assets/"):
            return httpx.Response(200, json={"symbol": path.rsplit("/", 1)[1], "tradable": True})
        if path == "/v2/positions":
            return httpx.Response(200, json=[{"symbol": s, "qty": str(q), "avg_entry_price": str(self.price),
                                              "current_price": str(self.price), "unrealized_pl": "0"}
                                             for s, q in self.positions.items() if q > 0])
        if path == "/v2/orders" and m == "POST":
            body = json.loads(req.content)
            self.submitted.append(body)
            oid = f"ord-{len(self.submitted)}"
            o = {"id": oid, "client_order_id": body.get("client_order_id"), "symbol": body["symbol"], "side": body["side"],
                 "qty": body["qty"], "filled_qty": "0", "filled_avg_price": None, "status": "accepted", "type": body["type"]}
            if body["type"] == "market":
                q = float(body["qty"])
                if body["side"] == "sell" and q > self.positions.get(body["symbol"], 0) + 1e-9:
                    return httpx.Response(422, json={"message": "insufficient qty available"})
                o.update({"status": "filled", "filled_qty": body["qty"], "filled_avg_price": str(self.price)})
                self.positions[body["symbol"]] = self.positions.get(body["symbol"], 0) + (q if body["side"] == "buy" else -q)
            self.orders[oid] = o
            return httpx.Response(200, json=o)
        if path.startswith("/v2/orders/") and m == "GET":
            return httpx.Response(200, json=self.orders[path.rsplit("/", 1)[1]])
        if path.startswith("/v2/orders/") and m == "DELETE":
            oid = path.rsplit("/", 1)[1]
            self.cancelled.append(oid)
            self.orders[oid]["status"] = "canceled"
            return httpx.Response(204)
        return httpx.Response(404, json={"message": "not found"})


def _setup(tmp_path, monkeypatch, fake=None, ready=True):
    eng = _engine(tmp_path, monkeypatch)
    fake = fake or FakeAlpaca()
    eng.live = GoLive(eng, transport=httpx.MockTransport(fake.handler))
    eng.agent.update({"watchlist": ["SPY", "BTCUSDT"], "use_research": False})

    async def always_in(symbol, candles):
        return 1, "test strategy says hold"
    monkeypatch.setattr(eng.agent, "_signal", always_in)
    if ready:
        monkeypatch.setattr(eng.learn, "level_complete", lambda lvl: True)
        monkeypatch.setattr(eng.learn, "level", lambda: 14)
        eng.db.set_kv("capstone", {"started_at": 0, "start_equity": 1, "spy_start": 1, "plan": "x", "report": {"final": True}})
        d = eng.live.data()
        d["evidence"] = {"ts": time.time(), "strategy": eng.live._strategy_key(), "symbols": {}, "eligible": ["SPY"]}
        eng.live._save(d)
    return eng, fake


def test_gates_block_until_ready(tmp_path, monkeypatch):
    eng, _ = _setup(tmp_path, monkeypatch, ready=False)
    r = eng.live.readiness()
    assert not r["ready_paper"] and not r["ready_live"]
    with pytest.raises(LiveError):
        asyncio.run(eng.live.set_route("alpaca_paper"))
    # unlocking everything doesn't count as finishing the course
    eng.learn.p["unlock_all"] = True
    assert not next(c for c in eng.live.readiness()["checks"] if c["id"] == "course")["ok"]


def test_live_needs_paper_period_and_typed_confirmation(tmp_path, monkeypatch):
    eng, _ = _setup(tmp_path, monkeypatch)
    assert eng.live.readiness()["ready_paper"] and not eng.live.readiness()["ready_live"]
    with pytest.raises(LiveError):
        asyncio.run(eng.live.set_route("alpaca_live", 1000, live_mod.CONFIRM_PHRASE))  # no paper period yet
    d = eng.live.data()
    d.update({"paper_first_fill": time.time() - 21 * 86400, "paper_fills": 3})
    eng.live._save(d)
    with pytest.raises(LiveError):
        asyncio.run(eng.live.set_route("alpaca_live", 1000, "yes"))
    with pytest.raises(LiveError):
        asyncio.run(eng.live.set_route("alpaca_live", 1_000_000, live_mod.CONFIRM_PHRASE))
    eng.agent.update({"mode": "auto"})
    st = asyncio.run(eng.live.set_route("alpaca_live", 1000, live_mod.CONFIRM_PHRASE))
    assert st["route"] == "alpaca_live" and eng.agent.config()["mode"] == "approve"
    with pytest.raises(ValueError):
        eng.agent.update({"mode": "auto"})  # never auto with real money


def test_paper_route_whole_shares_stop_after_fill_and_sell(tmp_path, monkeypatch, fake_prices):  # noqa: F811
    eng, fake = _setup(tmp_path, monkeypatch)
    asyncio.run(eng.live.set_route("alpaca_paper"))
    run = asyncio.run(eng.agent.run_once(manual=True))
    assert [p["symbol"] for p in run["new"]] == ["SPY"]  # crypto skipped on the broker route
    assert any("crypto" in s["decision"] for s in run["steps"] if s["symbol"] == "BTCUSDT")
    p = run["new"][0]
    out = asyncio.run(eng.agent.decide(p["id"], True))
    assert out["status"] == "sent"
    buy = fake.submitted[0]
    assert buy["side"] == "buy" and buy["type"] == "market" and float(buy["qty"]).is_integer()
    stop = fake.submitted[1]  # placed once the buy filled
    assert stop["type"] == "stop" and stop["time_in_force"] == "gtc" and stop["qty"] == buy["qty"]
    assert float(stop["stop_price"]) < 500
    d = eng.live.data()
    assert d["paper_fills"] == 1 and d["paper_first_fill"]
    assert eng.live.app_qty("SPY") == float(buy["qty"])
    assert eng.learn.p["counts"]["broker_paper_fill"] == 1
    # the strategy turns off -> sell proposal for exactly what the app holds, stop cancelled first

    async def flat(symbol, candles):
        return 0, "test strategy says cash"
    monkeypatch.setattr(eng.agent, "_signal", flat)
    sell = asyncio.run(eng.agent.run_once(manual=True))["new"][0]
    assert sell["side"] == "sell" and sell["qty"] == float(buy["qty"])
    asyncio.run(eng.agent.decide(sell["id"], True))
    assert fake.cancelled == ["ord-2"] and fake.submitted[-1]["side"] == "sell"
    assert eng.live.app_qty("SPY") == 0 and fake.positions["SPY"] == 0


def test_live_orders_need_confirmation_cap_and_freshness(tmp_path, monkeypatch, fake_prices):  # noqa: F811
    fake = FakeAlpaca(equity=50_000)
    eng, fake = _setup(tmp_path, monkeypatch, fake)
    d = eng.live.data()
    d.update({"paper_first_fill": time.time() - 30 * 86400, "paper_fills": 5})
    eng.live._save(d)
    asyncio.run(eng.live.set_route("alpaca_live", 1200, live_mod.CONFIRM_PHRASE))
    p = asyncio.run(eng.agent.run_once(manual=True))["new"][0]
    assert p["notional"] <= 1200 * 0.2 + 1  # sized from the $1,200 cap, not the $50,000 account
    with pytest.raises(ValueError):
        asyncio.run(eng.agent.decide(p["id"], True))  # no confirmation
    stale = dict(p, created=time.time() - 7 * 3600)
    with pytest.raises(LiveError):
        asyncio.run(eng.live.place(stale, confirmed=True))
    out = asyncio.run(eng.agent.decide(p["id"], True, confirmed=True))
    # $240 can't buy one $500 share: whole shares only, explained, and nothing sent
    assert out["status"] == "failed" and "Whole shares only" in out["error"] and not fake.submitted
    fake.price = 100.0
    eng.market.last_price["SPY"] = 100.0
    p2 = asyncio.run(eng.agent.run_once(manual=True))["new"][0]
    out = asyncio.run(eng.agent.decide(p2["id"], True, confirmed=True))
    assert out["status"] == "sent" and fake.submitted[0]["qty"] == "2"  # floor($240 / $100)
    assert 0 < eng.live.deployed() <= 1200


def test_daily_loss_limit_pauses_buys(tmp_path, monkeypatch, fake_prices):  # noqa: F811
    eng, fake = _setup(tmp_path, monkeypatch, FakeAlpaca(equity=9_700, last_equity=10_000))
    asyncio.run(eng.live.set_route("alpaca_paper"))
    run = asyncio.run(eng.agent.run_once(manual=True))
    spy = next(st for st in run["steps"] if st["symbol"] == "SPY")
    assert run["new"] == [] and "down more than 2%" in spy["decision"]


def test_kill_switch_cancels_app_orders_and_sells_only_app_shares(tmp_path, monkeypatch, fake_prices):  # noqa: F811
    eng, fake = _setup(tmp_path, monkeypatch)
    fake.positions["SPY"] = 7.0  # the learner's own shares, bought outside the app
    asyncio.run(eng.live.set_route("alpaca_paper"))
    p = asyncio.run(eng.agent.run_once(manual=True))["new"][0]
    asyncio.run(eng.agent.decide(p["id"], True))
    bought = eng.live.app_qty("SPY")
    assert bought >= 1
    state = asyncio.run(eng.agent.kill(sell_positions=True))
    assert state["broker"]["cancelled"] == 1 and state["broker"]["sold"] == ["alpaca_paper:SPY"]
    assert fake.submitted[-1]["qty"] == live_mod._qty(bought)
    assert fake.positions["SPY"] == 7.0  # their own shares are untouched


def test_evidence_check_uses_exact_settings_and_marks_eligibility(tmp_path, monkeypatch, fake_prices):  # noqa: F811
    eng, _ = _setup(tmp_path, monkeypatch, ready=False)
    ev = asyncio.run(eng.live.check_evidence())
    assert set(ev["symbols"]) == {"SPY"}  # crypto isn't tested for the broker
    row = ev["symbols"]["SPY"]
    assert {"test_return_pct", "buy_hold_pct", "beat_random_pct", "ok", "why"} <= set(row)
    assert eng.live.eligible() == ev["eligible"]
    eng.agent.update({"strategy": {"type": "builtin", "name": "momentum", "params": {"lookback": 30}}})
    assert eng.live.eligible() == []  # changing the strategy makes the evidence stale
