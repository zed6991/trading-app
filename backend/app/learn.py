"""Learning path: levels, lessons, quizzes, practice challenges, glossary and progress tracking.

The course is the spine of the app. Each level teaches a topic and unlocks the matching part of the app
(level N's features become usable once the user reaches level N). A level is complete when every lesson quiz is
answered correctly and every required (non-bonus) challenge is done; then the next level unlocks.

Text fields may contain glossary links written as [[slug]] or [[slug|shown text]]; the frontend turns them into
tappable terms. Progress lives in the kv table under "learn".
"""
import hashlib
import random
import re
import time
from typing import Any

from app import assets
from app.curriculum import (COURSE_OUTLINE, COURSE_TITLE, GLOSSARY, GLOSSARY_BY_SLUG, LEVELS, ROADMAP,  # noqa: F401
                            TOTAL_LEVELS)
from app.db import Database

PROTECTIVE_EXITS = {"stop_loss", "take_profit", "trailing_stop"}
MANUAL_REASONS = {"manual", "limit"}
CLIENT_EVENTS = {"chart", "indicator", "ticket"}  # actions only the browser can see

LESSONS: dict[str, dict] = {lesson["id"]: lesson for lvl in LEVELS for lesson in lvl["lessons"]}
LESSON_LEVEL: dict[str, int] = {lesson["id"]: lvl["n"] for lvl in LEVELS for lesson in lvl["lessons"]}
MAX_LEVEL = len(LEVELS)
TERM_RE = re.compile(r"\[\[([a-z0-9-]+)(?:\|([^\]]+))?\]\]")


def term_slugs(text: str) -> list[str]:
    return [m.group(1) for m in TERM_RE.finditer(text or "")]


def choice_order(lesson_id: str, question: int, n: int) -> list[int]:
    """Stable shuffled order for a question's choices (shown position -> original index), so the right answer
    isn't always in the same place."""
    seed = int(hashlib.sha256(f"{lesson_id}:{question}".encode()).hexdigest()[:8], 16)
    order = list(range(n))
    random.Random(seed).shuffle(order)
    return order


