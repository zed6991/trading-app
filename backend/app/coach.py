"""AI coach: answers the learner's questions in context, reviews their recent trades and picks their next step.

Everything the coach says is built on deterministic facts first (account, trades, course progress). The local LLM
only turns those facts into friendlier words; if it's unavailable or returns something unusable (wrong numbers,
bad JSON), the rule-based version is used. The coach teaches: it explains *why* and points to the lesson that
covers it, and leaves the decision to the learner.
"""
import json
import logging
import re
import time
from typing import Any

from app.ai import _extract_json, _trim_summary, coin_name
from app.learn import (GLOSSARY, GLOSSARY_BY_SLUG, LESSON_LEVEL, LESSONS, LEVELS, TERM_RE, Learning, _held,
                       _money)

logger = logging.getLogger("app.coach")

DAY = 86400.0
QUICK_FLIP_SECONDS = 300.0       # a trade closed within 5 minutes
REBUY_AFTER_LOSS_SECONDS = 600.0  # a buy within 10 minutes of a losing sell
MAX_HISTORY = 8                   # chat turns sent back to the model
MAX_MESSAGE_CHARS = 600
MAX_ANSWER_CHARS = 1200
REVIEW_KEY = "coach:review"

LEVEL_TOPICS = ("1 basics (price, cash, equity, P&L, fees)", "2 stop-loss, take-profit, position size, drawdowns",
                "3 candlestick charts, trends, support and resistance", "4 indicators: moving averages, RSI, MACD, "
                "Bollinger Bands", "5 strategies and backtesting", "6 auto-trading, AI filter, trading plan, psychology")


# --------------------------------------------------------------------------------------------- facts
def _lesson_ref(lesson_id: str | None, level: int) -> dict | None:
    """A lesson link the learner can open (only lessons at or below their level)."""
    if not lesson_id or lesson_id not in LESSONS:
        return None
    n = LESSON_LEVEL[lesson_id]
    return {"id": lesson_id, "title": LESSONS[lesson_id]["title"], "level": n, "locked": n > level}


def pair_trades(trades: list[dict]) -> list[dict]:
    """Match each sell with the buy it closed (same symbol, most recent earlier buy). Trades in any order."""
    open_buy: dict[str, dict] = {}
    rounds = []
    for t in sorted(trades, key=lambda r: (r["ts"], r.get("id") or 0)):
        if t["side"] == "buy":
            open_buy[t["symbol"]] = t
            continue
        entry = open_buy.pop(t["symbol"], None)
        held = (t["ts"] - entry["ts"]) if entry else None
        fees = t["fee"] + (entry["fee"] * min(1.0, t["qty"] / entry["qty"]) if entry and entry["qty"] else 0.0)
        pnl = t.get("pnl")
        if pnl is None and entry:
            pnl = (t["price"] - entry["price"]) * t["qty"] - fees
        rounds.append({"symbol": t["symbol"], "coin": coin_name(t["symbol"]), "ts": t["ts"], "pnl": pnl or 0.0,
                       "fees": fees, "reason": t.get("reason") or "manual", "held": held,
                       "entry": entry["price"] if entry else None, "exit": t["price"]})
    return rounds


