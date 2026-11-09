"""AI market analysis via a local Ollama model, with a deterministic rule-based fallback."""
import json
import logging
import math
import re
import time
from datetime import datetime, timezone

import httpx

from app.config import settings

logger = logging.getLogger("app.ai")

BIASES = ("bullish", "bearish", "neutral")
RECOMMENDATIONS = ("buy", "sell", "hold")
PROMPT_CANDLES = 30
LEVEL_LOOKBACK = 30
MAX_SUMMARY_CHARS = 600

SYSTEM_PROMPT = (
    "You are a disciplined crypto technical analyst for a long-only spot paper-trading desk. "
    "You base every call strictly on the price action, indicators and position provided; you never invent data, "
    "never chase, and you prefer 'hold' when signals conflict. "
    "Respond with ONLY a strict JSON object, no markdown, no extra keys, exactly these keys:\n"
    '{"bias": "bullish"|"bearish"|"neutral", '
    '"confidence": number between 0 and 1, '
    '"recommendation": "buy"|"sell"|"hold", '
    '"summary": string of at most 2 short sentences written for a complete beginner, '
    '"key_levels": {"support": number or null, "resistance": number or null}}\n'
    "Rules: 'sell' means exit an open long (if there is no position, use 'hold' instead of 'sell'). "
    "'buy' only when the trend and momentum agree. Support must be below the current price and resistance above it.\n"
    "Summary rules: use everyday words a 12-year-old would understand. Say what the price has been doing and why that "
    "leads to your recommendation. Never use trading jargon such as RSI, MACD, SMA, EMA, Bollinger, momentum, support, "
    "resistance, bullish, bearish, long or short. Never promise a profit."
)
COIN_NAMES = {"BTCUSDT": "Bitcoin", "ETHUSDT": "Ethereum", "SOLUSDT": "Solana"}


def coin_name(symbol: str) -> str:
    """Everyday name for a trading pair, e.g. BTCUSDT -> Bitcoin."""
    return COIN_NAMES.get(symbol, symbol.removesuffix("USDT"))


def _num(v) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _sig(x: float, digits: int = 6) -> str:
    """Format a number with ~`digits` significant figures (keeps tiny-priced coins readable)."""
    if x == 0:
        return "0"
    return f"{x:.{digits}g}" if abs(x) < 1 else f"{x:.{max(0, digits - len(str(int(abs(x)))))}f}"


def _trim_summary(text: str) -> str:
    text = " ".join(str(text or "").split())
    if not text:
        return ""
    sentences = re.split(r"(?<=[.!?])\s+", text)
    text = " ".join(sentences[:3])
    if len(text) > MAX_SUMMARY_CHARS:
        text = text[: MAX_SUMMARY_CHARS - 1].rstrip() + "…"
    return text


def _candle_table(candles: list[dict]) -> str:
    lines = ["time_utc,open,high,low,close,volume"]
    for c in candles[-PROMPT_CANDLES:]:
        t = datetime.fromtimestamp(c["time"], tz=timezone.utc).strftime("%m-%d %H:%M")
        lines.append(
            f"{t},{_sig(c['open'])},{_sig(c['high'])},{_sig(c['low'])},{_sig(c['close'])},{_sig(c['volume'], 4)}"
        )
    return "\n".join(lines)


def _key_levels(candles: list[dict]) -> dict:
    recent = candles[-LEVEL_LOOKBACK:]
    if not recent:
        return {"support": None, "resistance": None}
    return {"support": min(c["low"] for c in recent), "resistance": max(c["high"] for c in recent)}


def build_messages(symbol: str, candles: list[dict], indicators: dict, position: dict | None) -> list[dict]:
    ind = {k: (round(v, 6) if isinstance(v, float) else v) for k, v in (indicators or {}).items()}
    if position:
        pos_keys = ("qty", "avg_price", "mark", "unrealized_pnl", "pnl_pct", "stop_loss", "take_profit", "strategy")
        pos_txt = json.dumps({k: position.get(k) for k in pos_keys if k in position})
    else:
        pos_txt = "none (flat)"
    user = (
        f"Symbol: {symbol}\n"
        f"Last {min(len(candles), PROMPT_CANDLES)} candles (oldest first):\n{_candle_table(candles)}\n\n"
        f"Indicators (latest): {json.dumps(ind)}\n"
        f"Current position: {pos_txt}\n\n"
        "Return the JSON object now."
    )
    return [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}]


def _extract_json(text: str) -> dict:
    text = (text or "").strip()
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            raise ValueError("no JSON object in model output")
        obj = json.loads(m.group(0))
    if not isinstance(obj, dict):
        raise ValueError("model output is not a JSON object")
    return obj


