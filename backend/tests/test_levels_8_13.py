"""Levels 8-13 back end: LLM router, Code Lab sandbox, portfolios/factors, ML, research checks, agent and capstone.
Network calls are replaced with synthetic data; the Code Lab really runs Python in a subprocess."""
import asyncio
import tempfile
from pathlib import Path

import numpy as np
import pytest

from app import codelab, ml, portfolio, prices, research
from app.db import Database
from app.llm import LLMError, LLMRouter

DAY = 86400
T0 = 1_600_000_000 // DAY * DAY


def _candles(n, seed, drift=0.0003, vol=0.01, start=T0):
    rng = np.random.default_rng(seed)
    c = 100 * np.cumprod(1 + rng.normal(drift, vol, n))
    return [{"time": start + i * DAY, "open": float(x), "high": float(x) * 1.01, "low": float(x) * 0.99,
             "close": float(x), "volume": float(1e6 + rng.integers(0, 1e5)), "closed": True} for i, x in enumerate(c)]


@pytest.fixture()
def fake_prices(monkeypatch):
    async def cached(symbol, interval="1d", bars=1260):
        return _candles(min(bars, 2000), abs(hash(symbol)) % 1000)[-bars:]
    monkeypatch.setattr(prices, "cached_history", cached)
    monkeypatch.setattr(codelab, "DATA_DIR", Path(tempfile.mkdtemp()) / "codelab")
    return cached


def _db():
    return Database(Path(tempfile.mkdtemp()) / "t.db")


# ------------------------------------------------------------------------------------------ LLM router
def test_router_settings_spend_cap_and_fallback(monkeypatch):
    db = _db()
    r = LLMRouter(db, "http://127.0.0.1:9", "local-model", 1)
    monkeypatch.setattr(LLMRouter, "key", staticmethod(lambda: "sk-test"))
    r.local_available = True
    assert r.route("code") == "off"
    with pytest.raises(LLMError):
        asyncio.run(r.chat("code", []))  # AI switched off: callers use their rules
    r.update({"ai_enabled": True})
    assert r.route("code") == "local:local-model"
    r.update({"cloud_enabled": True, "cloud_tasks": ["code"], "monthly_cap_usd": 0.01})
    assert r.route("code").startswith("cloud:") and r.route("coach").startswith("local:")

    async def cloud(self, messages, temperature, max_tokens):
        return '{"ok": 1}', 0.02
    monkeypatch.setattr(LLMRouter, "_openrouter", cloud)
    out = asyncio.run(r.chat("code", [{"role": "user", "content": "hi"}]))
    assert out["json"] == {"ok": 1} and out["cost_usd"] == 0.02
    assert r.public()["cap_reached"] and r.route("code").startswith("local:")  # over the cap -> local

    async def local(self, messages, json_mode, temperature, num_ctx):
        return '{"from": "local"}'
    monkeypatch.setattr(LLMRouter, "_ollama", local)
    assert asyncio.run(r.chat("code", []))["source"] == "local:local-model"
    with pytest.raises(ValueError):
        r.update({"monthly_cap_usd": 500})
    monkeypatch.setattr(LLMRouter, "key", staticmethod(lambda: None))
    with pytest.raises(ValueError):
        LLMRouter(_db(), "x", "y", 1).update({"cloud_enabled": True})


def test_router_retries_bad_cloud_reply_then_falls_back(monkeypatch):
    r = LLMRouter(_db(), "http://127.0.0.1:9", "m", 1)
    r.local_available = True
    monkeypatch.setattr(LLMRouter, "key", staticmethod(lambda: "sk"))
    r.update({"ai_enabled": True, "cloud_enabled": True, "cloud_tasks": ["research"]})
    calls = []

    async def cloud(self, messages, temperature, max_tokens):
        calls.append(1)
        return "not json at all", 0.001
    async def local(self, messages, json_mode, temperature, num_ctx):
        return '{"a": 1}'
    monkeypatch.setattr(LLMRouter, "_openrouter", cloud)
    monkeypatch.setattr(LLMRouter, "_ollama", local)
    out = asyncio.run(r.chat("research", []))
    assert len(calls) == 2 and out["source"] == "local:m" and r.spend()["usd"] == pytest.approx(0.002)


