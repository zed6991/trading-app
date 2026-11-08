"""Learning path: curriculum integrity, progression rules and trade explanations."""
import tempfile
from pathlib import Path

import pytest

from app.db import Database
from app.learn import (CLIENT_EVENTS, GLOSSARY, GLOSSARY_BY_SLUG, LESSONS, LEVELS, MAX_LEVEL, Learning,
                       rule_explanation, term_slugs, trade_facts)


@pytest.fixture()
def learning():
    return Learning(Database(Path(tempfile.mkdtemp()) / "learn.db"))


def _all_text(lesson):
    parts = [lesson["takeaway"]]
    for c in lesson["cards"]:
        parts += [c["title"], c["body"]]
    for q in lesson["quiz"]:
        parts += [q["q"], q["why"], *q["choices"]]
    return parts


def test_glossary_slugs_unique_and_complete():
    slugs = [g["slug"] for g in GLOSSARY]
    assert len(slugs) == len(set(slugs))
    for g in GLOSSARY:
        assert g["short"] and g["term"] and 1 <= g["level"] <= MAX_LEVEL


def test_every_glossary_link_resolves():
    for lesson in LESSONS.values():
        for text in _all_text(lesson):
            for slug in term_slugs(text):
                assert slug in GLOSSARY_BY_SLUG, f"{lesson['id']} links unknown term {slug!r}"


def test_quizzes_are_well_formed():
    assert len(LESSONS) == sum(len(lvl["lessons"]) for lvl in LEVELS)  # ids unique
    for lesson in LESSONS.values():
        assert lesson["cards"] and lesson["quiz"]
        for q in lesson["quiz"]:
            assert 0 <= q["answer"] < len(q["choices"]) and q["why"]


def test_challenges_reachable():
    known = {"manual_buy", "manual_sell", "win", "risk", "brackets", "protect_exit", "analyze", "backtest",
             "backtest_strategy", "beat_hold", "auto_on", "explain", "auto_trade"} | CLIENT_EVENTS
    ids = set()
    for lvl in LEVELS:
        assert any(not c["bonus"] for c in lvl["challenges"])
        for c in lvl["challenges"]:
            assert c["event"] in known
            assert c["id"] not in ids
            ids.add(c["id"])


def _finish_lessons(learning, n):
    for lesson in LEVELS[n - 1]["lessons"]:
        for i, q in enumerate(lesson["quiz"]):
            learning.answer(lesson["id"], i, q["answer"])


def test_progression(learning):
    assert learning.level() == 1
    with pytest.raises(ValueError):
        learning.answer("l2-stops", 0, 1)  # locked
    res = learning.answer("l1-what-is-trading", 0, 1)
    assert res["correct"] is False and res["why"] == ""
    _finish_lessons(learning, 1)
    assert learning.level() == 1  # challenges still missing
    learning.record_fill({"side": "buy", "reason": "manual"})
    learning.record_fill({"side": "sell", "reason": "manual", "pnl": -3.0})
    assert learning.level() == 2
    lvl1 = learning.summary()["levels"][0]
    assert lvl1["status"] == "done"
    assert [c["done"] for c in lvl1["challenges"]] == [True, True, False]  # bonus 'win' not needed


def test_distinct_challenge_and_persistence(learning):
    learning.record_client("chart", "BTCUSDT")
    learning.record_client("chart", "BTCUSDT")
    ch = next(c for c in LEVELS[2]["challenges"] if c["event"] == "chart")
    assert learning.challenge_progress(ch) == 1
    learning.record_client("chart", "ETHUSDT")
    assert learning.challenge_done(ch)
    reloaded = Learning(learning.db)
    assert reloaded.challenge_done(ch)
    with pytest.raises(ValueError):
        learning.record_client("manual_buy")  # server-side events can't be faked from the browser


def test_backtest_events(learning):
    learning.record_backtest("sma_cross", {"total_return_pct": 5, "buy_hold_return_pct": 2, "num_trades": 4})
    learning.record_backtest("rsi_reversion", {"total_return_pct": -1, "buy_hold_return_pct": 2, "num_trades": 4})
    assert learning.p["counts"]["beat_hold"] == 1
    assert sorted(learning.p["sets"]["backtest_strategy"]) == ["rsi_reversion", "sma_cross"]


def test_unlock_all_and_reset(learning):
    assert learning.set_unlock_all(True)["level"] == MAX_LEVEL
    assert learning.lesson("l6-plan")["locked"] is False
    assert learning.reset()["level"] == 1


def test_lesson_hides_answers(learning):
    lesson = learning.lesson("l1-what-is-trading")
    assert all(set(q) == {"q", "choices"} for q in lesson["quiz"])


def test_explanations():
    buy = {"id": 1, "ts": 1000.0, "symbol": "BTCUSDT", "side": "buy", "qty": 0.1, "price": 50_000.0, "fee": 5.0,
           "pnl": None, "reason": "manual", "strategy": None}
    stop = {**buy, "id": 2, "ts": 1600.0, "side": "sell", "price": 49_000.0, "fee": 4.9, "pnl": -109.9,
            "reason": "stop_loss"}
    f = trade_facts(stop, buy, "Bitcoin")
    assert f["held_seconds"] == 600 and f["move_pct"] == pytest.approx(-2.0)
    e = rule_explanation(f)
    assert e["headline"].startswith("You lost $110") and "stop-loss" in e["why"]
    assert rule_explanation(trade_facts(buy, None, "Bitcoin"))["headline"] == "You bought $5,000 of Bitcoin"
    tiny = {**stop, "price": 50_050.0, "pnl": -4.995, "reason": "manual"}
    assert "turned a small price gain into a loss" in rule_explanation(trade_facts(tiny, buy, "Bitcoin"))["what"]
    orphan = rule_explanation(trade_facts(stop, None, "Bitcoin"))
    assert "isn't in your history" in orphan["why"]
