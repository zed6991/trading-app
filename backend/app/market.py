"""Binance market data: REST history + combined kline WebSocket stream (public endpoints, no API key)."""
import asyncio
import json
import logging
import time
from typing import Awaitable, Callable

import httpx
from websockets.asyncio.client import connect

from app.config import settings

logger = logging.getLogger("app.market")

MAX_CANDLES = 1000
MAX_HISTORY_BARS = 5000
REST_LIMIT = 1000
BACKOFF_START = 1.0
BACKOFF_MAX = 30.0

Callback = Callable[[str, dict], Awaitable[None]]


def parse_rest_kline(row: list, now_ms: int | None = None) -> dict:
    """Convert a Binance REST kline row into a candle dict."""
    now_ms = int(time.time() * 1000) if now_ms is None else now_ms
    return {
        "time": int(row[0]) // 1000,
        "open": float(row[1]),
        "high": float(row[2]),
        "low": float(row[3]),
        "close": float(row[4]),
        "volume": float(row[5]),
        "closed": int(row[6]) < now_ms,
    }


def parse_ws_kline(k: dict) -> dict:
    """Convert the `k` object of a Binance kline stream event into a candle dict."""
    return {
        "time": int(k["t"]) // 1000,
        "open": float(k["o"]),
        "high": float(k["h"]),
        "low": float(k["l"]),
        "close": float(k["c"]),
        "volume": float(k["v"]),
        "closed": bool(k["x"]),
    }


def upsert_candle(candles: list[dict], candle: dict, cap: int = MAX_CANDLES) -> str:
    """Insert a candle into an ascending list in place.

    Same open time as the last candle -> replace; newer -> append (then trim to `cap`);
    older -> replace a matching earlier candle if present, otherwise ignore.
    Returns "replaced", "appended" or "ignored".
    """
    if not candles or candle["time"] > candles[-1]["time"]:
        candles.append(candle)
        if len(candles) > cap:
            del candles[: len(candles) - cap]
        return "appended"
    if candle["time"] == candles[-1]["time"]:
        candles[-1] = candle
        return "replaced"
    for i in range(len(candles) - 2, -1, -1):
        t = candles[i]["time"]
        if t == candle["time"]:
            candles[i] = candle
            return "replaced"
        if t < candle["time"]:
            break
    return "ignored"


async def fetch_klines(
    client: httpx.AsyncClient | None,
    symbol: str,
    interval: str,
    limit: int = 1000,
    end_ms: int | None = None,
) -> list[dict]:
    """Fetch up to `limit` (max 1000) klines ending at `end_ms` (inclusive), ascending."""
    params: dict = {"symbol": symbol.upper(), "interval": interval, "limit": max(1, min(int(limit), REST_LIMIT))}
    if end_ms is not None:
        params["endTime"] = int(end_ms)
    own_client = client is None
    if own_client:
        client = httpx.AsyncClient(timeout=15.0)
    try:
        resp = await client.get(f"{settings.binance_rest}/api/v3/klines", params=params)
        resp.raise_for_status()
        rows = resp.json()
    finally:
        if own_client:
            await client.aclose()
    now_ms = int(time.time() * 1000)
    return [parse_rest_kline(row, now_ms) for row in rows]


async def fetch_history(symbol: str, interval: str, bars: int) -> list[dict]:
    """Fetch the most recent `bars` candles (capped at 5000), paginating backwards. Ascending, de-duplicated."""
    bars = max(1, min(int(bars), MAX_HISTORY_BARS))
    by_time: dict[int, dict] = {}
    end_ms: int | None = None
    async with httpx.AsyncClient(timeout=15.0) as client:
        while len(by_time) < bars:
            want = min(REST_LIMIT, bars - len(by_time))
            batch = await fetch_klines(client, symbol, interval, limit=want, end_ms=end_ms)
            if not batch:
                break
            new = 0
            for c in batch:
                if c["time"] not in by_time:
                    by_time[c["time"]] = c
                    new += 1
            if new == 0:
                break
            end_ms = batch[0]["time"] * 1000 - 1
            if len(batch) < want:
                # Exchange ran out of history.
                break
    candles = [by_time[t] for t in sorted(by_time)]
    return candles[-bars:]


