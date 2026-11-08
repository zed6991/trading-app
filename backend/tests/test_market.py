import asyncio
import json

import httpx

from app import market
from app.market import MarketData, fetch_history, fetch_klines, parse_rest_kline, parse_ws_kline, upsert_candle


def _candle(t, close=1.0, closed=True):
    return {"time": t, "open": close, "high": close, "low": close, "close": close, "volume": 1.0, "closed": closed}


def _ws_msg(symbol, t_sec, close, closed):
    return json.dumps({
        "stream": f"{symbol.lower()}@kline_1m",
        "data": {"e": "kline", "E": t_sec * 1000 + 500, "s": symbol, "k": {
            "t": t_sec * 1000, "T": t_sec * 1000 + 59999, "s": symbol, "i": "1m",
            "o": "100.0", "c": str(close), "h": "105.5", "l": "99.5", "v": "12.25", "x": closed,
        }},
    })


def test_parse_ws_kline():
    k = json.loads(_ws_msg("BTCUSDT", 1_700_000_000, 101.5, True))["data"]["k"]
    c = parse_ws_kline(k)
    assert c == {"time": 1_700_000_000, "open": 100.0, "high": 105.5, "low": 99.5, "close": 101.5,
                 "volume": 12.25, "closed": True}


def test_parse_rest_kline_closed_flag():
    row = [1_700_000_000_000, "1", "2", "0.5", "1.5", "10", 1_700_000_059_999, "0", 5, "0", "0", "0"]
    assert parse_rest_kline(row, now_ms=1_700_000_060_000)["closed"] is True
    forming = parse_rest_kline(row, now_ms=1_700_000_030_000)
    assert forming["closed"] is False
    assert forming["time"] == 1_700_000_000 and forming["close"] == 1.5


def test_upsert_replace_append_cap():
    candles = [_candle(60 * i) for i in range(5)]
    assert upsert_candle(candles, _candle(240, close=9.0, closed=False)) == "replaced"
    assert len(candles) == 5 and candles[-1]["close"] == 9.0
    assert upsert_candle(candles, _candle(300, close=2.0)) == "appended"
    assert candles[-1]["time"] == 300
    assert upsert_candle(candles, _candle(120, close=7.0)) == "replaced"
    assert candles[2]["close"] == 7.0
    assert upsert_candle(candles, _candle(-60)) == "ignored"
    # cap
    big = [_candle(60 * i) for i in range(1000)]
    upsert_candle(big, _candle(60 * 1000))
    assert len(big) == 1000 and big[0]["time"] == 60 and big[-1]["time"] == 60 * 1000
    empty = []
    assert upsert_candle(empty, _candle(0)) == "appended"


def test_handle_message_updates_and_callbacks():
    updates, closes = [], []

    async def on_update(sym, c):
        updates.append((sym, c))

    async def on_close(sym, c):
        closes.append((sym, c))

    async def run():
        md = MarketData(["BTCUSDT"], "1m", 100, on_update, on_close)
        md.candles["BTCUSDT"] = [_candle(1_700_000_000 - 60), _candle(1_700_000_000, closed=False)]
        await md.handle_message(_ws_msg("BTCUSDT", 1_700_000_000, 101.0, False))
        assert len(md.candles["BTCUSDT"]) == 2 and md.candles["BTCUSDT"][-1]["close"] == 101.0
        await md.handle_message(_ws_msg("BTCUSDT", 1_700_000_000, 102.0, True))
        await md.handle_message(_ws_msg("BTCUSDT", 1_700_000_060, 103.0, False))
        assert len(md.candles["BTCUSDT"]) == 3
        assert md.last_price["BTCUSDT"] == 103.0
        assert [c["time"] for c in md.closed_candles("BTCUSDT")] == [1_700_000_000 - 60, 1_700_000_000]
        # unknown symbol / malformed / non-kline are ignored
        assert await md.handle_message(_ws_msg("DOGEUSDT", 1_700_000_060, 1.0, False)) is None
        assert await md.handle_message("not json") is None
        assert await md.handle_message(json.dumps({"stream": "x", "data": {"e": "trade"}})) is None

    asyncio.run(run())
    assert len(updates) == 3 and len(closes) == 1
    assert closes[0][1]["close"] == 102.0


def test_callback_exception_does_not_propagate():
    async def boom(sym, c):
        raise RuntimeError("callback failure")

    async def run():
        md = MarketData(["ETHUSDT"], "1m", 100, boom, boom)
        c = await md.handle_message(_ws_msg("ETHUSDT", 1_700_000_000, 5.0, True))
        assert c is not None and md.candles["ETHUSDT"][-1]["close"] == 5.0

    asyncio.run(run())


def test_stream_url():
    md = MarketData(["BTCUSDT", "ethusdt"], "5m", 10)
    assert md.stream_url.endswith("/stream?streams=btcusdt@kline_5m/ethusdt@kline_5m")


def _fake_exchange(total_bars, start_ms=1_600_000_000_000, step_ms=60_000):
    """MockTransport handler emulating Binance /api/v3/klines with endTime + limit."""
    calls = []

    def handler(request: httpx.Request):
        q = request.url.params
        limit = int(q["limit"])
        end = int(q["endTime"]) if "endTime" in q else start_ms + (total_bars - 1) * step_ms
        times = [start_ms + i * step_ms for i in range(total_bars) if start_ms + i * step_ms <= end]
        times = times[-limit:]
        calls.append((limit, q.get("endTime")))
        rows = [[t, "1", "2", "0.5", str(t / 1e12), "3", t + step_ms - 1, "0", 1, "0", "0", "0"] for t in times]
        return httpx.Response(200, json=rows)

    return handler, calls


def test_fetch_klines_with_client():
    handler, calls = _fake_exchange(50)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await fetch_klines(client, "btcusdt", "1m", limit=20)

    out = asyncio.run(run())
    assert len(out) == 20 and calls[0][0] == 20
    assert all(a["time"] < b["time"] for a, b in zip(out, out[1:]))


def test_fetch_history_paginates(monkeypatch):
    handler, calls = _fake_exchange(3500)
    real_client = httpx.AsyncClient

    def patched(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(market.httpx, "AsyncClient", patched)
    out = asyncio.run(fetch_history("BTCUSDT", "1m", 2500))
    assert len(out) == 2500
    times = [c["time"] for c in out]
    assert times == sorted(set(times))
    assert times[-1] == (1_600_000_000_000 + 3499 * 60_000) // 1000
    assert [c[0] for c in calls] == [1000, 1000, 500]

    calls.clear()
    out = asyncio.run(fetch_history("BTCUSDT", "1m", 9000))  # capped at 5000, exchange only has 3500
    assert len(out) == 3500
    assert len(calls) == 4
