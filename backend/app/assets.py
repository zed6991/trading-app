"""The tradable universe: US stocks and ETFs (Yahoo Finance prices) plus crypto (Binance prices).

Every symbol the app knows is listed here with its everyday name, asset kind and cost model, so the rest of the
app never has to guess from the ticker.
"""
from __future__ import annotations

from typing import Any

# kind: "stock" | "etf" | "crypto". Stocks and ETFs trade on US exchanges in US market hours; crypto trades 24/7.
ASSETS: list[dict[str, Any]] = [
    {"symbol": "SPY", "name": "S&P 500 ETF", "kind": "etf", "sector": "Whole US market (500 big companies)"},
    {"symbol": "QQQ", "name": "Nasdaq-100 ETF", "kind": "etf", "sector": "Big US tech-heavy companies"},
    {"symbol": "TLT", "name": "US Treasury Bond ETF", "kind": "etf", "sector": "Long-term US government bonds"},
    {"symbol": "GLD", "name": "Gold ETF", "kind": "etf", "sector": "Gold"},
    {"symbol": "AAPL", "name": "Apple", "kind": "stock", "sector": "Technology"},
    {"symbol": "MSFT", "name": "Microsoft", "kind": "stock", "sector": "Technology"},
    {"symbol": "NVDA", "name": "Nvidia", "kind": "stock", "sector": "Technology (chips)"},
    {"symbol": "AMZN", "name": "Amazon", "kind": "stock", "sector": "Shopping and cloud"},
    {"symbol": "GOOGL", "name": "Alphabet (Google)", "kind": "stock", "sector": "Technology"},
    {"symbol": "TSLA", "name": "Tesla", "kind": "stock", "sector": "Cars and energy"},
    {"symbol": "JPM", "name": "JPMorgan Chase", "kind": "stock", "sector": "Banking"},
    {"symbol": "KO", "name": "Coca-Cola", "kind": "stock", "sector": "Drinks"},
    {"symbol": "XOM", "name": "ExxonMobil", "kind": "stock", "sector": "Oil and gas"},
    {"symbol": "BTCUSDT", "name": "Bitcoin", "kind": "crypto", "sector": "Crypto"},
    {"symbol": "ETHUSDT", "name": "Ethereum", "kind": "crypto", "sector": "Crypto"},
    {"symbol": "SOLUSDT", "name": "Solana", "kind": "crypto", "sector": "Crypto"},
]
ASSET_BY_SYMBOL: dict[str, dict[str, Any]] = {a["symbol"]: a for a in ASSETS}
BENCHMARK = "SPY"

# Costs. US brokers charge no commission on stocks/ETFs; you pay the bid-ask spread (modelled as slippage).
# Crypto exchanges charge a percentage fee on every trade.
FEE_RATE = {"stock": 0.0, "etf": 0.0, "crypto": 0.001}
TRADING_DAYS = {"stock": 252, "etf": 252, "crypto": 365}


def asset(symbol: str) -> dict[str, Any]:
    """Registry entry for `symbol` (unknown symbols are treated as crypto pairs if they end in USDT, else stocks)."""
    symbol = symbol.upper()
    found = ASSET_BY_SYMBOL.get(symbol)
    if found:
        return found
    if symbol.endswith("USDT"):
        return {"symbol": symbol, "name": symbol.removesuffix("USDT"), "kind": "crypto", "sector": "Crypto"}
    return {"symbol": symbol, "name": symbol, "kind": "stock", "sector": ""}


def kind(symbol: str) -> str:
    return asset(symbol)["kind"]


def is_crypto(symbol: str) -> bool:
    return kind(symbol) == "crypto"


def name(symbol: str) -> str:
    """Everyday name, e.g. BTCUSDT -> Bitcoin, AAPL -> Apple."""
    return asset(symbol)["name"]


def fee_rate(symbol: str, crypto_fee: float | None = None) -> float:
    """Fee per trade as a fraction of the trade's value."""
    k = kind(symbol)
    if k == "crypto" and crypto_fee is not None:
        return float(crypto_fee)
    return FEE_RATE[k]


def periods_per_year(symbol: str) -> int:
    return TRADING_DAYS[kind(symbol)]


def public_list(symbols: list[str]) -> list[dict[str, Any]]:
    """Asset info for the API, in `symbols` order."""
    return [{**asset(s), "fee_rate": fee_rate(s)} for s in symbols]
