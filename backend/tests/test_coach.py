"""Coach: trade stats, habit spotting, number checking, review/chat fallbacks and AI rewording."""
import asyncio
import json
import time
from pathlib import Path

import pytest

from app.coach import Coach, numbers_match, observations, pair_trades, trade_stats
from app.db import Database
from app.learn import Learning

NOW = time.time()


def _fill(i, side, price, ts, qty=1.0, reason="manual", pnl=None, symbol="BTCUSDT"):
    return {"id": i, "ts": ts, "symbol": symbol, "side": side, "qty": qty, "price": price,
            "fee": price * qty * 0.001, "pnl": pnl, "reason": reason, "strategy": None}


LIMITS = {"max_position_pct": 0.2, "max_open_positions": 3, "stop_loss_pct": 0.02, "take_profit_pct": 0.04,
          "trailing_stop_pct": 0, "daily_loss_limit_pct": 0.05}


class FakeAI:
    model = "fake"

    def __init__(self, reply):
        self.reply = reply
        self.calls = []

    async def _ask_model(self, messages, temperature=0.2):
        self.calls.append(messages)
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply if isinstance(self.reply, str) else json.dumps(self.reply)


class FakeEngine:
    def __init__(self, tmp: Path, ai_reply=None, available=False):
        self.db = Database(tmp / "coach.db")
        self.learn = Learning(self.db)
        self.ai = FakeAI(ai_reply)
        self.ai_health = {"available": available}
        self.limits = dict(LIMITS)

    def account(self):
        return {"cash": 90_000.0, "equity": 100_500.0, "total_return_pct": 0.5, "fees_paid": 12.0,
                "positions": [{"symbol": "ETHUSDT", "qty": 2.0, "avg_price": 3000.0, "mark": 3050.0,
                               "unrealized_pnl": 100.0, "pnl_pct": 1.67, "stop_loss": 2940.0, "take_profit": 3120.0}]}

    def risk(self):
        return self.limits

    def prices(self):
        return {"BTCUSDT": 60_000.0, "ETHUSDT": 3050.0}

    def add(self, f):
        self.db.execute("INSERT INTO trades(ts, symbol, side, qty, price, fee, pnl, reason, strategy) "
                        "VALUES(?,?,?,?,?,?,?,?,?)",
                        (f["ts"], f["symbol"], f["side"], f["qty"], f["price"], f["fee"], f["pnl"], f["reason"], None))


@pytest.fixture
def eng(tmp_path):
    return FakeEngine(tmp_path)


def test_pair_and_stats():
    t0 = NOW - 3600
    trades = [
        _fill(1, "buy", 100, t0), _fill(2, "sell", 110, t0 + 60, pnl=9.79, reason="take_profit"),
        _fill(3, "buy", 100, t0 + 120), _fill(4, "sell", 95, t0 + 180, pnl=-5.2),
        _fill(5, "buy", 100, t0 + 300),  # bought again 2 minutes after a loss
    ]
    rounds = pair_trades(trades)
    assert [r["entry"] for r in rounds] == [100, 100] and rounds[0]["held"] == 60
    s = trade_stats(trades, t0 - 1)
    assert (s["closed"], s["wins"], s["losses"], s["buys"]) == (2, 1, 1, 3)
    assert s["win_rate_pct"] == 50.0 and s["net_pnl"] == pytest.approx(4.59)
    assert s["protected_exits"] == 1 and s["quick_flips"] == 2 and s["rebuys_after_loss"] == 1
    assert s["manual_losses"] == 1


def test_window_excludes_old_trades_but_pairs_with_earlier_buy():
    trades = [_fill(1, "buy", 100, NOW - 10 * 86400), _fill(2, "buy", 50, NOW - 8 * 86400, symbol="ETHUSDT"),
              _fill(3, "sell", 60, NOW - 100, symbol="ETHUSDT", pnl=9.89)]
    s = trade_stats(trades, NOW - 7 * 86400)
    assert s["closed"] == 1 and s["buys"] == 0 and s["wins"] == 1


def test_observations_spot_bad_habits():
    s = trade_stats([], NOW)
    assert observations(s, LIMITS, 1)[0]["kind"] == "work"  # no trades yet
    t0 = NOW - 3600
    trades = []
    for k in range(3):  # three quick losing flips, each followed by a rebuy
        base = t0 + k * 200
        trades += [_fill(10 * k, "buy", 100, base), _fill(10 * k + 1, "sell", 97, base + 30, pnl=-3.2)]
    trades.append(_fill(99, "buy", 100, t0 + 640))
    obs = observations(trade_stats(trades, t0 - 1), {**LIMITS, "stop_loss_pct": 0, "max_position_pct": 0.5}, 2)
    texts = " ".join(o["text"] for o in obs if o["kind"] == "work")
    assert "within 5 minutes" in texts and "within 10 minutes of a loss" in texts
    assert "stop-loss is switched off" in texts and "50% of your account" in texts
    assert all(o["lesson"] is None or o["lesson"]["id"] for o in obs)


def test_level_one_skips_level_two_advice():
    obs = observations(trade_stats([_fill(1, "buy", 100, NOW - 60)], NOW - 3600),
                       {**LIMITS, "stop_loss_pct": 0}, 1)
    assert not any("stop-loss" in o["text"] for o in obs)


