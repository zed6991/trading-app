"""API integration tests. Market data is disabled and the DB lives in a temp dir.

app.config reads environment variables at IMPORT time, so the env must be set before any `app.*` import.
"""
import os
import tempfile
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="trading-app-test-")
os.environ["MARKET_DISABLED"] = "1"
os.environ["DB_PATH"] = str(Path(_TMP) / "test.db")
os.environ["SYMBOLS"] = "BTCUSDT,ETHUSDT"
os.environ["OLLAMA_URL"] = "http://127.0.0.1:9"  # unreachable -> fast failure, no real AI calls
os.environ["OLLAMA_TIMEOUT"] = "2"

import importlib  # noqa: E402
import sys  # noqa: E402
from dataclasses import replace  # noqa: E402

# Another test module may already have imported app.config (with the real DB path). Re-read the env so that
# app.main's module-level engine is built against the temp DB too.
if "app.config" in sys.modules and "app.main" not in sys.modules:
    importlib.reload(sys.modules["app.config"])

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import main  # noqa: E402
from app.engine import TradingEngine  # noqa: E402

# If another test module imported app.config before us, the env above was not applied at import time,
# so always build the engine from an explicit settings copy pointing at the temp DB.
TEST_SETTINGS = replace(
    main.settings,
    symbols=["BTCUSDT", "ETHUSDT"],
    db_path=Path(_TMP) / "test.db",
    ollama_url="http://127.0.0.1:9",
    ollama_timeout=2.0,
)

BTC_PRICE = 50_000.0


@pytest.fixture(scope="module")
def client():
    main.engine = TradingEngine(TEST_SETTINGS, enable_market=False)
    with TestClient(main.app) as c:
        main.engine.market.last_price["BTCUSDT"] = BTC_PRICE
        main.engine.market.last_price["ETHUSDT"] = 2_500.0
        yield c


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["market_connected"] is False
    assert "ai_available" in body


def test_state(client):
    r = client.get("/api/state")
    assert r.status_code == 200
    s = r.json()
    for key in ("symbols", "interval", "account", "engine", "risk", "status", "prices"):
        assert key in s
    assert s["symbols"] == ["BTCUSDT", "ETHUSDT"]
    assert s["engine"]["auto_trading"] is False
    assert s["engine"]["ai_filter"] is False
    assert s["engine"]["strategies"]["BTCUSDT"]["name"] == "sma_cross"
    assert s["prices"]["BTCUSDT"] == BTC_PRICE
    assert s["account"]["cash"] == pytest.approx(TEST_SETTINGS.starting_cash)
    assert set(s["status"]) >= {"market_connected", "ai_available", "ai_model"}


def test_strategies(client):
    r = client.get("/api/strategies")
    assert r.status_code == 200
    names = {s["name"] for s in r.json()}
    assert {"sma_cross", "ema_macd", "rsi_reversion", "bollinger_breakout"} <= names
    for s in r.json():
        assert isinstance(s["params"], dict) and s["description"]


def test_engine_update(client):
    r = client.put("/api/engine", json={"auto_trading": True, "strategies": {"ETHUSDT": {"name": "rsi_reversion", "params": {}}}})
    assert r.status_code == 200, r.text
    cfg = r.json()
    assert cfg["auto_trading"] is True
    assert cfg["ai_filter"] is False
    assert cfg["strategies"]["ETHUSDT"]["name"] == "rsi_reversion"
    assert cfg["strategies"]["BTCUSDT"]["name"] == "sma_cross"
    assert main.engine.strategy_for("ETHUSDT").name == "rsi_reversion"

    bad = client.put("/api/engine", json={"strategies": {"BTCUSDT": {"name": "does_not_exist"}}})
    assert bad.status_code == 400
    assert "detail" in bad.json()

    r = client.put("/api/engine", json={"auto_trading": False})
    assert r.json()["auto_trading"] is False
    assert r.json()["strategies"]["ETHUSDT"]["name"] == "rsi_reversion"  # partial update keeps the rest