# --------------------------------------------------------------------------------------------- progress
class Learning:
    """Tracks course progress in the kv table. All methods are synchronous and cheap."""

    KEY = "learn"

    def __init__(self, db: Database):
        self.db = db
        self.p = self._load()

    def _load(self) -> dict:
        raw = self.db.get_kv(self.KEY)
        p = raw if isinstance(raw, dict) else {}
        return {
            "answered": {k: sorted(set(v)) for k, v in (p.get("answered") or {}).items() if k in LESSONS},
            "counts": dict(p.get("counts") or {}),
            "sets": {k: list(v) for k, v in (p.get("sets") or {}).items()},
            "unlock_all": bool(p.get("unlock_all", False)),
            "started": p.get("started") or time.time(),
        }

    def _save(self) -> None:
        self.db.set_kv(self.KEY, self.p)

    # ---------------------------------------------------------------- status
    def lesson_done(self, lesson_id: str) -> bool:
        return len(self.p["answered"].get(lesson_id, [])) >= len(LESSONS[lesson_id]["quiz"])

    def challenge_progress(self, ch: dict) -> int:
        if ch["distinct"]:
            return len(set(self.p["sets"].get(ch["event"], [])))
        return int(self.p["counts"].get(ch["event"], 0))

    def challenge_done(self, ch: dict) -> bool:
        return self.challenge_progress(ch) >= ch["times"]

    def level_complete(self, lvl: dict) -> bool:
        return all(self.lesson_done(lesson["id"]) for lesson in lvl["lessons"]) and all(
            self.challenge_done(ch) for ch in lvl["challenges"] if not ch["bonus"])

    def finished(self) -> bool:
        return all(self.level_complete(lvl) for lvl in LEVELS)

    def level(self) -> int:
        """The highest unlocked level: the first incomplete one (or the last when everything is done)."""
        if self.p["unlock_all"]:
            return MAX_LEVEL
        for lvl in LEVELS:
            if not self.level_complete(lvl):
                return lvl["n"]
        return MAX_LEVEL

    def summary(self) -> dict:
        current = self.level()
        levels = []
        total = done = 0
        for lvl in LEVELS:
            complete = self.level_complete(lvl)
            status = "done" if complete else ("current" if lvl["n"] <= current else "locked")
            lessons = [{"id": l["id"], "title": l["title"], "minutes": l["minutes"], "done": self.lesson_done(l["id"])}
                       for l in lvl["lessons"]]
            challenges = [{"id": c["id"], "title": c["title"], "how": c["how"], "bonus": c["bonus"], "event": c["event"],
                           "done": self.challenge_done(c), "progress": min(self.challenge_progress(c), c["times"]),
                           "times": c["times"]} for c in lvl["challenges"]]
            for item in lessons + [c for c in challenges if not c["bonus"]]:
                total += 1
                done += bool(item["done"])
            levels.append({"n": lvl["n"], "title": lvl["title"], "phase": lvl.get("phase", ""),
                           "summary": lvl["summary"], "unlocks": lvl["unlocks"],
                           "status": status, "lessons": lessons, "challenges": challenges})
        roadmap = [{**r, "status": "soon", "lessons": [], "challenges": []} for r in ROADMAP]
        return {"title": COURSE_TITLE, "level": current, "max_level": MAX_LEVEL, "total_levels": TOTAL_LEVELS,
                "finished": self.finished(), "unlock_all": self.p["unlock_all"],
                "percent": round(100 * done / total) if total else 100, "levels": levels, "roadmap": roadmap}

    def lesson(self, lesson_id: str) -> dict:
        if lesson_id not in LESSONS:
            raise KeyError(f"Unknown lesson '{lesson_id}'")
        lesson = LESSONS[lesson_id]
        n = LESSON_LEVEL[lesson_id]
        return {
            "id": lesson_id, "level": n, "level_title": LEVELS[n - 1]["title"], "title": lesson["title"],
            "minutes": lesson["minutes"], "cards": lesson["cards"], "takeaway": lesson["takeaway"],
            "locked": n > self.level(), "done": self.lesson_done(lesson_id),
            "answered": self.p["answered"].get(lesson_id, []),
            "quiz": [{"q": q["q"], "choices": [q["choices"][j] for j in choice_order(lesson_id, i, len(q["choices"]))]}
                     for i, q in enumerate(lesson["quiz"])],
        }

    # ---------------------------------------------------------------- actions
    def answer(self, lesson_id: str, question: int, choice: int) -> dict:
        if lesson_id not in LESSONS:
            raise KeyError(f"Unknown lesson '{lesson_id}'")
        n = LESSON_LEVEL[lesson_id]
        before = self.level()
        if n > before:
            raise ValueError(f"Finish level {before} first to unlock this lesson.")
        quiz = LESSONS[lesson_id]["quiz"]
        if not 0 <= question < len(quiz):
            raise ValueError("That question doesn't exist.")
        q = quiz[question]
        if not 0 <= choice < len(q["choices"]):
            raise ValueError("That answer doesn't exist.")
        correct = choice_order(lesson_id, question, len(q["choices"]))[choice] == q["answer"]
        if correct:
            got = set(self.p["answered"].get(lesson_id, []))
            got.add(question)
            self.p["answered"][lesson_id] = sorted(got)
            self._save()
        after = self.level()
        return {"correct": correct, "why": q["why"] if correct else "", "hint": "" if correct else q.get("hint", ""),
                "lesson_done": self.lesson_done(lesson_id), "level": after, "leveled_up": after > before}

    def record(self, event: str, value: Any = None) -> None:
        """Count an action. With a value, also remember it in a set (for 'distinct' challenges)."""
        self.p["counts"][event] = int(self.p["counts"].get(event, 0)) + 1
        if value is not None:
            values = self.p["sets"].setdefault(event, [])
            if value not in values:
                values.append(value)
        self._save()

    def record_client(self, event: str, value: Any = None) -> dict:
        if event not in CLIENT_EVENTS:
            raise ValueError(f"Unknown learning event '{event}'")
        before = self.level()
        self.record(event, str(value)[:40] if value is not None else None)
        return {"level": self.level(), "leveled_up": self.level() > before}

    def record_fill(self, fill: dict) -> None:
        side, reason = fill.get("side"), fill.get("reason")
        if reason == "limit":
            self.record("limit_fill")
        if side == "buy":
            self.record("buy")
            if reason in MANUAL_REASONS:
                self.record("manual_buy")
                symbol = fill.get("symbol")
                if symbol:
                    self.record("buy_crypto" if assets.is_crypto(symbol) else "buy_stock")
        elif side == "sell":
            self.record("sell")
            if reason in MANUAL_REASONS:
                self.record("manual_sell")
            if (fill.get("pnl") or 0) > 0:
                self.record("win")
            if reason in PROTECTIVE_EXITS:
                self.record("protect_exit")
        if reason == "signal":
            self.record("auto_trade")

    def record_backtest(self, strategy: str, metrics: dict) -> None:
        self.record("backtest")
        self.record("backtest_strategy", strategy)
        ret, hold = metrics.get("total_return_pct"), metrics.get("buy_hold_return_pct")
        if isinstance(ret, (int, float)) and isinstance(hold, (int, float)) and metrics.get("num_trades") and ret > hold:
            self.record("beat_hold")

    def record_walkforward(self, result: dict) -> None:
        self.record("walkforward")
        outs = (result.get("out_sample") or {}).get("metrics") or {}
        ret, hold = outs.get("total_return_pct"), outs.get("buy_hold_return_pct")
        if isinstance(ret, (int, float)) and isinstance(hold, (int, float)) and outs.get("num_trades") and ret > hold:
            self.record("oos_beat_hold")

    def record_mix(self, result: dict) -> None:
        self.record("mix")
        if result.get("calmer_than_all"):
            self.record("mix_calm")
        if result.get("has_stock_and_crypto"):
            self.record("mix_cross")

    def record_luck(self, result: dict) -> None:
        self.record("luck_test")
        if (result.get("beat_pct") or 0) >= 95:
            self.record("luck_beat95")

    def set_unlock_all(self, on: bool) -> dict:
        self.p["unlock_all"] = bool(on)
        self._save()
        return self.summary()

    def reset(self) -> dict:
        self.p = {"answered": {}, "counts": {}, "sets": {}, "unlock_all": False, "started": time.time()}
        self._save()
        return self.summary()


