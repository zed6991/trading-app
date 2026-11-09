"""One way to get historical candles for any asset: Binance for crypto, Yahoo Finance for US stocks/ETFs.

Daily history (used by the stats lab, correlation, luck test and backtests) is cached in memory for a few hours.
"""
from __future__ import annotations

import asyncio
import time

from app import assets
from app.market import fetch_history as fetch_crypto_history
from app.stocks import fetch_stock_history

DAILY_TTL = 6 * 3600
_cache: dict[tuple[str, str, int], tuple[float, list[dict]]] = {}
_locks: dict[tuple[str, str, int], asyncio.Lock] = {}


async def history(symbol: str, interval: str, bars: int) -> list[dict]:
    """Most recent `bars` candles of `symbol` at `interval`, ascending."""
    symbol = symbol.upper()
    if assets.is_crypto(symbol):
        return await fetch_crypto_history(symbol, interval, bars)
    return await fetch_stock_history(symbol, interval, bars)


async def cached_history(symbol: str, interval: str = "1d", bars: int = 1260) -> list[dict]:
    """Like `history` but cached (daily/hourly data barely changes within a few hours)."""
    key = (symbol.upper(), interval, int(bars))
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < DAILY_TTL:
        return hit[1]
    lock = _locks.setdefault(key, asyncio.Lock())
    async with lock:
        hit = _cache.get(key)
        if hit and time.time() - hit[0] < DAILY_TTL:
            return hit[1]
        data = [c for c in await history(symbol, interval, bars) if c["closed"]]
        _cache[key] = (time.time(), data)
        return data


async def many_daily(symbols: list[str], bars: int = 1260) -> dict[str, list[dict]]:
    """Daily candles for several symbols at once. Raises ValueError naming any that failed."""
    results = await asyncio.gather(*(cached_history(s, "1d", bars) for s in symbols), return_exceptions=True)
    out, failed = {}, []
    for s, r in zip(symbols, results):
        if isinstance(r, BaseException) or not r:
            failed.append(s)
        else:
            out[s] = r
    if failed:
        raise ValueError(f"Couldn't load price history for {', '.join(failed)}. Try again in a minute.")
    return out
