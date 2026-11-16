"""Go live (Level 15): route the agent's approved proposals and the swing desk's approved plans to a real broker
(Alpaca), behind three gates.

Routes:
  sim           the app's own paper account (default; everything before Level 15 uses this)
  alpaca_paper  Alpaca's paper-trading API: real order handling, pretend money
  alpaca_live   Alpaca with real money

Gates (checked by rules on the server, never by the AI):
  1. Ready:   the course is really finished (unlocking everything doesn't count), the 28-day capstone report exists,
              and there is evidence: the agent's strategy passes the evidence check on at least one stock or ETF
              (only symbols that pass may be traded by the agent), or a swing setup has a paper track record
              (swing.TRACK_TRADES closed trades, positive average R, the luck check, the plan followed; only setups
              that pass may trade real money).
  2. Paper:   at least PAPER_DAYS days and PAPER_FILLS filled orders through Alpaca paper, the only truly unseen test.
  3. Live:    a typed confirmation and a dollar cap. Every live order needs a human approval (auto mode is off),
              whole shares only, US stocks and ETFs only, a stop-loss after every buy, buys blocked after the daily
              loss limit, and the kill switch cancels the app's orders and sells only what the app bought.

API keys come from the environment (.env) and never reach the browser.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import time
from typing import Any

import httpx

from app import assets, prices

logger = logging.getLogger("app.live")

ROUTES = ("sim", "alpaca_paper", "alpaca_live")
LAST_COURSE_LEVEL = 14  # levels that must really be finished before the broker (this module is level 15)
SWING_LIMIT_PAD = 0.005  # a swing buy-stop is a stop-limit: it won't chase a gap more than 0.5% past the entry
PAPER_DAYS = 20
PAPER_FILLS = 3
LUCK_BAR = 95.0  # % of random traders the strategy must beat
TEST_SPLIT = 0.7  # the last 30% of history is the test window
DEFAULT_CAP = 1000.0
MAX_CAP = 25_000.0
DAILY_LOSS_PCT = 0.02  # live and broker-paper: no new buys after the account is down 2% on the day
STALE_SECONDS = 6 * 3600
PRICE_DRIFT = 0.02
CONFIRM_PHRASE = "I can lose this money"
FINAL = {"filled", "canceled", "expired", "rejected", "replaced", "done_for_day"}
BASE_URLS = {"alpaca_paper": "https://paper-api.alpaca.markets", "alpaca_live": "https://api.alpaca.markets"}
ENV_KEYS = {"alpaca_paper": ("ALPACA_PAPER_KEY_ID", "ALPACA_PAPER_SECRET_KEY"),
            "alpaca_live": ("ALPACA_LIVE_KEY_ID", "ALPACA_LIVE_SECRET_KEY")}


def _now() -> float:
    return time.time()


class LiveError(Exception):
    """A broker-route problem the learner should see (blocked by a gate, broker refused, keys missing)."""


class Alpaca:
    """Tiny Alpaca Trading API v2 client. `transport` lets tests swap in a fake broker."""

    def __init__(self, route: str, key: str, secret: str, transport: httpx.AsyncBaseTransport | None = None):
        self.route = route
        self._client = httpx.AsyncClient(base_url=BASE_URLS[route], timeout=15, transport=transport,
                                         headers={"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret})

    async def _req(self, method: str, path: str, **kw) -> Any:
        try:
            r = await self._client.request(method, path, **kw)
        except httpx.HTTPError as exc:
            raise LiveError(f"Couldn't reach Alpaca ({type(exc).__name__}). Nothing was sent.") from exc
        if r.status_code in (401, 403):
            raise LiveError("Alpaca rejected the API keys. Check them in .env (paper and live keys are different).")
        if r.status_code >= 400:
            try:
                msg = r.json().get("message") or r.text
            except ValueError:
                msg = r.text
            raise LiveError(f"Alpaca said: {msg[:300]}")
        return r.json() if r.content else None

    async def account(self) -> dict:
        return await self._req("GET", "/v2/account")

    async def clock(self) -> dict:
        return await self._req("GET", "/v2/clock")

    async def positions(self) -> list[dict]:
        return await self._req("GET", "/v2/positions")

    async def asset(self, symbol: str) -> dict:
        return await self._req("GET", f"/v2/assets/{symbol}")

    async def replace(self, order_id: str, body: dict) -> dict:
        return await self._req("PATCH", f"/v2/orders/{order_id}", json=body)

    async def order(self, order_id: str) -> dict:
        return await self._req("GET", f"/v2/orders/{order_id}")

    async def submit(self, body: dict) -> dict:
        return await self._req("POST", "/v2/orders", json=body)

    async def cancel(self, order_id: str) -> None:
        try:
            await self._req("DELETE", f"/v2/orders/{order_id}")
        except LiveError as exc:  # already filled or cancelled: nothing to do
            if "not cancelable" not in str(exc).lower() and "not found" not in str(exc).lower():
                raise

    async def close(self) -> None:
        await self._client.aclose()


def has_keys(route: str) -> bool:
    if route not in ENV_KEYS:
        return False
    k, s = ENV_KEYS[route]
    return bool(os.environ.get(k)) and bool(os.environ.get(s))


class GoLive:
    KEY = "golive"

    def __init__(self, engine, transport: httpx.AsyncBaseTransport | None = None):
        self.engine = engine
        self.db = engine.db
        self._transport = transport  # tests
        self._clients: dict[str, Alpaca] = {}
        self._lock = asyncio.Lock()

    # ---------------------------------------------------------------- stored state
    def data(self) -> dict:
        raw = self.db.get_kv(self.KEY)
        d = {"route": "sim", "cap_usd": DEFAULT_CAP, "live_confirmed_at": None, "evidence": None, "orders": [],
             "paper_first_fill": None, "paper_fills": 0}
        if isinstance(raw, dict):
            d.update(raw)
        return d

    def _save(self, d: dict) -> None:
        d["orders"] = d["orders"][-300:]
        self.db.set_kv(self.KEY, d)

    @property
    def route(self) -> str:
        return self.data()["route"]

    def client(self, route: str | None = None) -> Alpaca:
        route = route or self.route
        if route not in BASE_URLS:
            raise LiveError("The agent is trading the app's paper account; no broker is connected.")
        if not has_keys(route) and self._transport is None:
            k, s = ENV_KEYS[route]
            raise LiveError(f"Add {k} and {s} to .env, then restart the app.")
        if route not in self._clients:
            k, s = ENV_KEYS[route]
            self._clients[route] = Alpaca(route, os.environ.get(k, "test"), os.environ.get(s, "test"), self._transport)
        return self._clients[route]

    # ---------------------------------------------------------------- evidence
    def _strategy_key(self) -> str:
        return json.dumps(self.engine.agent.config()["strategy"], sort_keys=True)

    async def check_evidence(self) -> dict:
        """Test the agent's strategy, with its exact settings, on every stock/ETF on its watchlist."""
        from app.backtest import run_backtest
        from app.broker import RiskLimits
        from app.quant import luck_test

        cfg = self.engine.agent.config()
        st = cfg["strategy"]
        symbols = [s for s in cfg["watchlist"] if not assets.is_crypto(s)]
        if not symbols:
            raise LiveError("Put at least one US stock or ETF on the agent's watchlist: crypto can't go through Alpaca here.")
        fee_slip = self.engine.settings.slippage_bps
        results = {}
        for sym in symbols:
            fee = assets.fee_rate(sym, self.engine.settings.fee_rate)
            row: dict[str, Any] = {"symbol": sym, "name": assets.name(sym)}
            try:
                if st["type"] == "custom":
                    from app.codelab import evaluate_strategy

                    code = self.engine.custom_strategies()[st["name"]]["code"]
                    ev = await evaluate_strategy(code, sym, fee, fee_slip, TEST_SPLIT)
                    if not ev["ok"]:
                        raise ValueError("your strategy code didn't run: " + str((ev.get("run") or {}).get("error"))[:200])
                    m = ev["out_sample"]["metrics"]
                    beat = (ev.get("luck") or {}).get("beat_pct")
                    row["lookahead"] = bool(ev["lookahead"])
                else:
                    candles = await prices.cached_history(sym, "1d", assets.periods_per_year(sym) * 6 + 1)
                    limits = RiskLimits(max_position_pct=1.0, max_open_positions=1, stop_loss_pct=0, take_profit_pct=0,
                                        trailing_stop_pct=0, daily_loss_limit_pct=0)
                    cut = int(len(candles) * TEST_SPLIT)
                    bpy = assets.periods_per_year(sym)
                    res = await asyncio.to_thread(run_backtest, candles, st["name"], st.get("params"), limits, 10_000,
                                                  fee, fee_slip, bpy, sym, cut)
                    m = res["metrics"]
                    luck = await asyncio.to_thread(luck_test, candles[cut:], st["name"], st.get("params"),
                                                   fee + fee_slip / 10_000, 300, 7)
                    beat = luck["beat_pct"]
                    row["lookahead"] = False
                row.update({"test_return_pct": m["total_return_pct"], "buy_hold_pct": m["buy_hold_return_pct"],
                            "trades": m["num_trades"], "beat_random_pct": beat})
                reasons = []
                if row["lookahead"]:
                    reasons.append("it peeks at future prices (look-ahead)")
                if not m["num_trades"]:
                    reasons.append("it made no trades in the test window")
                if m["total_return_pct"] <= m["buy_hold_return_pct"]:
                    reasons.append(f"it made {m['total_return_pct']:+.1f}% vs {m['buy_hold_return_pct']:+.1f}% for "
                                   "buying and holding")
                if beat is None or beat < LUCK_BAR:
                    reasons.append(f"it beat {beat or 0:.0f}% of random traders (needs {LUCK_BAR:.0f}%)")
                row["ok"] = not reasons
                row["why"] = ("Passed: beat buy & hold after costs and " f"{beat:.0f}% of random traders."
                              if not reasons else "Not yet: " + "; ".join(reasons) + ".")
            except Exception as exc:  # noqa: BLE001 - one symbol failing shouldn't hide the others
                row.update({"ok": False, "why": f"Couldn't test it: {exc}"})
            results[sym] = row
        ev = {"ts": _now(), "strategy": self._strategy_key(), "symbols": results,
              "eligible": [s for s, r in results.items() if r["ok"]]}
        d = self.data()
        d["evidence"] = ev
        self._save(d)
        self.engine.learn.record("live_evidence")
        return ev

    def eligible(self) -> list[str]:
        ev = self.data()["evidence"]
        if not ev or ev["strategy"] != self._strategy_key():
            return []
        return list(ev["eligible"])

    # ---------------------------------------------------------------- gates
    def readiness(self) -> dict:
        d = self.data()
        learn = self.engine.learn
        cap = self.engine.capstone.get() or {}
        report = cap.get("report") or {}
        ev = d["evidence"]
        ev_fresh = bool(ev) and ev["strategy"] == self._strategy_key()
        paper_days = (_now() - d["paper_first_fill"]) / 86400 if d["paper_first_fill"] else 0.0
        swing = getattr(self.engine, "swing", None)
        swing_ok = [s for s, r in (swing.track_record() if swing else {}).items() if r["eligible"]]
        checks = [
            {"id": "course", "title": f"Finish levels 1-{LAST_COURSE_LEVEL}",
             "ok": all(learn.level_complete(lvl) for lvl in _course_levels()),
             "detail": "Every lesson and challenge, done for real. 'Unlock everything' doesn't count here."},
            {"id": "capstone", "title": "Finish the 4-week capstone",
             "ok": bool(report.get("final")),
             "detail": "Run your written plan for 28 days and generate the final report."},
            {"id": "evidence", "title": "Pass an evidence check (the agent's strategy or a swing setup)",
             "ok": (ev_fresh and bool(ev["eligible"])) or bool(swing_ok),
             "detail": ("Stale: you changed the agent's strategy since the last check. " if ev and not ev_fresh else "")
                       + "Agent: beat buy & hold after costs in the last 30% of history and beat "
                       f"{LUCK_BAR:.0f}% of random traders. Swing desk: a setup with a paper track record "
                       + (f"(passing: {', '.join(swing_ok)})." if swing_ok else "(none yet).")},
            {"id": "keys_paper", "title": "Connect Alpaca paper trading",
             "ok": has_keys("alpaca_paper") or self._transport is not None,
             "detail": "Free paper account at alpaca.markets; put the paper keys in .env."},
            {"id": "paper", "title": f"Trade on Alpaca paper for {PAPER_DAYS} days ({PAPER_FILLS}+ filled orders)",
             "ok": paper_days >= PAPER_DAYS and d["paper_fills"] >= PAPER_FILLS,
             "detail": f"{paper_days:.0f} of {PAPER_DAYS} days, {d['paper_fills']} of {PAPER_FILLS} filled orders. "
                       "This is the only test on prices your strategy has truly never seen."},
            {"id": "keys_live", "title": "Add your live Alpaca keys",
             "ok": has_keys("alpaca_live") or self._transport is not None,
             "detail": "Only once everything above is green. Check that Alpaca accepts accounts from your country."},
        ]
        ready_paper = all(c["ok"] for c in checks if c["id"] in ("course", "capstone", "evidence", "keys_paper"))
        ready_live = all(c["ok"] for c in checks)
        return {"checks": checks, "ready_paper": ready_paper, "ready_live": ready_live}

    async def set_route(self, route: str, cap_usd: float | None = None, confirm: str = "") -> dict:
        if route not in ROUTES:
            raise ValueError(f"Unknown route {route}")
        d = self.data()
        r = self.readiness()
        if route == "alpaca_paper" and not r["ready_paper"]:
            raise LiveError("Not yet: finish the checklist items above Alpaca paper first.")
        if route == "alpaca_live":
            if not r["ready_live"]:
                raise LiveError("Not yet: every item on the checklist must be green before real money.")
            if confirm.strip().lower() != CONFIRM_PHRASE.lower():
                raise LiveError(f'Type "{CONFIRM_PHRASE}" to switch on real money.')
            cap = float(cap_usd if cap_usd is not None else d["cap_usd"])
            if not 100 <= cap <= MAX_CAP:
                raise LiveError(f"Pick a cap between $100 and ${MAX_CAP:,.0f}. Start with money you can afford to lose.")
            d["cap_usd"] = cap
            d["live_confirmed_at"] = _now()
            self.engine.learn.record("live_on")
        elif cap_usd is not None:
            d["cap_usd"] = float(min(max(cap_usd, 100), MAX_CAP))
        if route != "sim":
            await self.client(route).account()  # fail now if the keys are wrong, not at the first trade
            agent = self.engine.agent.config()
            if route == "alpaca_live" and agent["mode"] == "auto":
                self.engine.agent.update({"mode": "approve"})
        if route == "alpaca_paper":
            self.engine.learn.record("broker_paper_on")
        d["route"] = route
        self._save(d)
        await self.engine.log("warning" if route == "alpaca_live" else "info",
                              f"Agent orders now go to: {route}" + (f" (cap ${d['cap_usd']:,.0f})" if route != "sim" else ""))
        return await self.status()

    # ---------------------------------------------------------------- orders
    def app_qty(self, symbol: str, route: str | None = None) -> float:
        """Shares the app bought and still holds (filled buys minus filled sells, including stop-loss fills)."""
        route = route or self.route
        q = 0.0
        for o in self.data()["orders"]:
            if o["route"] == route and o["symbol"] == symbol:
                q += o["filled_qty"] if o["side"] == "buy" else -o["filled_qty"]
        return max(q, 0.0)

    def deployed(self, route: str | None = None) -> float:
        """Dollars the app has in the market on this route (cost basis), plus buys still waiting to fill."""
        route = route or self.route
        total = 0.0
        for o in self.data()["orders"]:
            if o["route"] != route:
                continue
            if o["side"] == "buy":
                total += o["filled_qty"] * (o["avg_price"] or o["est_price"])
                if o["status"] not in FINAL and o["kind"] == "entry":
                    total += (o["qty"] - o["filled_qty"]) * o["est_price"]
            else:
                total -= o["filled_qty"] * (o["avg_price"] or o["est_price"])
        return max(total, 0.0)

    async def sizing(self) -> dict:
        """What the agent may use on the current broker route: capped equity and cash, and whether buys are paused."""
        route = self.route
        acct = await self.client(route).account()
        equity = float(acct["equity"])
        last = float(acct.get("last_equity") or equity)
        cap = self.data()["cap_usd"] if route == "alpaca_live" else max(self.data()["cap_usd"], 10_000.0)
        deployed = self.deployed(route)
        held = [s for s in {o["symbol"] for o in self.data()["orders"] if o["route"] == route} if self.app_qty(s, route) > 0]
        halted = []
        if acct.get("trading_blocked") or acct.get("account_blocked"):
            halted.append("your Alpaca account is blocked from trading")
        if last > 0 and equity < last * (1 - DAILY_LOSS_PCT):
            halted.append(f"the account is down more than {DAILY_LOSS_PCT * 100:.0f}% today")
        return {"equity": min(equity, cap), "cash": max(min(float(acct["buying_power"]), cap - deployed), 0.0),
                "positions": held, "halted": halted, "cap": cap, "deployed": deployed}

    def _track(self, d: dict, order: dict, *, route: str, kind: str, proposal_id: int | None, est_price: float,
               stop_price: float | None = None) -> dict:
        row = {"id": order["id"], "client_id": order.get("client_order_id"), "route": route, "kind": kind,
               "symbol": order["symbol"], "side": order["side"], "qty": float(order.get("qty") or 0),
               "filled_qty": float(order.get("filled_qty") or 0), "avg_price": _f(order.get("filled_avg_price")),
               "status": order["status"], "est_price": est_price, "stop_price": stop_price, "proposal": proposal_id,
               "created": _now(), "stop_order": None, "counted": False}
        d["orders"].append(row)
        self._on_fill(d, row)
        return row

    def _on_fill(self, d: dict, o: dict) -> None:
        """Count each fully filled order once (paper fills feed gate 2)."""
        if o["status"] != "filled" or o.get("counted"):
            return
        o["counted"] = True
        if o["route"] == "alpaca_paper":
            d["paper_fills"] += 1
            d["paper_first_fill"] = d["paper_first_fill"] or _now()
            self.engine.learn.record("broker_paper_fill")
        elif o["route"] == "alpaca_live":
            self.engine.learn.record("live_fill")

    async def place(self, proposal: dict, confirmed: bool = False) -> dict:
        """Send an approved agent proposal to the broker. Returns the tracked order row."""
        route = self.route
        if route == "sim":
            raise LiveError("No broker route is on.")
        if route == "alpaca_live" and not confirmed:
            raise LiveError("Real money: confirm this order in the app first.")
        sym = proposal["symbol"]
        if assets.is_crypto(sym):
            raise LiveError("Crypto doesn't go through Alpaca here; only US stocks and ETFs.")
        if proposal["side"] == "buy" and sym not in self.eligible():
            raise LiveError(f"{sym} hasn't passed the evidence check for this strategy, so it can't trade on the broker.")
        if _now() - proposal["created"] > STALE_SECONDS:
            raise LiveError("This proposal is more than 6 hours old. Run the agent again for a fresh one.")
        price = self.engine.prices().get(sym) or proposal["price"]
        if abs(price / proposal["price"] - 1) > PRICE_DRIFT:
            raise LiveError(f"The price moved more than {PRICE_DRIFT * 100:.0f}% since the proposal. Run the agent again.")
        api = self.client(route)
        async with self._lock:
            d = self.data()
            if proposal["side"] == "buy":
                info = await api.asset(sym)
                if not info.get("tradable", False):
                    raise LiveError(f"Alpaca says {sym} isn't tradable right now.")
                s = await self.sizing()
                if s["halted"]:
                    raise LiveError("Buys are paused: " + "; ".join(s["halted"]) + ".")
                qty = math.floor(min(proposal["notional"], s["cash"]) / price)
                if qty < 1:
                    raise LiveError(f"One share of {sym} costs about ${price:,.0f}, more than this trade's size "
                                    f"(${min(proposal['notional'], s['cash']):,.0f}). Whole shares only: raise the cap or "
                                    "trade a cheaper ETF.")
                order = await api.submit({"symbol": sym, "qty": str(qty), "side": "buy", "type": "market",
                                          "time_in_force": "day", "client_order_id": f"qp-{proposal['id']}-b"})
                row = self._track(d, order, route=route, kind="entry", proposal_id=proposal["id"], est_price=price,
                                  stop_price=proposal.get("stop_loss"))
            else:
                qty = self.app_qty(sym, route)
                if qty <= 0:
                    raise LiveError(f"The app doesn't hold any {sym} on the broker.")
                for o in d["orders"]:  # free the shares the stop-loss is holding
                    if o["route"] == route and o["symbol"] == sym and o["kind"] == "stop" and o["status"] not in FINAL:
                        await api.cancel(o["id"])
                        o["status"] = "canceled"
                pos = {p["symbol"]: float(p["qty"]) for p in await api.positions()}
                qty = min(qty, pos.get(sym, 0.0))
                if qty <= 0:
                    raise LiveError(f"Alpaca shows no {sym} position to sell.")
                order = await api.submit({"symbol": sym, "qty": _qty(qty), "side": "sell", "type": "market",
                                          "time_in_force": "day", "client_order_id": f"qp-{proposal['id']}-s"})
                row = self._track(d, order, route=route, kind="exit", proposal_id=proposal["id"], est_price=price)
            self._save(d)
        await self.engine.log("warning" if route == "alpaca_live" else "info",
                              f"{route}: {row['side']} {row['qty']:g} {sym} sent (order {row['id'][:8]})")
        await self.sync()
        return row

    async def sync(self) -> dict:
        """Refresh every open order; place the stop-loss once a buy fills; count paper fills."""
        async with self._lock:
            d = self.data()
            changed = False
            for o in list(d["orders"]):
                if o["status"] in FINAL and not (o["kind"] == "entry" and o["filled_qty"] and not o["stop_order"]
                                                 and o["stop_price"]):
                    continue
                try:
                    api = self.client(o["route"])
                    if o["status"] not in FINAL:
                        fresh = await api.order(o["id"])
                        before = o["filled_qty"]
                        o.update({"status": fresh["status"], "filled_qty": float(fresh.get("filled_qty") or 0),
                                  "avg_price": _f(fresh.get("filled_avg_price"))})
                        if o["filled_qty"] > before:
                            changed = True
                        self._on_fill(d, o)
                    if (o["kind"] == "entry" and o["status"] == "filled" and o["stop_price"] and not o["stop_order"]):
                        stop = await api.submit({"symbol": o["symbol"], "qty": _qty(o["filled_qty"]), "side": "sell",
                                                 "type": "stop", "stop_price": f"{o['stop_price']:.2f}",
                                                 "time_in_force": "gtc", "client_order_id": f"qp-{o['proposal']}-sl"})
                        o["stop_order"] = stop["id"]
                        self._track(d, stop, route=o["route"], kind="stop", proposal_id=o["proposal"],
                                    est_price=o["stop_price"])
                        changed = True
                except LiveError as exc:
                    logger.warning("Sync of order %s failed: %s", o["id"], exc)
            self._save(d)
        if changed:
            await self.engine.hub.broadcast("live", {"changed": True})
        return {"orders": len(d["orders"])}

    async def kill(self, sell_positions: bool = False) -> dict:
        """Cancel every open order the app placed (on both broker routes) and optionally sell what it bought."""
        cancelled, sold, errors = 0, [], []
        async with self._lock:
            d = self.data()
            for o in d["orders"]:
                if o["status"] in FINAL:
                    continue
                try:
                    await self.client(o["route"]).cancel(o["id"])
                    o["status"] = "canceled"
                    cancelled += 1
                except LiveError as exc:
                    errors.append(str(exc))
            self._save(d)
        if sell_positions:
            for route in ("alpaca_paper", "alpaca_live"):
                syms = {o["symbol"] for o in d["orders"] if o["route"] == route}
                for sym in syms:
                    qty = self.app_qty(sym, route)
                    if qty <= 0:
                        continue
                    try:
                        api = self.client(route)
                        pos = {p["symbol"]: float(p["qty"]) for p in await api.positions()}
                        qty = min(qty, pos.get(sym, 0.0))
                        if qty > 0:
                            order = await api.submit({"symbol": sym, "qty": _qty(qty), "side": "sell", "type": "market",
                                                      "time_in_force": "day", "client_order_id": f"qp-kill-{int(_now())}-{sym}"})
                            async with self._lock:
                                d2 = self.data()
                                self._track(d2, order, route=route, kind="exit", proposal_id=None,
                                            est_price=self.engine.prices().get(sym) or 0.0)
                                self._save(d2)
                            sold.append(f"{route}:{sym}")
                    except LiveError as exc:
                        errors.append(str(exc))
        if cancelled or sold or errors:
            await self.engine.log("warning", f"KILL SWITCH (broker): {cancelled} orders cancelled, {len(sold)} sells sent"
                                  + (f", errors: {'; '.join(errors)[:300]}" if errors else ""))
        return {"cancelled": cancelled, "sold": sold, "errors": errors}

    # ---------------------------------------------------------------- swing desk orders
    def order_row(self, order_id: str | None) -> dict | None:
        if not order_id:
            return None
        return next((o for o in self.data()["orders"] if o["id"] == order_id), None)

    async def swing_entry(self, t: dict, confirmed: bool = False) -> dict:
        """A swing plan's entry: a stop-limit buy that waits for the price to trade up to the entry (GTC; the desk
        cancels it after the plan's valid days). The limit stops it chasing a gap far above the plan."""
        route = self.route
        if route == "sim":
            raise LiveError("No broker route is on.")
        if route == "alpaca_live" and not confirmed:
            raise LiveError("Real money: confirm this order in the app first.")
        sym = alpaca_symbol(t["symbol"])
        api = self.client(route)
        async with self._lock:
            info = await api.asset(sym)
            if not info.get("tradable", False):
                raise LiveError(f"Alpaca says {sym} isn't tradable right now.")
            s = await self.sizing()
            if s["halted"]:
                raise LiveError("Buys are paused: " + "; ".join(s["halted"]) + ".")
            qty = min(int(t["qty"]), math.floor(s["cash"] / t["entry"]))
            if qty < 1:
                raise LiveError(f"One share of {sym} at ${t['entry']:,.2f} is more than the cash the app may use "
                                f"(${s['cash']:,.0f}). Whole shares only: raise the cap or pick a cheaper stock.")
            order = await api.submit({"symbol": sym, "qty": str(qty), "side": "buy", "type": "stop_limit",
                                      "stop_price": f"{t['entry']:.2f}",
                                      "limit_price": f"{t['entry'] * (1 + SWING_LIMIT_PAD):.2f}",
                                      "time_in_force": "gtc", "client_order_id": f"qp-sw{t['id']}-b-{int(_now())}"})
            d = self.data()
            row = self._track(d, order, route=route, kind="swing_entry", proposal_id=None, est_price=t["entry"])
            row["ticket"] = t["id"]
            self._save(d)
        t["qty"] = qty
        await self.engine.log("warning" if route == "alpaca_live" else "info",
                              f"{route}: swing buy-stop {qty} {sym} at {t['entry']:.2f} sent (order {row['id'][:8]})")
        return row

    async def swing_exits(self, t: dict) -> dict:
        """After the entry fills: one order that is both the stop-loss and the take-profit (one cancels the other)."""
        route = t["route"]
        api = self.client(route)
        async with self._lock:
            order = await api.submit({"symbol": alpaca_symbol(t["symbol"]), "qty": str(int(t["qty"])), "side": "sell",
                                      "type": "limit", "time_in_force": "gtc", "order_class": "oco",
                                      "take_profit": {"limit_price": f"{t['target']:.2f}"},
                                      "stop_loss": {"stop_price": f"{t['stop']:.2f}"},
                                      "client_order_id": f"qp-sw{t['id']}-x-{int(_now())}"})
            d = self.data()
            tp = self._track(d, order, route=route, kind="swing_tp", proposal_id=None, est_price=t["target"])
            tp["ticket"] = t["id"]
            sl_id = None
            for leg in order.get("legs") or []:
                row = self._track(d, leg, route=route, kind="swing_sl", proposal_id=None, est_price=t["stop"],
                                  stop_price=t["stop"])
                row["ticket"] = t["id"]
                sl_id = leg["id"]
            self._save(d)
        return {"tp": tp["id"], "sl": sl_id}

    async def swing_cancel_entry(self, t: dict) -> None:
        row = self.order_row(t.get("entry_order"))
        if row and row["status"] not in FINAL:
            await self.client(row["route"]).cancel(row["id"])
            async with self._lock:
                d = self.data()
                r = next(o for o in d["orders"] if o["id"] == row["id"])
                r["status"] = "canceled"
                self._save(d)

    async def swing_move_stop(self, t: dict, new_stop: float) -> None:
        row = self.order_row(t.get("sl_order"))
        if not row or row["status"] in FINAL:
            raise LiveError("There's no live stop order for this trade at Alpaca.")
        fresh = await self.client(row["route"]).replace(row["id"], {"stop_price": f"{new_stop:.2f}"})
        async with self._lock:
            d = self.data()
            old = next(o for o in d["orders"] if o["id"] == row["id"])
            old["status"] = "replaced"
            new = self._track(d, fresh, route=row["route"], kind="swing_sl", proposal_id=None, est_price=new_stop,
                              stop_price=new_stop)
            new["ticket"] = t["id"]
            self._save(d)
        t["sl_order"] = fresh["id"]

    async def swing_exit_now(self, t: dict) -> dict:
        """Cancel the trade's stop and take-profit, then sell its shares at the market (time stop or by hand)."""
        route = t["route"]
        api = self.client(route)
        async with self._lock:
            d = self.data()
            for o in d["orders"]:
                if o.get("ticket") == t["id"] and o["kind"] in ("swing_tp", "swing_sl") and o["status"] not in FINAL:
                    await api.cancel(o["id"])
                    o["status"] = "canceled"
            sym = alpaca_symbol(t["symbol"])
            pos = {p["symbol"]: float(p["qty"]) for p in await api.positions()}
            qty = min(float(t["qty"]), pos.get(sym, 0.0), self.app_qty(sym, route))
            if qty <= 0:
                self._save(d)
                raise LiveError(f"Alpaca shows no {sym} shares bought by the app to sell.")
            order = await api.submit({"symbol": sym, "qty": _qty(qty), "side": "sell", "type": "market",
                                      "time_in_force": "day", "client_order_id": f"qp-sw{t['id']}-s-{int(_now())}"})
            row = self._track(d, order, route=route, kind="swing_exit", proposal_id=None,
                              est_price=self.engine.prices().get(t["symbol"]) or t["entry"])
            row["ticket"] = t["id"]
            self._save(d)
        await self.engine.log("warning" if route == "alpaca_live" else "info",
                              f"{route}: swing sell {qty:g} {sym} sent (order {row['id'][:8]})")
        return row

    # ---------------------------------------------------------------- status for the screen
    async def status(self) -> dict:
        d = self.data()
        out = {"route": d["route"], "cap_usd": d["cap_usd"], "confirm_phrase": CONFIRM_PHRASE,
               "readiness": self.readiness(), "evidence": d["evidence"], "eligible": self.eligible(),
               "orders": d["orders"][-30:][::-1], "keys": {r: has_keys(r) for r in ENV_KEYS},
               "rules": {"paper_days": PAPER_DAYS, "paper_fills": PAPER_FILLS, "luck_bar": LUCK_BAR,
                         "daily_loss_pct": DAILY_LOSS_PCT * 100, "max_cap": MAX_CAP}}
        if d["route"] != "sim":
            try:
                api = self.client()
                acct = await api.account()
                out["account"] = {k: _f(acct.get(k)) for k in ("equity", "last_equity", "cash", "buying_power")}
                out["account"]["currency"] = acct.get("currency", "USD")
                out["positions"] = [{"symbol": p["symbol"], "qty": float(p["qty"]), "app_qty": self.app_qty(p["symbol"]),
                                     "avg_price": _f(p.get("avg_entry_price")), "price": _f(p.get("current_price")),
                                     "pnl": _f(p.get("unrealized_pl"))} for p in await api.positions()]
                out["deployed"] = round(self.deployed(), 2)
            except LiveError as exc:
                out["error"] = str(exc)
        return out

    async def loop(self) -> None:
        while True:
            await asyncio.sleep(30)
            try:
                if any(o["status"] not in FINAL or (o["kind"] == "entry" and o["filled_qty"] and not o["stop_order"]
                                                    and o["stop_price"]) for o in self.data()["orders"]):
                    await self.sync()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.warning("Live sync loop error: %r", exc)

    async def close(self) -> None:
        for c in self._clients.values():
            await c.close()


def _course_levels() -> list[dict]:
    from app.curriculum import LEVELS

    return [lvl for lvl in LEVELS if lvl["n"] <= LAST_COURSE_LEVEL]


def alpaca_symbol(symbol: str) -> str:
    """Yahoo writes share classes with a dash (BRK-B), Alpaca with a dot (BRK.B)."""
    return symbol.replace("-", ".")


def _f(v) -> float | None:
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def _qty(q: float) -> str:
    return f"{q:.9f}".rstrip("0").rstrip(".")