# ------------------------------------------------------------------------------------------ Code Lab
def test_code_runs_sandboxed_with_outputs_and_errors(fake_prices):
    r = asyncio.run(codelab.run_code('import qp\nd = qp.prices("SPY")\nprint(len(d))\nqp.chart(d["close"], "c")\n'
                                     'qp.table(d.tail(2))', symbols=["SPY", "BTCUSDT"]))
    assert r["ok"] and r["stdout"].strip() == "1261" and [o["type"] for o in r["outputs"]] == ["chart", "table"]
    bad = asyncio.run(codelab.run_code("x = 1\nprint(undefined_name)", symbols=["SPY"]))
    assert not bad["ok"] and "Your code, line 2" in bad["error"] and "NameError" in bad["error"]
    slow = asyncio.run(codelab.run_code("while True:\n    pass", timeout=1.5, symbols=["SPY"]))
    assert "stopped" in slow["error"]


@pytest.mark.skipif(not codelab.SANDBOX, reason="macOS sandbox-exec only")
def test_sandbox_blocks_home_files_and_network(fake_prices):
    code = ('import os\ntry:\n    open(os.path.expanduser("~/.zshrc")).read()\n    print("LEAK")\nexcept Exception:\n'
            '    print("blocked")\nimport socket\ntry:\n    socket.create_connection(("1.1.1.1", 80), timeout=2)\n'
            '    print("NET")\nexcept Exception:\n    print("no net")')
    r = asyncio.run(codelab.run_code(code, symbols=["SPY"]))
    assert "LEAK" not in r["stdout"] and "NET" not in r["stdout"] and "no net" in r["stdout"]


def test_exercise_checker(fake_prices):
    ex = codelab.EXERCISE_BY_ID["ex-vol"]
    starter = asyncio.run(codelab.run_code(ex["starter"], extra=codelab.check_harness(ex), symbols=["SPY"]))
    assert starter["ok"] and starter["check"]["pass"] is False
    good = 'import qp, numpy as np\nvol = qp.prices("SPY")["close"].pct_change().std() * np.sqrt(252)'
    done = asyncio.run(codelab.run_code(good, extra=codelab.check_harness(ex), symbols=["SPY"]))
    assert done["check"]["pass"] is True


def test_strategy_evaluation_and_lookahead_detection(fake_prices):
    fair = asyncio.run(codelab.evaluate_strategy(codelab.STRATEGY_TEMPLATE, "SPY", 0.0, 2))
    assert fair["ok"] and fair["lookahead"] == [] and 0 < fair["time_in_market_pct"] < 100
    assert fair["out_sample"]["start"] > fair["in_sample"]["end"]
    cheat = 'def positions(df):\n    return (df["close"].shift(-1) > df["close"]).astype(int)\n'
    bad = asyncio.run(codelab.evaluate_strategy(cheat, "SPY", 0.0, 2))
    assert len(bad["lookahead"]) >= 3  # caught at (almost) every cut point
    missing = asyncio.run(codelab.evaluate_strategy("x = 1", "SPY", 0.0, 2))
    assert missing["ok"] is False and "positions" in missing["run"]["error"]


# ------------------------------------------------------------------------------------------ portfolios + factors
def test_weighting_schemes():
    rng = np.random.default_rng(1)
    rets = np.column_stack([rng.normal(0.0005, s, 800) for s in (0.005, 0.01, 0.03)])
    for scheme in portfolio.SCHEMES:
        w = portfolio.weights_for(scheme, rets)
        assert w.min() >= -1e-12 and abs(w.sum() - 1) < 1e-9, scheme
    rp = portfolio.weights_for("risk_parity", rets)
    assert np.allclose(portfolio.risk_contributions(rp, rets), 1 / 3, atol=1e-3)
    mv = portfolio.weights_for("min_variance", rets)
    assert mv[0] > mv[2]  # calmest asset gets the most
    assert portfolio._project_simplex(np.array([2.0, -1.0, 0.5])).sum() == pytest.approx(1)