def test_risk_update(client):
    r = client.put("/api/risk", json={"stop_loss_pct": 0.03, "max_open_positions": 5})
    assert r.status_code == 200, r.text
    limits = r.json()
    assert limits["stop_loss_pct"] == pytest.approx(0.03)
    assert limits["max_open_positions"] == 5
    assert client.get("/api/state").json()["risk"]["stop_loss_pct"] == pytest.approx(0.03)


def test_manual_order_and_close(client):
    r = client.post("/api/orders", json={"symbol": "BTCUSDT", "side": "buy", "qty": 0.1})
    assert r.status_code == 200, r.text
    fill = r.json()
    assert fill["side"] == "buy" and fill["symbol"] == "BTCUSDT"
    assert fill["qty"] == pytest.approx(0.1)
    assert fill["price"] >= BTC_PRICE  # slippage on buys
    assert fill["pnl"] is None
    assert isinstance(fill["id"], int)

    acct = client.get("/api/state").json()["account"]
    assert [p["symbol"] for p in acct["positions"]] == ["BTCUSDT"]
    assert acct["cash"] < TEST_SETTINGS.starting_cash

    dup = client.post("/api/orders", json={"symbol": "BTCUSDT", "side": "buy", "qty": 0.1})
    assert dup.status_code == 400  # position already open

    r = client.put("/api/positions/BTCUSDT", json={"stop_loss": 45_000, "take_profit": 60_000})
    assert r.status_code == 200, r.text
    assert r.json()["stop_loss"] == pytest.approx(45_000)

    main.engine.market.last_price["BTCUSDT"] = 51_000.0
    r = client.post("/api/positions/BTCUSDT/close")
    assert r.status_code == 200, r.text
    sell = r.json()
    assert sell["side"] == "sell" and sell["pnl"] is not None and sell["pnl"] > 0
    assert client.get("/api/state").json()["account"]["positions"] == []

    again = client.post("/api/positions/BTCUSDT/close")
    assert again.status_code == 400


def test_order_validation(client):
    assert client.post("/api/orders", json={"symbol": "BTCUSDT", "side": "hold"}).status_code == 400
    assert client.post("/api/orders", json={"symbol": "DOGEUSDT", "side": "buy"}).status_code == 400


def test_trades_listing(client):
    r = client.get("/api/trades?limit=10")
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) >= 2
    assert rows[0]["side"] == "sell" and rows[1]["side"] == "buy"  # newest first
    assert rows[0]["id"] > rows[1]["id"]


def test_logs_and_equity(client):
    logs = client.get("/api/logs?limit=50").json()
    assert logs and {"ts", "level", "message"} <= set(logs[0])
    assert logs[0]["ts"] >= logs[-1]["ts"]
    eq = client.get("/api/equity").json()
    assert isinstance(eq, list)


def test_reset_and_resume(client):
    r = client.post("/api/account/reset", json={"cash": 25_000})
    assert r.status_code == 200, r.text
    acct = r.json()
    assert acct["cash"] == pytest.approx(25_000)
    assert acct["starting_cash"] == pytest.approx(25_000)
    assert acct["positions"] == []
    eq = client.get("/api/equity").json()
    assert len(eq) == 1 and eq[0]["value"] == pytest.approx(25_000)
    r = client.post("/api/account/resume")
    assert r.status_code == 200
    assert r.json()["halted"] is False


def test_backtest_validation(client):
    r = client.post("/api/backtest", json={"symbol": "BTCUSDT", "interval": "1h", "bars": 500, "strategy": "nope"})
    assert r.status_code == 400
    r = client.post("/api/backtest", json={"symbol": "BTCUSDT", "interval": "7m", "bars": 500, "strategy": "sma_cross"})
    assert r.status_code == 400


def test_websocket_snapshot_and_ping(client):
    with client.websocket_connect("/ws") as ws:
        msg = ws.receive_json()
        assert msg["type"] == "snapshot"
        assert {"symbols", "account", "engine", "risk", "status", "prices"} <= set(msg["data"])
        ws.send_json({"type": "ping"})
        for _ in range(20):
            reply = ws.receive_json()
            if reply["type"] == "pong":
                break
        assert reply == {"type": "pong"}