# --------------------------------------------------------------------------------------------- trade explanations
def _money(x: float) -> str:
    v = abs(x)
    return f"${v:,.2f}" if v < 100 else f"${v:,.0f}"


def _held(seconds: float) -> str:
    minutes = seconds / 60
    if minutes < 1:
        return "less than a minute"
    if minutes < 90:
        return f"{minutes:.0f} minute{'s' if round(minutes) != 1 else ''}"
    hours = minutes / 60
    if hours < 36:
        return f"{hours:.1f} hours"
    return f"{hours / 24:.1f} days"


def trade_facts(trade: dict, entry: dict | None, coin: str) -> dict:
    """Plain numbers about one trade (and, for a sell, the buy it closed) used for explanations.
    `coin` is the asset's everyday name (kept as the key name for compatibility)."""
    is_stock = not assets.is_crypto(trade.get("symbol") or "BTCUSDT")
    facts: dict[str, Any] = {
        "coin": coin, "unit": "shares of " if is_stock else "", "side": trade["side"], "reason": trade.get("reason") or "manual",
        "strategy": trade.get("strategy"), "qty": trade["qty"], "price": trade["price"], "fee": trade["fee"],
        "value": trade["qty"] * trade["price"],
    }
    if trade["side"] == "sell" and entry:
        cost = entry["price"] * trade["qty"]
        gross = (trade["price"] - entry["price"]) * trade["qty"]
        entry_fee = entry["fee"] * (trade["qty"] / entry["qty"]) if entry["qty"] else entry["fee"]
        pnl = trade["pnl"] if trade.get("pnl") is not None else gross - entry_fee - trade["fee"]
        facts.update({
            "entry_price": entry["price"], "exit_price": trade["price"], "cost": cost, "gross": gross,
            "fees_total": entry_fee + trade["fee"], "pnl": pnl, "pnl_pct": 100 * pnl / cost if cost else 0.0,
            "move_pct": 100 * (trade["price"] / entry["price"] - 1) if entry["price"] else 0.0,
            "held_seconds": max(0.0, trade["ts"] - entry["ts"]),
        })
    elif trade["side"] == "sell" and trade.get("pnl") is not None:
        facts.update({"pnl": trade["pnl"]})
    return facts


