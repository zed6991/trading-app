"""Course content: "Zero to AI-powered quant trader".

Levels 1-7 are built (markets, risk, charts, statistics, correlation, edge-or-luck, proper backtesting). Levels 8-13
(Python, strategies in code, portfolios and factors, machine learning, LLM research, agents) are listed in ROADMAP
and shown as "coming soon" until they're built.

Text fields may contain glossary links written as [[slug]] or [[slug|shown text]]. Every term a lesson or the UI uses
must be in GLOSSARY, with `level` = the level whose lessons teach it.
"""
from typing import Any

COURSE_TITLE = "Zero to AI-powered quant trader"
COURSE_OUTLINE = ("1 markets and orders, 2 risk and position sizing, 3 charts and classic signals, "
                  "4 returns and volatility, 5 correlation, beta and diversification, 6 edge or luck, "
                  "7 testing strategies properly")


def _g(slug: str, term: str, level: int, short: str, more: str = "", aliases: list[str] | None = None) -> dict:
    return {"slug": slug, "term": term, "level": level, "aliases": aliases or [], "short": short, "more": more}


# --------------------------------------------------------------------------------------------- glossary
GLOSSARY: list[dict[str, Any]] = [
    # Level 1: markets and orders
    _g("stock", "Stock", 1, "A small piece of ownership in a company. Also called a share.",
       "Own one Apple share and you own a tiny slice of Apple. Its price rises and falls as people buy and sell it.",
       ["stocks", "share", "shares", "equities"]),
    _g("ticker", "Ticker", 1, "The short code for something you can trade, e.g. AAPL for Apple or SPY for the S&P 500 ETF.",
       "Crypto tickers here end in USDT, e.g. BTCUSDT means Bitcoin priced in dollars.", ["symbol", "usdt"]),
    _g("etf", "ETF", 1, "A fund you can buy like a single stock, which holds a whole basket of things.",
       "SPY holds the 500 biggest US companies, so one purchase spreads your money across all of them. TLT holds "
       "government bonds and GLD holds gold.", ["etfs", "exchange-traded fund", "fund"]),
    _g("index", "Index", 1, "A number that tracks a group of stocks, like the S&P 500.",
       "You can't buy an index directly, but ETFs like SPY copy one.", ["s&p 500", "nasdaq-100", "benchmark index"]),
    _g("crypto", "Crypto", 1, "Digital money like Bitcoin that trades online all day, every day.",
       "Crypto moves several times more than a typical stock, which makes it risky.", ["cryptocurrency", "coin", "coins"]),
    _g("exchange", "Exchange", 1, "The marketplace where buyers and sellers are matched, e.g. the New York Stock Exchange.",
       "Crypto trades on online exchanges such as Binance, which never close.", ["exchanges", "nyse", "nasdaq"]),
    _g("paper-trading", "Paper trading", 1, "Trading with pretend money so mistakes cost nothing.",
       "Everything in this app is paper trading. Prices are real; the money is not.", ["paper"]),
    _g("price", "Price", 1, "The last price a buyer and a seller agreed on.",
       "It changes every time a new deal happens, which is why it moves all day.",
       ["mark", "mark price", "last price", "last"]),
    _g("bid", "Bid", 1, "The highest price a buyer is offering right now. It's what you'd get if you sold immediately.",
       "", ["bid price"]),
    _g("ask", "Ask", 1, "The lowest price a seller will accept right now. It's what you'd pay if you bought immediately.",
       "", ["ask price", "offer"]),
    _g("spread", "Spread", 1, "The gap between the bid and the ask. It's a hidden cost every time you trade.",
       "Bid $99.98 and ask $100.02 is a 4-cent spread. Busy stocks like Apple have tiny spreads; quiet ones can be wide.",
       ["bid-ask spread"]),
    _g("market-order", "Market order", 1, "An order to buy or sell right now at the best price available.",
       "It fills almost instantly, but you don't control the exact price.", ["market orders"]),
    _g("limit-order", "Limit order", 1, "An order with your own price: buy only at this price or lower, or sell only at "
       "this price or higher.", "You control the price, but it might never fill if the market doesn't get there.",
       ["limit", "limit price", "limit orders", "waiting orders", "pending orders"]),
    _g("market-hours", "Market hours", 1, "US stocks trade 9:30am to 4pm New York time on weekdays. Crypto never closes.",
       "In Sydney that's overnight: the open is between 11:30pm and 1:30am depending on daylight saving.",
       ["market closed", "market open", "us market"]),
    _g("cash", "Cash", 1, "Money in your account that isn't invested in anything yet.",
       "You need cash to buy. Selling turns a position back into cash."),
    _g("position", "Position", 1, "Something you currently own, like 10 Apple shares or 0.1 Bitcoin.",
       "'Opening a position' means buying; 'closing' it means selling.", ["positions", "open positions", "holding"]),
    _g("quantity", "Quantity", 1, "How many shares or coins (or parts of one) you own or are trading.",
       "This app lets you buy parts of a share, like many real brokers do.", ["qty", "size"]),
    _g("notional", "Notional", 1, "The dollar value of a trade or position: quantity × price.",
       "10 shares at $200 have a notional value of $2,000.", ["value", "market value"]),
    _g("equity", "Equity", 1, "What your whole account is worth: cash plus the value of everything you own.",
       "This is the number to watch. It moves as prices move, even if you don't trade.",
       ["account value", "final equity"]),
    _g("pnl", "P&L", 1, "Profit and loss: how much you've made or lost.", "Positive means profit, negative a loss.",
       ["pnl", "profit and loss", "day pnl", "total pnl", "pnl %"]),
    _g("unrealized", "Unrealized P&L", 1, "Profit or loss on things you still own. It isn't locked in until you sell.",
       "It can disappear if the price turns.", ["upnl", "unrealized pnl", "unrealised"]),
    _g("realized", "Realized P&L", 1, "Profit or loss you locked in by selling.",
       "Once you sell, this number no longer changes with the price.", ["realized pnl", "realised"]),
    _g("fees", "Fees", 1, "What you're charged per trade. Here: no commission on US stocks and ETFs, 0.1% on crypto.",
       "Even with no commission you still pay the spread, so frequent trading always costs something.",
       ["fee", "fees paid", "commission"]),
    _g("slippage", "Slippage", 1, "Getting a slightly worse price than you saw, because the price moved as your order filled.",
       "This app adds a small amount of slippage to every trade, like a real market."),

    # Level 2: risk first
    _g("stop-loss", "Stop-loss", 2, "An automatic sell if the price falls to a level you chose. It caps your loss.",
       "In a fast drop, or a gap overnight, the sale can happen below your level. Think of it as 'about' the most "
       "you'll lose.", ["stop loss", "sl", "stop"]),
    _g("take-profit", "Take-profit", 2, "An automatic sell when the price rises to your target, locking in the gain.",
       "It stops a winning trade turning back into a losing one while you're not watching.", ["take profit", "tp", "target"]),
    _g("bracket", "Bracket", 2, "A stop-loss and a take-profit set together around a trade.",
       "The trade ends at whichever the price reaches first.", ["brackets", "exits"]),
    _g("trailing-stop", "Trailing stop", 2, "A stop-loss that moves up as the price rises, protecting gains as well as "
       "limiting losses.", "A 3% trailing stop always sits 3% below the highest price since you bought.", ["trailing"]),
    _g("position-size", "Position size", 2, "How much money you put into one trade.",
       "Here there's a cap as a share of your account, e.g. 20% means at most $20,000 of a $100,000 account.",
       ["max position", "position sizing", "per trade"]),
    _g("risk-per-trade", "Risk per trade", 2, "How many dollars you'd lose if a trade hits its stop-loss.",
       "Many traders keep it to about 1% of their account. Size = risk ÷ stop distance: risking $1,000 with a 5% stop "
       "means buying $20,000.", ["risk", "1% rule", "size by risk", "risk amount"]),
    _g("risk-reward", "Risk-reward ratio", 2, "How much you could lose compared with how much you could win.",
       "Risking $100 to make $200 is 1:2. With 1:2 you can be wrong two times out of three and roughly break even.",
       ["risk reward", "r:r"]),
    _g("diversification", "Diversification", 2, "Spreading money over several things so one bad one can't sink you.",
       "Level 5 shows how to measure it: it only works if the things don't all move together.",
       ["max open positions", "diversify", "diversified"]),
    _g("drawdown", "Drawdown", 2, "How far your account has fallen from its highest point.",
       "A 50% drawdown needs a 100% gain just to get back to even.", ["max drawdown", "drawdowns"]),
    _g("daily-loss-limit", "Daily loss limit", 2, "If you lose this much in one day, buying pauses until tomorrow.",
       "It stops a bad day becoming a disaster, and stops 'revenge trading' to win it back.",
       ["daily loss", "halted", "halt"]),
    _g("revenge-trading", "Revenge trading", 2, "Rushing into new trades to win back a loss.",
       "It usually makes losses bigger."),
    _g("fomo", "FOMO", 2, "Fear of missing out: buying because a price is shooting up and you feel left behind.",
       "FOMO buys usually come late, near the top.", ["fear of missing out"]),

    # Level 3: charts and classic signals
    _g("candlestick", "Candlestick", 3, "One bar on a price chart showing the open, high, low and close for a period.",
       "Green: it closed higher than it opened. Red: it closed lower.", ["candle", "candles", "candlesticks", "ohlc"]),
    _g("wick", "Wick", 3, "The thin line above or below a candle's body: how far the price went before coming back.",
       "", ["shadow", "wicks"]),
    _g("timeframe", "Timeframe", 3, "How much time each candle covers, e.g. 1 minute (1m), 1 hour (1h) or 1 day (1d).",
       "Short timeframes are noisy. Longer ones show the bigger picture.", ["interval", "bars"]),
    _g("volume", "Volume", 3, "How much was traded in a period. Big moves on high volume are more convincing.",
       "Volume is the bars at the bottom of the chart."),
    _g("trend", "Trend", 3, "The general direction of the price. Up means higher highs and higher lows.",
       "", ["uptrend", "downtrend"]),
    _g("support", "Support", 3, "A price where falls have stopped before, because buyers stepped in."),
    _g("resistance", "Resistance", 3, "A price where rises have stopped before, because sellers stepped in."),
    _g("gap", "Gap", 3, "When a stock opens at a very different price from where it closed, because of news overnight.",
       "A stop-loss can't protect you inside a gap: you get the opening price.", ["gaps", "gapped"]),
    _g("earnings", "Earnings", 3, "A company's quarterly report of how much it sold and earned. Big price moves often follow.",
       "", ["earnings report", "earnings day"]),
    _g("order-ticket", "Order ticket", 3, "The form you use to place a buy or sell.",
       "Leave quantity on 'Auto' and the app sizes the trade with your position-size setting.", ["order"]),
    _g("indicator", "Indicator", 3, "A calculation from past prices drawn on the chart to help spot trends or turns.",
       "Indicators only use the past. They describe; they don't predict.", ["indicators"]),
    _g("sma", "Moving average (SMA)", 3, "The average closing price over the last N candles, drawn as a smooth line.",
       "SMA20 averages 20 candles, SMA50 averages 50. Price above the line suggests an uptrend.",
       ["moving average", "sma20", "sma50", "sma 20", "sma 50", "simple moving average"]),
    _g("ema", "EMA", 3, "A moving average that gives more weight to recent prices, so it reacts faster.",
       "", ["exponential moving average"]),
    _g("crossover", "Crossover", 3, "When a fast moving average crosses a slow one: a common buy or sell signal.",
       "", ["cross", "golden cross", "death cross", "sma cross"]),
    _g("rsi", "RSI", 3, "A 0–100 score of how strongly the price moved recently. Above 70 = rose fast, below 30 = fell fast.",
       "High RSI means 'may cool off', not 'will fall'.", ["rsi14", "rsi 14", "relative strength index"]),
    _g("overbought", "Overbought / oversold", 3, "Risen (or fallen) so fast it may pause or bounce.", "", ["oversold"]),
    _g("macd", "MACD", 3, "Shows whether recent prices are speeding up or slowing down compared with the longer trend.",
       "", ["macd histogram", "macd_hist", "signal line"]),
    _g("bollinger", "Bollinger Bands", 3, "Two lines around a moving average that widen when prices are jumpy.",
       "A close above the upper band is unusually high for recent prices.", ["bb", "bb(20,2)", "bollinger band"]),
    _g("momentum", "Momentum", 3, "How fast and how strongly the price is moving in one direction.",
       "Level 7 turns it into a strategy: buy what has been rising.", ["momentum strategy"]),
    _g("bullish", "Bullish", 3, "Expecting the price to go up.", "", ["bull"]),
    _g("bearish", "Bearish", 3, "Expecting the price to go down.", "", ["bear"]),
    _g("confidence", "Confidence", 3, "How strongly the signals agree. It is not the chance of being right.",
       "", ["sureness"]),

    # Level 4: returns and volatility
    _g("return", "Return", 4, "How much something grew or shrank, as a percentage of what you started with.",
       "$10,000 growing to $11,000 is a 10% return.", ["returns", "total return", "total return %"]),
    _g("daily-return", "Daily return", 4, "Today's price change as a percentage of yesterday's close.",
       "$100 to $102 is +2%. Quants use percentages so a $10 stock and a $500 stock can be compared.",
       ["daily returns", "mean daily return"]),
    _g("compounding", "Compounding", 4, "Gains (and losses) building on earlier gains and losses.",
       "+10% then −10% leaves you at −1%, not zero: $100 → $110 → $99.", ["compound"]),
    _g("cagr", "Yearly growth (CAGR)", 4, "The steady yearly return that would get you from start to end.",
       "Doubling in 5 years is about 15% a year.", ["cagr", "annual return", "yearly growth"]),
    _g("volatility", "Volatility", 4, "How much and how quickly the price swings: the typical size of a move.",
       "Measured with the standard deviation of daily returns.", ["volatile", "vol", "annual volatility"]),
    _g("standard-deviation", "Standard deviation", 4, "A measure of how spread out numbers are around their average.",
       "For daily returns: about two days in three move less than one standard deviation.", ["std", "std dev", "sd"]),
    _g("annualised", "Annualised", 4, "Scaled up to a yearly figure so different things can be compared.",
       "Daily volatility × √252 (trading days) gives yearly volatility for stocks; × √365 for crypto.",
       ["annualized", "per year"]),
    _g("sharpe", "Sharpe ratio", 4, "Return compared with how bumpy the ride was. Higher means more return per unit of risk.",
       "Roughly: below 0 bad, around 0.5 typical for the stock market, above 1 good, above 2 rare.", ["sharpe ratio"]),
    _g("distribution", "Distribution", 4, "How often each size of move happens, shown as a histogram.",
       "Most days are small moves; a few are big ones.", ["histogram"]),
    _g("normal-distribution", "Bell curve", 4, "The 'normal distribution': a smooth, symmetric shape where extreme values are very rare.",
       "Many risk models assume returns follow it. They don't quite.", ["normal distribution", "bell curve", "normal"]),
    _g("fat-tails", "Fat tails", 4, "Extreme moves happen far more often than the bell curve says.",
       "The bell curve expects a 3-standard-deviation day about once in 370 days. Markets have many more.",
       ["fat tail", "tail risk", "big days", "3-sigma"]),

    # Level 5: how assets move together
    _g("correlation", "Correlation", 5, "How much two things move together, from −1 to +1.",
       "+1 = always the same direction, 0 = unrelated, −1 = always opposite.", ["correlated", "corr"]),
    _g("beta", "Beta", 5, "How much a stock tends to move when the whole market moves 1%.",
       "Beta 1.5: when SPY moves 1% the stock tends to move 1.5%. Beta 0.5: half as much.", ["betas"]),
    _g("factor", "Factor", 5, "A common driver that moves many stocks at once, such as the overall market.",
       "Level 10 adds others like value and momentum.", ["factors", "market factor"]),
    _g("benchmark", "Benchmark", 5, "What you compare yourself with, usually just holding SPY.",
       "If you can't beat the benchmark after costs, holding it is simpler."),
    _g("portfolio", "Portfolio", 5, "Everything you own, taken together.", "", ["portfolios", "mix"]),
    _g("weight", "Weight", 5, "The share of a portfolio in one asset, e.g. 60% stocks and 40% bonds.", "", ["weights"]),
    _g("rebalancing", "Rebalancing", 5, "Trading back to your target weights after prices move them.",
       "If stocks rise from 60% to 70% of your portfolio, you sell some to get back to 60%.", ["rebalance", "rebalanced"]),
    _g("bonds", "Bonds", 5, "Loans to a government or company that pay interest. Bond ETFs like TLT often move differently from stocks.",
       "", ["bond", "treasuries"]),

    # Level 6: edge or luck?
    _g("edge", "Edge", 6, "A real, repeatable advantage, as opposed to luck.", "Most strategies don't have one."),
    _g("random-walk", "Random walk", 6, "A price path made of random steps. It can look surprisingly like a real chart.",
       "If a pattern also shows up in random data, it can't be what's making money.", ["random walks"]),
    _g("baseline", "Baseline", 6, "A simple comparison to beat, like buying and holding or trading at random.",
       "", ["random traders", "random trader"]),
    _g("buy-and-hold", "Buy & hold", 6, "What you'd have made by buying at the start and doing nothing.",
       "If a strategy can't beat simply holding, it isn't adding value.", ["buy and hold", "buy & hold"]),
    _g("sample-size", "Sample size", 6, "How many examples a result is based on.",
       "5 trades say almost nothing. 100 say a little more.", ["trades count", "number of trades"]),
    _g("data-snooping", "Data snooping", 6, "Trying lots of ideas on the same data until one looks great by luck.",
       "Also called p-hacking or multiple testing. The more you try, the better the best one looks.",
       ["p-hacking", "multiple testing", "data mining"]),

    # Level 7: testing strategies properly
    _g("strategy", "Strategy", 7, "A fixed set of rules that decides when to buy and when to sell.",
       "Rules remove guesswork and can be tested on past data.", ["strategies"]),
    _g("trend-following", "Trend following", 7, "Strategies that buy after a move up has started, hoping it continues.",
       "Moving-average crossovers and breakouts are trend following.", ["breakout"]),
    _g("mean-reversion", "Mean reversion", 7, "Strategies that buy after a sharp fall, betting on a snap back toward normal.",
       "They suffer in long downtrends.", ["reversion"]),
    _g("backtest", "Backtest", 7, "Running a strategy on past prices to see how it would have done.",
       "Good for ruling ideas out. A good backtest doesn't promise future results.", ["backtesting", "backtester"]),
    _g("win-rate", "Win rate", 7, "The share of trades that made money.",
       "A 40% win rate can still be profitable if the wins are bigger than the losses."),
    _g("profit-factor", "Profit factor", 7, "Total money won ÷ total money lost. Above 1 means profitable overall."),
    _g("costs", "Trading costs", 7, "Fees, spread and slippage added together.",
       "A strategy that trades every day can lose most of its gains to costs.", ["trading costs", "cost"]),
    _g("look-ahead-bias", "Look-ahead bias", 7, "A backtest accidentally using information that wasn't known yet at the time.",
       "This app's backtester trades at the next bar's open to avoid it.", ["look-ahead", "lookahead"]),
    _g("survivorship-bias", "Survivorship bias", 7, "Only testing on things that survived, which makes the past look better.",
       "This app's stock list is today's big winners. Companies that shrank or failed aren't in it.", ["survivorship"]),
    _g("overfitting", "Overfitting", 7, "Tuning a strategy until it fits past data perfectly, so it fails on new data.",
       "The more settings you tune, the more likely a great backtest is luck.", ["curve fitting", "overfit"]),
    _g("in-sample", "In-sample", 7, "The data you used to build and tune a strategy.", "", ["tuning period", "training data"]),
    _g("out-of-sample", "Out-of-sample", 7, "Data the strategy never saw while you built it. The honest test.",
       "", ["test period", "oos", "unseen data"]),
    _g("walk-forward", "Walk-forward test", 7, "Tune on the past, then test once on the period right after it.",
       "If results collapse on the test part, the strategy was overfit.", ["walk forward", "walk-forward"]),
    _g("auto-trading", "Auto-trading", 7, "Letting the app place trades whenever your strategy signals.",
       "Taught properly in Level 13. Your safety net still applies to every automatic trade.", ["auto trading", "auto"]),
    _g("ai-filter", "AI filter", 7, "Auto-trading only takes a buy signal if the AI analyst also likes it.",
       "Taught properly in Levels 12 and 13."),
]
GLOSSARY_BY_SLUG = {g["slug"]: g for g in GLOSSARY}