def test_websocket_receives_trade_broadcast(client):
    main.engine.market.last_price["ETHUSDT"] = 2_500.0
    with client.websocket_connect("/ws") as ws:
        assert ws.receive_json()["type"] == "snapshot"
        r = client.post("/api/orders", json={"symbol": "ETHUSDT", "side": "buy", "qty": 1})
        assert r.status_code == 200, r.text
        seen = set()
        for _ in range(20):
            seen.add(ws.receive_json()["type"])
            if {"trade", "account"} <= seen:
                break
        assert {"trade", "account"} <= seen
    client.post("/api/positions/ETHUSDT/close")


def test_persistence_roundtrip(client):
    stored = main.engine.db.get_kv("broker")
    assert isinstance(stored, dict)
    assert main.engine.db.get_kv("engine")["strategies"]["ETHUSDT"]["name"] == "rsi_reversion"
    assert main.engine.db.get_kv("risk")["stop_loss_pct"] == pytest.approx(0.03)


def test_advice_gives_one_plain_action(client, monkeypatch):
    async def history(*_):
        return [{"time": 1_700_000_000 + i * 3600, "open": 100, "high": 101, "low": 99, "close": 100, "volume": 1}
                for i in range(100)]

    async def analyze(symbol, candles, indicators, position):
        return {"symbol": symbol, "recommendation": verdict, "bias": "neutral", "confidence": 0.5,
                "summary": "Plain words.", "source": "test"}

    monkeypatch.setattr("app.engine.fetch_history", history)
    monkeypatch.setattr(main.engine.ai, "analyze", analyze)
    client.post("/api/account/reset")

    verdict = "buy"
    out = client.post("/api/advice/btcusdt").json()
    assert (out["name"], out["action"], out["owned"]) == ("Bitcoin", "buy", False)
    verdict = "sell"  # nothing to sell yet
    assert client.post("/api/advice/BTCUSDT").json()["action"] == "wait"

    assert client.post("/api/orders", json={"symbol": "BTCUSDT", "side": "buy"}).status_code == 200
    assert client.post("/api/advice/BTCUSDT").json()["action"] == "sell"
    verdict = "buy"  # already owned
    assert client.post("/api/advice/BTCUSDT").json()["action"] == "wait"
    assert client.post("/api/advice/DOGEUSDT").status_code == 400
    client.post("/api/account/reset")


def test_learning_api(client):
    s = client.get("/api/learn").json()
    assert s["level"] == 1 and len(s["levels"]) == 6 and s["levels"][1]["status"] == "locked"
    lesson = client.get("/api/learn/lessons/l1-what-is-trading").json()
    assert lesson["cards"] and "answer" not in lesson["quiz"][0]
    r = client.post("/api/learn/lessons/l1-what-is-trading/answer", json={"question": 0, "choice": 0}).json()
    assert r["correct"] is True and r["why"]
    assert client.post("/api/learn/lessons/l2-stops/answer", json={"question": 0, "choice": 1}).status_code == 400
    assert client.post("/api/learn/events", json={"name": "chart", "value": "BTCUSDT"}).status_code == 200
    assert client.post("/api/learn/events", json={"name": "win"}).status_code == 400
    assert len(client.get("/api/learn/glossary").json()) > 40


def test_explain_trade_api(client):
    buy = client.post("/api/orders", json={"symbol": "ETHUSDT", "side": "buy"}).json()
    sell = client.post("/api/positions/ETHUSDT/close").json()
    e = client.post(f"/api/trades/{sell['id']}/explain").json()
    assert e["trade_id"] == sell["id"] and e["headline"].startswith("You ") and e["source"] == "rules"
    assert client.post(f"/api/trades/{buy['id']}/explain").json()["headline"].startswith("You bought")
    assert client.post("/api/trades/999999/explain").status_code == 400
    counts = client.get("/api/learn").json()["levels"][0]["challenges"]
    assert counts[0]["done"] and counts[1]["done"]
