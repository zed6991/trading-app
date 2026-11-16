"""Swing trading desk (Level 14): scan -> plan -> Time Machine -> risk gate -> approve -> orders -> journal.

One trade is one *ticket*. Its life:
  planned  -> you wrote a plan (setup, thesis, entry, stop, target, time stop) and the app sized it
  working  -> you approved a plan that passed the risk gate today; a buy-stop waits for the entry price
  open     -> the entry filled; the stop, the target and the time stop are live
  closing  -> (broker only) a time-stop or manual sell was sent and hasn't filled yet
  closed / expired / cancelled / rejected

Orders go where Go live points (`engine.live.route`):
  sim           the app's paper account. Handled here on daily bars, like a broker's day orders: a buy-stop fills
                when the day's high reaches it (at the open if it gaps above), the stop and target are checked on
                each day's low and high (a gap through the stop fills at the open), and the time stop sells at the
                close of the last day. While the US market is open, live prices are checked every few minutes.
  alpaca_*      a stop-limit buy at Alpaca; after the fill, a one-cancels-other stop + take-profit; time stops and
                manual exits cancel those and sell the shares the app bought (see live.py).

Nothing is ever sent without a PASS from the risk gate *today* for that exact plan (fingerprint) and your approval.
The learner always decides; the app never picks a stock for them.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import json
import logging
import math
import time
from typing import Any

import numpy as np

from app import prices, research, swingmath, universe
from app.broker import RiskError
from app.stocks import NY, _session, market_status

logger = logging.getLogger("app.swing")

ACTIVE = ("working", "open", "closing")
DONE = ("closed", "expired", "cancelled", "rejected")
HISTORY_BARS = 1260
SCAN_BARS = 300
SCAN_CONCURRENCY = 6
UPDATE_OPEN_SECONDS = 300
UPDATE_CLOSED_SECONDS = 1800
MISTAKES = ["moved my stop", "chased the entry", "sold early on a feeling", "ignored the time stop",
            "traded through earnings", "position too big", "no clear thesis", "broke a risk rule"]
DEFAULT_SETTINGS = {
    "risk_pct": 0.005,          # dollars at risk per trade, as a share of equity (0.5%: $500 on $100k)
    "max_heat_pct": 0.03,       # total dollars at risk across every open and working trade
    "max_positions": 5,
    "max_per_sector": 2,
    "min_rr": 1.5,              # target must be at least 1.5x as far as the stop
    "max_new_per_day": 3,
    "block_earnings": True,     # block plans whose hold window likely includes an earnings report
    "min_dollar_volume": 20_000_000.0,
    "r_target": 2.0,
    "valid_days": 3,
    "time_stop_days": 10,
}
LIMITS = {"risk_pct": (0.001, 0.02), "max_heat_pct": (0.005, 0.10), "max_positions": (1, 10), "max_per_sector": (1, 5),
          "min_rr": (1.0, 5.0), "max_new_per_day": (1, 10), "min_dollar_volume": (1_000_000.0, 1e10),
          "r_target": (1.0, 5.0), "valid_days": (1, 10), "time_stop_days": (2, 40)}
TRACK_TRADES = 20
TRACK_FOLLOWED = 90.0
TRACK_LUCK = 80.0
EARNINGS_TTL = 24 * 3600
_earnings_cache: dict[str, tuple[float, dict]] = {}


def _now() -> float:
    return time.time()


def ny_today() -> str:
    return dt.datetime.now(NY).date().isoformat()


def bar_date(c: dict) -> str:
    return dt.datetime.fromtimestamp(int(c["time"]), dt.timezone.utc).date().isoformat()


def sessions_between(d0: str, d1: str) -> int:
    """Trading sessions after d0 up to and including d1."""
    a, b = dt.date.fromisoformat(d0), dt.date.fromisoformat(d1)
    n, d = 0, a + dt.timedelta(days=1)
    while d <= b:
        n += _session(d) is not None
        d += dt.timedelta(days=1)
    return n


def session_key() -> str:
    """The NY session this evening's routine prepares for: today if the market is open, else the next session."""
    st = market_status()
    if st["open"]:
        return ny_today()
    if st["opens_at"]:
        return dt.datetime.fromtimestamp(st["opens_at"], NY).date().isoformat()
    return ny_today()


async def earnings_estimate(symbol: str, hold_days: int) -> dict:
    """Rough next-earnings window from SEC filings: the last earnings 8-K (item 2.02) plus about 91 days, +/-10 days.
    It's an estimate, and it's labelled as one."""
    if universe.is_etf(symbol):
        return {"status": "none", "detail": "ETFs don't report earnings."}
    hit = _earnings_cache.get(symbol)
    if hit and _now() - hit[0] < EARNINGS_TTL:
        est = hit[1]
    else:
        try:
            got = await asyncio.wait_for(research.filings(symbol.replace("-", "."), forms=("8-K",), limit=2,
                                                          earnings_only=True), 15)
            dates = [f["date"] for f in got["filings"]]
        except Exception as exc:  # noqa: BLE001 - unknown is an honest answer
            est = {"status": "unknown", "detail": f"Couldn't read SEC filings ({type(exc).__name__}). Check the "
                                                  "company's investor-relations page for the next report date."}
            _earnings_cache[symbol] = (_now(), est)
            return est
        if not dates:
            est = {"status": "unknown", "detail": "No earnings releases (8-K item 2.02) found at the SEC; foreign "
                                                  "companies file differently. Check the company's own calendar."}
        else:
            last = dt.date.fromisoformat(dates[0])
            nxt = last + dt.timedelta(days=91)
            today = dt.date.fromisoformat(ny_today())
            while nxt < today - dt.timedelta(days=10):
                nxt += dt.timedelta(days=91)
            est = {"status": "estimate", "last": last.isoformat(), "next": nxt.isoformat(),
                   "from": (nxt - dt.timedelta(days=10)).isoformat(), "to": (nxt + dt.timedelta(days=10)).isoformat()}
        _earnings_cache[symbol] = (_now(), est)
    if est["status"] != "estimate":
        return est
    today = dt.date.fromisoformat(ny_today())
    hold_end = today + dt.timedelta(days=math.ceil(hold_days * 7 / 5) + 2)
    overlap = dt.date.fromisoformat(est["from"]) <= hold_end and dt.date.fromisoformat(est["to"]) >= today
    return {**est, "overlap": overlap, "hold_end": hold_end.isoformat(),
            "detail": (f"Last earnings release filed {est['last']}, so the next is likely around {est['next']} "
                       f"(between {est['from']} and {est['to']}). " +
                       ("That's inside this trade's hold window: the price can gap far past your stop on the report."
                        if overlap else "That's after this trade's hold window."))}