# --------------------------------------------------------------------------------------------- helpers
def _q(question: str, choices: list[str], answer: int, why: str, hint: str = "") -> dict:
    return {"q": question, "choices": choices, "answer": answer, "why": why, "hint": hint}


def _ch(cid: str, title: str, how: str, event: str, times: int = 1, *, distinct: bool = False,
        bonus: bool = False) -> dict:
    """A practice challenge. Done when `event` has happened `times` times (or with `times` distinct values)."""
    return {"id": cid, "title": title, "how": how, "event": event, "times": times, "distinct": distinct, "bonus": bonus}


def _card(title: str, body: str, visual: str | None = None) -> dict:
    return {"title": title, "body": body, **({"visual": visual} if visual else {})}


# --------------------------------------------------------------------------------------------- levels
LEVELS: list[dict[str, Any]] = [
    {
        "n": 1, "title": "How markets work", "phase": "Markets and money",
        "summary": "Stocks, ETFs and crypto; how orders work; and what the numbers in your account mean.",
        "unlocks": ["Practice: trade US stocks, ETFs and crypto with $100,000 of pretend money",
                    "Market and limit orders"],
        "lessons": [
            {
                "id": "l1-markets", "title": "What you can trade", "minutes": 3,
                "cards": [
                    _card("Buy, then sell",
                          "Trading means buying something and selling it later. If the [[price]] rose in between, you "
                          "make money. If it fell, you lose money.\n\nNobody knows what prices will do next, including "
                          "professionals. Good traders are wrong a lot. They just lose small when they're wrong."),
                    _card("Stocks: a slice of a company",
                          "A [[stock]] (or share) is a tiny piece of ownership in a company. When lots of people want "
                          "Apple shares, the price rises; when they want to sell, it falls.\n\nEvery stock has a "
                          "[[ticker]], a short code: AAPL is Apple, NVDA is Nvidia."),
                    _card("ETFs: a basket in one go",
                          "An [[etf|ETF]] holds many things at once but trades like one stock. SPY holds the 500 biggest "
                          "US companies (the S&P 500 [[index]]). TLT holds government bonds and GLD holds gold.\n\n"
                          "One SPY purchase spreads your money across 500 companies.", "live-prices"),
                    _card("Crypto: never closes",
                          "[[crypto|Crypto]] like Bitcoin trades on online [[exchange|exchanges]] 24 hours a day, 7 days a "
                          "week, and moves several times more than a typical stock.\n\nEverything here is "
                          "[[paper-trading|paper trading]]: real prices, pretend money. Mistakes cost nothing."),
                ],
                "takeaway": "Stocks are slices of companies, ETFs are baskets, crypto is digital money that never sleeps. "
                            "Nobody wins every trade.",
                "quiz": [
                    _q("You own one share of Apple. What do you own?",
                       ["A tiny piece of the company", "An Apple product", "A loan to Apple"], 0,
                       "A share is a small slice of ownership in the company."),
                    _q("Why might someone buy SPY instead of one company's stock?",
                       ["It holds 500 companies, so one bad company hurts less", "It can't go down",
                        "It's always cheaper"], 0,
                       "An ETF like SPY spreads your money across hundreds of companies. It can still fall, though."),
                    _q("Which of these can you trade at 3am on a Sunday?",
                       ["Bitcoin", "Apple shares", "SPY"], 0,
                       "Crypto never closes. US stocks and ETFs only trade on weekdays during market hours."),
                ],
            },
            {
                "id": "l1-orders", "title": "Placing an order", "minutes": 3,
                "cards": [
                    _card("Bid, ask and the spread",
                          "At any moment there's a [[bid]] (the most a buyer will pay) and an [[ask]] (the least a seller "
                          "will take). You buy at the ask and sell at the bid.\n\nIf the bid is $99.98 and the ask is "
                          "$100.02, buying and immediately selling loses 4 cents a share. That gap is the [[spread]]."),
                    _card("Market orders: right now",
                          "A [[market-order|market order]] says 'buy now at the best price available'. It fills almost "
                          "instantly, but you don't pick the exact price, and in a fast market you can get a slightly "
                          "worse one ([[slippage]])."),
                    _card("Limit orders: your price",
                          "A [[limit-order|limit order]] names your price. 'Buy Apple at $250 or lower' waits until the "
                          "price drops to $250, then fills. If it never gets there, it never fills.\n\nA sell limit "
                          "works the other way: 'sell at $280 or higher'."),
                    _card("When markets are open",
                          "US stocks trade 9:30am to 4pm New York time on weekdays ([[market-hours|market hours]]). In "
                          "Sydney that's overnight.\n\nOutside those hours this app fills practice market orders at the "
                          "last price so you can keep learning. A real broker would wait for the open, and limit orders "
                          "here wait too."),
                ],
                "takeaway": "Market orders are fast; limit orders control the price. You always pay the spread.",
                "quiz": [
                    _q("The bid is $50.00 and the ask is $50.10. You buy with a market order. Roughly what do you pay?",
                       ["$50.10", "$50.00", "$50.05"], 0, "Buyers pay the ask. Sellers get the bid."),
                    _q("Apple is $260. You place a limit buy at $250. What happens?",
                       ["It waits, and only fills if the price drops to $250 or lower", "It buys now at $260",
                        "It buys now at $250"], 0,
                       "A limit buy only fills at your price or better. If Apple never drops to $250, it never fills."),
                    _q("When do you want a market order rather than a limit order?",
                       ["When getting in now matters more than the exact price", "When you want a specific price",
                        "Never"], 0,
                       "Market orders trade speed for price control. Limit orders do the opposite."),
                ],
            },
            {
                "id": "l1-account", "title": "Cash, positions and profit", "minutes": 3,
                "cards": [
                    _card("Cash and positions",
                          "Your account holds [[cash]] and [[position|positions]] (things you own). 10 Apple shares "
                          "at $200 is a [[quantity]] of 10 and a [[notional]] value of $2,000.\n\n[[equity|Equity]] is cash plus the current value "
                          "of your positions. It's the number that tells you how you're really doing.", "account"),
                    _card("Profit you have vs profit you've banked",
                          "While you still own something, its gain or loss is [[unrealized|unrealized]]: it can change "
                          "or vanish. When you sell, it becomes [[realized|realized]]. Together they're your "
                          "[[pnl|P&L]]."),
                    _card("The cost of trading",
                          "US stocks and ETFs have no commission here (like most US brokers today), but you still pay "
                          "the spread. Crypto charges a [[fees|fee]] of 0.1% each way: $1 on a $1,000 buy and about $1 "
                          "more when you sell.\n\nSmall amounts, but they add up fast if you trade often."),
                ],
                "takeaway": "Equity = cash + positions. Profit isn't yours until you sell. Every trade starts slightly "
                            "behind because of costs.",
                "quiz": [
                    _q("You have $50,000 cash, $20,000 of SPY and $10,000 of Bitcoin. What is your equity?",
                       ["$80,000", "$50,000", "$30,000"], 0,
                       "Equity is cash plus everything you own: $50,000 + $20,000 + $10,000."),
                    _q("Your Nvidia shares are up $300 but you haven't sold. That $300 is:",
                       ["Unrealized: it could still change", "Realized: it's locked in", "Not profit at all"], 0,
                       "Until you sell, the profit is unrealized."),
                    _q("You buy $1,000 of Bitcoin and sell at exactly the same price. You now have:",
                       ["A little less than $1,000", "Exactly $1,000", "A little more than $1,000"], 0,
                       "You paid a fee on the buy and the sell, plus a little slippage."),
                ],
            },
        ],
        "challenges": [
            _ch("buy-stock", "Buy a stock or ETF",
                "On Practice, pick any stock or ETF (try SPY) and press Buy.", "buy_stock"),
            _ch("buy-crypto", "Buy some crypto", "On Practice, pick Bitcoin, Ethereum or Solana and press Buy.",
                "buy_crypto"),
            _ch("place-limit", "Place a limit order",
                "On Practice, open any asset, choose 'Limit order' and set a buy price a little below today's price.",
                "limit_order"),
            _ch("first-sell", "Sell something you own",
                "On Practice, press 'Sell' on something you own. It's fine if you're down.", "manual_sell"),
            _ch("ask-coach", "Ask the coach a question", "Press 'Ask coach' at the bottom right, e.g. 'what's a spread?'",
                "coach_ask", bonus=True),
            _ch("first-win", "Close a trade in profit", "Sell something for more than you paid, after costs.", "win",
                bonus=True),
        ],
    },
    {
        "n": 2, "title": "Risk first", "phase": "Markets and money",
        "summary": "Stop-losses, sizing every trade by the dollars you could lose, and the maths of losing.",
        "unlocks": ["Safety-net settings on Practice", "Size-by-risk buying", "Editing the exits on things you own"],
        "lessons": [
            {
                "id": "l2-stops", "title": "Stop-loss and take-profit", "minutes": 3,
                "cards": [
                    _card("Your exit if things go wrong",
                          "A [[stop-loss]] sells for you if the price falls to a level you picked. Buy at $100 with a "
                          "stop at $95 and you lose about $5 a share at most.\n\nAbout, because in a fast drop, or when "
                          "a stock opens much lower after bad news, it sells at the next available price."),
                    _card("Your exit if things go right",
                          "A [[take-profit]] sells when the price rises to your target, locking in the gain. Together "
                          "they're a [[bracket]]: the trade ends at whichever is reached first.", "bracket"),
                    _card("Trailing stops",
                          "A [[trailing-stop|trailing stop]] follows the price up. With a 5% trailing stop, if your "
                          "stock climbs from $100 to $120, the stop rises from $95 to $114. It never moves down."),
                ],
                "takeaway": "Decide where you'll get out before you get in, for losing and for winning.",
                "quiz": [
                    _q("You buy at $200 with a stop-loss at $190. Roughly the most you'll lose per share is:",
                       ["$10", "$190", "$20"], 0, "The stop sells around $190, $10 below what you paid."),
                    _q("Why can a stop-loss sell below its level?",
                       ["The price can jump past it, e.g. a stock opening lower after bad news", "It's broken",
                        "It always sells at exactly the level"], 0,
                       "A stop becomes a sale at the next available price. If the price jumps past your level, you get the "
                       "next price."),
                    _q("A trailing stop is 10% and the price rises from $50 to $80. Where is the stop?",
                       ["$72", "$45", "$70"], 0, "10% below the highest price since you bought: $80 × 0.9 = $72."),
                ],
            },
            {
                "id": "l2-sizing", "title": "Size by the dollars you could lose", "minutes": 4,
                "cards": [
                    _card("Start with the loss, not the gain",
                          "Professionals decide first how much they're willing to lose on one trade: their "
                          "[[risk-per-trade|risk per trade]]. A common rule is about 1% of the account.\n\nOn $100,000, "
                          "that's $1,000."),
                    _card("Then work out the size",
                          "Size = risk ÷ stop distance.\n\nRisk $1,000 with a stop 5% below the price: buy $1,000 ÷ 0.05 "
                          "= $20,000. If the stop is hit you lose about $1,000.\n\nA tighter 2% stop lets you buy more "
                          "($50,000), a wider 10% stop less ($10,000). Same $1,000 at risk either way."),
                    _card("A cap on top",
                          "Your [[position-size|position size]] setting caps any single trade, e.g. at 20% of the "
                          "account, so one stock can't dominate. If the risk maths asks for more than the cap, risk "
                          "less or use a wider stop."),
                    _card("Win more than you lose",
                          "The [[risk-reward|risk-reward ratio]] compares the loss at your stop with the gain at your "
                          "target. Risk $1,000 to make $2,000 (1:2) and you can be wrong 2 times in 3 and still about "
                          "break even, before costs."),
                ],
                "takeaway": "Pick the dollars you'll risk first (about 1%), then size = risk ÷ stop distance.",
                "quiz": [
                    _q("Account $50,000, risk 1%, stop 5% below the price. How much should you buy?",
                       ["$10,000", "$500", "$2,500"], 0, "Risk is $500. $500 ÷ 0.05 = $10,000."),
                    _q("You keep the same dollar risk but move the stop from 5% to 10% away. Your size should:",
                       ["Halve", "Double", "Stay the same"], 0,
                       "Size = risk ÷ stop distance. Doubling the distance halves the size."),
                    _q("Risk $100 to make $300. If you win 1 trade in 3, you roughly:",
                       ["Make $100 over the three trades", "Lose $100", "Break even exactly"], 0,
                       "Two losses (−$200) and one win (+$300) leaves +$100, before costs."),
                ],
            },
            {
                "id": "l2-drawdowns", "title": "The maths of losing", "minutes": 3,
                "cards": [
                    _card("Losses hurt more than gains help",
                          "Lose 50% and you need +100% to get back. $10,000 → $5,000, and the $5,000 must double.\n\n"
                          "−10% needs +11%. −25% needs +33%. −50% needs +100%. That's why small losses matter so much. "
                          "The fall from your account's high is your [[drawdown]]."),
                    _card("Streaks happen",
                          "Even with a fair coin, 5 losses in a row happens surprisingly often. Risk 1% a trade and five "
                          "losses cost about 5%. Risk 20% a trade and five losses wipe out about two thirds.\n\nSmall "
                          "bets are what let you survive long enough to be right."),
                    _card("Rules for bad days",
                          "Your [[daily-loss-limit|daily loss limit]] pauses buying after a bad day. It blocks "
                          "[[revenge-trading|revenge trading]] (rushing to win it back) and [[fomo|FOMO]] buys "
                          "(chasing something that's already flying).\n\n[[diversification|Diversification]]: holding "
                          "a few different things means one bad one can't sink you."),
                ],
                "takeaway": "Big losses need much bigger gains to recover. Keep each bet small so streaks can't hurt you badly.",
                "quiz": [
                    _q("Your account falls 25%. What gain gets you back to where you started?",
                       ["About 33%", "25%", "50%"], 0, "$100 → $75. $75 needs +$25, which is 33% of $75."),
                    _q("Which risk per trade survives a streak of 5 losses best?",
                       ["1% of the account", "10% of the account", "25% of the account"], 0,
                       "Five 1% losses cost about 5%. Five 25% losses cost about 76%."),
                    _q("You just lost $2,000 and want a big trade to win it back. That's:",
                       ["Revenge trading: stop and follow your plan", "A smart move", "Diversification"], 0,
                       "Trying to win losses back fast usually makes them bigger."),
                ],
            },
        ],
        "challenges": [
            _ch("set-safety-net", "Set your safety net",
                "On Practice, open 'Your safety net', choose a stop-loss, take-profit and per-trade cap, and press Save.",
                "risk"),
            _ch("risk-sized-buy", "Buy using size-by-risk",
                "On Practice, open any asset, choose 'Size by risk', enter the dollars you'd accept losing (try 1% of "
                "your account) and buy.", "risk_sized"),
            _ch("edit-stop", "Change the exits on something you own",
                "On Practice, press 'Change my exits' on a position and save a new stop-loss.", "brackets", bonus=True),
            _ch("auto-exit", "Let a stop-loss or take-profit close a trade",
                "Keep a position open until the price reaches one of your exits.", "protect_exit", bonus=True),
        ],
    },
    {
        "n": 3, "title": "Charts and classic signals", "phase": "Markets and money",
        "summary": "Candlesticks, trends, gaps, and the indicators traders use most.",
        "unlocks": ["Charts dashboard with the order ticket", "Chart indicators", "The AI analyst"],
        "lessons": [
            {
                "id": "l3-candles", "title": "Candlesticks", "minutes": 3,
                "cards": [
                    _card("One candle, four prices",
                          "Each [[candlestick]] shows a period of time: where the price opened, closed, and its high and "
                          "low. Green means it closed higher than it opened; red means lower.", "candle"),
                    _card("Wicks and timeframes",
                          "The thin [[wick|wicks]] show how far the price went before coming back. The "
                          "[[timeframe]] is how long each candle covers: 1 minute, 1 hour, 1 day.\n\nShort timeframes "
                          "are mostly noise. Daily candles show the bigger picture."),
                    _card("Volume",
                          "The bars under the chart are [[volume]]: how much was traded. A big move on big volume means "
                          "many people agreed; on tiny volume it means less.\n\nOn the Charts screen you trade with the "
                          "[[order-ticket|order ticket]] next to the chart."),
                ],
                "takeaway": "Each candle is open, high, low and close for one period. Look at longer timeframes for the "
                            "real picture.",
                "quiz": [
                    _q("A green candle means the price:",
                       ["Closed higher than it opened", "Will go up next", "Had no trades"], 0,
                       "Colour describes that period only. It doesn't predict the next one."),
                    _q("Which timeframe shows the bigger picture?", ["Daily", "1-minute", "They're the same"], 0,
                       "Longer candles smooth out the noise."),
                ],
            },
            {
                "id": "l3-trends", "title": "Trends, levels and gaps", "minutes": 3,
                "cards": [
                    _card("Trends",
                          "An up [[trend]] makes higher highs and higher lows. A downtrend does the opposite. Trading "
                          "with the trend tends to be easier than fighting it.", "trend"),
                    _card("Support and resistance",
                          "[[support|Support]] is a price where falls have stopped before; [[resistance]] is where "
                          "rises have stopped. They're areas, not exact lines, and they break."),
                    _card("Gaps and earnings",
                          "Stocks stop trading overnight, but news doesn't. If bad news comes out, a stock can open far "
                          "below yesterday's close: a [[gap]]. Four times a year companies report [[earnings]], and "
                          "10% gaps are common then.\n\nA stop-loss can't protect you inside a gap. Crypto trades "
                          "nonstop, so it rarely gaps."),
                ],
                "takeaway": "Trends, support and resistance are useful maps, not promises. Stocks can gap past your "
                            "stop overnight, especially around earnings.",
                "quiz": [
                    _q("Higher highs and higher lows are:", ["An uptrend", "A downtrend", "Support"], 0,
                       "Each peak and dip is higher than the last."),
                    _q("Your stop is at $95. Bad news comes out overnight and the stock opens at $85. You sell at about:",
                       ["$85", "$95", "$90"], 0, "The stop triggers at the first price available, which is the gap open."),
                    _q("Why does crypto rarely gap?", ["It trades nonstop, so there's no overnight close",
                                                         "It's less risky", "It has no news"], 0,
                       "Gaps happen when the market is closed while news arrives."),
                ],
            },
            {
                "id": "l3-signals", "title": "Moving averages, RSI and friends", "minutes": 4,
                "cards": [
                    _card("Indicators describe the past",
                          "An [[indicator]] is a calculation on past prices drawn on the chart. It can't see the "
                          "future; it just summarises what happened in a way that's easier to read."),
                    _card("Moving averages",
                          "A [[sma|moving average]] is the average close of the last N candles. Price above a rising "
                          "average suggests an uptrend. When a fast average crosses above a slow one, that's a "
                          "[[crossover]], a classic buy signal. An [[ema|EMA]] is a faster-reacting version."),
                    _card("RSI",
                          "[[rsi|RSI]] scores recent moves from 0 to 100. Above 70 = rose fast "
                          "([[overbought|overbought]]); below 30 = fell fast. In a strong trend RSI can stay high for "
                          "weeks, so 'overbought' doesn't mean 'will fall'."),
                    _card("MACD and Bollinger Bands",
                          "[[macd|MACD]] shows [[momentum]]: is the short-term trend speeding up or slowing down? "
                          "[[bollinger|Bollinger Bands]] mark unusually high or low prices for recent volatility.\n\n"
                          "When signals agree traders call the picture [[bullish]] or [[bearish]]. The AI analyst's "
                          "[[confidence]] is how strongly they agree, not the chance it's right."),
                ],
                "takeaway": "Indicators summarise the past. Whether any of them actually makes money is a question "
                            "Levels 6 and 7 teach you to test.",
                "quiz": [
                    _q("The price is above its 50-day moving average, and the average is rising. That suggests:",
                       ["An uptrend", "A downtrend", "Nothing at all"], 0, "Price above a rising average is a classic "
                       "uptrend sign."),
                    _q("RSI is 78. What does that tell you?", ["It rose fast recently", "It will fall tomorrow",
                                                               "It's cheap"], 0,
                       "High RSI describes the recent move. It doesn't predict the next one."),
                    _q("The AI analyst says 'bullish, confidence 0.8'. That means:",
                       ["The signals mostly agree on up", "80% chance of profit", "Guaranteed rise"], 0,
                       "Confidence is agreement between signals, not a probability of making money."),
                ],
            },
        ],
        "challenges": [
            _ch("view-charts", "Look at the chart of a stock and a coin",
                "Open Charts and click two different assets, e.g. SPY and Bitcoin.", "chart", 2, distinct=True),
            _ch("indicator-on", "Turn on an indicator", "On Charts, tick SMA20, SMA50 or BB, or switch RSI/MACD.",
                "indicator"),
            _ch("use-ticket", "Place a trade with the order ticket", "On Charts, use the order ticket on the right.",
                "ticket"),
            _ch("ask-ai", "Ask the AI analyst", "On Charts, press 'Analyze now'.", "analyze", bonus=True),
        ],
    },
    {
        "n": 4, "title": "Returns and volatility", "phase": "Thinking in numbers",
        "summary": "Measure assets like a quant: returns, volatility, the Sharpe ratio, drawdowns and fat tails.",
        "unlocks": ["Lab: Stats for every asset (5 years of daily data)"],
        "lessons": [
            {
                "id": "l4-returns", "title": "Returns, the quant way", "minutes": 3,
                "cards": [
                    _card("Percentages, not dollars",
                          "Quants measure moves as [[return|returns]]. A $10 stock rising $1 is +10%; a $500 stock "
                          "rising $1 is +0.2%. Percentages let you compare anything with anything.\n\nA "
                          "[[daily-return|daily return]] is today's change ÷ yesterday's close: $100 → $102 is +2%."),
                    _card("Compounding",
                          "Returns multiply, they don't add. +10% then −10%: $100 → $110 → $99. You're down 1%, not "
                          "flat.\n\nThat's [[compounding]]. It's why big swings drag down long-run growth even when "
                          "the ups and downs look equal."),
                    _card("Yearly growth",
                          "[[cagr|Yearly growth (CAGR)]] is the steady yearly return that gets you from start to end. "
                          "$10,000 → $20,000 in 5 years is about 15% a year, even if some years were −20% and others "
                          "+40%."),
                ],
                "takeaway": "Use percentage returns to compare things. Returns compound, so losses drag more than "
                            "they look.",
                "quiz": [
                    _q("A stock goes $40 → $42. What's the return?", ["+5%", "+2%", "+20%"], 0, "$2 ÷ $40 = 5%."),
                    _q("+50% then −50%. Where are you?", ["Down 25%", "Back to even", "Up 25%"], 0,
                       "$100 → $150 → $75."),
                    _q("Why compare returns instead of dollar moves?",
                       ["So assets with different prices can be compared", "Dollars aren't real", "It looks bigger"], 0,
                       "$1 means a lot on a $10 stock and little on a $500 one."),
                ],
            },
            {
                "id": "l4-volatility", "title": "Volatility: how bumpy is it?", "minutes": 4,
                "cards": [
                    _card("Typical move size",
                          "[[volatility|Volatility]] is how big the typical move is. It's measured with the "
                          "[[standard-deviation|standard deviation]] of daily returns: about two days in three move "
                          "less than that.\n\nIn dollars: $10,000 in SPY typically moves about $100 a day. In Bitcoin, "
                          "more like $300."),
                    _card("From daily to yearly",
                          "Volatility is usually quoted per year ([[annualised]]): daily volatility × √252 for stocks "
                          "(trading days) or × √365 for crypto. That's roughly × 16 or × 19.\n\nSo 1% a day ≈ 16% a "
                          "year: SPY-like. 3% a day ≈ 57% a year: crypto-like."),
                    _card("Bumpier isn't better or worse",
                          "Higher volatility means bigger gains and bigger losses. What matters is how much return you "
                          "get for the bumps you sit through. That's the next lesson."),
                ],
                "takeaway": "Volatility is the typical size of a move. Yearly ≈ daily × 16 for stocks.",
                "quiz": [
                    _q("An asset's daily volatility is 2%. Roughly what's its yearly volatility (stock, 252 days)?",
                       ["About 32%", "About 2%", "About 500%"], 0, "2% × √252 ≈ 2% × 15.9 ≈ 32%."),
                    _q("$10,000 in an asset with 3% daily volatility typically moves about:",
                       ["$300 a day", "$30 a day", "$3,000 a day"], 0, "3% of $10,000."),
                    _q("Higher volatility means:", ["Bigger swings both ways", "Higher returns guaranteed",
                                                    "Lower risk"], 0, "Volatility cuts both ways."),
                ],
            },
            {
                "id": "l4-risk-adjusted", "title": "Sharpe, drawdowns and fat tails", "minutes": 4,
                "cards": [
                    _card("Return per bump",
                          "The [[sharpe|Sharpe ratio]] divides return by volatility. A: +10% a year with 10% "
                          "volatility (Sharpe 1). B: +15% with 30% volatility (Sharpe 0.5). B made more, but A gave "
                          "more return for each unit of risk.\n\nThe stock market has averaged roughly 0.4 to 0.6."),
                    _card("The worst fall",
                          "Max [[drawdown]] is the biggest fall from a high. It's the number that tells you how painful "
                          "holding would have felt. Many people sell at the bottom of a deep drawdown."),
                    _card("Fat tails",
                          "If daily returns followed a [[normal-distribution|bell curve]], a 3-standard-deviation day "
                          "would happen about once every 370 days. Real markets have far more: [[fat-tails|fat "
                          "tails]].\n\nThe Lab's [[distribution]] chart puts the real days next to the bell curve so "
                          "you can see it."),
                ],
                "takeaway": "Judge return together with risk (Sharpe, max drawdown). Extreme days are more common than "
                            "the bell curve says.",
                "quiz": [
                    _q("Fund A: 8% a year, 8% volatility. Fund B: 12% a year, 24% volatility. Better Sharpe?",
                       ["A (1.0 vs 0.5)", "B, because it made more", "They're equal"], 0,
                       "8 ÷ 8 = 1.0 versus 12 ÷ 24 = 0.5."),
                    _q("Fat tails mean:", ["Huge moves happen more often than the bell curve predicts",
                                           "Prices only go up", "Volatility is zero"], 0,
                       "Risk models that assume a bell curve underestimate crashes."),
                    _q("Why look at max drawdown as well as return?",
                       ["It shows how painful holding would have been", "It's the same as return", "It predicts profit"],
                       0, "A great return is no use if you'd have sold in panic halfway down."),
                ],
            },
        ],
        "challenges": [
            _ch("stats-two", "Compare a stock ETF with crypto",
                "Open Lab → Stats and look at SPY, then at Bitcoin. Compare their volatility and max drawdown.",
                "stats", 2, distinct=True),
            _ch("stats-more", "Look at the stats of 5 different assets",
                "In Lab → Stats, try a few more, e.g. KO, NVDA and TLT.", "stats", 5, distinct=True, bonus=True),
        ],
    },
    {
        "n": 5, "title": "How assets move together", "phase": "Thinking in numbers",
        "summary": "Correlation, beta and the market factor, and why mixing assets can lower risk.",
        "unlocks": ["Lab: correlation map with beta", "Lab: portfolio mixer"],
        "lessons": [
            {
                "id": "l5-correlation", "title": "Correlation", "minutes": 3,
                "cards": [
                    _card("Do they move together?",
                          "[[correlation|Correlation]] runs from −1 to +1. +1: always move the same way. 0: unrelated. "
                          "−1: always opposite.\n\nApple and Microsoft are strongly positive: they're both big tech, "
                          "driven by the same news."),
                    _card("Different assets, different drivers",
                          "Stocks and long-term government [[bonds]] (TLT) are often close to zero or negative. Gold "
                          "has its own drivers. Crypto has become more linked to tech stocks over time."),
                    _card("Two warnings",
                          "Correlation isn't cause: two things can move together without one causing the other.\n\n"
                          "And correlations change. In a crash, many assets fall together, so diversification helps "
                          "less exactly when you want it most."),
                ],
                "takeaway": "Correlation measures how much two things move together. It changes, and it rises in crashes.",
                "quiz": [
                    _q("Correlation of +0.9 means:", ["They usually move the same way", "They move opposite",
                                                      "One causes the other"], 0, "Close to +1 means moving together."),
                    _q("Which pair would you expect to have the lowest correlation?",
                       ["SPY and TLT (stocks and bonds)", "AAPL and MSFT", "SPY and QQQ"], 0,
                       "The others are all big US stocks driven by the same news."),
                    _q("During a market crash, correlations between stocks usually:", ["Rise", "Fall to zero",
                                                                                       "Turn negative"], 0,
                       "Everything gets sold together, so diversification helps less."),
                ],
            },
            {
                "id": "l5-beta", "title": "The market factor and beta", "minutes": 3,
                "cards": [
                    _card("Most stocks ride the market",
                          "A big part of any stock's move is just the whole market moving. That shared driver is the "
                          "market [[factor]], and SPY is the usual [[benchmark]] for it."),
                    _card("Beta",
                          "[[beta|Beta]] says how much a stock tends to move when SPY moves 1%. Beta 1.5: about 1.5%. "
                          "Beta 0.5: about 0.5%.\n\nCoca-Cola tends to have a low beta (people buy drinks in "
                          "recessions); Nvidia and Tesla high ones."),
                    _card("Why quants care",
                          "If your strategy made 20% while the market made 20%, and your beta was 1, you didn't add "
                          "anything: you just held the market with extra steps. Real skill is return the market "
                          "doesn't explain."),
                ],
                "takeaway": "Beta measures how much of a stock's move comes from the market. Beat the benchmark, not just zero.",
                "quiz": [
                    _q("A stock has beta 2. SPY falls 3% today. The stock most likely:",
                       ["Falls about 6%", "Rises 6%", "Falls about 1.5%"], 0, "2 × 3% = 6%, in the same direction."),
                    _q("Which is likely to have the lowest beta?", ["Coca-Cola", "Nvidia", "Tesla"], 0,
                       "Steady businesses tend to move less than the market."),
                    _q("Your strategy and SPY both made 15% this year with beta 1. Your skill added:",
                       ["About nothing", "15%", "30%"], 0, "Holding SPY would have done the same."),
                ],
            },
            {
                "id": "l5-diversification", "title": "Mixing assets", "minutes": 4,
                "cards": [
                    _card("The free lunch",
                          "A [[portfolio]] of two assets that don't move together is calmer than either alone, because "
                          "one's bad days are often the other's good days.\n\nThat's [[diversification]], often called "
                          "the only free lunch in investing."),
                    _card("Weights and rebalancing",
                          "Each asset's [[weight]] is its share of the portfolio, e.g. 60% SPY and 40% TLT. As prices "
                          "move, the weights drift. [[rebalancing|Rebalancing]] trades back to the targets, which "
                          "means selling some of what rose and buying what fell."),
                    _card("The numbers",
                          "Two assets each with 20% volatility: correlated +1, a 50/50 mix is still 20%. Correlation 0: "
                          "about 14%. Correlation −0.5: 10%.\n\nThe lower the correlation, the bigger the benefit. "
                          "Adding a very volatile asset like crypto can raise risk even when correlation is low."),
                ],
                "takeaway": "Mixing assets that don't move together lowers risk. Low correlation is what makes it work.",
                "quiz": [
                    _q("Two assets with correlation +1 mixed 50/50. Compared with each alone, the mix is:",
                       ["Just as bumpy", "Much calmer", "Twice as bumpy"], 0,
                       "If they always move together, mixing changes nothing."),
                    _q("Your target is 60% stocks / 40% bonds. Stocks rally and are now 70%. Rebalancing means:",
                       ["Sell some stocks, buy bonds", "Buy more stocks", "Do nothing"], 0,
                       "Trade back to the 60/40 target."),
                    _q("What makes diversification work best?", ["Low or negative correlation",
                                                                  "High correlation", "Owning only one asset"], 0,
                       "The less they move together, the more the bumps cancel."),
                ],
            },
        ],
        "challenges": [
            _ch("corr-map", "Open the correlation map", "Open Lab → Correlation and look at which pairs move together.",
                "corr"),
            _ch("calm-mix", "Build a mix that's calmer than every asset in it",
                "In Lab → Mixer, pick 2 to 5 assets and weights until the mix's volatility is below every single "
                "asset's. Hint: try SPY with TLT or GLD.", "mix_calm"),
            _ch("mix-crypto", "Mix stocks and crypto", "In Lab → Mixer, include at least one stock or ETF and one coin.",
                "mix_cross", bonus=True),
        ],
    },
    {
        "n": 6, "title": "Edge or luck?", "phase": "Thinking in numbers",
        "summary": "Why random results look like skill, and how to compare a strategy with random traders.",
        "unlocks": ["Lab: luck test (your strategy vs 500 random traders)", "Lab: best-of-20 random strategies"],
        "lessons": [
            {
                "id": "l6-randomness", "title": "Randomness fools us", "minutes": 3,
                "cards": [
                    _card("Streaks are normal",
                          "Flip a coin 100 times and you'll very likely see a run of 6 or more heads in a row. Our "
                          "brains read streaks as meaning something. Usually they're just chance."),
                    _card("Random charts look real",
                          "A [[random-walk|random walk]] adds up random daily moves. Its chart has 'trends', 'support' "
                          "and 'breakouts', even though nothing caused them.\n\nIf a pattern shows up just as often in "
                          "random data, it can't be what's making money."),
                    _card("What an edge is",
                          "An [[edge]] is a real, repeatable advantage. Most trading ideas don't have one, and the hard "
                          "part is telling an edge apart from a lucky run."),
                ],
                "takeaway": "Streaks and patterns appear in pure randomness. Assume luck until the evidence says otherwise.",
                "quiz": [
                    _q("A trader wins 6 trades in a row. That proves:", ["Very little on its own", "They have an edge",
                                                                         "They'll win the 7th"], 0,
                       "Six-in-a-row streaks happen regularly by chance."),
                    _q("A chart pattern appears just as often in random data. It's:",
                       ["Probably useless for making money", "A strong signal", "Proof of an edge"], 0,
                       "If chance makes it too, it can't explain profits."),
                ],
            },
            {
                "id": "l6-baseline", "title": "Compare with a random trader", "minutes": 4,
                "cards": [
                    _card("Good compared with what?",
                          "A strategy made 20%. Is that good? You need a [[baseline]]. Two simple ones:\n\n[[buy-and-hold|"
                          "Buy & hold]]: what doing nothing would have made.\nRandom traders: coin-flippers who trade "
                          "about as often and hold about as long as your strategy."),
                    _card("The luck test",
                          "Run 500 random traders on the same prices. If your strategy beats 60% of them, about 4 in "
                          "10 coin-flippers did as well: no evidence of skill. Beat 95%+, and luck becomes a stretch."),
                    _card("Sample size",
                          "5 trades tell you almost nothing. 100 tell you a little more. A small [[sample-size|sample "
                          "size]] is how lucky streaks get mistaken for skill."),
                ],
                "takeaway": "Compare every result with buy & hold and with random traders. More trades means more evidence.",
                "quiz": [
                    _q("Your strategy beats 70% of random traders. What's the honest conclusion?",
                       ["Luck could easily explain it", "It has a clear edge", "It will beat 70% next year"], 0,
                       "Nearly 1 in 3 coin-flippers did as well."),
                    _q("Which is stronger evidence?", ["60% wins over 200 trades", "100% wins over 4 trades",
                                                       "They're equal"], 0, "Four trades is far too small a sample."),
                    _q("A strategy made +12%. Buy & hold made +25%. The strategy:",
                       ["Underperformed doing nothing", "Did great", "Beat the market"], 0,
                       "Always check what holding would have made."),
                ],
            },
            {
                "id": "l6-many-tries", "title": "Try enough ideas and one will look great", "minutes": 3,
                "cards": [
                    _card("Best of 20",
                          "Test 20 random strategies on the same history and pick the best. It will look impressive, "
                          "because you picked the luckiest of 20.\n\nThen run it on the next period: it's back to "
                          "average. Luck doesn't repeat."),
                    _card("Data snooping",
                          "Trying idea after idea on the same data until one works is [[data-snooping|data "
                          "snooping]] (also called p-hacking). Every extra try makes the best result look better, "
                          "without being any more real."),
                    _card("The fixes",
                          "Count how many things you tried. Be more sceptical the more you tried. And test the winner "
                          "on data it has never seen. That's Level 7."),
                ],
                "takeaway": "The best of many tries is mostly luck. Test winners on fresh data.",
                "quiz": [
                    _q("You tested 50 strategies and the best made 80%. Most likely:",
                       ["Luck played a big part", "It's a great strategy", "It'll make 80% again"], 0,
                       "The best of 50 tries is usually the luckiest of 50."),
                    _q("What's the best fix for data snooping?",
                       ["Test the winner on data it never saw", "Try even more ideas", "Use a shorter history"], 0,
                       "Fresh data can't have been snooped."),
                ],
            },
        ],
        "challenges": [
            _ch("luck-test", "Run a luck test", "Open Lab → Luck test, pick an asset and a strategy, and run it.",
                "luck_test"),
            _ch("best-of-20", "Watch the best of 20 random strategies",
                "In Lab → Luck test, run 'Best of 20 random strategies' and compare the two periods.", "luck_many"),
            _ch("beat-95", "Find a strategy that beats 95% of random traders",
                "Try different assets and strategies. It's rare, and when it happens, test it again in Level 7.",
                "luck_beat95", bonus=True),
        ],
    },
    {
        "n": 7, "title": "Testing strategies properly", "phase": "Testing strategies",
        "summary": "Strategy types, reading a backtest, costs, the biases that make backtests lie, and walk-forward tests.",
        "unlocks": ["Lab: backtester with costs", "Lab: walk-forward test", "Strategy settings on Charts"],
        "lessons": [
            {
                "id": "l7-strategies", "title": "Three kinds of strategy", "minutes": 3,
                "cards": [
                    _card("Rules, not feelings",
                          "A [[strategy]] is a fixed set of rules for when to buy and sell. Rules can be tested on past "
                          "data, and they don't panic."),
                    _card("Trend following and momentum",
                          "[[trend-following|Trend following]] buys after a move up has started, e.g. a moving-average "
                          "crossover. A [[momentum]] strategy buys what has risen over the last few months. Both suffer "
                          "in choppy, sideways markets."),
                    _card("Mean reversion",
                          "[[mean-reversion|Mean reversion]] does the opposite: buy after a sharp fall (e.g. RSI below "
                          "30), betting on a bounce. It suffers in long downtrends, when 'cheap' keeps getting cheaper."),
                ],
                "takeaway": "Trend and momentum ride moves; mean reversion fades them. Each fails in some kind of market.",
                "quiz": [
                    _q("'Buy when the 20-day average crosses above the 50-day' is:", ["Trend following",
                                                                                     "Mean reversion", "Random"], 0,
                       "It waits for an uptrend to start."),
                    _q("Where does mean reversion usually struggle?", ["Long downtrends", "Choppy markets",
                                                                       "It never struggles"], 0,
                       "Buying dips keeps losing if the dips keep going."),
                ],
            },
            {
                "id": "l7-backtests", "title": "Reading a backtest, costs included", "minutes": 4,
                "cards": [
                    _card("The key numbers",
                          "A [[backtest]] runs your rules on past prices. Read the total [[return]] next to "
                          "[[buy-and-hold|buy & hold]], then the [[sharpe|Sharpe]] and max [[drawdown]]. "
                          "[[win-rate|Win rate]] and [[profit-factor|profit factor]] (money won ÷ money lost) show how it "
                          "made its money."),
                    _card("Costs eat fast strategies",
                          "[[costs|Trading costs]] are fees plus spread plus slippage. At 0.1% each way, a strategy that "
                          "trades every day pays about 0.2% a day: over 40% a year before it makes a cent.\n\nThe Lab's "
                          "backtester lets you set costs. Always test with realistic ones."),
                    _card("All in, or part in?",
                          "The Lab's backtester puts the whole account into each trade so results compare fairly with "
                          "buy & hold. Your real position size and stop-loss would change the numbers."),
                ],
                "takeaway": "Compare with buy & hold, look at risk as well as return, and include realistic costs.",
                "quiz": [
                    _q("A strategy: +30% return, max drawdown 60%. Buy & hold: +35%, drawdown 25%. Which is better?",
                       ["Buy & hold", "The strategy", "Can't say"], 0, "More return and a much smaller drawdown."),
                    _q("Win rate 35%, profit factor 1.8. This strategy:", ["Made money overall", "Lost money",
                                                                          "Broke even"], 0,
                       "Profit factor above 1 means wins outweighed losses, despite losing most trades."),
                    _q("Who suffers most from costs?", ["A strategy trading many times a day", "Buy & hold",
                                                        "A strategy trading twice a year"], 0,
                       "Costs are paid on every trade."),
                ],
            },
            {
                "id": "l7-biases", "title": "Why backtests lie", "minutes": 4,
                "cards": [
                    _card("Look-ahead bias",
                          "[[look-ahead-bias|Look-ahead bias]] is using information that wasn't known yet, like "
                          "buying at today's open using today's close. This app's backtester always trades at the next "
                          "bar's open to avoid it."),
                    _card("Survivorship bias",
                          "This app's stock list is today's famous winners: Nvidia, Apple, Microsoft. Companies that "
                          "shrank or went bust aren't on it. Testing only survivors makes the past look easier than it "
                          "was: [[survivorship-bias|survivorship bias]]."),
                    _card("Overfitting",
                          "Tweak enough settings and you can make any strategy look brilliant on the past. That's "
                          "[[overfitting]]. It's the same trap as data snooping: the settings fitted the noise, not "
                          "a real pattern."),
                ],
                "takeaway": "Backtests flatter you through look-ahead, survivorship and overfitting. Assume the real "
                            "result will be worse.",
                "quiz": [
                    _q("You test a strategy only on stocks in today's S&P 500. That risks:", ["Survivorship bias",
                                                                                             "Look-ahead bias",
                                                                                             "No bias"], 0,
                       "Companies that dropped out (often after falling) are missing."),
                    _q("You tried 200 setting combinations and kept the best. The main danger is:", ["Overfitting",
                                                                                                     "Too few costs",
                                                                                                     "Gaps"], 0,
                       "The best of 200 fits past noise."),
                ],
            },
            {
                "id": "l7-walkforward", "title": "The honest test: out-of-sample", "minutes": 4,
                "cards": [
                    _card("Split the history",
                          "Build and tune on the first part, the [[in-sample|in-sample]] data. Then test once on "
                          "the rest, which the strategy has never seen: the [[out-of-sample|out-of-sample]] data."),
                    _card("Walk-forward",
                          "A [[walk-forward|walk-forward test]] does this in order: tune on the past, then trade the "
                          "period right after. If the in-sample result was +60% and out-of-sample is −5%, the strategy "
                          "was overfit."),
                    _card("One look only",
                          "If you peek at the test result, re-tune and test again, the test data has become tuning "
                          "data. Professionals keep a final chunk of history locked away for exactly this reason."),
                ],
                "takeaway": "Tune on the past, test once on unseen data. A strategy that collapses out-of-sample was luck.",
                "quiz": [
                    _q("In-sample +45%, out-of-sample −8%. Most likely:", ["Overfitting", "A great strategy",
                                                                           "Bad luck in the test"], 0,
                       "Big in-sample results that vanish on new data are the classic sign."),
                    _q("Why not re-tune after seeing the out-of-sample result?",
                       ["Then it's no longer unseen data", "It takes too long", "You should"], 0,
                       "Each peek leaks the test into the tuning."),
                    _q("What's the honest number to judge a strategy by?", ["Out-of-sample result",
                                                                            "Best in-sample result",
                                                                            "Win rate"], 0,
                       "Only results on unseen data say anything about the future."),
                ],
            },
        ],
        "challenges": [
            _ch("run-backtest", "Run a backtest", "Open Lab → Backtest, pick an asset and strategy, and run it.",
                "backtest"),
            _ch("compare-strategies", "Compare two strategies", "Backtest two different strategies on the same asset.",
                "backtest_strategy", 2, distinct=True),
            _ch("walk-forward", "Run a walk-forward test",
                "In Lab → Backtest, press 'Walk-forward test' and compare tuning and test results.", "walkforward"),
            _ch("oos-beat-hold", "Beat buy & hold out-of-sample",
                "Find a strategy whose walk-forward test result beats buy & hold on the unseen part.", "oos_beat_hold",
                bonus=True),
        ],
    },
]