def test_simulate_rebalancing_costs_and_vol_target():
    rets = np.array([[0.1, 0.0], [0.0, 0.0], [0.0, 0.0]])
    w = np.array([0.5, 0.5])
    free, _ = portfolio.simulate(rets, w, "never", 0.0)
    assert free[0] == pytest.approx(0.05)
    costly, turnover = portfolio.simulate(rets, w, "daily", 0.01)
    assert costly[0] < free[0] and turnover > 0
    r = np.random.default_rng(2).normal(0, 0.02, 400)
    vt = portfolio.vol_target(r, 0.10)
    assert vt[30:].std() < r[30:].std()


def test_compare_kelly_and_factors():
    series = {s: _candles(900, i) for i, s in enumerate(["SPY", "TLT", "GLD", "QQQ"])}
    out = portfolio.compare(series, target_vol=0.1)
    assert {r["scheme"] for r in out["rows"]} == set(portfolio.SCHEMES) and "vol_target" in out["rows"][0]
    k = portfolio.kelly(_candles(800, 3, drift=0.001), "SPY")
    assert k["half_kelly"] == pytest.approx(k["full_kelly"] / 2, abs=0.01) and len(k["paths"]) == 3
    rng = np.random.default_rng(5)
    mkt = rng.normal(0.0004, 0.01, 700)
    factors = {etf: [{"time": T0 + i * DAY, "close": float(v)} for i, v in
                     enumerate(100 * np.cumprod(1 + mkt + (rng.normal(0, 0.004, 700) if etf != "SPY" else 0)))]
               for etf in portfolio.FACTOR_ETFS}
    target = [{"time": T0 + i * DAY, "close": float(v)} for i, v in enumerate(100 * np.cumprod(1 + 1.5 * mkt))]
    fb = portfolio.factor_breakdown(factors, "TEST", target)
    market = next(l for l in fb["loadings"] if l["factor"] == "market")
    assert market["beta"] == pytest.approx(1.5, abs=0.05) and fb["r2"] > 0.95 and "alpha" in fb["verdict"]


# ------------------------------------------------------------------------------------------ ML
def test_ml_honest_split_shuffle_and_leak():
    c = _candles(1500, 7)
    honest = ml.train(c, "SPY", "logistic", 5)
    assert 30 < honest["accuracy_test_pct"] < 75 and "strategy" in honest and honest["test_rows"] > 300
    leak = ml.train(c, "SPY", "logistic", 5, leak=True)
    assert leak["accuracy_test_pct"] > 90 and leak["importance"][0]["feature"] == ml.LEAK and "leak" in leak["verdict"]
    shuffled = ml.train(c, "SPY", "boosting", 5, shuffle=True)
    assert "strategy" not in shuffled and "Shuffled" in shuffled["verdict"]
    with pytest.raises(ValueError):
        ml.train(c[:300], "SPY")


def test_ml_features_use_only_the_past():
    c = _candles(400, 8)
    full = ml.build_features(c, 5)
    part = ml.build_features(c[:300], 5)
    cols = list(ml.FEATURES)
    assert np.allclose(full[cols].iloc[250:290].to_numpy(), part[cols].iloc[250:290].to_numpy(), equal_nan=True)


# ------------------------------------------------------------------------------------------ research checks
SOURCE = ("Revenue of $96.2 billion, up 106% from a year ago. Data Center revenue of $89.0 billion, up 117% "
          "from a year ago. NVIDIA Corporation (NVDA) reported on August 26, 2026.")