def validate_analysis(raw: dict, price: float | None = None) -> dict:
    """Validate and clamp a model-produced analysis. Raises ValueError if unusable."""
    if not isinstance(raw, dict):
        raise ValueError("analysis must be an object")
    bias = str(raw.get("bias", "")).strip().lower()
    rec = str(raw.get("recommendation", "")).strip().lower()
    if bias not in BIASES:
        raise ValueError(f"invalid bias {bias!r}")
    if rec not in RECOMMENDATIONS:
        raise ValueError(f"invalid recommendation {rec!r}")
    conf = _num(raw.get("confidence"))
    if conf is None:
        raise ValueError("missing confidence")
    if 2 <= conf <= 100:  # model answered in percent (e.g. 85 -> 0.85); 1 < x < 2 is just clamped
        conf /= 100.0
    conf = round(min(1.0, max(0.0, conf)), 3)
    summary = _trim_summary(raw.get("summary", ""))
    if not summary:
        raise ValueError("empty summary")
    levels = raw.get("key_levels") if isinstance(raw.get("key_levels"), dict) else {}
    support = _num(levels.get("support", raw.get("support")))
    resistance = _num(levels.get("resistance", raw.get("resistance")))
    if support is not None and support <= 0:
        support = None
    if resistance is not None and resistance <= 0:
        resistance = None
    if price is not None and price > 0:
        if support is not None and support > price:
            support = None
        if resistance is not None and resistance < price:
            resistance = None
    return {
        "bias": bias,
        "confidence": conf,
        "recommendation": rec,
        "summary": summary,
        "key_levels": {"support": support, "resistance": resistance},
    }


def rule_based_analysis(symbol: str, candles: list[dict], indicators: dict, position: dict | None) -> dict:
    """Deterministic analysis from RSI, MACD histogram, price vs SMA20/SMA50 and Bollinger position."""
    ind = indicators or {}
    price = _num(ind.get("price"))
    if price is None and candles:
        price = float(candles[-1]["close"])
    score = 0.0
    reasons: list[str] = []
    checks = 0

    rsi = _num(ind.get("rsi14"))
    if rsi is not None:
        checks += 1
        if rsi < 30:
            score += 1
            reasons.append("the price has fallen fast and may be due a bounce")
        elif rsi > 70:
            score -= 1
            reasons.append("the price has risen fast and may be due to cool off")
        elif rsi >= 55:
            score += 0.5
            reasons.append("buyers have had the upper hand lately")
        elif rsi <= 45:
            score -= 0.5
            reasons.append("sellers have had the upper hand lately")

    hist = _num(ind.get("macd_hist"))
    if hist is not None:
        checks += 1
        if hist > 0:
            score += 1
            reasons.append("the latest move is upward")
        elif hist < 0:
            score -= 1
            reasons.append("the latest move is downward")

    for key, label in (("sma20", "its recent average"), ("sma50", "its longer-term average")):
        ma = _num(ind.get(key))
        if ma is not None and price is not None:
            checks += 1
            if price > ma:
                score += 1
                reasons.append(f"price is above {label}")
            elif price < ma:
                score -= 1
                reasons.append(f"price is below {label}")

    upper, lower = _num(ind.get("bb_upper")), _num(ind.get("bb_lower"))
    if upper is not None and lower is not None and price is not None and upper > lower:
        checks += 1
        pos = (price - lower) / (upper - lower)
        if pos < 0.1:
            score += 0.5
            reasons.append("the price is unusually low right now")
        elif pos > 0.9:
            score -= 0.5
            reasons.append("the price is unusually high right now")

    max_score = 4.5
    if score >= 1.5:
        bias = "bullish"
    elif score <= -1.5:
        bias = "bearish"
    else:
        bias = "neutral"

    has_pos = bool(position) and (_num(position.get("qty")) or 0) > 0
    if bias == "bullish" and not has_pos:
        rec = "buy"
    elif bias == "bearish" and has_pos:
        rec = "sell"
    else:
        rec = "hold"

    confidence = 0.0 if checks == 0 else round(min(0.9, 0.3 + 0.6 * abs(score) / max_score), 3)
    if bias == "neutral":
        confidence = round(min(confidence, 0.5), 3)

    if reasons:
        mood = {"bullish": "positive", "bearish": "negative", "neutral": "mixed"}[bias]
        lead = f"Things look {mood} for {coin_name(symbol)}"
        summary = f"{lead}: {', '.join(reasons[:3])}."
        if len(reasons) > 3:
            summary += f" Also {', '.join(reasons[3:5])}."
    else:
        summary = f"There isn't enough information on {coin_name(symbol)} yet."
    summary += f" Our suggestion: {'wait' if rec == 'hold' else rec}."

    return {
        "bias": bias,
        "confidence": confidence,
        "recommendation": rec,
        "summary": _trim_summary(summary),
        "key_levels": _key_levels(candles),
    }