def rule_explanation(f: dict) -> dict:
    """Deterministic beginner explanation. Always accurate on the numbers; used as the AI fallback."""
    coin = f["coin"]
    reason = f["reason"]
    strat = (f.get("strategy") or "your").replace("_", " ")
    if f["side"] == "buy":
        how = ("by hand" if reason == "manual" else "with a limit order" if reason == "limit"
               else f"automatically, because the {strat} strategy gave a buy signal")
        return {
            "headline": f"You bought {_money(f['value'])} of {coin}",
            "what": f"You bought {f['qty']:.6g} {f.get('unit', '')}{coin} at {_money(f['price'])} {how}. "
                    + (f"The fee was {_money(f['fee'])}." if f["fee"] > 0 else "There was no commission."),
            "why": "A buy is only half a trade. Until you sell, any profit or loss is unrealized and can still change. "
                   "Your stop-loss and take-profit decide where it ends if you don't step in.",
            "lesson": "Before every buy, know where you'll get out if you're wrong (stop-loss) and if you're right "
                      "(take-profit).",
            "lesson_id": "l2-stops",
        }
    pnl = f.get("pnl")
    if pnl is None or "entry_price" not in f:
        return {
            "headline": f"You sold {coin}" + (f" for a {'gain' if (pnl or 0) >= 0 else 'loss'} of {_money(pnl)}" if pnl is not None else ""),
            "what": f"You sold {f['qty']:.6g} {f.get('unit', '')}{coin} at {_money(f['price'])}.",
            "why": "The matching buy for this sale isn't in your history any more, so the full story isn't available.",
            "lesson": "Reviewing each trade soon after it closes is the quickest way to improve.",
            "lesson_id": "l2-drawdowns",
        }
    won = pnl > 0
    headline = f"You {'made' if won else 'lost'} {_money(pnl)} ({f['pnl_pct']:+.2f}%) on {coin}"
    direction = "rose" if f["move_pct"] >= 0 else "fell"
    what = (f"You bought at {_money(f['entry_price'])} and sold at {_money(f['exit_price'])} after "
            f"{_held(f['held_seconds'])}. The price {direction} {abs(f['move_pct']):.2f}% in that time.")
    if f["fees_total"] > 0:
        fee_note = f" Fees for buying and selling came to {_money(f['fees_total'])}"
        fee_note += ", which turned a small price gain into a loss." if f["gross"] > 0 >= pnl else "."
    else:
        fee_note = " There was no commission, but the spread still cost a little." if f["gross"] > 0 >= pnl else ""
    if reason == "stop_loss":
        why = ("The price fell to your stop-loss, so it sold automatically. That's the safety net working: the loss "
               "stayed small instead of growing.")
        lesson, lid = ("Small, planned losses are normal. What matters is that your wins end up bigger than your "
                       "losses."), "l2-sizing"
    elif reason == "trailing_stop":
        why = ("The price moved, then dropped back by your trailing-stop distance, so it sold to protect what you "
               "had." if won else "The price dropped by your trailing-stop distance, so it sold to limit the loss.")
        lesson, lid = "A trailing stop lets a winner run while protecting part of the gain.", "l2-stops"
    elif reason == "take_profit":
        why = "The price rose to your take-profit target, so it sold automatically and locked in the gain."
        lesson, lid = ("Taking profit at a planned level stops a winning trade from turning into a losing one while "
                       "you're not watching."), "l2-stops"
    elif reason == "signal":
        why = f"The {strat} strategy gave a sell signal, so auto-trading sold."
        lesson, lid = ("One trade says little about a strategy. Judge it over many trades, like a backtest does."
                       ), "l6-baseline"
    else:
        how = "with a limit order" if reason == "limit" else "by hand"
        why = f"You sold {how} after the price {direction} from {_money(f['entry_price'])} to {_money(f['exit_price'])}."
        if won:
            lesson = ("Ask yourself: did you sell because of a plan or a feeling? Writing your reason down before you "
                      "trade makes you better over time.")
        else:
            lesson = ("Selling at a loss by hand is fine if your reason for buying no longer holds. If it was fear, a "
                      "stop-loss set in advance can decide for you.")
        lid = "l2-sizing"
    return {"headline": headline, "what": what + fee_note, "why": why, "lesson": lesson, "lesson_id": lid}