# Levels planned but not built yet. Shown on the course page as "coming soon".
ROADMAP: list[dict[str, Any]] = [
    {"n": 8, "title": "Python for quants", "phase": "Code",
     "summary": "pandas basics: load prices, compute returns and volatility, draw charts. An AI pair-programmer "
                "explains and fixes your code.", "unlocks": ["Code Lab with an AI pair-programmer"]},
    {"n": 9, "title": "Your strategy in code", "phase": "Code",
     "summary": "Write a signal function, run it through the same backtester and walk-forward test.",
     "unlocks": ["Custom strategies"]},
    {"n": 10, "title": "Portfolios and factors", "phase": "Portfolios and risk",
     "summary": "Volatility targeting, risk parity, why mean-variance is fragile, rebalancing costs, half-Kelly; value, "
                "momentum and quality factors.", "unlocks": ["Portfolio builder", "Factor breakdown of your returns"]},
    {"n": 11, "title": "Machine-learning signals", "phase": "Machine learning",
     "summary": "Features and labels, splitting by time, leakage, gradient boosting, and why accuracy isn't profit.",
     "unlocks": ["ML Lab"]},
    {"n": 12, "title": "LLMs as research analysts", "phase": "LLMs and agents",
     "summary": "Summarise SEC filings and earnings news, turn text into data, catch hallucinations, and avoid the "
                "'LLM already knows the future' trap.", "unlocks": ["Research agent (local AI)"]},
    {"n": 13, "title": "Agents and the capstone", "phase": "LLMs and agents",
     "summary": "Research → signal → risk check → paper order, with your approval, logs and a kill switch. Then run "
                "your strategy for 4 weeks and write it up against SPY.", "unlocks": ["Auto-trading agent"]},
]
TOTAL_LEVELS = len(LEVELS) + len(ROADMAP)