class AIAnalyst:
    def __init__(self, url: str | None = None, model: str | None = None, timeout: float | None = None):
        self.url = (url or settings.ollama_url).rstrip("/")
        self.model = model or settings.ollama_model
        self.timeout = float(timeout if timeout is not None else settings.ollama_timeout)
        self.transport: httpx.AsyncBaseTransport | None = None  # injectable for tests

    def _client(self, timeout: float) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=timeout, transport=self.transport)

    async def _ask_model(self, messages: list[dict], temperature: float = 0.2) -> str:
        body = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "format": "json",
            "think": False,
            "options": {"temperature": temperature, "num_ctx": 8192},
        }
        async with self._client(self.timeout) as client:
            resp = await client.post(f"{self.url}/api/chat", json=body)
            resp.raise_for_status()
            data = resp.json()
        content = (data.get("message") or {}).get("content")
        if not isinstance(content, str):
            raise ValueError("Ollama response missing message.content")
        return content

    async def analyze(self, symbol: str, candles: list[dict], indicators: dict, position: dict | None) -> dict:
        start = time.perf_counter()
        indicators = indicators or {}
        price = _num(indicators.get("price"))
        if price is None and candles:
            price = float(candles[-1]["close"])
        source = f"ollama:{self.model}"
        try:
            content = await self._ask_model(build_messages(symbol, candles, indicators, position))
            result = validate_analysis(_extract_json(content), price)
        except Exception as exc:  # noqa: BLE001 - any failure falls back to rules
            logger.warning("AI analysis via %s failed (%s: %s); using rule-based fallback", self.model, type(exc).__name__, exc)
            result = rule_based_analysis(symbol, candles, indicators, position)
            source = "rules"
        return {
            "ts": time.time(),
            "symbol": symbol,
            **result,
            "source": source,
            "latency_ms": int((time.perf_counter() - start) * 1000),
        }

    async def explain_trade(self, facts: dict, draft: dict, level: int) -> dict | None:
        """Reword a rule-based trade explanation for a learner. Returns {what, why, lesson} or None on any failure.

        Numbers come from `facts`; the model is told not to change them. Terms the learner hasn't reached yet
        (by level) must not appear, so the explanation always matches what they've been taught.
        """
        system = (
            "You are a patient trading teacher reviewing ONE paper trade with a beginner. "
            f"The learner is on level {level} of 6 (1 basics, 2 stop-loss/take-profit/position size, 3 charts, "
            "4 indicators, 5 strategies/backtesting, 6 automation). Only use trading terms from their level or "
            "below, and explain any term in a few words. Use the exact numbers given; never invent prices or "
            "reasons. Be kind and concrete, never promise profit. Respond with ONLY strict JSON: "
            '{"what": "1-2 sentences: what happened", "why": "1-2 sentences: why it ended this way", '
            '"lesson": "1 sentence: the single most useful takeaway for next time"}'
        )
        user = f"Trade facts: {json.dumps(facts, default=str)}\nDraft explanation: {json.dumps(draft)}\nReturn the JSON now."
        try:
            raw = _extract_json(await self._ask_model([{"role": "system", "content": system},
                                                       {"role": "user", "content": user}]))
            out = {k: _trim_summary(raw.get(k, "")) for k in ("what", "why", "lesson")}
            if not all(out.values()):
                raise ValueError("missing fields")
            return out
        except Exception as exc:  # noqa: BLE001 - fall back to the rule-based draft
            logger.warning("Trade explanation via %s failed (%s); using rules", self.model, exc)
            return None

    async def health(self) -> dict:
        try:
            async with self._client(min(self.timeout, 5.0)) as client:
                resp = await client.get(f"{self.url}/api/tags")
                resp.raise_for_status()
                models = resp.json().get("models") or []
        except Exception as exc:  # noqa: BLE001
            return {"available": False, "model": self.model, "error": f"{type(exc).__name__}: {exc}"}
        names = set()
        for m in models:
            for key in ("name", "model"):
                if m.get(key):
                    names.add(m[key])
        wanted = {self.model, f"{self.model}:latest"} if ":" not in self.model else {self.model}
        if names & wanted:
            return {"available": True, "model": self.model, "error": None}
        return {"available": False, "model": self.model, "error": f"model {self.model!r} not found in Ollama"}