class SwingDesk:
    KEY = "swing"

    def __init__(self, engine):
        self.engine = engine
        self.db = engine.db
        self._lock = asyncio.Lock()
        self._regime: dict | None = None
        self._regime_ts = 0.0
        self._last_update = 0.0

    # ---------------------------------------------------------------- stored state
    def data(self) -> dict:
        raw = self.db.get_kv(self.KEY)
        d = {"settings": dict(DEFAULT_SETTINGS), "tickets": [], "next_id": 1, "scan": None, "routine": {},
             "weekly": None}
        if isinstance(raw, dict):
            d.update({k: v for k, v in raw.items() if k != "settings"})
            d["settings"].update(raw.get("settings") or {})
        return d

    def _save(self, d: dict) -> None:
        done = [t for t in d["tickets"] if t["status"] in DONE]
        if len(done) > 500:  # keep every active ticket, trim the oldest finished ones
            drop = {t["id"] for t in sorted(done, key=lambda t: t["created"])[:len(done) - 500]}
            d["tickets"] = [t for t in d["tickets"] if t["id"] not in drop]
        self.db.set_kv(self.KEY, d)

    def settings(self) -> dict:
        return self.data()["settings"]

    def update_settings(self, partial: dict) -> dict:
        d = self.data()
        s = d["settings"]
        for k, v in partial.items():
            if v is None:
                continue
            if k == "block_earnings":
                s[k] = bool(v)
            elif k in LIMITS:
                lo, hi = LIMITS[k]
                val = float(v)
                if not lo <= val <= hi:
                    raise ValueError(f"{k} must be between {lo:g} and {hi:g}")
                s[k] = int(val) if isinstance(DEFAULT_SETTINGS[k], int) else val
            else:
                raise KeyError(f"Unknown setting '{k}'")
        self._save(d)
        return s

    def _ticket(self, d: dict, tid: int) -> dict:
        for t in d["tickets"]:
            if t["id"] == int(tid):
                return t
        raise KeyError(f"Trade plan {tid} not found")

    # ---------------------------------------------------------------- money
    @property
    def route(self) -> str:
        return self.engine.live.route

    async def _account(self) -> dict:
        """Equity and cash the desk sizes from on the current route."""
        if self.route == "sim":
            b = self.engine.broker
            eq = b.equity(self.engine.prices())
            return {"equity": eq, "cash": b.cash, "route": "sim", "max_position_pct": b.limits.max_position_pct,
                    "broker_max_positions": b.limits.max_open_positions, "broker_positions": list(b.positions)}
        s = await self.engine.live.sizing()
        return {"equity": s["equity"], "cash": s["cash"], "route": self.route, "max_position_pct": 0.25,
                "broker_max_positions": 10, "broker_positions": s["positions"], "halted": s["halted"]}

    @staticmethod
    def _qty(risk_dollars: float, entry: float, stop: float, equity: float, max_pos_pct: float, cash: float) -> tuple[int, str]:
        risk = entry - stop
        if risk <= 0:
            return 0, "the stop isn't below the entry"
        by_risk = math.floor(risk_dollars / risk)
        by_size = math.floor(max_pos_pct * equity / entry)
        by_cash = math.floor(max(cash, 0) / entry)
        qty = max(min(by_risk, by_size, by_cash), 0)
        why = (f"${risk_dollars:,.0f} at risk / ${risk:,.2f} a share = {by_risk} shares" +
               (f", capped at {qty} by the position-size limit ({max_pos_pct * 100:.0f}% of the account)"
                if qty == by_size and by_size < by_risk else "") +
               (f", capped at {qty} by your cash" if qty == by_cash and by_cash < min(by_risk, by_size) else ""))
        return qty, why

    # ---------------------------------------------------------------- data
    async def _daily(self, symbol: str, bars: int = HISTORY_BARS) -> list[dict]:
        return await prices.cached_history(symbol, "1d", bars)

    async def _fresh(self, symbol: str) -> list[dict]:
        """Recent daily bars including today's forming one (closed=False) while the market is open."""
        return await prices.history(symbol, "1d", 40)

    async def regime(self) -> dict:
        if self._regime and _now() - self._regime_ts < 3600:
            return self._regime
        try:
            self._regime = swingmath.regime(await self._daily("SPY", SCAN_BARS))
        except Exception as exc:  # noqa: BLE001
            return {"label": "unknown", "detail": f"Couldn't load SPY ({type(exc).__name__})."}
        self._regime_ts = _now()
        return self._regime

    # ---------------------------------------------------------------- 1. scan
    async def scan(self) -> dict:
        syms = universe.symbols()
        st = self.settings()
        sem = asyncio.Semaphore(SCAN_CONCURRENCY)

        async def load(s):
            async with sem:
                try:
                    return s, await self._daily(s, SCAN_BARS)
                except Exception:  # noqa: BLE001
                    return s, None

        results = await asyncio.gather(*(load(s) for s in syms))
        failed, thin, scanned, hits, as_of = [], 0, 0, [], None
        for sym, candles in results:
            if not candles or len(candles) < swingmath.MIN_BARS:
                failed.append(sym)
                continue
            a = swingmath.arrays(candles)
            ind = swingmath.indicators(a)
            i = len(candles) - 1
            dvol = float(ind["dollar_vol20"][i])
            if a["c"][i] < 5 or not dvol >= st["min_dollar_volume"]:
                thin += 1
                continue
            scanned += 1
            as_of = max(as_of or "", bar_date(candles[-1]))
            masks = swingmath.setup_masks(a, ind)
            fired = [k for k, m in masks.items() if m[i]]
            if not fired:
                continue
            info = universe.info(sym)
            plan = swingmath.plan_levels(a, ind, i, st["r_target"])
            hits.append({"symbol": sym, "name": info["name"], "sector": info["sector"], "setups": fired,
                         "close": round(float(a["c"][i]), 2), "dollar_volume": round(dvol),
                         "vs_200": round(float(100 * (a["c"][i] / ind["sma200"][i] - 1)), 1),
                         "rsi": round(float(ind["rsi"][i]), 1),
                         "plan": {k: plan[k] for k in ("entry", "stop", "target", "atr")},
                         "risk_pct": round(100 * plan["risk_per_share"] / plan["entry"], 2)})
        hits.sort(key=lambda h: -h["dollar_volume"])
        out = {"ts": _now(), "as_of": as_of, "universe": len(syms), "loaded": len(syms) - len(failed),
               "failed": failed, "thin": thin, "scanned": scanned, "hits": hits, "regime": await self.regime(),
               "session": session_key(),
               "counts": {k: sum(1 for h in hits if k in h["setups"]) for k in swingmath.SETUPS}}
        d = self.data()
        d["scan"] = out
        self._save(d)
        self.engine.learn.record("swing_scan")
        return out

    # ---------------------------------------------------------------- 2. plan
    def _fingerprint(self, t: dict) -> str:
        key = {k: t.get(k) for k in ("symbol", "entry", "stop", "target", "qty", "valid_days", "time_stop_days")}
        return hashlib.sha256((json.dumps(key, sort_keys=True) + ny_today()).encode()).hexdigest()[:16]

    async def new_plan(self, symbol: str, setup: str, thesis: str, entry: float | None = None,
                       stop: float | None = None, target: float | None = None, r_target: float | None = None,
                       stop_rule: str = "swing", valid_days: int | None = None,
                       time_stop_days: int | None = None) -> dict:
        symbol = symbol.upper().strip()
        if setup not in swingmath.SETUPS and setup != "own":
            raise KeyError(f"Unknown setup '{setup}'. Pick one of: {', '.join(swingmath.SETUPS)}, or 'own'.")
        thesis = (thesis or "").strip()
        if len(thesis) < 15:
            raise ValueError("Write your thesis first: one sentence on why this trade, and what would prove you wrong.")
        st = self.settings()
        r_target = float(r_target or st["r_target"])
        candles = await self._daily(symbol)
        if len(candles) < swingmath.MIN_BARS:
            raise ValueError(f"Not enough daily history for {symbol} (need {swingmath.MIN_BARS} days).")
        a = swingmath.arrays(candles)
        ind = swingmath.indicators(a)
        i = len(candles) - 1
        base = swingmath.plan_levels(a, ind, i, r_target, stop_rule)
        entry = round(float(entry), 2) if entry else base["entry"]
        stop = round(float(stop), 2) if stop else (swingmath.round_down(entry - base["risk_per_share"])
                                                   if entry != base["entry"] else base["stop"])
        if not target:
            target = swingmath.round_up(entry + r_target * (entry - stop))
        target = round(float(target), 2)
        problems = swingmath.validate_plan(entry, stop, target, base["atr"])
        if problems:
            raise ValueError(" ".join(problems))
        acct = await self._account()
        risk_dollars = st["risk_pct"] * acct["equity"]
        qty, qty_why = self._qty(risk_dollars, entry, stop, acct["equity"], acct["max_position_pct"], acct["cash"])
        info = universe.info(symbol)
        masks = swingmath.setup_masks(a, ind)
        d = self.data()
        t = {"id": d["next_id"], "created": _now(), "status": "planned", "symbol": symbol, "name": info["name"],
             "sector": info["sector"], "setup": setup, "setup_fired_today": bool(setup in masks and masks[setup][i]),
             "thesis": thesis[:500], "signal_date": bar_date(candles[-1]), "last_close": round(float(a["c"][i]), 2),
             "entry": entry, "stop": stop, "target": target, "stop_rule": stop_rule, "stop_note": base["stop_note"]
             if (entry, stop) == (base["entry"], base["stop"]) else "your own stop",
             "atr": base["atr"], "dollar_volume": round(float(ind["dollar_vol20"][i])),
             "valid_days": int(valid_days or st["valid_days"]), "time_stop_days": int(time_stop_days or st["time_stop_days"]),
             "qty": qty, "qty_why": qty_why, "risk_dollars": round(risk_dollars, 2), "replay": None, "history": None,
             "check": None, "earnings": await earnings_estimate(symbol, int(time_stop_days or st["time_stop_days"])
                                                                 + int(valid_days or st["valid_days"])),
             "events": [{"ts": _now(), "what": "planned"}], "review": None}
        self._derive(t)
        d["tickets"].append(t)
        d["next_id"] += 1
        self._save(d)
        self.engine.learn.record("swing_plan")
        return t

    @staticmethod
    def _derive(t: dict) -> None:
        risk = t["entry"] - t["stop"]
        t["risk_per_share"] = round(risk, 4)
        t["rr"] = round((t["target"] - t["entry"]) / risk, 2) if risk > 0 else None
        t["max_loss"] = round(t["qty"] * risk, 2)
        t["notional"] = round(t["qty"] * t["entry"], 2)

    async def edit_plan(self, tid: int, changes: dict) -> dict:
        async with self._lock:
            d = self.data()
            t = self._ticket(d, tid)
            if t["status"] != "planned":
                raise ValueError("Only a plan that hasn't been approved can be edited. Once it's live, you can only "
                                 "raise the stop (never lower it).")
            for k in ("entry", "stop", "target"):
                if changes.get(k) is not None:
                    t[k] = round(float(changes[k]), 2)
            for k in ("valid_days", "time_stop_days", "qty"):
                if changes.get(k) is not None:
                    t[k] = max(int(changes[k]), 0)
            if changes.get("thesis") is not None:
                if len(changes["thesis"].strip()) < 15:
                    raise ValueError("Keep a thesis of at least one sentence.")
                t["thesis"] = changes["thesis"].strip()[:500]
            problems = swingmath.validate_plan(t["entry"], t["stop"], t["target"], t["atr"])
            if problems:
                raise ValueError(" ".join(problems))
            if changes.get("qty") is None and any(changes.get(k) is not None for k in ("entry", "stop")):
                acct = await self._account()
                t["qty"], t["qty_why"] = self._qty(t["risk_dollars"], t["entry"], t["stop"], acct["equity"],
                                                   acct["max_position_pct"], acct["cash"])
            elif changes.get("qty") is not None:
                t["qty_why"] = "you set the number of shares"
            self._derive(t)
            if any(changes.get(k) is not None for k in ("entry", "stop", "target", "time_stop_days")):
                t["replay"] = None
            t["check"] = None  # any change needs a fresh risk check
            t["events"].append({"ts": _now(), "what": "edited"})
            self._save(d)
            return t

    # ---------------------------------------------------------------- 3. Time Machine
    async def replay(self, tid: int) -> dict:
        d = self.data()
        t = self._ticket(d, tid)
        candles = await self._daily(t["symbol"])
        slip = self.engine.settings.slippage_bps / 10_000
        a = swingmath.arrays(candles)
        tm = await asyncio.to_thread(swingmath.time_machine, a, t["entry"], t["stop"], t["target"],
                                     t["time_stop_days"], 10_000, 19, 500, slip)
        hist = None
        if t["setup"] in swingmath.SETUPS:
            hist = await asyncio.to_thread(swingmath.setup_history, candles, t["setup"], t["rr"] or 2.0,
                                           t["valid_days"], t["time_stop_days"], t["stop_rule"], slip)
        async with self._lock:
            d = self.data()
            t = self._ticket(d, tid)
            t["replay"], t["history"] = tm, hist
            t["events"].append({"ts": _now(), "what": "replayed"})
            self._save(d)
        self.engine.learn.record("swing_replay")
        return t

    # ---------------------------------------------------------------- 4. risk gate
    async def check(self, tid: int) -> dict:
        d = self.data()
        t = self._ticket(d, tid)
        if t["status"] != "planned":
            raise ValueError("Only plans that haven't been approved yet are checked.")
        st = d["settings"]
        acct = await self._account()
        eq = acct["equity"]
        active = [x for x in d["tickets"] if x["status"] in ACTIVE and x["id"] != t["id"]]
        checks: list[dict] = []

        def add(cid, ok, title, detail, level="block"):
            checks.append({"id": cid, "ok": bool(ok), "level": level, "title": title, "detail": detail})

        problems = swingmath.validate_plan(t["entry"], t["stop"], t["target"], t["atr"])
        add("plan", not problems, "The plan makes sense", " ".join(problems) or
            f"Buy above ${t['entry']:,.2f}, stop ${t['stop']:,.2f}, target ${t['target']:,.2f}.")
        add("thesis", len(t["thesis"]) >= 15, "You wrote a thesis", "Why this trade, and what proves you wrong.")
        add("qty", t["qty"] >= 1, "At least one whole share",
            f"{t['qty']} shares ({t['qty_why']})." if t["qty"] >= 1 else
            f"{t['qty_why']}: that's under one share. Use a closer stop or a cheaper stock.")
        max_loss = st["risk_pct"] * eq
        add("loss", t["max_loss"] <= max_loss * 1.01, "Loss at the stop fits your limit",
            f"{t['qty']} shares x ${t['risk_per_share']:,.2f} = ${t['max_loss']:,.2f} if the stop fills "
            f"(limit ${max_loss:,.0f}, {st['risk_pct'] * 100:.1f}% of ${eq:,.0f}).")
        add("rr", (t["rr"] or 0) >= st["min_rr"], "Reward is worth the risk",
            f"Target is {t['rr']:.2f}x as far as the stop (minimum {st['min_rr']:g}).")
        heat = sum(x["qty"] * max((x.get("fill_price") or x["entry"]) - x["stop"], 0) for x in active)
        add("heat", heat + t["max_loss"] <= st["max_heat_pct"] * eq * 1.01, "Total risk stays small",
            f"Open and waiting trades risk ${heat:,.0f}; with this one ${heat + t['max_loss']:,.0f} "
            f"(limit ${st['max_heat_pct'] * eq:,.0f}, {st['max_heat_pct'] * 100:.0f}% of the account).")
        n_pos = len(active)
        broker_n = len(acct["broker_positions"]) + sum(1 for x in active if x["status"] == "working")
        add("positions", n_pos < st["max_positions"] and broker_n < acct["broker_max_positions"], "Room for another trade",
            f"{n_pos} of {st['max_positions']} swing trades open or waiting"
            + (f"; the paper account allows {acct['broker_max_positions']} positions in total and has {broker_n}"
               if acct["route"] == "sim" else "") + ".")
        same_sector = [x["symbol"] for x in active if x["sector"] == t["sector"]]
        add("sector", len(same_sector) < st["max_per_sector"], "Not too much in one sector",
            f"{len(same_sector)} other {t['sector']} trade(s){': ' + ', '.join(same_sector) if same_sector else ''} "
            f"(limit {st['max_per_sector']}). Stocks in one sector tend to fall together.")
        dup = [x for x in active if x["symbol"] == t["symbol"]] or t["symbol"] in acct["broker_positions"]
        add("duplicate", not dup, "No trade already on this stock",
            "You already hold or are waiting to buy it." if dup else "Nothing open in it.")
        cash_needed = t["notional"] + sum(x["notional"] for x in active if x["status"] == "working")
        add("size", t["notional"] <= acct["max_position_pct"] * eq * 1.001 and cash_needed <= acct["cash"] * 1.001,
            "Position size and cash", f"${t['notional']:,.0f} position (limit ${acct['max_position_pct'] * eq:,.0f}); "
            f"cash ${acct['cash']:,.0f} covers this and any waiting buys (${cash_needed:,.0f}).")
        today = ny_today()
        new_today = sum(1 for x in d["tickets"] if x.get("approved_date") == today)
        add("today", new_today < st["max_new_per_day"], "Not overtrading today",
            f"{new_today} of {st['max_new_per_day']} new trades approved today.")
        add("liquidity", t["dollar_volume"] >= st["min_dollar_volume"], "Easy to buy and sell",
            f"About ${t['dollar_volume'] / 1e6:,.0f}M trades a day (minimum ${st['min_dollar_volume'] / 1e6:,.0f}M).")
        try:
            last = (await self._fresh(t["symbol"]))[-1]["close"]
        except Exception:  # noqa: BLE001
            last = t["last_close"]
        where = f"Price now ${last:,.2f}; entry ${t['entry']:,.2f}. "
        if last > t["entry"] + 0.5 * t["atr"]:
            add("chase", False, "Not chasing", where + "It already ran more than half a normal day's move past your "
                "entry, so the risk to your stop is bigger than you planned. Let it go, or plan a new entry.")
        elif last >= t["entry"]:
            add("chase", False, "Not chasing", where + "It's already above your entry, so the order would fill at "
                "about the current price, a little worse than planned.", "warn")
        else:
            add("chase", True, "Not chasing", where + "The buy-stop waits until the price trades up to your entry.")
        e = t["earnings"] or {"status": "unknown", "detail": ""}
        if e["status"] == "estimate":
            add("earnings", not e["overlap"], "No earnings report during the trade", e["detail"],
                "block" if st["block_earnings"] else "warn")
        elif e["status"] == "unknown":
            add("earnings", False, "Earnings date unknown", e["detail"], "warn")
        reg = await self.regime()
        add("market", reg["label"] != "downtrend", "The market's trend", reg["detail"], "warn")
        tm = t["replay"]
        leans_stop = bool(tm) and ("toward the stop" in tm["verdict"] or tm["avg_r"] <= 0)
        add("replay", tm is not None and not leans_stop, "Time Machine",
            (tm["verdict"] + (" The replays lean against this plan: you may still take it, but know the odds."
                              if leans_stop else "")) if tm else
            "You haven't replayed this plan yet. It's free: do it first.", "warn")
        corr = await self._max_correlation(t["symbol"], [x["symbol"] for x in active])
        if corr:
            add("correlation", corr[1] < 0.8, "Not a copy of a trade you already have",
                f"Moves {corr[1]:.2f} correlated with {corr[0]} over the last 6 months"
                + (": it's close to doubling that bet." if corr[1] >= 0.8 else "."), "warn")
        if self.route == "alpaca_live":
            rec = self.track_record().get(t["setup"]) or {}
            add("track", rec.get("eligible", False), "This setup has a paper track record",
                rec.get("why", f"Real money needs {TRACK_TRADES}+ closed paper trades in this setup with a positive "
                                "average, a luck check and the plan followed."))
        if acct.get("halted"):
            add("halted", False, "Broker account trading", "; ".join(acct["halted"]))
        if not market_status()["open"]:
            add("hours", True, "Market closed", "The order will wait for the next session.", "info")
        blocked = [c for c in checks if not c["ok"] and c["level"] == "block"]
        verdict = "PASS" if not blocked else "BLOCK"
        result = {"verdict": verdict, "checks": checks, "ts": _now(), "ny_date": today,
                  "fingerprint": self._fingerprint(t), "price": last, "route": self.route,
                  "summary": ("Passed every rule. A pass means it's allowed, not that it's a good trade." if not blocked
                              else "Blocked: " + "; ".join(c["title"].lower() for c in blocked) + ".")}
        async with self._lock:
            d = self.data()
            t = self._ticket(d, tid)
            t["check"] = result
            t["events"].append({"ts": _now(), "what": f"risk check {verdict}"})
            self._save(d)
        self.engine.learn.record("swing_check")
        if verdict == "PASS":
            self.engine.learn.record("swing_pass")
        return t

    async def _max_correlation(self, symbol: str, others: list[str]) -> tuple[str, float] | None:
        others = [o for o in others if o != symbol]
        if not others:
            return None
        try:
            series = await asyncio.gather(*(self._daily(s) for s in [symbol, *others]))
        except Exception:  # noqa: BLE001
            return None
        def rets(c):
            x = np.array([b["close"] for b in c[-127:]])
            return np.diff(np.log(x))
        base = rets(series[0])
        best = None
        for sym, c in zip(others, series[1:]):
            r = rets(c)
            n = min(len(base), len(r))
            if n < 30:
                continue
            cc = float(np.corrcoef(base[-n:], r[-n:])[0, 1])
            if best is None or cc > best[1]:
                best = (sym, cc)
        return best

    # ---------------------------------------------------------------- 5. approve and orders
    async def approve(self, tid: int, confirmed: bool = False) -> dict:
        async with self._lock:
            d = self.data()
            t = self._ticket(d, tid)
            if t["status"] != "planned":
                raise ValueError("This plan isn't waiting for approval.")
            chk = t["check"]
            if not chk or chk["verdict"] != "PASS":
                raise ValueError("Run the risk check first: only a plan that passed can be approved.")
            if chk["ny_date"] != ny_today() or chk["fingerprint"] != self._fingerprint(t):
                raise ValueError("The plan or the day changed since the risk check. Check it again.")
            if chk["route"] != self.route:
                raise ValueError("Where orders go changed since the risk check. Check it again.")
            route = self.route
            if route == "alpaca_live" and not confirmed:
                raise ValueError("Real money: confirm this order first.")
            t["route"] = route
            t["approved_at"] = _now()
            t["approved_date"] = ny_today()
            t["status"] = "working"
            if route == "sim":
                try:
                    bars = await self._fresh(t["symbol"])
                    closed = [b for b in bars if b["closed"]]
                    t["processed_through"] = bar_date(closed[-1]) if closed else t["signal_date"]
                except Exception:  # noqa: BLE001
                    t["processed_through"] = t["signal_date"]
                if market_status()["open"]:
                    t["live_day"] = ny_today()  # today's earlier prices happened before you approved
                t["sessions_waited"] = 0
            else:
                try:
                    row = await self.engine.live.swing_entry(t, confirmed=confirmed)
                except Exception:
                    t["status"], t["route"] = "planned", None
                    t.pop("approved_at", None)
                    t.pop("approved_date", None)
                    raise
                t["entry_order"] = row["id"]
            t["events"].append({"ts": _now(), "what": f"approved: buy-stop {t['qty']} at ${t['entry']:,.2f} ({route})"})
            self._save(d)
        self.engine.learn.record("swing_approve")
        await self.engine.log("warning" if route == "alpaca_live" else "info",
                              f"SWING approved #{t['id']}: buy {t['qty']} {t['symbol']} above {t['entry']:.2f}, "
                              f"stop {t['stop']:.2f}, target {t['target']:.2f} ({route})")
        await self._broadcast()
        if route == "sim":
            await self.update(force=True)
            t = self._ticket(self.data(), tid)
        return t

    async def cancel(self, tid: int) -> dict:
        async with self._lock:
            d = self.data()
            t = self._ticket(d, tid)
            if t["status"] == "planned":
                t["status"] = "cancelled"
            elif t["status"] == "working":
                if t.get("route", "sim") != "sim":
                    await self.engine.live.swing_cancel_entry(t)
                t["status"] = "cancelled"
            else:
                raise ValueError("Only plans and waiting orders can be cancelled. To get out of an open trade, close it.")
            t["events"].append({"ts": _now(), "what": "cancelled"})
            self._save(d)
        await self._broadcast()
        return t

    async def close_now(self, tid: int) -> dict:
        """Sell an open trade now, outside the plan."""
        async with self._lock:
            d = self.data()
            t = self._ticket(d, tid)
            if t["status"] != "open":
                raise ValueError("Only open trades can be closed.")
            if t["route"] == "sim":
                px = self.engine.prices().get(t["symbol"])
                if not px:
                    bars = await self._fresh(t["symbol"])
                    px = bars[-1]["close"]
                await self._exit_sim(t, float(px), "manual", "you closed it by hand")
            else:
                await self.engine.live.swing_exit_now(t)
                t["status"] = "closing"
                t["closing_reason"] = "manual"
            self._save(d)
        await self._broadcast()
        return t

    async def raise_stop(self, tid: int, new_stop: float) -> dict:
        """Move the stop up (never down): lock in some gain or cut the risk."""
        async with self._lock:
            d = self.data()
            t = self._ticket(d, tid)
            if t["status"] not in ("open", "working"):
                raise ValueError("Only working or open trades have a live stop.")
            new_stop = round(float(new_stop), 2)
            if new_stop <= t["stop"]:
                raise ValueError(f"A stop can only move up (it's ${t['stop']:,.2f}). Moving it down means risking more "
                                 "than you planned: that's the most common way small losses become big ones.")
            ref = self.engine.prices().get(t["symbol"]) or t.get("last_price") or t["entry"]
            if t["status"] == "open" and new_stop >= ref:
                raise ValueError(f"The stop must stay below the current price (${ref:,.2f}).")
            if t["status"] == "working" and new_stop >= t["entry"]:
                raise ValueError("The stop must stay below the entry.")
            if t["route"] != "sim" and t["status"] == "open":
                await self.engine.live.swing_move_stop(t, new_stop)
            t["stop_history"] = (t.get("stop_history") or []) + [{"ts": _now(), "from": t["stop"], "to": new_stop}]
            t["stop"] = new_stop
            if t["route"] == "sim" and t["status"] == "open" and t["symbol"] in self.engine.broker.positions:
                async with self.engine.lock:
                    self.engine.broker.set_brackets(t["symbol"], new_stop, t["target"])
                    self.engine.persist_broker()
            t["events"].append({"ts": _now(), "what": f"stop raised to ${new_stop:,.2f}"})
            self._save(d)
        self.engine.learn.record("swing_raise_stop")
        await self._broadcast()
        return t

    # ---------------------------------------------------------------- the paper account's order handling
    async def _fill_sim(self, t: dict, px: float, date: str, by_tick: bool) -> None:
        eng = self.engine
        async with eng.lock:
            try:
                fill = eng.broker.open_long(t["symbol"], px, _now(), strategy=f"swing:{t['setup']}", qty=t["qty"],
                                            reason="swing")
            except RiskError as exc:
                t["status"] = "rejected"
                t["events"].append({"ts": _now(), "what": f"the paper account refused the buy: {exc}"})
                await eng.log("warning", f"SWING #{t['id']} buy {t['symbol']} refused: {exc}")
                return
            eng.broker.set_brackets(t["symbol"], t["stop"], t["target"])
            out = await eng._record_fill(fill)
        t.update({"status": "open", "fill_price": fill["price"], "fill_ts": fill["ts"], "fill_date": date,
                  "fill_by_tick": by_tick, "fill_trade_id": out["id"], "held_sessions": 0, "stop_planned": t["stop"],
                  "low_since": fill["price"], "high_since": fill["price"]})
        t["events"].append({"ts": _now(), "what": f"bought {t['qty']} at ${fill['price']:,.2f}"})
        eng.learn.record("swing_fill")
        await eng.hub.broadcast("swing_alert", {"id": t["id"], "text": f"Entry filled: {t['qty']} {t['symbol']} at "
                                                                         f"${fill['price']:,.2f}"})

    async def _exit_sim(self, t: dict, px: float, outcome: str, why: str) -> None:
        eng = self.engine
        reason = {"target": "take_profit", "stop": "stop_loss", "gap_stop": "stop_loss", "time": "time_stop",
                  "manual": "manual"}[outcome]
        async with eng.lock:
            if t["symbol"] in eng.broker.positions:
                fill = eng.broker.close(t["symbol"], px, _now(), reason=reason)
                await eng._record_fill(fill)
                exit_px, pnl = fill["price"], fill["pnl"]
            else:
                exit_px, pnl = px, (px - t["fill_price"]) * t["qty"]
        self._finish(t, exit_px, pnl, outcome, why)
        await eng.hub.broadcast("swing_alert", {"id": t["id"], "text": f"{t['symbol']} closed ({why}): "
                                                                         f"{t['r_multiple']:+.2f}R"})

    def _finish(self, t: dict, exit_px: float, pnl: float, outcome: str, why: str) -> None:
        risk = t["entry"] - t["stop_planned"] if t.get("stop_planned") else t["entry"] - t["stop"]
        t.update({"status": "closed", "exit_price": exit_px, "exit_ts": _now(), "exit_reason": outcome,
                  "exit_why": why, "pnl": round(pnl, 2), "r_multiple": round((exit_px - t["fill_price"]) / risk, 3),
                  "followed_plan": outcome != "manual"})
        if t.get("low_since") is not None:
            t["mae_r"] = round((t["low_since"] - t["fill_price"]) / risk, 2)
            t["mfe_r"] = round((t["high_since"] - t["fill_price"]) / risk, 2)
        t["events"].append({"ts": _now(), "what": f"closed: {why}, {t['r_multiple']:+.2f}R (${pnl:,.2f})"})
        self.engine.learn.record("swing_close")

    async def _reconcile_sim(self, t: dict) -> bool:
        """The paper position was sold elsewhere (Practice screen, or the engine's live stop). Close the ticket from
        the trade history. Returns True if it did."""
        if t["symbol"] in self.engine.broker.positions:
            return False
        row = self.db.one("SELECT price, pnl, reason FROM trades WHERE symbol = ? AND side = 'sell' AND ts >= ? "
                          "ORDER BY ts DESC LIMIT 1", (t["symbol"], t["fill_ts"]))
        if row is None:
            return False
        outcome = {"take_profit": "target", "stop_loss": "stop", "trailing_stop": "stop"}.get(row["reason"], "manual")
        self._finish(t, float(row["price"]), float(row["pnl"] or 0), outcome,
                     {"target": "hit the target", "stop": "hit the stop"}.get(outcome, "sold outside the desk"))
        return True

    def _track_range(self, t: dict, low: float, high: float) -> None:
        t["low_since"] = min(t.get("low_since", low), low)
        t["high_since"] = max(t.get("high_since", high), high)

    async def _process_bar(self, t: dict, b: dict, date: str) -> None:
        """One completed daily bar for a sim ticket."""
        o, h, lo, c = b["open"], b["high"], b["low"], b["close"]
        if t["status"] == "working":
            t["sessions_waited"] = t.get("sessions_waited", 0) + 1
            if date == t.get("live_day"):  # approved mid-session: only the close is known to come after approval
                if c >= t["entry"]:
                    await self._fill_sim(t, c, date, by_tick=True)
            elif h >= t["entry"]:
                await self._fill_sim(t, max(t["entry"], o), date, by_tick=False)
                if t["status"] == "open":
                    self._track_range(t, lo, h)
                    if lo <= t["stop"]:
                        await self._exit_sim(t, t["stop"], "stop", "hit the stop on the day it filled")
                    elif c >= t["target"]:
                        await self._exit_sim(t, t["target"], "target", "hit the target on the day it filled")
                return
            if t["status"] == "working" and t["sessions_waited"] >= t["valid_days"]:
                t["status"] = "expired"
                t["events"].append({"ts": _now(), "what": f"expired: the price never reached ${t['entry']:,.2f} in "
                                                          f"{t['valid_days']} sessions"})
            return
        if t["status"] != "open":
            return
        if date == t.get("fill_date"):  # filled during this session by a live price: only the close is after the fill
            self._track_range(t, min(c, t["fill_price"]), max(c, t["fill_price"]))
            if c <= t["stop"]:
                await self._exit_sim(t, c, "stop", "closed under the stop on the day it filled")
            return
        self._track_range(t, lo, h)
        t["held_sessions"] = t.get("held_sessions", 0) + 1
        if o <= t["stop"]:
            await self._exit_sim(t, o, "gap_stop", "gapped down through the stop overnight")
        elif o >= t["target"]:
            await self._exit_sim(t, o, "target", "gapped up over the target")
        elif lo <= t["stop"]:
            await self._exit_sim(t, t["stop"], "stop", "hit the stop")
        elif h >= t["target"]:
            await self._exit_sim(t, t["target"], "target", "hit the target")
        elif t["held_sessions"] >= t["time_stop_days"]:
            await self._exit_sim(t, c, "time", f"time stop: {t['time_stop_days']} sessions without reaching a side")

    async def _process_tick(self, t: dict, px: float, today: str) -> None:
        """A live price during today's session (the forming bar)."""
        if t["status"] == "working" and px >= t["entry"]:
            await self._fill_sim(t, px, today, by_tick=True)
            return
        if t["status"] != "open":
            return
        self._track_range(t, px, px)
        if px <= t["stop"]:
            await self._exit_sim(t, px, "stop", "hit the stop")
        elif px >= t["target"]:
            await self._exit_sim(t, t["target"], "target", "hit the target")

    async def _update_sim(self, t: dict) -> None:
        if t["status"] == "open" and await self._reconcile_sim(t):
            return
        bars = await self._fresh(t["symbol"])
        for b in bars:
            if not b["closed"]:
                continue
            date = bar_date(b)
            if date <= t.get("processed_through", ""):
                continue
            await self._process_bar(t, b, date)
            t["processed_through"] = date
            if t["status"] not in ACTIVE:
                return
        last = bars[-1] if bars else None
        if last is not None:
            t["last_price"] = last["close"]
            if t["status"] == "open":
                self.engine.broker.equity({t["symbol"]: float(last["close"])})  # mark the position
        if last is not None and not last["closed"] and market_status()["open"]:
            await self._process_tick(t, float(last["close"]), ny_today())

    async def _update_broker(self, t: dict) -> None:
        """Follow a ticket's orders at Alpaca (live.sync refreshes their status)."""
        live = self.engine.live
        today = ny_today()
        if t["status"] == "working":
            row = live.order_row(t["entry_order"])
            if row and row["status"] == "filled":
                t.update({"status": "open", "fill_price": row["avg_price"], "fill_ts": _now(), "fill_date": today,
                          "stop_planned": t["stop"], "held_sessions": 0, "low_since": row["avg_price"],
                          "high_since": row["avg_price"], "qty": int(row["filled_qty"])})
                self._derive(t)
                exits = await live.swing_exits(t)
                t["tp_order"], t["sl_order"] = exits["tp"], exits["sl"]
                t["events"].append({"ts": _now(), "what": f"bought {t['qty']} at ${row['avg_price']:,.2f} at Alpaca; "
                                                          "stop and target placed"})
                self.engine.learn.record("swing_fill")
            elif row and row["status"] in ("canceled", "expired", "rejected"):
                t["status"] = "cancelled" if row["status"] != "rejected" else "rejected"
                t["events"].append({"ts": _now(), "what": f"Alpaca: entry order {row['status']}"})
            elif sessions_between(t["approved_date"], today) >= t["valid_days"] and market_status()["open"]:
                await live.swing_cancel_entry(t)
                t["status"] = "expired"
                t["events"].append({"ts": _now(), "what": f"expired after {t['valid_days']} sessions"})
            return
        if t["status"] == "open":
            for key, outcome, why in (("tp_order", "target", "hit the target"), ("sl_order", "stop", "hit the stop")):
                row = live.order_row(t.get(key))
                if row and row["status"] == "filled":
                    self._finish(t, row["avg_price"], (row["avg_price"] - t["fill_price"]) * t["qty"], outcome, why)
                    return
            try:
                bars = await self._fresh(t["symbol"])
                after = [b for b in bars if bar_date(b) > t["fill_date"]]
                for b in after:
                    self._track_range(t, b["low"], b["high"])
                t["last_price"] = bars[-1]["close"] if bars else t.get("last_price")
            except Exception:  # noqa: BLE001
                pass
            t["held_sessions"] = sessions_between(t["fill_date"], today)
            if t["held_sessions"] >= t["time_stop_days"] and market_status()["open"]:
                row = await live.swing_exit_now(t)
                t.update({"status": "closing", "closing_reason": "time", "exit_order": row["id"]})
                t["events"].append({"ts": _now(), "what": "time stop: sell sent"})
            return
        if t["status"] == "closing":
            row = live.order_row(t.get("exit_order"))
            if row and row["status"] == "filled":
                reason = t.get("closing_reason", "manual")
                self._finish(t, row["avg_price"], (row["avg_price"] - t["fill_price"]) * t["qty"], reason,
                             "time stop" if reason == "time" else "you closed it by hand")

    async def update(self, force: bool = False) -> dict:
        """Move every working and open ticket forward. Safe to call any time."""
        changed = 0
        async with self._lock:
            d = self.data()
            for t in d["tickets"]:
                if t["status"] not in ACTIVE:
                    continue
                before = (t["status"], t.get("processed_through"), t.get("last_price"))
                try:
                    if t.get("route", "sim") == "sim":
                        await self._update_sim(t)
                    else:
                        await self._update_broker(t)
                except Exception as exc:  # noqa: BLE001 - one ticket failing mustn't stop the others
                    logger.warning("Swing update for #%s failed: %r", t["id"], exc)
                changed += before != (t["status"], t.get("processed_through"), t.get("last_price"))
            self._save(d)
            self._last_update = _now()
        if changed:
            self.engine.persist_broker()
            await self._broadcast()
        return {"changed": changed}

    async def loop(self) -> None:
        while True:
            await asyncio.sleep(60)
            try:
                if not any(t["status"] in ACTIVE for t in self.data()["tickets"]):
                    continue
                wait = UPDATE_OPEN_SECONDS if market_status()["open"] else UPDATE_CLOSED_SECONDS
                if _now() - self._last_update >= wait:
                    await self.update()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.warning("Swing loop error: %r", exc)

    async def _broadcast(self) -> None:
        await self.engine.hub.broadcast("swing", {"changed": True})

    # ---------------------------------------------------------------- 6. journal and edge review
    def review(self, tid: int, note: str, followed_plan: bool, mistakes: list[str] | None = None) -> dict:
        d = self.data()
        t = self._ticket(d, tid)
        if t["status"] != "closed":
            raise ValueError("Review a trade once it has closed.")
        note = (note or "").strip()
        if len(note) < 10:
            raise ValueError("Write at least a sentence: what happened, and what you'd repeat or change.")
        bad = [m for m in (mistakes or []) if m not in MISTAKES]
        if bad:
            raise ValueError(f"Unknown mistake tag: {bad[0]}")
        t["review"] = {"ts": _now(), "note": note[:1000], "followed_plan": bool(followed_plan),
                       "mistakes": list(dict.fromkeys(mistakes or []))}
        t["followed_plan"] = bool(followed_plan) and not mistakes
        self._save(d)
        self.engine.learn.record("swing_review", t["id"])
        return t

    def _closed(self, d: dict | None = None) -> list[dict]:
        d = d or self.data()
        return sorted([t for t in d["tickets"] if t["status"] == "closed"], key=lambda t: t["exit_ts"])

    def journal(self) -> dict:
        d = self.data()
        closed = self._closed(d)
        rows = [{"r": t["r_multiple"], "pnl": t["pnl"], "followed_plan": t.get("followed_plan")} for t in closed]
        by_setup = {}
        for s in sorted({t["setup"] for t in closed}):
            sub = [t for t in closed if t["setup"] == s]
            by_setup[s] = swingmath.edge_stats([{"r": t["r_multiple"], "pnl": t["pnl"],
                                                 "followed_plan": t.get("followed_plan")} for t in sub])
            replayed = [t["replay"]["avg_r"] for t in sub if t.get("replay")]
            by_setup[s]["expected_r"] = round(float(np.mean(replayed)), 3) if replayed else None
        by_exit = {}
        for t in closed:
            by_exit[t["exit_reason"]] = by_exit.get(t["exit_reason"], 0) + 1
        mistakes = {}
        for t in closed:
            for m in (t.get("review") or {}).get("mistakes", []):
                mistakes[m] = mistakes.get(m, 0) + 1
        curve, total = [], 0.0
        for t in closed:
            total += t["r_multiple"]
            curve.append({"time": int(t["exit_ts"]), "value": round(total, 3)})
        followed = [r for r in rows if r["followed_plan"]]
        broke = [r for r in rows if r["followed_plan"] is False]
        return {"overall": swingmath.edge_stats(rows), "by_setup": by_setup, "by_exit": by_exit, "mistakes": mistakes,
                "curve": curve, "unreviewed": [t["id"] for t in closed if not t.get("review")],
                "followed": swingmath.edge_stats(followed) if followed else None,
                "broke_plan": swingmath.edge_stats(broke) if broke else None,
                "track_record": self.track_record(d), "lesson": self._edge_lesson(rows, by_setup)}

    @staticmethod
    def _edge_lesson(rows: list[dict], by_setup: dict) -> str:
        n = len(rows)
        if n == 0:
            return "No closed trades yet. Your journal starts with your first one."
        if n < 20:
            return (f"{n} closed trade{'s' if n != 1 else ''}. Under 20 trades, a few lucky or unlucky results swing "
                    "every number here: keep going before you judge a setup.")
        best = max(by_setup.items(), key=lambda kv: kv[1].get("expectancy_r") or -9)
        worst = min(by_setup.items(), key=lambda kv: kv[1].get("expectancy_r") or 9)
        if best[0] == worst[0]:
            return f"All your trades are {best[0]} trades. Expectancy {best[1]['expectancy_r']:+.2f}R a trade."
        return (f"Your best setup so far is {swingmath.SETUPS.get(best[0], {}).get('name', best[0])} "
                f"({best[1]['expectancy_r']:+.2f}R a trade over {best[1]['trades']}); your weakest is "
                f"{swingmath.SETUPS.get(worst[0], {}).get('name', worst[0])} ({worst[1]['expectancy_r']:+.2f}R over "
                f"{worst[1]['trades']}). Doing more of what works is the whole game.")

    def track_record(self, d: dict | None = None) -> dict:
        """Per setup: is the paper record good enough to trade with real money? (used by Go live)"""
        out = {}
        closed = [t for t in self._closed(d) if t.get("route", "sim") in ("sim", "alpaca_paper")]
        for s in sorted({t["setup"] for t in closed}):
            sub = [t for t in closed if t["setup"] == s]
            st = swingmath.edge_stats([{"r": t["r_multiple"], "pnl": t["pnl"], "followed_plan": t.get("followed_plan")}
                                       for t in sub])
            reasons = []
            if st["trades"] < TRACK_TRADES:
                reasons.append(f"{st['trades']} of {TRACK_TRADES} closed paper trades")
            if (st.get("expectancy_r") or 0) <= 0:
                reasons.append(f"average {st.get('expectancy_r', 0):+.2f}R a trade (needs above 0)")
            if (st.get("positive_in_resamples_pct") or 0) < TRACK_LUCK:
                reasons.append(f"luck check {st.get('positive_in_resamples_pct') or 0:.0f}% (needs {TRACK_LUCK:.0f}%)")
            if (st.get("followed_pct") or 0) < TRACK_FOLLOWED:
                reasons.append(f"plan followed {st.get('followed_pct') or 0:.0f}% (needs {TRACK_FOLLOWED:.0f}%)")
            out[s] = {**st, "eligible": not reasons,
                      "why": "Passed: enough trades, a positive average, the luck check and the plan followed."
                      if not reasons else "Not yet: " + "; ".join(reasons) + "."}
        return out

    def weekly(self) -> dict:
        d = self.data()
        since = _now() - 7 * 86400
        week = [t for t in self._closed(d) if t["exit_ts"] >= since]
        stats = swingmath.edge_stats([{"r": t["r_multiple"], "pnl": t["pnl"], "followed_plan": t.get("followed_plan")}
                                      for t in week])
        planned = [t for t in d["tickets"] if t["created"] >= since]
        points = []
        if not week:
            points.append("No trades closed this week. That's fine: no setup is better than a forced one.")
        else:
            points.append(f"{len(week)} trade(s) closed: {stats['wins']} won, {stats['losses']} lost, "
                          f"{stats['total_r']:+.2f}R in total (${stats['pnl']:,.2f}).")
            best = max(week, key=lambda t: t["r_multiple"])
            worst = min(week, key=lambda t: t["r_multiple"])
            points.append(f"Best: {best['symbol']} {best['r_multiple']:+.2f}R ({best['exit_why']}). "
                          f"Worst: {worst['symbol']} {worst['r_multiple']:+.2f}R ({worst['exit_why']}).")
            if worst["r_multiple"] < -1.2:
                points.append(f"{worst['symbol']} lost more than 1R: a gap or a fast market went past the stop. "
                              "That's why each trade risks only a small slice of the account.")
            if stats.get("followed_pct") is not None:
                points.append(f"You followed your plan on {stats['followed_pct']:.0f}% of them.")
        unrev = [t["symbol"] for t in self._closed(d) if not t.get("review")]
        if unrev:
            points.append(f"Still to review in your journal: {', '.join(unrev[:6])}.")
        ex = sum(1 for t in planned if t["status"] == "expired")
        if ex:
            points.append(f"{ex} buy-stop(s) expired without triggering. The stock didn't prove itself, and you "
                          "risked nothing: that's the entry rule working.")
        tip = ("Next week: before each approval, read your thesis out loud and ask what would prove it wrong."
               if not week or stats.get("followed_pct", 100) >= 90 else
               "Next week: one rule only. Don't touch a trade between the approval and the exit unless you're raising "
               "the stop.")
        out = {"ts": _now(), "closed": len(week), "stats": stats, "planned": len(planned), "points": points, "tip": tip}
        d["weekly"] = out
        self._save(d)
        self.engine.learn.record("swing_weekly")
        return out

    # ---------------------------------------------------------------- 7. the evening routine
    def routine(self, d: dict | None = None) -> dict:
        d = d or self.data()
        key = session_key()
        r = d["routine"].get(key) or {}
        scan = d["scan"]
        recent = _now() - 20 * 3600
        closed = self._closed(d)
        items = [
            {"id": "market", "title": "Check the market's trend", "auto": False, "done": bool(r.get("market")),
             "how": "Read the market box: is SPY in an uptrend, mixed, or a downtrend?"},
            {"id": "review", "title": "Review open trades and waiting orders", "auto": False,
             "done": bool(r.get("review")),
             "how": "Anything stopped, hit a target, or close to its time stop? Raise a stop if your plan says so."},
            {"id": "scan", "title": "Run the scan", "auto": True, "done": bool(scan and scan["ts"] >= recent),
             "how": "Scan for today's setups."},
            {"id": "plan", "title": "Plan a trade, or decide not to", "auto": False,
             "done": bool(r.get("no_trade")) or any(t["created"] >= recent for t in d["tickets"]),
             "how": "Write a plan for the best candidate (thesis, Time Machine, risk check), or press 'No trade today'."},
            {"id": "journal", "title": "Journal every closed trade", "auto": True,
             "done": all(t.get("review") for t in closed), "how": "Review each closed trade in the Journal."},
        ]
        done = all(i["done"] for i in items)
        if done and not r.get("completed"):
            r["completed"] = _now()
            d["routine"][key] = r
            d["routine"] = dict(sorted(d["routine"].items())[-60:])
            self._save(d)
            self.engine.learn.record("swing_routine", key)
        st = market_status()
        return {"session": key, "items": items, "done": done, "opens_at": st["opens_at"], "closes_at": st["closes_at"],
                "market_open": st["open"], "label": st["label"]}

    def tick_routine(self, item: str) -> dict:
        if item not in ("market", "review", "no_trade"):
            raise ValueError("That routine step is ticked automatically.")
        d = self.data()
        key = session_key()
        r = d["routine"].setdefault(key, {})
        r[item] = _now()
        self._save(d)
        return self.routine()

    # ---------------------------------------------------------------- the screen
    async def state(self) -> dict:
        d = self.data()
        tickets = sorted(d["tickets"], key=lambda t: -t["created"])
        active = [t for t in tickets if t["status"] in ACTIVE]
        heat = sum(t["qty"] * max((t.get("fill_price") or t["entry"]) - t["stop"], 0) for t in active)
        try:
            acct = await self._account()
        except Exception as exc:  # noqa: BLE001
            acct = {"equity": None, "cash": None, "route": self.route, "error": str(exc)}
        return {"settings": d["settings"], "route": self.route, "account": {k: acct.get(k) for k in ("equity", "cash", "route")},
                "heat": round(heat, 2), "plans": [t for t in tickets if t["status"] == "planned"][:20],
                "active": active, "recent": [t for t in tickets if t["status"] in DONE][:40],
                "scan": d["scan"], "regime": await self.regime(), "routine": self.routine(d),
                "weekly": d["weekly"], "setups": swingmath.SETUPS, "mistakes": MISTAKES,
                "rules": {"track_trades": TRACK_TRADES, "track_followed": TRACK_FOLLOWED, "track_luck": TRACK_LUCK}}
