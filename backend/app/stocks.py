"""US stock and ETF prices from Yahoo Finance's public chart endpoint (no API key), plus US market hours.

Yahoo's free data is delayed by up to ~15 minutes for some exchanges and only offers polling, so `StockFeed` polls
1-minute bars and exposes the same interface as the crypto `MarketData` (candles, last_price, closed_candles,
connected, start/stop, on_update/on_close callbacks).
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import date, datetime, timedelta
from typing import Awaitable, Callable
from zoneinfo import ZoneInfo

import httpx

from app.market import MAX_CANDLES, upsert_candle

logger = logging.getLogger("app.stocks")

YAHOO_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) PaperTrader/1.0"}
NY = ZoneInfo("America/New_York")
OPEN_TIME = (9, 30)
CLOSE_TIME = (16, 0)
EARLY_CLOSE = (13, 0)
POLL_OPEN = 15.0
POLL_CLOSED = 300.0
CONCURRENCY = 4

# NYSE/Nasdaq full-day holidays and 1pm early closes.
HOLIDAYS = {
    date(2025, 1, 1), date(2025, 1, 9), date(2025, 1, 20), date(2025, 2, 17), date(2025, 4, 18), date(2025, 5, 26),
    date(2025, 6, 19), date(2025, 7, 4), date(2025, 9, 1), date(2025, 11, 27), date(2025, 12, 25),
    date(2026, 1, 1), date(2026, 1, 19), date(2026, 2, 16), date(2026, 4, 3), date(2026, 5, 25), date(2026, 6, 19),
    date(2026, 7, 3), date(2026, 9, 7), date(2026, 11, 26), date(2026, 12, 25),
    date(2027, 1, 1), date(2027, 1, 18), date(2027, 2, 15), date(2027, 3, 26), date(2027, 5, 31), date(2027, 6, 18),
    date(2027, 7, 5), date(2027, 9, 6), date(2027, 11, 25), date(2027, 12, 24),
}
EARLY_CLOSES = {date(2025, 7, 3), date(2025, 11, 28), date(2025, 12, 24), date(2026, 11, 27), date(2026, 12, 24),
                date(2027, 11, 26)}

# Yahoo intervals: our name -> (yahoo interval, seconds per bar, bars per trading day, longest allowed range)
INTERVALS = {
    "1m": ("1m", 60, 390, "5d"),
    "5m": ("5m", 300, 78, "1mo"),
    "15m": ("15m", 900, 26, "1mo"),
    "1h": ("60m", 3600, 7, "2y"),
    "1d": ("1d", 86400, 1, "max"),
}
RANGES = [("1d", 1), ("5d", 5), ("1mo", 30), ("3mo", 91), ("6mo", 182), ("1y", 365), ("2y", 730), ("5y", 1826),
          ("10y", 3652), ("max", 10**6)]

Callback = Callable[[str, dict], Awaitable[None]]


# ------------------------------------------------------------------------------------------ market hours
def _session(d: date) -> tuple[datetime, datetime] | None:
    """Regular-session open/close (New York time) for date `d`, or None if the market is shut all day."""
    if d.weekday() >= 5 or d in HOLIDAYS:
        return None
    close = EARLY_CLOSE if d in EARLY_CLOSES else CLOSE_TIME
    return (datetime(d.year, d.month, d.day, *OPEN_TIME, tzinfo=NY),
            datetime(d.year, d.month, d.day, *close, tzinfo=NY))


def market_status(now: float | None = None) -> dict:
    """US stock market status: open now?, when it next opens/closes (unix seconds) and a plain-English label."""
    ts = time.time() if now is None else now
    local = datetime.fromtimestamp(ts, NY)
    today = _session(local.date())
    if today and today[0] <= local < today[1]:
        return {"open": True, "closes_at": today[1].timestamp(), "opens_at": None,
                "label": f"US market open until {today[1].strftime('%-I:%M %p')} New York time"}
    d = local.date()
    for _ in range(10):
        s = _session(d)
        if s and s[0] > local:
            wait = s[0].timestamp() - ts
            hours = wait / 3600
            when = f"in {hours:.0f} hours" if hours >= 1.5 else f"in {wait / 60:.0f} minutes"
            return {"open": False, "opens_at": s[0].timestamp(), "closes_at": None,
                    "label": f"US market closed. Opens {when} ({s[0].strftime('%a %-I:%M %p')} New York time)"}
        d += timedelta(days=1)
    return {"open": False, "opens_at": None, "closes_at": None, "label": "US market closed"}


# ------------------------------------------------------------------------------------------ Yahoo fetch
def _pick_range(interval: str, bars: int) -> str:
    _, _, per_day, longest = INTERVALS[interval]
    days_needed = bars / per_day * 365 / 252 + 4
    limit = dict(RANGES)[longest]
    for name, days in RANGES:
        if days >= days_needed or days >= limit:
            return name
    return longest


def parse_chart(payload: dict, interval: str, now: float | None = None) -> list[dict]:
    """Yahoo chart JSON -> ascending candle dicts. Daily bars are adjusted for splits and dividends."""
    now = time.time() if now is None else now
    result = (payload.get("chart") or {}).get("result") or []
    if not result:
        err = (payload.get("chart") or {}).get("error") or {}
        raise ValueError(err.get("description") or "no data returned")
    r = result[0]
    stamps = r.get("timestamp") or []
    quote = (r.get("indicators") or {}).get("quote") or [{}]
    q = quote[0]
    adj = ((r.get("indicators") or {}).get("adjclose") or [{}])[0].get("adjclose") if interval == "1d" else None
    step = INTERVALS[interval][1]
    open_now = market_status(now)["open"]
    out: dict[int, dict] = {}
    for i, t in enumerate(stamps):
        try:
            o, h, lo, c = q["open"][i], q["high"][i], q["low"][i], q["close"][i]
            v = q.get("volume", [0] * len(stamps))[i] or 0
        except (KeyError, IndexError, TypeError):
            continue
        if None in (o, h, lo, c) or c <= 0:
            continue
        factor = 1.0
        if adj is not None and i < len(adj) and adj[i]:
            factor = float(adj[i]) / float(c)
        if interval == "1d":
            # Daily bars are stamped at the session open; normalise to midnight UTC of that date.
            day = datetime.fromtimestamp(int(t), NY).date()
            t = int((datetime(day.year, day.month, day.day) - datetime(1970, 1, 1)).total_seconds())
            closed = not (open_now and day == datetime.fromtimestamp(now, NY).date())
        else:
            closed = int(t) + step <= now
        out[int(t)] = {"time": int(t), "open": float(o) * factor, "high": float(h) * factor,
                       "low": float(lo) * factor, "close": float(c) * factor, "volume": float(v), "closed": closed}
    return [out[t] for t in sorted(out)]


async def fetch_chart(client: httpx.AsyncClient | None, symbol: str, interval: str, range_: str,
                      days: float | None = None) -> list[dict]:
    """Yahoo chart data. With `days` (daily bars only) an explicit date range is used, because range=max makes
    Yahoo silently switch to coarser bars."""
    if interval not in INTERVALS:
        raise ValueError(f"Stocks support these timeframes: {', '.join(INTERVALS)}")
    params = {"interval": INTERVALS[interval][0], "includePrePost": "false", "events": "div,splits"}
    if days is not None and interval == "1d":
        now = int(time.time())
        params.update({"period1": max(0, now - int(days * 86400)), "period2": now + 86400})
    else:
        params["range"] = range_
    own = client is None
    if own:
        client = httpx.AsyncClient(timeout=15.0, headers=HEADERS)
    try:
        resp = await client.get(YAHOO_CHART.format(symbol=symbol.upper()), params=params)
        resp.raise_for_status()
        payload = resp.json()
    finally:
        if own:
            await client.aclose()
    return parse_chart(payload, interval)


async def fetch_stock_history(symbol: str, interval: str, bars: int) -> list[dict]:
    """The most recent `bars` candles for a US stock/ETF (as many as Yahoo allows for that timeframe)."""
    bars = max(1, int(bars))
    days = bars * 365 / 252 + 10 if interval == "1d" else None
    candles = await fetch_chart(None, symbol, interval, _pick_range(interval, bars), days=days)
    return candles[-bars:]


# ------------------------------------------------------------------------------------------ live feed
class StockFeed:
    """Polls Yahoo for 1-minute bars of several stocks. Same interface as app.market.MarketData."""

    def __init__(self, symbols: list[str], history_bars: int, on_update: Callback | None = None,
                 on_close: Callback | None = None):
        self.symbols = [s.upper() for s in symbols]
        self.interval = "1m"
        self.history_bars = max(1, min(int(history_bars), MAX_CANDLES))
        self.on_update = on_update
        self.on_close = on_close
        self.candles: dict[str, list[dict]] = {s: [] for s in self.symbols}
        self.last_price: dict[str, float] = {}
        self.last_ok: float = 0.0
        self._task: asyncio.Task | None = None
        self._stopping = False

    @property
    def connected(self) -> bool:
        limit = (POLL_OPEN if market_status()["open"] else POLL_CLOSED) * 3 + 30
        return bool(self.symbols) and time.time() - self.last_ok < limit

    def closed_candles(self, symbol: str) -> list[dict]:
        return [c for c in self.candles.get(symbol.upper(), []) if c["closed"]]

    async def start(self) -> None:
        if not self.symbols or (self._task and not self._task.done()):
            return
        self._stopping = False
        await self._poll(bootstrap=True)
        self._task = asyncio.create_task(self._run(), name="stock-feed")

    async def stop(self) -> None:
        self._stopping = True
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._task = None

    async def _run(self) -> None:
        while not self._stopping:
            await asyncio.sleep(POLL_OPEN if market_status()["open"] else POLL_CLOSED)
            try:
                await self._poll()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.warning("Stock poll failed: %r", exc)

    async def _poll(self, bootstrap: bool = False) -> None:
        sem = asyncio.Semaphore(CONCURRENCY)
        range_ = "5d" if bootstrap else "1d"
        async with httpx.AsyncClient(timeout=15.0, headers=HEADERS) as client:
            async def one(symbol: str):
                async with sem:
                    return await fetch_chart(client, symbol, "1m", range_)
            results = await asyncio.gather(*(one(s) for s in self.symbols), return_exceptions=True)
        ok = False
        for symbol, result in zip(self.symbols, results):
            if isinstance(result, BaseException):
                logger.warning("Prices for %s unavailable: %r", symbol, result)
                continue
            ok = True
            await self._merge(symbol, result, fire=not bootstrap)
        if ok:
            self.last_ok = time.time()

    async def _merge(self, symbol: str, fresh: list[dict], fire: bool) -> None:
        if not fresh:
            return
        buf = self.candles[symbol]
        before_last = buf[-1]["time"] if buf else 0
        was_closed = {c["time"] for c in buf[-5:] if c["closed"]}
        for candle in fresh:
            upsert_candle(buf, candle, cap=MAX_CANDLES)
        if len(buf) > self.history_bars and not fire:
            del buf[: len(buf) - self.history_bars]
        last = buf[-1]
        changed = self.last_price.get(symbol) != last["close"] or last["time"] != before_last
        self.last_price[symbol] = last["close"]
        if not fire:
            return
        for candle in fresh:
            if candle["closed"] and candle["time"] >= before_last and candle["time"] not in was_closed:
                await self._fire(self.on_close, symbol, candle)
        if changed:
            await self._fire(self.on_update, symbol, last)

    @staticmethod
    async def _fire(cb: Callback | None, symbol: str, candle: dict) -> None:
        if cb is None:
            return
        try:
            await cb(symbol, dict(candle))
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("Stock callback failed for %s", symbol)
