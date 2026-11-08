import asyncio
import json

import httpx

from app.ai import AIAnalyst, rule_based_analysis, validate_analysis


def _candles(n=60, start=100.0, step=1.0):
    out = []
    for i in range(n):
        c = start + i * step
        out.append({"time": 1_700_000_000 + 60 * i, "open": c - step / 2, "high": c + 1, "low": c - 1,
                    "close": c, "volume": 10.0 + i, "closed": True})
    return out


BULL_IND = {"price": 160.0, "sma20": 150.0, "sma50": 135.0, "rsi14": 62.0, "macd_hist": 0.8,
            "bb_upper": 170.0, "bb_mid": 150.0, "bb_lower": 130.0}
BEAR_IND = {"price": 100.0, "sma20": 110.0, "sma50": 120.0, "rsi14": 38.0, "macd_hist": -0.5,
            "bb_upper": 125.0, "bb_mid": 110.0, "bb_lower": 95.0}


def test_validate_clamps_and_normalises():
    out = validate_analysis({
        "bias": "Bullish", "confidence": 1.7, "recommendation": " BUY ",
        "summary": "One.  Two!\nThree? Four.", "key_levels": {"support": "95.5", "resistance": None},
        "extra": 1,
    }, price=100.0)
    assert out["bias"] == "bullish" and out["recommendation"] == "buy"
    assert out["confidence"] == 1.0
    assert out["summary"] == "One. Two! Three?"
    assert out["key_levels"] == {"support": 95.5, "resistance": None}
    assert set(out) == {"bias", "confidence", "recommendation", "summary", "key_levels"}


def test_validate_confidence_bounds_and_levels():
    out = validate_analysis({"bias": "neutral", "confidence": 250, "recommendation": "hold", "summary": "x",
                             "key_levels": {"support": 120, "resistance": 90}}, price=100.0)
    assert out["confidence"] == 1.0
    assert out["key_levels"] == {"support": None, "resistance": None}  # wrong side of price
    out = validate_analysis({"bias": "neutral", "confidence": -3, "recommendation": "hold", "summary": "x",
                             "key_levels": "junk"}, price=100.0)
    assert out["confidence"] == 0.0 and out["key_levels"] == {"support": None, "resistance": None}
    out = validate_analysis({"bias": "neutral", "confidence": 85, "recommendation": "hold", "summary": "x"})
    assert out["confidence"] == 0.85


def test_validate_rejects_bad_enums():
    import pytest
    for bad in ({"bias": "moon", "confidence": 0.5, "recommendation": "buy", "summary": "x"},
                {"bias": "bullish", "confidence": 0.5, "recommendation": "short", "summary": "x"},
                {"bias": "bullish", "confidence": "high", "recommendation": "buy", "summary": "x"},
                {"bias": "bullish", "confidence": 0.5, "recommendation": "buy", "summary": "  "}):
        with pytest.raises(ValueError):
            validate_analysis(bad, price=100.0)


def test_rule_based_bullish_and_bearish():
    candles = _candles()
    bull = rule_based_analysis("BTCUSDT", candles, BULL_IND, None)
    assert bull["bias"] == "bullish" and bull["recommendation"] == "buy"
    assert 0 < bull["confidence"] <= 0.9
    assert bull["key_levels"]["support"] == min(c["low"] for c in candles[-30:])
    assert bull["key_levels"]["resistance"] == max(c["high"] for c in candles[-30:])
    bear_flat = rule_based_analysis("BTCUSDT", candles, BEAR_IND, None)
    assert bear_flat["bias"] == "bearish" and bear_flat["recommendation"] == "hold"
    bear_pos = rule_based_analysis("BTCUSDT", candles, BEAR_IND, {"qty": 0.5, "avg_price": 105})
    assert bear_pos["recommendation"] == "sell"
    assert bull["summary"].count(".") <= 4  # short
    for text in (bull["summary"], bear_flat["summary"]):
        assert not any(w in text for w in ("RSI", "MACD", "SMA", "Bollinger", "bullish", "bearish"))
    assert "Bitcoin" in bull["summary"]
    empty = rule_based_analysis("BTCUSDT", [], {}, None)
    assert empty["bias"] == "neutral" and empty["confidence"] == 0.0
    assert empty["key_levels"] == {"support": None, "resistance": None}


def _analyst(handler):
    a = AIAnalyst("http://ollama.test", "test-model", 5)
    a.transport = httpx.MockTransport(handler)
    return a


def test_analyze_uses_model_output():
    seen = {}

    def handler(request: httpx.Request):
        body = json.loads(request.content)
        seen.update(body)
        content = json.dumps({"bias": "bullish", "confidence": 0.72, "recommendation": "buy",
                              "summary": "Trend up. MACD positive.",
                              "key_levels": {"support": 150.0, "resistance": 165.0}})
        return httpx.Response(200, json={"message": {"role": "assistant", "content": content}})

    out = asyncio.run(_analyst(handler).analyze("BTCUSDT", _candles(), BULL_IND, None))
    assert out["source"] == "ollama:test-model"
    assert out["bias"] == "bullish" and out["confidence"] == 0.72
    assert out["key_levels"] == {"support": 150.0, "resistance": 165.0}
    assert out["symbol"] == "BTCUSDT" and isinstance(out["latency_ms"], int) and out["ts"] > 0
    assert seen["think"] is False and seen["stream"] is False and seen["format"] == "json"
    assert seen["options"] == {"temperature": 0.2, "num_ctx": 8192}
    assert seen["messages"][0]["role"] == "system"
    assert "BTCUSDT" in seen["messages"][1]["content"]
    assert seen["messages"][1]["content"].count("\n") >= 30


def test_analyze_falls_back_on_invalid_json_http_error_and_network_error():
    def bad_json(request):
        return httpx.Response(200, json={"message": {"content": "I think it goes up!"}})

    def bad_enum(request):
        return httpx.Response(200, json={"message": {"content": json.dumps({"bias": "moon"})}})

    def server_error(request):
        return httpx.Response(500, json={"error": "boom"})

    def network(request):
        raise httpx.ConnectError("refused")

    for h in (bad_json, bad_enum, server_error, network):
        out = asyncio.run(_analyst(h).analyze("ETHUSDT", _candles(), BULL_IND, None))
        assert out["source"] == "rules", h.__name__
        assert out["bias"] == "bullish" and out["recommendation"] == "buy"


def test_health():
    def tags(request):
        return httpx.Response(200, json={"models": [{"name": "test-model:latest", "model": "test-model:latest"}]})

    def tags_missing(request):
        return httpx.Response(200, json={"models": [{"name": "other:latest"}]})

    def down(request):
        raise httpx.ConnectError("refused")

    assert asyncio.run(_analyst(tags).health()) == {"available": True, "model": "test-model", "error": None}
    h = asyncio.run(_analyst(tags_missing).health())
    assert h["available"] is False and "not found" in h["error"]
    h = asyncio.run(_analyst(down).health())
    assert h["available"] is False and h["error"]