class MarketData:
    """Keeps rolling candles per symbol, fed by REST bootstrap + Binance combined kline stream."""

    def __init__(
        self,
        symbols: list[str],
        interval: str,
        history_bars: int,
        on_update: Callback | None = None,
        on_close: Callback | None = None,
    ):
        self.symbols = [s.upper() for s in symbols]
        self.interval = interval
        self.history_bars = max(1, min(int(history_bars), MAX_CANDLES))
        self.on_update = on_update
        self.on_close = on_close
        self.candles: dict[str, list[dict]] = {s: [] for s in self.symbols}
        self.last_price: dict[str, float] = {}
        self.connected: bool = False
        self._task: asyncio.Task | None = None
        self._ws = None
        self._stopping = False

    @property
    def stream_url(self) -> str:
        streams = "/".join(f"{s.lower()}@kline_{self.interval}" for s in self.symbols)
        return f"{settings.binance_ws}/stream?streams={streams}"

    def closed_candles(self, symbol: str) -> list[dict]:
        return [c for c in self.candles.get(symbol.upper(), []) if c["closed"]]

    async def _bootstrap(self) -> None:
        results = await asyncio.gather(
            *(fetch_history(s, self.interval, self.history_bars) for s in self.symbols), return_exceptions=True
        )
        for symbol, result in zip(self.symbols, results):
            if isinstance(result, BaseException):
                logger.error("History bootstrap failed for %s: %s", symbol, result)
                continue
            self.candles[symbol] = result[-MAX_CANDLES:]
            if result:
                self.last_price[symbol] = result[-1]["close"]
            logger.info("Loaded %d %s bars for %s", len(result), self.interval, symbol)

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stopping = False
        await self._bootstrap()
        self._task = asyncio.create_task(self._run(), name="market-data-stream")

    async def stop(self) -> None:
        self._stopping = True
        ws = self._ws
        if ws is not None:
            try:
                await ws.close()
            except Exception:  # noqa: BLE001
                pass
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._task = None
        self._ws = None
        self.connected = False

    async def _run(self) -> None:
        backoff = BACKOFF_START
        while not self._stopping:
            try:
                async with connect(
                    self.stream_url, ping_interval=20, ping_timeout=20, close_timeout=5, max_size=2**20
                ) as ws:
                    self._ws = ws
                    self.connected = True
                    logger.info("Connected to Binance stream (%s)", ", ".join(self.symbols))
                    async for raw in ws:
                        await self.handle_message(raw)
                        backoff = BACKOFF_START
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                if not self._stopping:
                    logger.warning("Binance stream error: %r", exc)
            finally:
                self._ws = None
                self.connected = False
            if self._stopping:
                break
            logger.info("Reconnecting to Binance stream in %.0fs", backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, BACKOFF_MAX)

    async def handle_message(self, raw: str | bytes) -> dict | None:
        """Process one combined-stream message. Returns the parsed candle (or None if ignored)."""
        try:
            msg = json.loads(raw)
            data = msg.get("data", msg)
            if data.get("e") != "kline":
                return None
            k = data["k"]
            symbol = str(k.get("s") or data.get("s")).upper()
            candle = parse_ws_kline(k)
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            logger.warning("Ignoring malformed stream message: %r", exc)
            return None
        if symbol not in self.candles:
            return None
        upsert_candle(self.candles[symbol], candle)
        self.last_price[symbol] = candle["close"]
        await self._fire(self.on_update, symbol, candle)
        if candle["closed"]:
            await self._fire(self.on_close, symbol, candle)
        return candle

    @staticmethod
    async def _fire(cb: Callback | None, symbol: str, candle: dict) -> None:
        if cb is None:
            return
        try:
            await cb(symbol, dict(candle))
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("Market callback %s failed for %s", getattr(cb, "__name__", cb), symbol)