def trade_stats(trades: list[dict], since: float) -> dict:
    """Numbers about trades closed (and buys made) since `since`. `trades` should reach back further so sells in
    the window can find the buy they closed."""
    rounds = [r for r in pair_trades(trades) if r["ts"] >= since]
    in_window = [t for t in trades if t["ts"] >= since]
    buys = [t for t in in_window if t["side"] == "buy"]
    wins = [r for r in rounds if r["pnl"] > 0]
    losses = [r for r in rounds if r["pnl"] <= 0]
    reasons: dict[str, int] = {}
    for r in rounds:
        reasons[r["reason"]] = reasons.get(r["reason"], 0) + 1
    held = [r["held"] for r in rounds if r["held"] is not None]
    losing_sells = sorted(r["ts"] for r in losses)
    rebuys = sum(1 for b in buys if any(0 <= b["ts"] - ts <= REBUY_AFTER_LOSS_SECONDS for ts in losing_sells))
    net = sum(r["pnl"] for r in rounds)
    fees = sum(t["fee"] for t in in_window)
    return {
        "buys": len(buys),
        "closed": len(rounds),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_pct": round(100 * len(wins) / len(rounds), 1) if rounds else None,
        "net_pnl": round(net, 2),
        "fees": round(fees, 2),
        "gross_pnl": round(net + sum(r["fees"] for r in rounds), 2),
        "closed_fees": round(sum(r["fees"] for r in rounds), 2),
        "avg_win": round(sum(r["pnl"] for r in wins) / len(wins), 2) if wins else None,
        "avg_loss": round(sum(r["pnl"] for r in losses) / len(losses), 2) if losses else None,
        "best": max(rounds, key=lambda r: r["pnl"]) if rounds else None,
        "worst": min(rounds, key=lambda r: r["pnl"]) if rounds else None,
        "exits": reasons,
        "protected_exits": sum(reasons.get(k, 0) for k in ("stop_loss", "take_profit", "trailing_stop")),
        "manual_losses": sum(1 for r in losses if r["reason"] == "manual"),
        "quick_flips": sum(1 for h in held if h < QUICK_FLIP_SECONDS),
        "avg_held_seconds": sum(held) / len(held) if held else None,
        "rebuys_after_loss": rebuys,
        "coins": sorted({coin_name(t["symbol"]) for t in in_window}),
    }


def observations(s: dict, limits: dict, level: int) -> list[dict]:
    """Plain-English habits spotted in the stats. kind 'good' or 'work'; each links the lesson that covers it."""
    out: list[dict] = []

    def add(kind: str, text: str, lesson_id: str | None = None, weight: int = 1) -> None:
        out.append({"kind": kind, "text": text, "lesson": _lesson_ref(lesson_id, level), "weight": weight})

    if s["closed"] == 0 and s["buys"] == 0:
        add("work", "You haven't traded yet. A first small buy and sell teaches more than any lesson.", None, 3)
        return out
    if s["closed"] == 0:
        add("good", f"You made {s['buys']} buy{'s' if s['buys'] != 1 else ''}. Nothing has been sold yet, so the "
                    "result isn't decided.", None)
    if s["protected_exits"]:
        n = s["protected_exits"]
        add("good", f"Your safety net closed {n} trade{'s' if n != 1 else ''} for you without you having to watch.",
            "l2-stops", 2)
    if s["avg_win"] is not None and s["avg_loss"] is not None:
        if s["avg_win"] >= abs(s["avg_loss"]):
            add("good", f"Your average win ({_money(s['avg_win'])}) is bigger than your average loss "
                        f"({_money(s['avg_loss'])}). That's the habit that makes money over time.", "l1-what-is-trading", 2)
        else:
            add("work", f"Your average loss ({_money(s['avg_loss'])}) is bigger than your average win "
                        f"({_money(s['avg_win'])}). Even if you win often, that adds up against you.",
                "l2-stops" if level >= 2 else "l1-what-is-trading", 3)
    elif s["wins"] and not s["losses"]:
        add("good", f"All {s['wins']} closed trade{'s were' if s['wins'] != 1 else ' was a'} "
                    f"win{'s' if s['wins'] != 1 else ''}. Enjoy it, but losses are normal and will come.", None)
    if s["closed"] and s["fees"] > 0 and s["gross_pnl"] > 0 and s["fees"] >= 0.5 * s["gross_pnl"]:
        add("work", f"Fees came to {_money(s['fees'])}, which ate at least half of what your trades made before fees.",
            "l1-your-account", 3)
    elif s["net_pnl"] < 0 and s["gross_pnl"] <= 0 and s["closed_fees"] >= abs(s["gross_pnl"]):
        add("work", f"Fees cost you {_money(s['closed_fees'])}, more than the price moves did "
                    f"({_money(s['gross_pnl'])}). Trades that end quickly pay the fees without giving the price time "
                    "to move.", "l1-your-account", 3)
    elif s["closed"] >= 3 and s["closed_fees"] > 0 and s["gross_pnl"] < 0:
        add("work", f"You paid {_money(s['closed_fees'])} in fees on top of the losses. Fewer, better-planned trades "
                    "cost less.", "l1-your-account", 2)
    if s["quick_flips"] >= 3:
        add("work", f"{s['quick_flips']} trades were sold within 5 minutes of buying. Prices wobble a lot over minutes, "
                    "so very short trades are mostly luck plus fees.", "l1-your-account", 2)
    if s["rebuys_after_loss"] >= 2:
        add("work", f"{s['rebuys_after_loss']} times you bought again within 10 minutes of a loss. Rushing back in to win "
                    "it back usually makes losses bigger.", "l6-plan", 3)
    if s["manual_losses"] >= 2 and level >= 2:
        add("work", f"{s['manual_losses']} losing trades were sold by hand. Setting your exit before you buy takes the "
                    "panic out of it.", "l2-stops", 2)
    if s["closed"] and not any(o["kind"] == "good" for o in out):
        add("good", f"You finished {s['closed']} trade{'s' if s['closed'] != 1 else ''} and you're reviewing "
                    f"{'them' if s['closed'] != 1 else 'it'}. That's how traders get better.", None)
    if level >= 2:
        if not limits.get("stop_loss_pct"):
            add("work", "Your stop-loss is switched off, so a falling coin can keep losing with nothing to stop it.",
                "l2-stops", 3)
        if (limits.get("max_position_pct") or 0) > 0.25:
            add("work", f"You put {limits['max_position_pct'] * 100:.0f}% of your account into each trade. Two or "
                        "three bad trades in a row would hurt a lot.", "l2-sizing", 2)
    return out