def test_verify_extraction_flags_hallucinations():
    raw = {"summary": "Revenue was $96.2 billion and margins hit 81%.", "sentiment": 3,
           "key_numbers": [{"label": "Revenue", "value": "$96.2 billion", "quote": "Revenue of $96.2 billion, up 106% from a year ago"},
                           {"label": "Made up", "value": "$12.0 billion", "quote": "Gaming revenue of $12.0 billion"},
                           {"label": "Wrong value", "value": "$90.0 billion", "quote": "Data Center revenue of $89.0 billion"}]}
    out = research.verify_extraction(raw, SOURCE)
    assert [n["verified"] for n in out["key_numbers"]] == [True, False, False]
    assert out["key_numbers"][1]["problem"] == "quote not found in the filing"
    assert out["key_numbers"][2]["problem"] == "value doesn't match its quote"
    assert out["unsupported_summary_numbers"] == ["81%"] and out["sentiment"] == 1.0 and out["trust"] == "check"


def test_anonymise_and_html_to_text():
    text = research.anonymise(SOURCE, research._short_name("NVIDIA CORP") + ["NVDA"])
    assert "NVIDIA" not in text and "NVDA" not in text and "2026" not in text and "August" not in text
    assert "[COMPANY]" in text and "$96.2 billion" in text
    html = "<html><head><style>x{}</style></head><body><p>Revenue&nbsp;rose</p><table><tr><td>Q2</td><td>$5</td></tr></table></body></html>"
    plain = research.html_to_text(html)
    assert "Revenue rose" in plain and "Q2 | $5" in plain and "x{}" not in plain


# ------------------------------------------------------------------------------------------ agent + capstone
def _engine(tmp_path, monkeypatch):
    from dataclasses import replace

    from app.config import settings
    from app.engine import TradingEngine

    cfg = replace(settings, symbols=["SPY", "BTCUSDT"], db_path=tmp_path / "e.db", ollama_url="http://127.0.0.1:9",
                  ollama_timeout=1.0)
    eng = TradingEngine(cfg, enable_market=False)
    eng.market.last_price.update({"SPY": 500.0, "BTCUSDT": 60_000.0})
    monkeypatch.setattr(eng, "market_open_for", lambda s: True)
    return eng


def test_agent_proposes_sizes_and_executes_on_approval(tmp_path, monkeypatch, fake_prices):
    eng = _engine(tmp_path, monkeypatch)
    eng.agent.update({"watchlist": ["SPY", "BTCUSDT"], "use_research": False,
                      "strategy": {"type": "builtin", "name": "momentum", "params": {"lookback": 20, "threshold": 0.0}}})

    async def always_in(symbol, candles):
        return 1, "test strategy says hold"
    monkeypatch.setattr(eng.agent, "_signal", always_in)
    run = asyncio.run(eng.agent.run_once(manual=True))
    assert len(run["new"]) == 2 and all(p["status"] == "pending" for p in run["new"])
    p = run["new"][0]
    assert p["side"] == "buy" and p["stop_loss"] < p["price"] and p["notional"] <= 0.2 * 100_000 + 1
    assert p["ai"]["source"] == "rules" and p["ai"]["note"].startswith("Rule check")  # AI off -> rule check
    again = asyncio.run(eng.agent.run_once())
    assert again["new"] == []  # proposals already waiting
    asyncio.run(eng.agent.decide(p["id"], True))
    assert p["symbol"] in eng.broker.positions and eng.broker.positions[p["symbol"]].strategy == "agent"
    assert eng.learn.p["counts"]["agent_approve"] == 1
    with pytest.raises(ValueError):
        asyncio.run(eng.agent.decide(p["id"], True))
    state = asyncio.run(eng.agent.kill(sell_positions=True))
    assert state["sold"] == 1 and not eng.broker.positions and not state["pending"] and not state["config"]["enabled"]
    with pytest.raises(ValueError):
        eng.agent.update({"mode": "auto"})  # level 13 only