def test_numbers_match():
    allowed = 'net_pnl 4.59, avg_win "$9.79", fees $12.40'
    assert numbers_match("You made $4.59 and paid $12.4 in fees over 3 trades.", allowed)
    assert not numbers_match("You made $45.90.", allowed)
    assert numbers_match("Level 2 and 1,000 isn't there", allowed + " 1000")


def test_rule_review_and_next_step(eng):
    eng.add(_fill(1, "buy", 100, NOW - 600))
    eng.add(_fill(2, "sell", 104, NOW - 300, pnl=3.8, reason="take_profit"))
    r = asyncio.run(eng.ai and Coach(eng).review(7))
    assert r["source"] == "rules" and r["stats"]["closed"] == 1
    assert r["headline"].startswith("1 closed trade") and r["went_well"]
    assert r["next"]["primary"]["kind"] == "lesson" and r["next"]["primary"]["id"] == "l1-what-is-trading"
    assert eng.learn.p["counts"]["coach_review"] == 1


def test_review_uses_ai_wording_when_numbers_hold(tmp_path):
    reply = {"headline": "Nice start: 1 trade, up $3.80.", "went_well": ["Your plan sold at the target."],
             "work_on": [], "next_step": "Try the next lesson."}
    eng = FakeEngine(tmp_path, reply, available=True)
    eng.add(_fill(1, "buy", 100, NOW - 600))
    eng.add(_fill(2, "sell", 104, NOW - 300, pnl=3.8, reason="take_profit"))
    r = asyncio.run(Coach(eng).review(7))
    assert r["source"] == "ollama:fake" and r["headline"] == reply["headline"]
    # Cached: a second call doesn't hit the model again.
    asyncio.run(Coach(eng).review(7))
    assert len(eng.ai.calls) == 1


def test_review_rejects_invented_numbers(tmp_path):
    reply = {"headline": "You made $380 this week!", "went_well": ["Great."], "work_on": [], "next_step": "Go on."}
    eng = FakeEngine(tmp_path, reply, available=True)
    eng.add(_fill(1, "buy", 100, NOW - 600))
    eng.add(_fill(2, "sell", 104, NOW - 300, pnl=3.8, reason="take_profit"))
    assert asyncio.run(Coach(eng).review(7))["source"] == "rules"


def test_offline_chat_defines_terms_and_routes(eng):
    coach = Coach(eng)
    out = asyncio.run(coach.ask("what is a stop-loss?"))
    assert out["source"] == "rules" and out["answer"].startswith("Stop-loss")
    assert "Level 2" in out["answer"] and out["lesson"] is None  # not taught yet at level 1
    out = asyncio.run(coach.ask("What does equity mean"))
    assert out["answer"].startswith("Equity") and out["lesson"]["id"] == "l1-your-account"
    out = asyncio.run(coach.ask("how am I doing?"))
    assert "No trades" in out["answer"]
    out = asyncio.run(coach.ask("tell me a joke"))
    assert "next step" in out["answer"] and out["lesson"]["id"] == "l1-what-is-trading"
    assert eng.learn.p["counts"]["coach_ask"] == 4
    with pytest.raises(ValueError):
        asyncio.run(coach.ask("   "))


def test_ai_chat_context_and_lesson_gating(tmp_path):
    reply = {"answer": "Equity is cash plus coins. Yours is $100,500.", "lesson_id": "l4-momentum",
             "follow_ups": ["What are fees?", "x", "y", "z"]}
    eng = FakeEngine(tmp_path, reply, available=True)
    out = asyncio.run(Coach(eng).ask("what's my equity?", [{"role": "user", "content": "hi"},
                                                         {"role": "assistant", "content": "hello"}], "practice"))
    assert out["source"] == "ollama:fake" and out["lesson"] is None  # level-4 lesson is locked at level 1
    assert len(out["follow_ups"]) == 3
    msgs = eng.ai.calls[0]
    facts = msgs[1]["content"]
    assert "Owns 2 Ethereum" in facts and "Practice screen" in facts and "Words not taught yet" in facts
    assert [m["role"] for m in msgs[2:]] == ["user", "assistant", "user"]


def test_ai_chat_falls_back_on_error(tmp_path):
    eng = FakeEngine(tmp_path, RuntimeError("boom"), available=True)
    out = asyncio.run(Coach(eng).ask("what is equity"))
    assert out["source"] == "rules" and out["answer"].startswith("Equity")
    assert "offline" not in out["answer"]


def test_fees_bigger_than_price_moves_and_something_good():
    t0 = NOW - 3600
    trades = [_fill(1, "buy", 1000, t0), _fill(2, "sell", 999.5, t0 + 600, pnl=-2.5)]  # moved -0.5, fees ~2
    obs = observations(trade_stats(trades, t0 - 1), LIMITS, 1)
    assert any(o["kind"] == "work" and "more than the price moves" in o["text"] for o in obs)
    assert any(o["kind"] == "good" for o in obs)


def test_review_keeps_empty_sections_empty(tmp_path):
    reply = {"headline": "1 trade, up $3.80.", "went_well": ["Sold at the target."],
             "work_on": ["Something invented."], "next_step": "Try the next lesson."}
    eng = FakeEngine(tmp_path, reply, available=True)
    eng.add(_fill(1, "buy", 100, NOW - 600))
    eng.add(_fill(2, "sell", 104, NOW - 300, pnl=3.8, reason="take_profit"))
    r = asyncio.run(Coach(eng).review(7))
    assert r["source"] == "ollama:fake" and r["work_on"] == []