# --------------------------------------------------------------------------------------------- number checking
_NUM_RE = re.compile(r"-?\d[\d,]*(?:\.\d+)?")


def _numbers(text: str) -> list[float]:
    vals = []
    for m in _NUM_RE.finditer(text or ""):
        try:
            vals.append(abs(float(m.group(0).replace(",", ""))))
        except ValueError:
            pass
    return vals


def numbers_match(text: str, allowed_text: str) -> bool:
    """True when every number in `text` (bar small counts) also appears in `allowed_text`, allowing rounding."""
    allowed = _numbers(allowed_text)
    for v in _numbers(text):
        if v <= 10 and float(v).is_integer():
            continue  # "3 trades", "Level 2", "one of 2"
        if not any(abs(v - a) <= max(0.011, 0.01 * a) for a in allowed):
            return False
    return True


# --------------------------------------------------------------------------------------------- the coach
class Coach:
    def __init__(self, engine: Any):
        self.engine = engine  # TradingEngine: account(), risk(), prices(), db, ai, ai_health, learn

    @property
    def learn(self) -> Learning:
        return self.engine.learn

    def _ai_ready(self) -> bool:
        return bool(self.engine.ai_health.get("available"))

    # ---------------------------------------------------------------- next step
    def next_step(self, review: dict | None = None) -> dict:
        """The next lesson or challenge in the course, plus a lesson worth revisiting based on their trades."""
        level = self.learn.level()
        summary = self.learn.summary()
        primary = None
        if not summary["finished"]:
            lvl = next((l for l in summary["levels"] if l["n"] == level), None)
            if lvl:
                todo = next((l for l in lvl["lessons"] if not l["done"]), None)
                if todo:
                    primary = {"kind": "lesson", "id": todo["id"], "title": todo["title"],
                               "why": f"It's the next lesson in Level {level}: {lvl['title']}."}
                else:
                    ch = next((c for c in lvl["challenges"] if not c["done"] and not c["bonus"]), None)
                    if ch:
                        primary = {"kind": "challenge", "id": ch["id"], "title": ch["title"], "why": ch["how"]}
        if primary is None:
            primary = {"kind": "practice", "id": None, "title": "Keep practising and review your trades",
                       "why": "You've finished the course. Trade small, write down why, and check your weekly review."}
        revisit = None
        for ob in sorted((review or {}).get("observations") or [], key=lambda o: -o["weight"]):
            ref = ob.get("lesson")
            if ob["kind"] == "work" and ref and not ref["locked"] and self.learn.lesson_done(ref["id"]):
                revisit = {**ref, "why": ob["text"]}
                break
        return {"level": level, "primary": primary, "revisit": revisit}

    # ---------------------------------------------------------------- weekly review
    def _trades_since(self, since: float) -> list[dict]:
        # Reach back a day further so sells early in the window can find the buy they closed.
        return self.engine.db.query("SELECT * FROM trades WHERE ts >= ? ORDER BY id ASC", (since - DAY,))

    def rule_review(self, days: int) -> dict:
        level = self.learn.level()
        since = time.time() - days * DAY
        stats = trade_stats(self._trades_since(since), since)
        obs = observations(stats, self.engine.risk(), level)
        if stats["closed"]:
            sign = "up" if stats["net_pnl"] > 0 else "down" if stats["net_pnl"] < 0 else "flat"
            headline = (f"{stats['closed']} closed trade{'s' if stats['closed'] != 1 else ''} in the last {days} days: "
                        f"{stats['wins']} won, {stats['losses']} lost, "
                        + (f"{sign} {_money(stats['net_pnl'])} after fees." if sign != "flat" else "about break-even."))
        elif stats["buys"]:
            headline = f"{stats['buys']} buy{'s' if stats['buys'] != 1 else ''} in the last {days} days and nothing sold yet."
        else:
            headline = f"No trades in the last {days} days."
        good = [o["text"] for o in obs if o["kind"] == "good"][:3]
        work = [o["text"] for o in sorted(obs, key=lambda o: -o["weight"]) if o["kind"] == "work"][:3]
        step = self.next_step({"observations": obs})
        if step["revisit"]:
            next_text = f"Re-read \"{step['revisit']['title']}\". It covers the thing to work on most."
        else:
            next_text = f"Next up: {step['primary']['title'].rstrip('.?!')}. {step['primary']['why']}"
        return {"days": days, "level": level, "stats": _public_stats(stats), "observations": obs,
                "headline": headline, "went_well": good, "work_on": work, "next_step": next_text,
                "next": step, "source": "rules"}

    async def review(self, days: int = 7) -> dict:
        days = max(1, min(int(days), 90))
        last = self.engine.db.one("SELECT MAX(id) AS id FROM trades") or {}
        cache_key = f"{days}:{last.get('id') or 0}:{self.learn.level()}:{json.dumps(self.engine.risk(), sort_keys=True)}"
        cached = self.engine.db.get_kv(REVIEW_KEY)
        if isinstance(cached, dict) and cached.get("key") == cache_key and (
                cached["review"]["source"] != "rules" or not self._ai_ready()):
            self.learn.record("coach_review")
            return cached["review"]
        review = self.rule_review(days)
        if self._ai_ready() and (review["stats"]["closed"] or review["stats"]["buys"]):
            worded = await self._reword_review(review)
            if worded:
                review.update(worded)
                review["source"] = f"ollama:{self.engine.ai.model}"
        review["generated_at"] = time.time()
        self.engine.db.set_kv(REVIEW_KEY, {"key": cache_key, "review": review})
        self.learn.record("coach_review")
        return review

    async def _reword_review(self, review: dict) -> dict | None:
        level = review["level"]
        draft = {k: review[k] for k in ("headline", "went_well", "work_on", "next_step")}
        system = (
            "You are a warm, honest trading coach writing a short weekly review for a beginner who practises with "
            f"pretend money. They are on level {level} of 6 ({'; '.join(LEVEL_TOPICS)}). "
            "Rewrite the draft so it reads like a coach talking to them: plain English, short sentences, "
            "encouraging but truthful. Keep every number exactly as given and add no new numbers, prices or facts. "
            "Only use trading words from their level or below. Never promise profit or tell them to use real money. "
            "Respond with ONLY strict JSON with the same keys: "
            '{"headline": "1 sentence", "went_well": ["up to 3 short items"], "work_on": ["up to 3 short items"], '
            '"next_step": "1-2 sentences"}'
        )
        user = f"Facts: {json.dumps(review['stats'], default=str)}\nDraft: {json.dumps(draft)}\nReturn the JSON now."
        try:
            raw = _extract_json(await self.engine.ai._ask_model(
                [{"role": "system", "content": system}, {"role": "user", "content": user}], temperature=0.3))
            out = {
                "headline": _trim_summary(raw.get("headline", "")),
                "went_well": [_trim_summary(x) for x in (raw.get("went_well") or []) if str(x).strip()][:3],
                "work_on": [_trim_summary(x) for x in (raw.get("work_on") or []) if str(x).strip()][:3],
                "next_step": _trim_summary(raw.get("next_step", "")),
            }
            if not out["headline"] or not out["next_step"]:
                raise ValueError("missing headline or next step")
            for key in ("went_well", "work_on"):
                out[key] = out[key][:len(draft[key])]  # reword the points, don't pad with new ones
                if draft[key] and not out[key]:
                    raise ValueError(f"dropped {key}")
            allowed = json.dumps(review["stats"], default=str) + json.dumps(draft)
            if not numbers_match(json.dumps(out), allowed):
                raise ValueError("changed the numbers")
            return out
        except Exception as exc:  # noqa: BLE001 - the rule-based review is always available
            logger.warning("Coach review via %s failed (%s); using rules", self.engine.ai.model, exc)
            return None

    # ---------------------------------------------------------------- chat
    def context(self, view: str | None = None, symbol: str | None = None) -> str:
        """What the coach knows about the learner right now, as compact text for the model."""
        level = self.learn.level()
        summary = self.learn.summary()
        done = [l["title"] for lvl in summary["levels"] for l in lvl["lessons"] if l["done"]]
        taught = sorted(g["term"] for g in GLOSSARY if g["level"] <= level)
        later = sorted(f"{g['term']} (level {g['level']})" for g in GLOSSARY if g["level"] > level)
        acct = self.engine.account()
        lines = [
            f"Learner level: {level} of 6 ({LEVELS[level - 1]['title']}). Course {summary['percent']}% done.",
            f"Lessons finished: {', '.join(done) if done else 'none yet'}.",
            f"Words they have learned: {', '.join(taught)}.",
            f"Words not taught yet: {', '.join(later) if later else 'none'}.",
            f"Account (pretend money): cash {_money(acct['cash'])}, equity {_money(acct['equity'])}, "
            f"total return {acct['total_return_pct']:+.2f}%, fees paid {_money(acct['fees_paid'])}.",
        ]
        for p in acct["positions"]:
            lines.append(
                f"Owns {p['qty']:.6g} {coin_name(p['symbol'])}: bought at {_money(p['avg_price'])}, now "
                f"{_money(p['mark'])}, {'+' if p['unrealized_pnl'] >= 0 else '-'}{_money(p['unrealized_pnl'])} "
                f"({p['pnl_pct']:+.2f}%), stop-loss {_money(p['stop_loss']) if p['stop_loss'] else 'none'}, "
                f"take-profit {_money(p['take_profit']) if p['take_profit'] else 'none'}.")
        if not acct["positions"]:
            lines.append("Owns no coins right now.")
        risk = self.engine.risk()
        lines.append(f"Safety net: stop-loss {risk['stop_loss_pct'] * 100:.1f}%, take-profit "
                     f"{risk['take_profit_pct'] * 100:.1f}%, {risk['max_position_pct'] * 100:.0f}% of account per trade.")
        rounds = pair_trades(self.engine.db.query("SELECT * FROM trades ORDER BY id DESC LIMIT 40"))[-5:]
        for r in rounds:
            how = {"manual": "sold by hand", "stop_loss": "sold automatically by the stop-loss",
                   "take_profit": "sold automatically by the take-profit", "trailing_stop": "sold by the trailing stop",
                   "signal": "sold by the auto-trading strategy"}.get(r["reason"], r["reason"])
            bought = f"bought at {_money(r['entry'])}, " if r["entry"] else ""
            lines.append(f"Recent closed trade: {r['coin']}, {bought}sold at {_money(r['exit'])}, {how}"
                         + (f" after {_held(r['held'])}" if r["held"] is not None else "")
                         + f". Result after fees: {'a gain of' if r['pnl'] > 0 else 'a loss of'} {_money(r['pnl'])}"
                         f" (fees {_money(r['fees'])}).")
        prices = self.engine.prices()
        if prices:
            lines.append("Live prices: " + ", ".join(f"{coin_name(s)} {_money(v)}" for s, v in prices.items()) + ".")
        where = {"learn": "the course page", "practice": "the Practice screen (simple buy/sell)",
                 "dashboard": "the Dashboard (charts and tools)", "glossary": "the Glossary"}.get(view or "", None)
        if where:
            lines.append(f"They are looking at {where}" + (f", with {coin_name(symbol.upper())} selected." if symbol else "."))
        step = self.next_step()
        lines.append(f"Their next step in the course: {step['primary']['title']}.")
        lessons = [f"{lid} = {l['title']} (level {LESSON_LEVEL[lid]})" for lid, l in LESSONS.items()
                   if LESSON_LEVEL[lid] <= level]
        lines.append("Lessons they can open (id = title): " + "; ".join(lessons) + ".")
        return "\n".join(lines)

    def _system_prompt(self, level: int) -> str:
        return (
            "You are the coach inside a trading course app. The learner practises crypto trading with pretend money "
            "and real prices, and learns level by level "
            f"({'; '.join(LEVEL_TOPICS)}). They are on level {level}.\n"
            "How to answer:\n"
            "- Teach, don't decide. Explain the idea and what to look at, then leave the choice to them. If they ask "
            "whether to buy or sell, don't answer yes or no. Walk them through the checks for their level (level 1: how "
            "much they could lose and the fees; level 2: where the stop-loss and take-profit would be and how much to "
            "put in; level 3+: what the chart shows; level 4+: whether indicators agree), mention the app's "
            "suggestion on the Practice screen is an educated guess, and say it's their call.\n"
            "- Plain English and short sentences, like a friendly teacher. Use dollar examples before percentages.\n"
            "- Only use words they have learned. If you must use a word not taught yet, explain it in a few words and "
            "say which level covers it.\n"
            "- Use the facts about their account exactly. Never invent prices, trades or results.\n"
            "- Never promise profit, never predict prices with certainty, and never encourage using real money, "
            "borrowing or betting big. Remind them it's practice if it matters.\n"
            "- Stay on topic. If the question isn't about trading, money basics or this app (for example poems, "
            "stories, code or homework), don't do it: say in one sentence that you only help with trading and this "
            "app, and suggest a trading question instead.\n"
            "- At most 120 words. Write trading words in normal lowercase (price, position), not Title Case.\n"
            "Respond with ONLY strict JSON: "
            '{"answer": "your reply", "lesson_id": "id of the ONE most relevant lesson they can open, or null", '
            '"follow_ups": ["up to 3 short questions they might ask next"]}'
        )

    async def ask(self, message: str, history: list[dict] | None = None, view: str | None = None,
                  symbol: str | None = None) -> dict:
        message = " ".join(str(message or "").split())[:MAX_MESSAGE_CHARS]
        if not message:
            raise ValueError("Type a question first.")
        level = self.learn.level()
        self.learn.record("coach_ask")
        if self._ai_ready():
            msgs = [{"role": "system", "content": self._system_prompt(level)},
                    {"role": "system", "content": "Facts about the learner right now:\n" + self.context(view, symbol)}]
            for turn in (history or [])[-MAX_HISTORY:]:
                role = turn.get("role")
                text = str(turn.get("content") or "")[:MAX_ANSWER_CHARS]
                if role in ("user", "assistant") and text.strip():
                    msgs.append({"role": role, "content": text})
            msgs.append({"role": "user", "content": message})
            try:
                raw = _extract_json(await self.engine.ai._ask_model(msgs, temperature=0.4))
                answer = " ".join(str(raw.get("answer") or "").split(" "))[:MAX_ANSWER_CHARS].strip()
                if not answer:
                    raise ValueError("empty answer")
                lesson = _lesson_ref(raw.get("lesson_id") if isinstance(raw.get("lesson_id"), str) else None, level)
                follow = [str(f).strip()[:120] for f in (raw.get("follow_ups") or []) if str(f).strip()][:3]
                return {"answer": answer, "lesson": lesson if lesson and not lesson["locked"] else None,
                        "follow_ups": follow, "source": f"ollama:{self.engine.ai.model}"}
            except Exception as exc:  # noqa: BLE001 - fall back to the rule-based coach
                logger.warning("Coach chat via %s failed (%s); using rules", self.engine.ai.model, exc)
        return await self.rule_answer(message, level)

    async def rule_answer(self, message: str, level: int) -> dict:
        """Offline coach: defines glossary words, gives the next step or the weekly review."""
        text = message.lower()
        offline = not self._ai_ready()
        note = (" (The AI coach is offline because the local AI model isn't running, so I can only define words, "
                "give your next step and review your trades.)") if offline else ""
        if re.search(r"\b(review|how am i doing|how did i do|my week|my trades|performance)\b", text):
            r = await self.review(7)
            parts = [r["headline"]] + [f"Going well: {x}" for x in r["went_well"][:1]] + \
                    [f"Work on: {x}" for x in r["work_on"][:1]]
            ref = (r["next"].get("revisit") or None)
            return {"answer": " ".join(parts) + note, "lesson": ref and {k: ref[k] for k in ("id", "title", "level", "locked")},
                    "follow_ups": ["What should I learn next?"], "source": "rules"}
        hit = _find_term(text)
        if hit:
            g = GLOSSARY_BY_SLUG[hit]
            lesson_id = _lesson_teaching(hit)
            ref = _lesson_ref(lesson_id, level)
            when = "" if g["level"] <= level else f" You'll learn this properly in Level {g['level']}."
            return {"answer": f"{g['term']}: {g['short']} {g.get('more', '')}{when}".strip() + note,
                    "lesson": ref if ref and not ref["locked"] else None, "follow_ups": [], "source": "rules"}
        step = self.next_step()
        p = step["primary"]
        ref = _lesson_ref(p["id"], level) if p["kind"] == "lesson" else None
        lead = "I can't answer that one without the AI model." if offline else "I didn't quite get that."
        return {"answer": f"{lead} Your next step is \"{p['title']}\". {p['why']}{note}",
                "lesson": ref, "follow_ups": ["What is a stop-loss?", "How am I doing?"], "source": "rules"}