def test_capstone_start_and_report(tmp_path, monkeypatch):
    eng = _engine(tmp_path, monkeypatch)
    with pytest.raises(ValueError):
        asyncio.run(eng.capstone.start("too short"))
    st = asyncio.run(eng.capstone.start("I will hold a risk-parity mix of SPY, TLT and GLD, rebalance monthly and stop "
                                        "if the account falls 10%."))
    assert st["days"] == 0 and not st["ready"]
    rep = asyncio.run(eng.capstone.report())
    assert rep["final"] is False and rep["source"] == "rules" and "SPY" in rep["writeup"]["headline"]
    assert "capstone_report" not in eng.learn.p["counts"]


def test_apply_portfolio_respects_and_can_fit_limits(tmp_path, monkeypatch):
    from app.broker import RiskError

    eng = _engine(tmp_path, monkeypatch)
    with pytest.raises(RiskError):
        asyncio.run(eng.apply_portfolio({"SPY": 60, "BTCUSDT": 40}, 0.9))
    out = asyncio.run(eng.apply_portfolio({"SPY": 60, "BTCUSDT": 40}, 0.9, fit_limits=True))
    assert len(out["trades"]) == 2 and eng.broker.limits.max_position_pct >= 0.54
    assert all(p.stop_loss is None for p in eng.broker.positions.values())
    spy_value = eng.broker.positions["SPY"].qty * 500
    assert spy_value == pytest.approx(0.54 * 100_000, rel=0.01)
    asyncio.run(eng.apply_portfolio({"SPY": 100}, 0.5, fit_limits=True))
    assert list(eng.broker.positions) == ["SPY"] and eng.learn.p["counts"]["portfolio_rebalance"] == 1


# ------------------------------------------------------------------------------------------ AI off
def test_rule_assist_explains_errors_and_hints():
    ex = codelab.EXERCISE_BY_ID["ex-vol"]
    out = codelab.rule_assist("vol = None", "Traceback\n  Your code, line 4\nTypeError: unsupported", "", ex, "explain", "")
    assert out["source"] == "rules" and "TypeError on line 4" in out["explanation"] and len(out["hints"]) == 1
    assert len(codelab.rule_assist("", "", "", ex, "hint", "")["hints"]) == 3
    peek = codelab.rule_assist("x = close.shift(-1)", "", "", None, "explain", "")
    assert "look-ahead" in peek["explanation"]
    assert all(e["hints"] for e in codelab.EXERCISES)


def test_rule_extract_and_lexicon():
    text = ("Forward-looking statements are subject to risks. The Company posted quarterly revenue of $114.5 billion, "
            "up 12 percent year over year. Diluted earnings per share was $2.10, up 14 percent. Gross margin was 47.9 "
            "percent. The board declared a cash dividend of $0.27 per share. Revenue is expected to be $120.0 billion "
            "next quarter. Weak demand and tariff headwinds remain a risk to margins.")
    out = research.rule_extract(text)
    values = {n["label"]: n["value"] for n in out["key_numbers"]}
    assert values["Revenue"] == "$114.5 billion" and values["Earnings per share"] == "$2.10"
    assert values["Gross margin"] == "47.9 percent" and values["Dividend"] == "$0.27"
    assert "expected" in out["guidance"] and out["risks"] and "Forward-looking" not in out["summary"]
    checked = research.verify_extraction(out, text)
    assert checked["verified"] == checked["checked"] == len(out["key_numbers"]) and checked["trust"] == "high"
    assert research.lexicon_score("Record revenue, strong growth")[0] > 0 > research.lexicon_score("Shares plunge on weak outlook and probe")[0]


def test_agent_rule_check():
    from app.agent import Agent

    calm = Agent._rule_check({"side": "buy", "news_average": 0.1, "vol_ratio": 1.0, "market_open": True})
    assert calm["concern"] is False and "no red flags" in calm["note"]
    bad = Agent._rule_check({"side": "buy", "news_average": -0.5, "vol_ratio": 2.2, "market_open": False})
    assert bad["concern"] is True and "negative" in bad["note"] and "jumpy" in bad["note"] and "closed" in bad["note"]
