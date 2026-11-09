"""Trading agent and capstone (Level 13).

Each run, for every symbol on the watchlist:
  1. Research: score recent headlines (LLM, cached).
  2. Signal: your strategy (built-in or one you wrote in the Code Lab) on daily candles: hold (1) or cash (0).
  3. Risk check (rules, not AI): size so a stop-loss 2 daily standard deviations away loses `risk_pct` of equity,
     capped by the safety net; respect max positions, the daily loss limit and market hours.
  4. AI check: the LLM reads the facts and may flag a concern. It never changes the size or invents a trade.
  5. Proposal: waits for your approval (default) or, in auto mode, executes if nothing was flagged.
Every step is logged. The kill switch stops the agent, cancels proposals and waiting orders, and can sell its positions.

Capstone: start a 4-week run, then get a report of your paper account vs SPY with an AI-drafted write-up whose numbers
are checked against the facts.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import time
from datetime import datetime, timezone

import numpy as np

from app import assets, prices
from app.broker import RiskError
from app.strategies import STRATEGIES, make_strategy

logger = logging.getLogger("app.agent")

DEFAULT = {"enabled": False, "mode": "approve", "watchlist": ["SPY", "QQQ", "GLD", "BTCUSDT"],
           "strategy": {"type": "builtin", "name": "sma_cross", "params": {"fast": 20, "slow": 100}},
           "interval_min": 60, "risk_pct": 0.01, "use_research": True, "last_run": 0.0}
MAX_LOG = 40
CAPSTONE_DAYS = 28


def _now() -> float:
    return time.time()


class Agent:
    def __init__(self, engine):
        self.engine = engine
        self.db = engine.db
        self._running = False

    # ---------------------------------------------------------------- config
    def config(self) -> dict:
        raw = self.db.get_kv("agent")
        out = json.loads(json.dumps(DEFAULT))
        if isinstance(raw, dict):
            out.update({k: v for k, v in raw.items() if k in DEFAULT})
        return out

    def update(self, partial: dict) -> dict:
        cfg = self.config()
        if "watchlist" in partial and partial["watchlist"] is not None:
            wl = list(dict.fromkeys(self.engine.check_symbol(s) for s in partial["watchlist"]))
            if not 1 <= len(wl) <= 8:
                raise ValueError("Pick 1 to 8 assets for the agent to watch.")
            cfg["watchlist"] = wl
        if partial.get("strategy") is not None:
            st = partial["strategy"]
            if st.get("type") == "custom":
                if st.get("name") not in self.engine.custom_strategies():
                    raise ValueError("Save that strategy in the Code Lab first.")
                cfg["strategy"] = {"type": "custom", "name": st["name"]}
            else:
                strat = make_strategy(st.get("name", "sma_cross"), st.get("params") or {})
                cfg["strategy"] = {"type": "builtin", "name": strat.name, "params": dict(strat.params)}
        if partial.get("mode") in ("approve", "auto"):
            if partial["mode"] == "auto" and self.engine.learn.level() < 13:
                raise ValueError("Auto mode unlocks at Level 13.")
            cfg["mode"] = partial["mode"]
        if partial.get("interval_min") is not None:
            cfg["interval_min"] = int(min(max(int(partial["interval_min"]), 15), 1440))
        if partial.get("risk_pct") is not None:
            r = float(partial["risk_pct"])
            if not 0.001 <= r <= 0.03:
                raise ValueError("Risk per trade must be between 0.1% and 3% of the account.")
            cfg["risk_pct"] = r
        if partial.get("use_research") is not None:
            cfg["use_research"] = bool(partial["use_research"])
        if partial.get("enabled") is not None:
            cfg["enabled"] = bool(partial["enabled"])
            if cfg["enabled"]:
                self.engine.learn.record("agent_on")
        self.db.set_kv("agent", cfg)
        return self.state()

    def proposals(self) -> list[dict]:
        raw = self.db.get_kv("agent_proposals")
        return raw if isinstance(raw, list) else []

    def _save_proposals(self, items: list[dict]) -> None:
        self.db.set_kv("agent_proposals", items[-60:])

    def runs(self) -> list[dict]:
        raw = self.db.get_kv("agent_runs")
        return raw if isinstance(raw, list) else []

    def state(self) -> dict:
        cfg = self.config()
        pending = [p for p in self.proposals() if p["status"] == "pending"]
        nxt = cfg["last_run"] + cfg["interval_min"] * 60 if cfg["enabled"] else None
        return {"config": cfg, "pending": pending, "recent": [p for p in self.proposals() if p["status"] != "pending"][-15:][::-1],
                "runs": self.runs()[-10:][::-1], "running": self._running, "next_run": nxt,
                "strategies": [{"type": "builtin", "name": n} for n in STRATEGIES]
                + [{"type": "custom", "name": n} for n in self.engine.custom_strategies()],
                "ai_route": self.engine.llm.route("agent")}

    # ---------------------------------------------------------------- one run
    async def _signal(self, symbol: str, candles: list[dict]) -> tuple[int | None, str]:
        st = self.config()["strategy"]
        if st["type"] == "custom":
            from app.codelab import run_code, strategy_harness

            code = self.engine.custom_strategies()[st["name"]]["code"]
            run = await run_code(code, extra=strategy_harness(symbol))
            if not run["ok"] or not run["result"]:
                return None, f"your strategy '{st['name']}' failed: {(run['error'] or '')[-160:]}"
            pos = run["result"]["positions"][-1]
            return int(pos >= 0.5), f"your strategy '{st['name']}' says {'hold' if pos >= 0.5 else 'cash'}"
        strat = make_strategy(st["name"], st.get("params"))
        sig = strat.signals(candles)
        state = None
        for s in sig:  # replay the signals to know whether the strategy is currently in or out
            if s == "buy":
                state = 1
            elif s == "sell":
                state = 0
        if state is None:
            return None, f"{st['name']} hasn't given a signal yet"
        last = next((s for s in reversed(sig) if s), None)
        return state, f"{st['name'].replace('_', ' ')} is {'in (last signal: buy)' if state else 'out (last signal: sell)'}" \
            if last else f"{st['name']} state {state}"

    @staticmethod
    def _rule_check(facts: dict) -> dict:
        """The no-AI risk review: plain rules on the same facts."""
        concerns = []
        news = facts.get("news_average")
        if facts["side"] == "buy" and news is not None and news <= -0.3:
            concerns.append(f"recent headlines lean negative (score {news:+.2f})")
        if facts["side"] == "buy" and facts.get("vol_ratio") and facts["vol_ratio"] >= 1.8:
            concerns.append(f"it's {facts['vol_ratio']:.1f}x as jumpy as its 1-year norm")
        if not facts.get("market_open"):
            concerns.append("the US market is closed, so a real order would wait for the open")
        note = ("Rule check: " + "; ".join(concerns) + ".") if concerns else \
            "Rule check: no red flags (news not clearly negative, volatility near normal, market open)."
        flag = any(not c.startswith("the US market") for c in concerns)
        return {"concern": flag, "note": note, "source": "rules", "cost_usd": 0.0}

    async def _ai_check(self, facts: dict) -> dict:
        if not self.engine.llm.enabled:
            return self._rule_check(facts)
        system = ("You are the risk reviewer for a paper-trading agent run by a learner. You get facts about ONE proposed "
                  "trade. Flag a concern only for a concrete reason in the facts (for example very negative recent news, "
                  "the market being closed, unusually high volatility, or a signal that contradicts the news). You can't "
                  "change the size or suggest other trades. Respond with ONLY JSON: "
                  '{"concern": true|false, "note": "1-2 plain sentences that only use the facts given"}')
        try:
            reply = await self.engine.llm.chat("agent", [{"role": "system", "content": system},
                                                         {"role": "user", "content": json.dumps(facts, default=str)}],
                                               max_tokens=250, temperature=0.1)
            j = reply["json"] or {}
            return {"concern": bool(j.get("concern")), "note": str(j.get("note", ""))[:300], "source": reply["source"],
                    "cost_usd": reply["cost_usd"]}
        except Exception as exc:  # noqa: BLE001 - the AI check is advisory
            return {"concern": False, "note": f"AI check unavailable ({type(exc).__name__}); rules only.", "source": "none",
                    "cost_usd": 0.0}

    async def run_once(self, manual: bool = False) -> dict:
        if self._running:
            raise ValueError("The agent is already running.")
        self._running = True
        cfg = self.config()
        steps, new = [], []
        try:
            eng = self.engine
            acct = eng.account()
            equity = acct["equity"]
            limits = eng.broker.limits
            pending_syms = {p["symbol"] for p in self.proposals() if p["status"] == "pending"}
            for symbol in cfg["watchlist"]:
                step = {"symbol": symbol, "name": assets.name(symbol)}
                try:
                    candles = await prices.cached_history(symbol, "1d", assets.periods_per_year(symbol) * 2 + 1)
                    price = eng.prices().get(symbol) or candles[-1]["close"]
                    state, why = await self._signal(symbol, candles)
                    step["signal"] = why
                    owned = eng.broker.positions.get(symbol)
                    news = None
                    if cfg["use_research"]:
                        try:
                            news = await eng.researcher.news(symbol)
                            step["news"] = {"average": news["average"],
                                            "headlines": [{"title": i["title"], "score": i["score"]} for i in news["items"][:5]]}
                        except Exception as exc:  # noqa: BLE001
                            step["news"] = {"error": f"news unavailable ({type(exc).__name__})"}
                    closes = np.array([c["close"] for c in candles[-61:]], dtype=float)
                    vol_d = float(np.std(closes[1:] / closes[:-1] - 1, ddof=1))
                    long = np.array([c["close"] for c in candles[-253:]], dtype=float)
                    vol_long = float(np.std(long[1:] / long[:-1] - 1, ddof=1))
                    vol_ratio = vol_d / vol_long if vol_long > 0 else None
                    step["risk"] = {"daily_vol_pct": round(vol_d * 100, 2),
                                    "vol_ratio": round(vol_ratio, 2) if vol_ratio else None}
                    side = None
                    if state == 1 and not owned:
                        side = "buy"
                    elif state == 0 and owned and owned.strategy == "agent":
                        side = "sell"
                    if side is None:
                        step["decision"] = "no trade" + (" (already holding)" if owned and state == 1 else "")
                        steps.append(step)
                        continue
                    if symbol in pending_syms:
                        step["decision"] = "a proposal for this is already waiting"
                        steps.append(step)
                        continue
                    proposal = {"id": int(_now() * 1000) + len(new), "symbol": symbol, "name": assets.name(symbol),
                                "side": side, "price": price, "created": _now(), "status": "pending", "reasons": [why]}
                    if side == "buy":
                        stop_dist = max(2 * vol_d, 0.01)
                        notional = cfg["risk_pct"] * equity / stop_dist
                        cap = limits.max_position_pct * equity
                        capped = notional > cap
                        notional = min(notional, cap, acct["cash"] * 0.99)
                        problems = []
                        if acct["halted"]:
                            problems.append("trading is paused by the daily loss limit")
                        if len(acct["positions"]) + sum(1 for p in new if p["side"] == "buy") >= limits.max_open_positions:
                            problems.append(f"already at the {limits.max_open_positions}-position limit")
                        if notional < 50:
                            problems.append("not enough cash")
                        proposal.update({"qty": notional / price, "notional": round(notional, 2),
                                         "stop_loss": round(price * (1 - stop_dist), 6),
                                         "risk_dollars": round(notional * stop_dist, 2)})
                        proposal["reasons"].append(
                            f"Size: risk {cfg['risk_pct'] * 100:.1f}% of equity (${cfg['risk_pct'] * equity:,.0f}) with a stop "
                            f"{stop_dist * 100:.1f}% away (2 daily standard deviations) = ${notional:,.0f}"
                            + (" (capped by your per-trade limit)" if capped else ""))
                        if problems:
                            step["decision"] = "buy blocked by the risk check: " + "; ".join(problems)
                            steps.append(step)
                            continue
                    else:
                        proposal.update({"qty": owned.qty, "notional": round(owned.qty * price, 2)})
                    if not eng.market_open_for(symbol):
                        proposal["reasons"].append("The US market is closed: approving fills at the last price (practice).")
                    facts = {"symbol": symbol, "side": side, "signal": why, "news_average": news and news.get("average"),
                             "headlines": step.get("news", {}).get("headlines"), "daily_volatility_pct": round(vol_d * 100, 2),
                             "market_open": eng.market_open_for(symbol), "size_usd": proposal["notional"],
                             "vol_ratio": vol_ratio}
                    ai = await self._ai_check(facts)
                    proposal["ai"] = ai
                    proposal["flagged"] = ai["concern"]
                    step["ai"] = ai
                    step["decision"] = f"proposed {side}" + (" (flagged by the risk check)" if ai["concern"] else "")
                    new.append(proposal)
                except Exception as exc:  # noqa: BLE001 - one bad symbol shouldn't stop the run
                    logger.exception("Agent step failed for %s", symbol)
                    step["decision"] = f"skipped: {exc}"
                steps.append(step)
            items = self.proposals() + new
            self._save_proposals(items)
            cfg = self.config()
            cfg["last_run"] = _now()
            self.db.set_kv("agent", cfg)
            run = {"ts": _now(), "manual": manual, "mode": cfg["mode"], "steps": steps,
                   "proposals": [p["id"] for p in new]}
            runs = self.runs() + [run]
            self.db.set_kv("agent_runs", runs[-MAX_LOG:])
            self.engine.learn.record("agent_run")
            await eng.log("info", f"Agent run: {len(new)} proposal(s) from {len(steps)} assets")
            if cfg["mode"] == "auto":
                for p in new:
                    if not p["flagged"]:
                        await self.decide(p["id"], True, auto=True)
            return {**run, "new": new}
        finally:
            self._running = False

    async def decide(self, proposal_id: int, approve: bool, auto: bool = False) -> dict:
        items = self.proposals()
        p = next((x for x in items if x["id"] == int(proposal_id)), None)
        if p is None:
            raise KeyError(f"Proposal {proposal_id} not found")
        if p["status"] != "pending":
            raise ValueError(f"That proposal is already {p['status']}.")
        if not approve:
            p["status"] = "rejected"
            p["decided"] = _now()
            self._save_proposals(items)
            self.engine.learn.record("agent_reject")
            return p
        eng = self.engine
        p["status"] = "executing"  # claim it first, so a double click can't execute it twice
        self._save_proposals(items)
        try:
            async with eng.lock:
                price = eng.prices().get(p["symbol"]) or p["price"]
                if p["side"] == "buy":
                    qty = min(p["qty"], p["notional"] / price)
                    fill = eng.broker.open_long(p["symbol"], price, _now(), strategy="agent", qty=qty, reason="agent")
                    eng.broker.set_brackets(p["symbol"], p.get("stop_loss"), None)
                else:
                    fill = eng.broker.close(p["symbol"], price, _now(), reason="agent")
                out = await eng._record_fill(fill)
        except RiskError as exc:
            p["status"] = "failed"
            p["error"] = str(exc)
        else:
            p["status"] = "auto-executed" if auto else "approved"
            p["trade_id"] = out["id"]
            if not auto:
                self.engine.learn.record("agent_approve")
        p["decided"] = _now()
        self._save_proposals(items)
        return p

    async def kill(self, sell_positions: bool = False) -> dict:
        cfg = self.config()
        cfg["enabled"] = False
        cfg["mode"] = "approve"
        self.db.set_kv("agent", cfg)
        items = self.proposals()
        for p in items:
            if p["status"] == "pending":
                p["status"] = "cancelled"
                p["decided"] = _now()
        self._save_proposals(items)
        eng = self.engine
        await eng.update_engine({"auto_trading": False})
        for o in list(eng.orders):
            await eng.cancel_order(o["id"])
        sold = []
        if sell_positions:
            for sym, pos in list(eng.broker.positions.items()):
                if pos.strategy == "agent":
                    sold.append(await eng.close_position(sym))
        self.engine.learn.record("kill_switch")
        await eng.log("warning", "KILL SWITCH: agent stopped, proposals and orders cancelled"
                      + (f", {len(sold)} agent positions sold" if sold else ""))
        return {**self.state(), "sold": len(sold)}

    async def loop(self) -> None:
        """Background scheduler: run when enabled and due."""
        while True:
            await asyncio.sleep(30)
            try:
                cfg = self.config()
                if cfg["enabled"] and not self._running and _now() - cfg["last_run"] >= cfg["interval_min"] * 60:
                    await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.warning("Agent loop error: %r", exc)


# ------------------------------------------------------------------------------------------ capstone
def _daily_equity(rows: list[dict]) -> list[tuple[str, float]]:
    by_day: dict[str, float] = {}
    for r in rows:
        d = datetime.fromtimestamp(r["ts"], tz=timezone.utc).strftime("%Y-%m-%d")
        by_day[d] = float(r["equity"])  # last snapshot of each day
    return sorted(by_day.items())


def _verdict(f: dict) -> str:
    span = f"{f['days']:.0f} days" if f["days"] >= 1.5 else "less than two days"
    gap = f["return_pct"] - f["spy_return_pct"]
    if abs(gap) < 0.05:
        head = "You and SPY are about level so far."
    elif gap > 0:
        head = f"You're ahead of SPY by {gap:.2f} percentage points."
    else:
        head = f"SPY is ahead of you by {-gap:.2f} percentage points, which is the most common result."
    return f"{head} {span[0].upper() + span[1:]} is far too short to judge a strategy either way: treat it as one data point."


class Capstone:
    def __init__(self, engine):
        self.engine = engine
        self.db = engine.db

    def get(self) -> dict | None:
        raw = self.db.get_kv("capstone")
        return raw if isinstance(raw, dict) else None

    async def start(self, plan: str) -> dict:
        plan = " ".join(str(plan or "").split())[:2000]
        if len(plan) < 40:
            raise ValueError("Write your plan first (at least a couple of sentences): what you'll trade, your rules, "
                             "how you size and when you'll stop.")
        await self.engine.snapshot_equity(force=True)
        spy = self.engine.prices().get("SPY")
        if not spy:
            candles = await prices.cached_history("SPY", "1d", 30)
            spy = candles[-1]["close"]
        acct = self.engine.account()
        data = {"started_at": _now(), "start_equity": acct["equity"], "spy_start": spy, "plan": plan, "report": None}
        self.db.set_kv("capstone", data)
        self.engine.learn.record("capstone_start")
        return self.status()

    def status(self) -> dict | None:
        c = self.get()
        if not c:
            return None
        days = (_now() - c["started_at"]) / 86400
        acct = self.engine.account()
        spy_now = self.engine.prices().get("SPY") or c["spy_start"]
        return {**c, "days": round(days, 1), "days_needed": CAPSTONE_DAYS, "ready": days >= CAPSTONE_DAYS,
                "equity": acct["equity"], "return_pct": round((acct["equity"] / c["start_equity"] - 1) * 100, 2),
                "spy_return_pct": round((spy_now / c["spy_start"] - 1) * 100, 2)}

    def facts(self) -> dict:
        c = self.get()
        if not c:
            raise ValueError("Start the capstone first.")
        rows = self.db.query("SELECT ts, equity FROM equity WHERE ts >= ? ORDER BY ts", (c["started_at"] - 60,))
        eq = _daily_equity(rows)
        trades = self.db.query("SELECT * FROM trades WHERE ts >= ? ORDER BY id", (c["started_at"],))
        sells = [t for t in trades if t["side"] == "sell" and t["pnl"] is not None]
        st = self.status()
        f = {"days": st["days"], "start_equity": round(c["start_equity"], 2), "end_equity": round(st["equity"], 2),
             "return_pct": st["return_pct"], "spy_return_pct": st["spy_return_pct"],
             "beat_spy": st["return_pct"] > st["spy_return_pct"], "trades": len(trades), "closed_trades": len(sells),
             "wins": sum(1 for t in sells if t["pnl"] > 0), "fees": round(sum(t["fee"] for t in trades), 2),
             "agent_trades": sum(1 for t in trades if t["reason"] == "agent"), "daily_points": len(eq)}
        if len(eq) >= 3:
            v = np.array([x[1] for x in eq])
            r = v[1:] / v[:-1] - 1
            peaks = np.maximum.accumulate(v)
            f["max_drawdown_pct"] = round(float(np.max((peaks - v) / peaks)) * 100, 2)
            if len(r) >= 2 and r.std(ddof=1) > 0:
                f["vol_annual_pct"] = round(float(r.std(ddof=1)) * math.sqrt(252) * 100, 2)
                f["sharpe"] = round(float(r.mean()) / float(r.std(ddof=1)) * math.sqrt(252), 2)
        if sells:
            f["win_rate_pct"] = round(f["wins"] / len(sells) * 100, 1)
        return f

    async def report(self) -> dict:
        from app.coach import numbers_match

        c = self.get()
        if not c:
            raise ValueError("Start the capstone first.")
        f = self.facts()
        draft = {
            "headline": f"{f['days']:.0f} days: {f['return_pct']:+.2f}% vs SPY {f['spy_return_pct']:+.2f}%.",
            "what_happened": f"Equity went from ${f['start_equity']:,.2f} to ${f['end_equity']:,.2f} over {f['trades']} trades "
                             f"({f['agent_trades']} by the agent), paying ${f['fees']:,.2f} in fees.",
            "verdict": _verdict(f),
            "next": "Keep running it, log every decision, and compare again after more trades. Re-test any change out-of-sample.",
        }
        source = "rules"
        final = f["days"] >= CAPSTONE_DAYS
        system = ("You write a short, honest capstone write-up for a learner's paper-trading experiment. The run has lasted "
                  f"exactly {f['days']:.1f} days; never describe it as longer. Plan: {c['plan']}\nUse ONLY the facts given; "
                  "keep every number exactly; add no new numbers, including numbers written as words. Be candid that "
                  "a few weeks is a small sample. Respond with ONLY JSON with keys headline, what_happened, verdict, next "
                  "(each 1-3 sentences).")
        try:
            if not final:
                raise RuntimeError("preview: rules only")  # the AI write-up is saved for the final report
            reply = await self.engine.llm.chat("agent", [{"role": "system", "content": system},
                                                         {"role": "user", "content": json.dumps({"facts": f, "draft": draft})}],
                                               max_tokens=600, temperature=0.3)
            j = reply["json"] or {}
            worded = {k: str(j.get(k, ""))[:600] for k in draft}
            if all(worded.values()) and numbers_match(json.dumps(worded), json.dumps(f) + json.dumps(draft) + c["plan"]):
                draft, source = worded, reply["source"]
        except Exception as exc:  # noqa: BLE001
            if final:
                logger.warning("Capstone write-up via AI failed: %r", exc)
        report = {"facts": f, "writeup": draft, "source": source, "generated_at": _now(), "final": final}
        c["report"] = report
        self.db.set_kv("capstone", c)
        if report["final"]:
            self.engine.learn.record("capstone_report")
        return report