def _public_stats(s: dict) -> dict:
    """Stats safe to send to the browser and the model (rounds summarised, not raw rows)."""
    out = {k: v for k, v in s.items() if k not in ("best", "worst")}
    for key in ("best", "worst"):
        r = s.get(key)
        out[key] = None if not r else {"coin": r["coin"], "pnl": round(r["pnl"], 2), "reason": r["reason"]}
    return out


def _find_term(text: str) -> str | None:
    """The glossary slug mentioned in a question, preferring the longest name (e.g. 'stop-loss' over 'stop')."""
    best, best_len = None, 0
    for g in GLOSSARY:
        for name in [g["term"], g["slug"], *g.get("aliases", [])]:
            n = name.lower()
            if len(n) < 3:
                continue
            if re.search(rf"(?<![a-z0-9]){re.escape(n)}(?![a-z0-9])", text) and len(n) > best_len:
                best, best_len = g["slug"], len(n)
    return best


def _lesson_teaching(slug: str) -> str | None:
    """First lesson whose text links the term."""
    for lid, lesson in LESSONS.items():
        texts = [c.get("body", "") for c in lesson["cards"]] + [lesson.get("takeaway", "")]
        if any(slug in (m.group(1) for m in TERM_RE.finditer(t)) for t in texts):
            return lid
    return None
