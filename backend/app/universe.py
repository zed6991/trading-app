"""The swing scanner's universe: large, liquid US stocks and ETFs, each with a sector.

Large and liquid on purpose: tight spreads, plenty of volume, and fewer wild gaps than small caps, which matters when
you hold overnight. The sector lets the risk gate stop you from owning five chip stocks at once. Tickers change over
time; one that Yahoo no longer knows is reported as "couldn't load", never silently dropped. Add your own with
SWING_EXTRA_SYMBOLS in .env (comma-separated).
"""
from __future__ import annotations

import os

# (symbol, name, sector)
_ROWS: list[tuple[str, str, str]] = [
    # Technology
    ("AAPL", "Apple", "Technology"), ("MSFT", "Microsoft", "Technology"), ("NVDA", "Nvidia", "Technology"),
    ("AVGO", "Broadcom", "Technology"), ("ORCL", "Oracle", "Technology"), ("CRM", "Salesforce", "Technology"),
    ("ADBE", "Adobe", "Technology"), ("AMD", "AMD", "Technology"), ("CSCO", "Cisco", "Technology"),
    ("ACN", "Accenture", "Technology"), ("IBM", "IBM", "Technology"), ("INTC", "Intel", "Technology"),
    ("QCOM", "Qualcomm", "Technology"), ("TXN", "Texas Instruments", "Technology"), ("AMAT", "Applied Materials", "Technology"),
    ("MU", "Micron", "Technology"), ("LRCX", "Lam Research", "Technology"), ("KLAC", "KLA", "Technology"),
    ("ADI", "Analog Devices", "Technology"), ("NOW", "ServiceNow", "Technology"), ("INTU", "Intuit", "Technology"),
    ("PANW", "Palo Alto Networks", "Technology"), ("CRWD", "CrowdStrike", "Technology"), ("SNPS", "Synopsys", "Technology"),
    ("CDNS", "Cadence Design", "Technology"), ("ANET", "Arista Networks", "Technology"), ("MRVL", "Marvell", "Technology"),
    ("PLTR", "Palantir", "Technology"), ("DELL", "Dell", "Technology"), ("FTNT", "Fortinet", "Technology"),
    ("SHOP", "Shopify", "Technology"), ("UBER", "Uber", "Technology"), ("TSM", "TSMC", "Technology"),
    ("ASML", "ASML", "Technology"), ("ON", "ON Semiconductor", "Technology"), ("WDAY", "Workday", "Technology"),
    # Communication
    ("GOOGL", "Alphabet (Google)", "Communication"), ("META", "Meta Platforms", "Communication"),
    ("NFLX", "Netflix", "Communication"), ("DIS", "Disney", "Communication"), ("CMCSA", "Comcast", "Communication"),
    ("T", "AT&T", "Communication"), ("VZ", "Verizon", "Communication"), ("TMUS", "T-Mobile US", "Communication"),
    ("SPOT", "Spotify", "Communication"),
    # Consumer discretionary
    ("AMZN", "Amazon", "Consumer discretionary"), ("TSLA", "Tesla", "Consumer discretionary"),
    ("HD", "Home Depot", "Consumer discretionary"), ("MCD", "McDonald's", "Consumer discretionary"),
    ("NKE", "Nike", "Consumer discretionary"), ("LOW", "Lowe's", "Consumer discretionary"),
    ("SBUX", "Starbucks", "Consumer discretionary"), ("TJX", "TJX Companies", "Consumer discretionary"),
    ("BKNG", "Booking Holdings", "Consumer discretionary"), ("CMG", "Chipotle", "Consumer discretionary"),
    ("ABNB", "Airbnb", "Consumer discretionary"), ("GM", "General Motors", "Consumer discretionary"),
    ("F", "Ford", "Consumer discretionary"), ("MAR", "Marriott", "Consumer discretionary"),
    ("ROST", "Ross Stores", "Consumer discretionary"), ("LULU", "Lululemon", "Consumer discretionary"),
    ("RCL", "Royal Caribbean", "Consumer discretionary"), ("ORLY", "O'Reilly Automotive", "Consumer discretionary"),
    # Consumer staples
    ("WMT", "Walmart", "Consumer staples"), ("COST", "Costco", "Consumer staples"), ("PG", "Procter & Gamble", "Consumer staples"),
    ("KO", "Coca-Cola", "Consumer staples"), ("PEP", "PepsiCo", "Consumer staples"), ("PM", "Philip Morris", "Consumer staples"),
    ("MO", "Altria", "Consumer staples"), ("MDLZ", "Mondelez", "Consumer staples"), ("CL", "Colgate-Palmolive", "Consumer staples"),
    ("TGT", "Target", "Consumer staples"), ("KHC", "Kraft Heinz", "Consumer staples"), ("KDP", "Keurig Dr Pepper", "Consumer staples"),
    # Health care
    ("LLY", "Eli Lilly", "Health care"), ("UNH", "UnitedHealth", "Health care"), ("JNJ", "Johnson & Johnson", "Health care"),
    ("ABBV", "AbbVie", "Health care"), ("MRK", "Merck", "Health care"), ("PFE", "Pfizer", "Health care"),
    ("TMO", "Thermo Fisher", "Health care"), ("ABT", "Abbott", "Health care"), ("DHR", "Danaher", "Health care"),
    ("AMGN", "Amgen", "Health care"), ("ISRG", "Intuitive Surgical", "Health care"), ("GILD", "Gilead", "Health care"),
    ("VRTX", "Vertex", "Health care"), ("BMY", "Bristol-Myers Squibb", "Health care"), ("CVS", "CVS Health", "Health care"),
    ("MDT", "Medtronic", "Health care"), ("SYK", "Stryker", "Health care"), ("REGN", "Regeneron", "Health care"),
    ("BSX", "Boston Scientific", "Health care"), ("NVO", "Novo Nordisk", "Health care"),
    # Financials
    ("JPM", "JPMorgan Chase", "Financials"), ("BAC", "Bank of America", "Financials"), ("WFC", "Wells Fargo", "Financials"),
    ("C", "Citigroup", "Financials"), ("GS", "Goldman Sachs", "Financials"), ("MS", "Morgan Stanley", "Financials"),
    ("BLK", "BlackRock", "Financials"), ("SCHW", "Charles Schwab", "Financials"), ("AXP", "American Express", "Financials"),
    ("V", "Visa", "Financials"), ("MA", "Mastercard", "Financials"), ("PYPL", "PayPal", "Financials"),
    ("BRK-B", "Berkshire Hathaway", "Financials"), ("SPGI", "S&P Global", "Financials"), ("CB", "Chubb", "Financials"),
    ("PGR", "Progressive", "Financials"), ("COF", "Capital One", "Financials"), ("USB", "US Bancorp", "Financials"),
    ("PNC", "PNC Financial", "Financials"), ("COIN", "Coinbase", "Financials"), ("HOOD", "Robinhood", "Financials"),
    # Industrials
    ("CAT", "Caterpillar", "Industrials"), ("DE", "Deere", "Industrials"), ("BA", "Boeing", "Industrials"),
    ("GE", "GE Aerospace", "Industrials"), ("HON", "Honeywell", "Industrials"), ("UNP", "Union Pacific", "Industrials"),
    ("UPS", "UPS", "Industrials"), ("RTX", "RTX", "Industrials"), ("LMT", "Lockheed Martin", "Industrials"),
    ("ETN", "Eaton", "Industrials"), ("FDX", "FedEx", "Industrials"), ("WM", "Waste Management", "Industrials"),
    ("CSX", "CSX", "Industrials"), ("MMM", "3M", "Industrials"), ("NOC", "Northrop Grumman", "Industrials"),
    ("DAL", "Delta Air Lines", "Industrials"), ("UAL", "United Airlines", "Industrials"), ("GEV", "GE Vernova", "Industrials"),
    # Energy
    ("XOM", "ExxonMobil", "Energy"), ("CVX", "Chevron", "Energy"), ("COP", "ConocoPhillips", "Energy"),
    ("SLB", "Schlumberger", "Energy"), ("EOG", "EOG Resources", "Energy"), ("OXY", "Occidental", "Energy"),
    ("MPC", "Marathon Petroleum", "Energy"), ("PSX", "Phillips 66", "Energy"), ("VLO", "Valero", "Energy"),
    # Materials
    ("LIN", "Linde", "Materials"), ("FCX", "Freeport-McMoRan", "Materials"), ("NEM", "Newmont", "Materials"),
    ("APD", "Air Products", "Materials"), ("SHW", "Sherwin-Williams", "Materials"), ("NUE", "Nucor", "Materials"),
    # Utilities
    ("NEE", "NextEra Energy", "Utilities"), ("DUK", "Duke Energy", "Utilities"), ("SO", "Southern Company", "Utilities"),
    ("CEG", "Constellation Energy", "Utilities"), ("VST", "Vistra", "Utilities"),
    # Real estate
    ("PLD", "Prologis", "Real estate"), ("AMT", "American Tower", "Real estate"), ("EQIX", "Equinix", "Real estate"),
    ("O", "Realty Income", "Real estate"),
    # ETFs (whole market, sectors, and other asset classes)
    ("SPY", "S&P 500 ETF", "ETF: US market"), ("QQQ", "Nasdaq-100 ETF", "ETF: US market"),
    ("IWM", "Russell 2000 small caps ETF", "ETF: US market"), ("DIA", "Dow Jones ETF", "ETF: US market"),
    ("XLK", "Tech sector ETF", "ETF: sector"), ("XLF", "Financials sector ETF", "ETF: sector"),
    ("XLE", "Energy sector ETF", "ETF: sector"), ("XLV", "Health care sector ETF", "ETF: sector"),
    ("XLI", "Industrials sector ETF", "ETF: sector"), ("XLY", "Consumer discretionary ETF", "ETF: sector"),
    ("XLP", "Consumer staples ETF", "ETF: sector"), ("XLU", "Utilities sector ETF", "ETF: sector"),
    ("SMH", "Semiconductor ETF", "ETF: sector"), ("GLD", "Gold ETF", "ETF: other"), ("SLV", "Silver ETF", "ETF: other"),
    ("TLT", "US Treasury Bond ETF", "ETF: other"),
]

UNIVERSE: list[dict[str, str]] = [{"symbol": s, "name": n, "sector": sec} for s, n, sec in _ROWS]
_BY_SYMBOL = {u["symbol"]: u for u in UNIVERSE}


def symbols() -> list[str]:
    extra = [s.strip().upper() for s in os.environ.get("SWING_EXTRA_SYMBOLS", "").split(",") if s.strip()]
    return list(dict.fromkeys([u["symbol"] for u in UNIVERSE] + extra))


def info(symbol: str) -> dict[str, str]:
    symbol = symbol.upper()
    return _BY_SYMBOL.get(symbol) or {"symbol": symbol, "name": symbol, "sector": "Other"}


def is_etf(symbol: str) -> bool:
    return info(symbol)["sector"].startswith("ETF")
