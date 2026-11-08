"""Learning path: levels, lessons, quizzes, practice challenges, glossary and progress tracking.

The course is the spine of the app. Each level teaches a topic and unlocks the matching part of the app
(level N's features become usable once the user reaches level N). A level is complete when every lesson quiz is
answered correctly and every required (non-bonus) challenge is done; then the next level unlocks.

Text fields may contain glossary links written as [[slug]] or [[slug|shown text]]; the frontend turns them into
tappable terms. Progress lives in the kv table under "learn".
"""
import re
import time
from typing import Any

from app.db import Database

PROTECTIVE_EXITS = {"stop_loss", "take_profit", "trailing_stop"}
CLIENT_EVENTS = {"chart", "indicator", "ticket"}  # actions only the browser can see

# --------------------------------------------------------------------------------------------- glossary
# level = the level whose lessons teach the term.
GLOSSARY: list[dict[str, Any]] = [
    {"slug": "crypto", "term": "Crypto", "level": 1, "aliases": ["cryptocurrency"],
     "short": "Digital money like Bitcoin that trades online all day, every day.",
     "more": "Crypto prices can move several percent in an hour, which makes them good for practice but risky with real money."},
    {"slug": "paper-trading", "term": "Paper trading", "level": 1, "aliases": ["paper"],
     "short": "Trading with pretend money so mistakes cost nothing.",
     "more": "Everything in this app is paper trading. Prices are real; the money is not."},
    {"slug": "price", "term": "Price", "level": 1, "aliases": ["mark", "mark price", "last price"],
     "short": "The last price a buyer and a seller agreed on.",
     "more": "It changes every time a new deal happens, which is why it moves all day."},
    {"slug": "usdt", "term": "USDT", "level": 1, "aliases": ["tether"],
     "short": "A digital dollar. BTCUSDT means 'Bitcoin priced in dollars'.",
     "more": "USDT is designed to stay worth about one US dollar."},
    {"slug": "cash", "term": "Cash", "level": 1, "aliases": [],
     "short": "Money in your account that isn't in any coin yet.",
     "more": "You need cash to buy. Selling a coin turns it back into cash."},
    {"slug": "position", "term": "Position", "level": 1, "aliases": ["positions", "open positions", "holding"],
     "short": "A coin you currently own.",
     "more": "'Opening a position' means buying; 'closing' it means selling."},
    {"slug": "quantity", "term": "Quantity", "level": 1, "aliases": ["qty", "size"],
     "short": "How many coins (or parts of a coin) you own or are trading.",
     "more": "You can buy part of a coin, e.g. 0.01 Bitcoin."},
    {"slug": "notional", "term": "Notional", "level": 1, "aliases": ["value", "market value"],
     "short": "The dollar value of a trade or position: quantity × price.",
     "more": "0.1 Bitcoin at $60,000 has a notional value of $6,000."},
    {"slug": "equity", "term": "Equity", "level": 1, "aliases": ["account value", "final equity"],
     "short": "What your whole account is worth: cash plus the value of your coins.",
     "more": "This is the number to watch. It goes up and down as prices move, even if you don't trade."},
    {"slug": "pnl", "term": "P&L", "level": 1, "aliases": ["pnl", "profit and loss", "day pnl", "total pnl", "pnl %"],
     "short": "Profit and loss: how much you've made or lost.",
     "more": "Positive means profit, negative means a loss."},
    {"slug": "unrealized", "term": "Unrealized P&L", "level": 1, "aliases": ["upnl", "unrealized pnl", "unrealised"],
     "short": "Profit or loss on coins you still own. It isn't locked in until you sell.",
     "more": "It can disappear if the price turns. 'Paper profit' is another name for it."},
    {"slug": "realized", "term": "Realized P&L", "level": 1, "aliases": ["realized pnl", "realised"],
     "short": "Profit or loss you locked in by selling.",
     "more": "Once you sell, this number no longer changes with the price."},
    {"slug": "fees", "term": "Fees", "level": 1, "aliases": ["fee", "fees paid"],
     "short": "What the exchange charges per trade. Here it's 0.1% each time you buy or sell.",
     "more": "Fees are charged on the way in and the way out, so frequent trading adds up fast."},
    {"slug": "slippage", "term": "Slippage", "level": 1, "aliases": [],
     "short": "Getting a slightly worse price than you saw, because the price moved as your order filled.",
     "more": "This app simulates a small amount of slippage on every trade, like a real exchange."},
    {"slug": "stop-loss", "term": "Stop-loss", "level": 2, "aliases": ["stop loss", "sl", "stop"],
     "short": "An automatic sell if the price falls to a level you chose. It caps your loss.",
     "more": "In a fast drop the sale can happen a little below your level, so think of it as 'about' the most you'll lose."},
    {"slug": "take-profit", "term": "Take-profit", "level": 2, "aliases": ["take profit", "tp", "target"],
     "short": "An automatic sell when the price rises to your target, locking in the gain.",
     "more": "It stops a winning trade from turning back into a losing one while you're not watching."},
    {"slug": "bracket", "term": "Bracket", "level": 2, "aliases": ["brackets"],
     "short": "A stop-loss and a take-profit set together around a trade.",
     "more": "The trade will end at one of the two, whichever the price reaches first."},
    {"slug": "trailing-stop", "term": "Trailing stop", "level": 2, "aliases": ["trailing"],
     "short": "A stop-loss that moves up as the price rises, so it protects gains as well as limiting losses.",
     "more": "A 3% trailing stop always sits 3% below the highest price since you bought."},
    {"slug": "position-size", "term": "Position size", "level": 2, "aliases": ["max position", "position sizing"],
     "short": "How much money you put into one trade.",
     "more": "Here it's set as a share of your account, e.g. 20% means at most $20,000 of a $100,000 account per trade."},
    {"slug": "risk-reward", "term": "Risk-reward ratio", "level": 2, "aliases": ["risk reward", "r:r"],
     "short": "How much you could lose compared with how much you could win on a trade.",
     "more": "Risking $100 to make $200 is 1:2. With 1:2 you can be wrong two times out of three and still roughly break even."},
    {"slug": "diversification", "term": "Diversification", "level": 2, "aliases": ["max open positions"],
     "short": "Spreading money over several trades so one bad one can't sink you.",
     "more": "The 'max open positions' setting limits how many coins you hold at once."},
    {"slug": "drawdown", "term": "Drawdown", "level": 2, "aliases": ["max drawdown"],
     "short": "How far your account has fallen from its highest point.",
     "more": "Max drawdown is the worst fall. A 50% drawdown needs a 100% gain just to get back to even."},
    {"slug": "daily-loss-limit", "term": "Daily loss limit", "level": 2, "aliases": ["daily loss", "halted", "halt"],
     "short": "If you lose this much in one day, buying pauses until tomorrow.",
     "more": "It stops a bad day becoming a disaster, and stops 'revenge trading' to win it back."},
    {"slug": "candlestick", "term": "Candlestick", "level": 3, "aliases": ["candle", "candles", "candlesticks", "ohlc"],
     "short": "One bar on a price chart showing the open, high, low and close for a period of time.",
     "more": "Green: the price closed higher than it opened. Red: it closed lower."},
    {"slug": "wick", "term": "Wick", "level": 3, "aliases": ["shadow", "wicks"],
     "short": "The thin line above or below a candle's body: how high or low the price went before coming back.",
     "more": "A long upper wick means buyers pushed the price up but sellers pushed it back down."},
    {"slug": "timeframe", "term": "Timeframe", "level": 3, "aliases": ["interval", "bars"],
     "short": "How much time each candle covers, e.g. 1 minute (1m) or 1 hour (1h).",
     "more": "Short timeframes are noisy. Longer ones show the bigger picture."},
    {"slug": "trend", "term": "Trend", "level": 3, "aliases": ["uptrend", "downtrend"],
     "short": "The general direction of the price. Up means higher highs and higher lows.",
     "more": "Trading in the direction of the trend tends to be easier than fighting it."},
    {"slug": "support", "term": "Support", "level": 3, "aliases": [],
     "short": "A price level where the price has stopped falling before, because buyers stepped in.",
     "more": "If the price breaks clearly below support, it often keeps falling."},
    {"slug": "resistance", "term": "Resistance", "level": 3, "aliases": [],
     "short": "A price level where the price has stopped rising before, because sellers stepped in.",
     "more": "A clear break above resistance can be the start of a new move up."},
    {"slug": "volume", "term": "Volume", "level": 3, "aliases": [],
     "short": "How much was traded in a period. Big moves on high volume are more convincing.",
     "more": "Volume is the bars at the bottom of the chart."},
    {"slug": "order-ticket", "term": "Order ticket", "level": 3, "aliases": ["order"],
     "short": "The form you use to place a buy or sell.",
     "more": "Leave quantity on 'Auto' and the app sizes the trade using your position-size setting."},
    {"slug": "bullish", "term": "Bullish", "level": 4, "aliases": ["bull"],
     "short": "Expecting the price to go up.",
     "more": "From a bull, which strikes upward with its horns."},
    {"slug": "bearish", "term": "Bearish", "level": 4, "aliases": ["bear"],
     "short": "Expecting the price to go down.",
     "more": "From a bear, which swipes downward with its paws."},
    {"slug": "indicator", "term": "Indicator", "level": 4, "aliases": ["indicators"],
     "short": "A calculation from past prices drawn on the chart to help spot trends or turning points.",
     "more": "Indicators only use the past. They describe; they don't predict."},
    {"slug": "sma", "term": "Moving average (SMA)", "level": 4,
     "aliases": ["moving average", "sma20", "sma50", "sma 20", "sma 50", "simple moving average"],
     "short": "The average closing price over the last N candles, drawn as a smooth line.",
     "more": "SMA20 averages 20 candles (short term), SMA50 averages 50 (longer term). Price above the line suggests an uptrend."},
    {"slug": "ema", "term": "EMA", "level": 4, "aliases": ["exponential moving average"],
     "short": "A moving average that gives more weight to recent prices, so it reacts faster.",
     "more": "Faster also means more false alarms."},
    {"slug": "crossover", "term": "Crossover", "level": 4, "aliases": ["cross", "golden cross", "death cross", "sma cross"],
     "short": "When a fast moving average crosses a slow one, a common buy or sell signal.",
     "more": "Fast crossing above slow: possible start of an uptrend. Crossing below: possible downtrend."},
    {"slug": "rsi", "term": "RSI", "level": 4, "aliases": ["rsi14", "rsi 14", "relative strength index"],
     "short": "A 0–100 score of how strongly the price has moved recently. Above 70 = rose fast, below 30 = fell fast.",
     "more": "High RSI means 'may be due to cool off', not 'will fall'. In strong trends RSI can stay high for a long time."},
    {"slug": "overbought", "term": "Overbought / oversold", "level": 4, "aliases": ["oversold"],
     "short": "Overbought: risen so fast it may pause. Oversold: fallen so fast it may bounce.",
     "more": "Usually judged with RSI above 70 or below 30."},
    {"slug": "macd", "term": "MACD", "level": 4, "aliases": ["macd histogram", "macd_hist", "signal line"],
     "short": "Shows momentum: whether recent prices are speeding up or slowing down compared with the longer trend.",
     "more": "When the MACD bars are above zero the short-term average is above the long-term one, so momentum is upward."},
    {"slug": "momentum", "term": "Momentum", "level": 4, "aliases": [],
     "short": "How fast and how strongly the price is moving in one direction.",
     "more": "Strong momentum often continues for a while; fading momentum can warn of a turn."},
    {"slug": "bollinger", "term": "Bollinger Bands", "level": 4, "aliases": ["bb", "bb(20,2)", "bollinger band"],
     "short": "Two lines around a moving average that widen when the price is jumpy and narrow when it's calm.",
     "more": "A close above the upper band is unusually high for recent prices; below the lower band is unusually low."},
    {"slug": "volatility", "term": "Volatility", "level": 4, "aliases": ["volatile"],
     "short": "How much and how quickly the price swings.",
     "more": "High volatility means bigger possible gains and bigger possible losses."},
    {"slug": "confidence", "term": "Confidence", "level": 4, "aliases": ["sureness"],
     "short": "How strongly the signals agree with each other. It is not the chance of being right.",
     "more": "A confident call can still be wrong, which is why stop-losses matter."},
    {"slug": "strategy", "term": "Strategy", "level": 5, "aliases": ["strategies"],
     "short": "A fixed set of rules that decides when to buy and when to sell.",
     "more": "Rules remove guesswork and emotion, and they can be tested on past data."},
    {"slug": "trend-following", "term": "Trend following", "level": 5, "aliases": ["breakout"],
     "short": "Strategies that buy when a move up has started, hoping it continues.",
     "more": "The SMA cross, EMA+MACD and Bollinger breakout strategies are trend following."},
    {"slug": "mean-reversion", "term": "Mean reversion", "level": 5, "aliases": ["reversion"],
     "short": "Strategies that buy after a sharp fall, betting the price snaps back toward normal.",
     "more": "The RSI reversion strategy works this way. It suffers in strong downtrends."},
    {"slug": "backtest", "term": "Backtest", "level": 5, "aliases": ["backtesting", "backtester"],
     "short": "Running a strategy on past prices to see how it would have done.",
     "more": "Useful for ruling strategies out. A good backtest doesn't promise future results."},
    {"slug": "total-return", "term": "Total return", "level": 5, "aliases": ["return"],
     "short": "How much the account grew or shrank overall, as a percentage.",
     "more": "+10% on $10,000 means you ended with $11,000."},
    {"slug": "buy-and-hold", "term": "Buy & hold", "level": 5, "aliases": ["buy and hold", "buy & hold", "benchmark"],
     "short": "What you'd have made by buying at the start and doing nothing.",
     "more": "If a strategy can't beat simply holding, it isn't adding value."},
    {"slug": "alpha", "term": "Alpha", "level": 5, "aliases": [],
     "short": "How much better (or worse) a strategy did than buy & hold.",
     "more": "Positive alpha means the strategy beat just holding the coin."},
    {"slug": "win-rate", "term": "Win rate", "level": 5, "aliases": [],
     "short": "The share of trades that made money.",
     "more": "A 40% win rate can still be profitable if the wins are much bigger than the losses."},
    {"slug": "profit-factor", "term": "Profit factor", "level": 5, "aliases": [],
     "short": "Total money won divided by total money lost. Above 1 means profitable overall.",
     "more": "1.5 means every $1 lost was matched by $1.50 won."},
    {"slug": "sharpe", "term": "Sharpe ratio", "level": 5, "aliases": ["sharpe"],
     "short": "Return compared with how bumpy the ride was. Higher is smoother.",
     "more": "Roughly: below 0 is bad, around 1 is decent, above 2 is very good (and suspicious in a backtest)."},
    {"slug": "overfitting", "term": "Overfitting", "level": 5, "aliases": ["curve fitting"],
     "short": "Tweaking a strategy until it fits past data perfectly, so it fails on new data.",
     "more": "The more settings you tune, the more likely a great backtest is just luck."},
    {"slug": "auto-trading", "term": "Auto-trading", "level": 6, "aliases": ["auto trading", "auto"],
     "short": "Letting the app place trades for you whenever your strategy gives a signal.",
     "more": "Your stop-loss, position size and daily loss limit still apply to every automatic trade."},
    {"slug": "ai-filter", "term": "AI filter", "level": 6, "aliases": [],
     "short": "Auto-trading only takes a buy signal if the AI analyst also likes the coin.",
     "more": "Fewer trades, hopefully better ones. The AI can be wrong too."},
    {"slug": "trading-plan", "term": "Trading plan", "level": 6, "aliases": ["plan"],
     "short": "Your written rules: what you trade, when you buy, when you sell and how much you risk.",
     "more": "Decide before you trade, then follow it. Review it weekly, not mid-trade."},
    {"slug": "fomo", "term": "FOMO", "level": 6, "aliases": ["fear of missing out"],
     "short": "Fear of missing out: buying because a price is shooting up and you feel left behind.",
     "more": "FOMO buys usually come late, near the top."},
    {"slug": "revenge-trading", "term": "Revenge trading", "level": 6, "aliases": [],
     "short": "Rushing into new trades to win back a loss.",
     "more": "It usually leads to bigger losses. The daily loss limit exists partly to stop it."},
]
GLOSSARY_BY_SLUG = {g["slug"]: g for g in GLOSSARY}


# --------------------------------------------------------------------------------------------- curriculum
def _q(question: str, choices: list[str], answer: int, why: str, hint: str = "") -> dict:
    return {"q": question, "choices": choices, "answer": answer, "why": why, "hint": hint}


def _ch(cid: str, title: str, how: str, event: str, times: int = 1, *, distinct: bool = False,
        bonus: bool = False) -> dict:
    """A practice challenge. Done when `event` has happened `times` times (or with `times` distinct values)."""
    return {"id": cid, "title": title, "how": how, "event": event, "times": times, "distinct": distinct, "bonus": bonus}


LEVELS: list[dict[str, Any]] = [
    {
        "n": 1, "title": "Trading basics",
        "summary": "What trading is, how prices move, and what the numbers in your account mean.",
        "unlocks": ["Practice: buy and sell real coins with $100,000 of pretend money"],
        "lessons": [
            {
                "id": "l1-what-is-trading", "title": "What is trading?", "minutes": 2,
                "cards": [
                    {"title": "Buy, then sell",
                     "body": "Trading means buying something and selling it later. If the [[price]] went up in between, "
                             "you make money. If it went down, you lose money.\n\nIn this app you trade [[crypto]] coins "
                             "such as Bitcoin, Ethereum and Solana."},
                    {"title": "The price moves all the time", "visual": "live-price",
                     "body": "A coin's price is simply the last price a buyer and a seller agreed on. New deals happen "
                             "every second, so the price is always moving.\n\nThis is the real Bitcoin price right now:"},
                    {"title": "Practice money", "visual": "account",
                     "body": "You have $100,000 of pretend money. That's called [[paper-trading|paper trading]]: real "
                             "prices, fake money.\n\nMistakes cost nothing here, which makes this the place to make them."},
                    {"title": "Nobody knows the future",
                     "body": "Even professionals are wrong a lot of the time. Good traders aren't right every time. "
                             "When they're wrong they lose a little, and when they're right they win more.\n\n"
                             "That one idea is what this whole course builds on."},
                ],
                "takeaway": "You make money when you sell for more than you paid. Nobody wins every trade, so the goal "
                            "is small losses and bigger wins.",
                "quiz": [
                    _q("You buy a coin for $100 and later sell it for $110. What happened?",
                       ["You made $10", "You lost $10", "Nothing, because it's the same coin"], 0,
                       "You sold for $10 more than you paid, so you made $10 (before fees, which you'll meet next lesson)."),
                    _q("What sets a coin's price?",
                       ["The last price a buyer and a seller agreed on", "The government", "This app"], 0,
                       "Every deal between a buyer and a seller sets a new price. That's why it moves all day."),
                    _q("What do good traders have in common?",
                       ["They're never wrong", "They keep losses small and let wins be bigger",
                        "They trade as often as possible"], 1,
                       "Being wrong is normal. Keeping losses smaller than wins is what makes money over time."),
                ],
            },
            {
                "id": "l1-your-account", "title": "Cash, coins and profit", "minutes": 3,
                "cards": [
                    {"title": "Cash and positions",
                     "body": "Your account holds two things: [[cash]] (money not in any coin yet) and "
                             "[[position|positions]] (coins you own).\n\nBuying turns cash into a position. Selling "
                             "turns it back into cash."},
                    {"title": "Equity: what it's all worth", "visual": "account",
                     "body": "[[equity|Equity]] is your cash plus the current value of your coins. It moves whenever "
                             "prices move, even if you don't do anything.\n\nEquity is the number that tells you how "
                             "you're really doing."},
                    {"title": "Profit you have vs profit you've banked",
                     "body": "While you still own a coin, any profit or loss is [[unrealized|unrealized]]. It can "
                             "change, or disappear completely.\n\nWhen you sell, it becomes [[realized|realized]]: "
                             "locked in for good. Together they're your [[pnl|P&L]] (profit and loss)."},
                    {"title": "The cost of trading",
                     "body": "Every buy and every sell has a [[fees|fee]] of 0.1%. A $1,000 buy costs $1, and selling "
                             "it costs about $1 more.\n\nYou also get a slightly worse price than you see, called "
                             "[[slippage]]. Small amounts, but they add up if you trade a lot."},
                ],
                "takeaway": "Equity = cash + coins. Profit isn't yours until you sell. Fees make every trade start "
                            "slightly behind.",
                "quiz": [
                    _q("You have $50,000 cash and own $30,000 of Bitcoin. What is your equity?",
                       ["$30,000", "$50,000", "$80,000"], 2,
                       "Equity is cash plus the value of your coins: $50,000 + $30,000 = $80,000."),
                    _q("Your coin is up $200 but you haven't sold. That $200 is:",
                       ["Realized profit, it's yours", "Unrealized profit, it could still change",
                        "Not profit at all"], 1,
                       "Until you sell, the profit is unrealized. If the price drops, it can shrink or vanish."),
                    _q("You buy $1,000 of a coin and sell it at exactly the same price. You now have:",
                       ["Exactly $1,000", "A little less than $1,000", "A little more than $1,000"], 1,
                       "You paid a fee on the buy and on the sell (plus a little slippage), so you end slightly behind."),
                ],
            },
        ],
        "challenges": [
            _ch("first-buy", "Make your first buy",
                "Go to Practice and press a Buy button on any coin. Pick one the app suggests, or just pick one.",
                "manual_buy"),
            _ch("first-sell", "Sell a coin you own",
                "On Practice, press 'Sell my …' on a coin you own. It's fine if you're down; this is practice.",
                "manual_sell"),
            _ch("first-win", "Close a trade in profit", "Sell a coin for more than you paid, after fees.",
                "win", bonus=True),
        ],
    },
    {
        "n": 2, "title": "Protecting your money",
        "summary": "Stop-losses, take-profits and position sizing: the habits that keep losses small.",
        "unlocks": ["Safety net settings on Practice", "Changing the stop-loss and take-profit on coins you own"],
        "lessons": [
            {
                "id": "l2-stops", "title": "Stop-loss and take-profit", "minutes": 3,
                "cards": [
                    {"title": "Your automatic exit if things go wrong",
                     "body": "A [[stop-loss]] sells a coin for you if its price falls to a level you picked.\n\n"
                             "Buy at $100 with a stop-loss at $98, and the most you should lose is about $2 per coin. "
                             "In a very fast drop it can sell a little lower, so treat it as 'about'."},
                    {"title": "Your automatic exit if things go right",
                     "body": "A [[take-profit]] sells for you when the price rises to your target, locking in the gain "
                             "before it can slip away."},
                    {"title": "Together: a bracket", "visual": "bracket",
                     "body": "Set both and you have a [[bracket]]. The trade ends at whichever the price reaches first.\n\n"
                             "This app adds one to every buy automatically: by default a 2% stop-loss and a 4% "
                             "take-profit."},
                    {"title": "Decide before, not during",
                     "body": "When a trade is going against you, fear and hope take over. People hold losers too long "
                             "('it'll come back') and sell winners too early.\n\nSetting your exits before you buy "
                             "means a calm you makes the decision, not a stressed you."},
                ],
                "takeaway": "Every trade needs an exit plan. A stop-loss caps the loss and a take-profit banks the "
                            "gain, both decided before you buy.",
                "quiz": [
                    _q("What does a stop-loss do?",
                       ["Stops the price from falling", "Sells automatically if the price falls to your level",
                        "Stops you from buying more"], 1,
                       "It can't stop the price; it gets you out when the price reaches the level you chose."),
                    _q("You buy at $200 with a 5% stop-loss. Roughly where does it sell?",
                       ["$190", "$195", "$210"], 0,
                       "5% of $200 is $10, so the stop sits at $190."),
                    _q("Why set your stop-loss before you buy?",
                       ["It's required by law", "So fear and hope don't make the decision for you",
                        "It makes the trade cheaper"], 1,
                       "Mid-trade emotions are strong. A plan made in advance is usually a better plan."),
                ],
            },
            {
                "id": "l2-sizing", "title": "How much to put in each trade", "minutes": 3,
                "cards": [
                    {"title": "Position size",
                     "body": "[[position-size|Position size]] is how much money goes into one trade. This app's default "
                             "is up to 20% of your account per trade."},
                    {"title": "What you actually risk",
                     "body": "Your real risk is the position size × the distance to your stop-loss.\n\n"
                             "$20,000 in a trade with a 2% stop-loss risks about $400. That's 0.4% of a $100,000 "
                             "account. Many traders keep this at 1–2% of their account or less."},
                    {"title": "Don't put all your eggs in one basket",
                     "body": "Holding a few smaller positions instead of one big one is [[diversification]]. The "
                             "'max open positions' setting limits how many you hold at once."},
                    {"title": "Win more than you lose",
                     "body": "The default 2% stop and 4% target give a [[risk-reward|risk-reward ratio]] of 1:2: risk $1 "
                             "to make $2.\n\nWith 1:2 you can be wrong two times out of three and still roughly break "
                             "even (before fees). That's why the size of wins vs losses matters more than how often "
                             "you're right."},
                ],
                "takeaway": "Risk = position size × stop distance. Keep it small, spread it out, and aim for wins "
                            "bigger than losses.",
                "quiz": [
                    _q("You put $10,000 into a trade with a 5% stop-loss. About how much are you risking?",
                       ["$50", "$500", "$5,000"], 1,
                       "5% of $10,000 is $500. That's roughly what you'd lose if the stop is hit."),
                    _q("Which trade has the better risk-reward?",
                       ["Risk $200 to make $100", "Risk $100 to make $100", "Risk $100 to make $200"], 2,
                       "Risking $100 to make $200 is 1:2: the win is twice the size of the loss."),
                    _q("Why not put your whole account into one trade?",
                       ["One bad trade could wipe out a big chunk of your money", "The app won't let you",
                        "It makes fees higher"], 0,
                       "Big positions mean big losses when you're wrong, and everyone is wrong sometimes."),
                ],
            },
            {
                "id": "l2-drawdowns", "title": "Losing streaks", "minutes": 2,
                "cards": [
                    {"title": "Losses are harder to undo than you think",
                     "body": "A fall from your account's high point is a [[drawdown]]. Getting back is harder than "
                             "falling: lose 10% and you need +11% to recover. Lose 50% and you need +100%."},
                    {"title": "Streaks happen",
                     "body": "Even a good approach can lose five times in a row by plain bad luck. Small position "
                             "sizes mean a streak hurts but doesn't end the game."},
                    {"title": "The daily brake",
                     "body": "This app has a [[daily-loss-limit|daily loss limit]]: if your account falls 5% in one day, "
                             "buying pauses until tomorrow.\n\nIt protects you from a bad day, and from "
                             "[[revenge-trading|revenge trading]] to win the money back."},
                ],
                "takeaway": "Big losses need much bigger gains to recover. Small sizes and a daily limit keep losing "
                            "streaks survivable.",
                "quiz": [
                    _q("Your account drops 50%. What gain do you need to get back to where you started?",
                       ["+50%", "+75%", "+100%"], 2,
                       "Half of your money has to double to get back: $50,000 needs +$50,000, which is +100%."),
                    _q("Buying has paused after a big losing day. What's the point?",
                       ["To stop a bad day turning into a disaster", "It's a bug", "To make you pay extra fees"], 0,
                       "The daily loss limit is a brake. It starts again tomorrow, when you're calmer."),
                ],
            },
        ],
        "challenges": [
            _ch("set-safety-net", "Save your safety net settings",
                "On Practice, open 'Your safety net', change a number (for example the stop-loss %) and press Save.",
                "risk"),
            _ch("edit-stop", "Move the stop-loss on a coin you own",
                "Own a coin, then on its card press 'Change my exits', type a new stop-loss price and save.",
                "brackets"),
            _ch("auto-exit", "Let a stop-loss or take-profit close a trade",
                "Leave a coin with exits set and let the price hit one of them. Patience is a skill.",
                "protect_exit", bonus=True),
        ],
    },
    {
        "n": 3, "title": "Reading charts",
        "summary": "Candlesticks, trends, support and resistance: seeing what the price has been doing.",
        "unlocks": ["The Dashboard: live candlestick charts and the order ticket"],
        "lessons": [
            {
                "id": "l3-candles", "title": "Candlesticks", "minutes": 3,
                "cards": [
                    {"title": "One candle, four prices", "visual": "candle",
                     "body": "Charts are made of [[candlestick|candlesticks]]. Each one covers a slice of time and "
                             "shows four prices: where it opened, the highest, the lowest, and where it closed."},
                    {"title": "Green and red",
                     "body": "Green: the price closed higher than it opened (it went up in that period). Red: it "
                             "closed lower.\n\nThe thick part is the body (open to close). The thin lines are "
                             "[[wick|wicks]]: how far the price went before coming back."},
                    {"title": "What wicks tell you",
                     "body": "A long upper wick means buyers pushed the price up, but sellers pushed it back down "
                             "before the period ended. A long lower wick means the opposite: sellers tried, buyers "
                             "won."},
                    {"title": "Timeframes",
                     "body": "Each candle covers a [[timeframe]]. The Dashboard uses 1-minute candles. The coin "
                             "suggestions on Practice use 1-hour candles.\n\nShort timeframes are noisy. Zoom out to "
                             "see the real direction."},
                ],
                "takeaway": "Each candle shows open, high, low and close for one period. Green went up, red went "
                            "down, and wicks show where the price was rejected.",
                "quiz": [
                    _q("A green candle means:",
                       ["The price closed higher than it opened", "The price will go up next",
                        "Lots of people bought"], 0,
                       "Green only describes what happened in that period: it closed above where it opened."),
                    _q("A candle has a long wick on top. What happened?",
                       ["The price went up and stayed there", "The price went up but got pushed back down",
                        "Nothing traded"], 1,
                       "The wick shows the price got up there, but couldn't hold it by the close."),
                    _q("On a 1-hour chart, each candle covers:",
                       ["1 minute", "1 hour", "1 day"], 1,
                       "The timeframe is how long each candle lasts."),
                ],
            },
            {
                "id": "l3-trends", "title": "Trends, support and resistance", "minutes": 3,
                "cards": [
                    {"title": "Which way is it going?", "visual": "trend",
                     "body": "A [[trend]] is the general direction. An uptrend makes higher highs and higher lows, "
                             "like climbing stairs. A downtrend makes lower highs and lower lows.\n\n"
                             "'The trend is your friend': buying in an uptrend is easier than catching a falling coin."},
                    {"title": "Floors and ceilings",
                     "body": "[[support|Support]] is a price where falls have stopped before, because buyers stepped "
                             "in: a floor. [[resistance|Resistance]] is where rises have stalled before: a ceiling.\n\n"
                             "When the price breaks clearly through one, it often keeps going."},
                    {"title": "Is anyone behind the move?",
                     "body": "[[volume|Volume]] (the bars at the bottom of the chart) shows how much was traded. A "
                             "breakout on high volume is more believable than one on low volume."},
                ],
                "takeaway": "Look for the trend first. Support and resistance show where the price has turned before, "
                            "and volume shows how much conviction is behind a move.",
                "quiz": [
                    _q("Higher highs and higher lows mean:",
                       ["An uptrend", "A downtrend", "No trend"], 0,
                       "Each peak and each dip is higher than the last: the price is climbing."),
                    _q("The price keeps bouncing up from about $95. $95 is:",
                       ["Resistance", "Support", "The stop-loss"], 1,
                       "A level where falls keep stopping is support: buyers keep stepping in there."),
                    _q("The price breaks above resistance. Which makes the breakout more believable?",
                       ["Very low volume", "High volume", "A red candle"], 1,
                       "High volume means lots of traders are behind the move."),
                ],
            },
        ],
        "challenges": [
            _ch("view-charts", "Look at the chart for two different coins",
                "Open Dashboard and click the coin tabs above the chart.", "chart", 2, distinct=True),
            _ch("use-ticket", "Place an order with the order ticket",
                "On Dashboard, use the Order ticket on the right. Leave quantity on Auto.", "ticket"),
        ],
    },
    {
        "n": 4, "title": "Indicators",
        "summary": "Moving averages, RSI, MACD and Bollinger Bands, and why one signal is never enough.",
        "unlocks": ["Indicator lines on the chart", "The AI analyst card"],
        "lessons": [
            {
                "id": "l4-moving-averages", "title": "Moving averages", "minutes": 3,
                "cards": [
                    {"title": "Smoothing out the noise",
                     "body": "A [[sma|moving average]] is the average closing price of the last N candles, drawn as a "
                             "line. It smooths out the jumps so you can see the trend.\n\nSMA20 uses the last 20 candles "
                             "(short term). SMA50 uses 50 (longer term)."},
                    {"title": "Above or below?",
                     "body": "Price above its moving average suggests an uptrend. Below suggests a downtrend. The "
                             "line often acts like moving [[support]] or [[resistance]]."},
                    {"title": "Crossovers",
                     "body": "When the fast average (SMA20) crosses above the slow one (SMA50), it's a "
                             "[[crossover]] that many traders read as 'a new uptrend may be starting'. Crossing "
                             "below is the opposite.\n\nThis is exactly what the SMA cross strategy trades."},
                    {"title": "The catch: they lag",
                     "body": "Averages are built from the past, so they always react late. An [[ema|EMA]] weights recent "
                             "prices more and reacts faster, but it gives more false alarms."},
                ],
                "takeaway": "Moving averages show the trend. Price above = up, fast crossing slow = possible new "
                            "trend. They're always a little late.",
                "quiz": [
                    _q("What does SMA20 show?",
                       ["The average price of the last 20 candles", "The price in 20 minutes", "The 20 biggest trades"], 0,
                       "It's an average of recent closing prices, not a prediction."),
                    _q("SMA20 crosses above SMA50. Many traders read this as:",
                       ["A possible new uptrend", "A guaranteed profit", "A reason to sell"], 0,
                       "A fast average rising above a slow one suggests the recent trend is turning up. Possible, never certain."),
                    _q("Why do moving average signals often come late?",
                       ["They're calculated from past prices", "The app is slow", "They only update once a day"], 0,
                       "Any average of the past trails behind the price."),
                ],
            },
            {
                "id": "l4-momentum", "title": "RSI and MACD", "minutes": 3,
                "cards": [
                    {"title": "RSI: has it moved too fast?",
                     "body": "[[rsi|RSI]] scores recent price moves from 0 to 100. Above 70 the price has risen fast "
                             "([[overbought]]) and may be due to cool off. Below 30 it has fallen fast (oversold) and "
                             "may bounce."},
                    {"title": "Overbought isn't a sell button",
                     "body": "In a strong uptrend RSI can stay above 70 for a long time while the price keeps rising. "
                             "RSI tells you how fast it moved, not what happens next."},
                    {"title": "MACD: speeding up or slowing down?",
                     "body": "[[macd|MACD]] compares a fast average with a slow one to measure [[momentum]]. When its "
                             "bars are above zero, momentum is upward. When they shrink toward zero, the move is "
                             "losing steam."},
                ],
                "takeaway": "RSI shows whether a move has been unusually fast. MACD shows whether momentum is building "
                            "or fading. Neither predicts the future.",
                "quiz": [
                    _q("RSI is 78. What does that mean?",
                       ["The price has risen fast and may cool off", "The price will definitely fall",
                        "78% of traders are buying"], 0,
                       "Above 70 means a fast rise. It may pause, or in a strong trend it may keep going."),
                    _q("RSI has been above 70 for days and the price keeps rising. This is:",
                       ["Impossible", "Normal in a strong uptrend", "A sign the app is broken"], 1,
                       "Strong trends can stay 'overbought' for a long time."),
                    _q("The MACD bars are above zero and growing. Momentum is:",
                       ["Upward and strengthening", "Downward", "Gone"], 0,
                       "Bars above zero mean upward momentum; growing bars mean it's getting stronger."),
                ],
            },
            {
                "id": "l4-confirm", "title": "Bollinger Bands and confirming signals", "minutes": 2,
                "cards": [
                    {"title": "Bands that breathe",
                     "body": "[[bollinger|Bollinger Bands]] are two lines around a moving average. They widen when the "
                             "price gets jumpy ([[volatility|volatile]]) and squeeze together when it's calm. A close "
                             "outside the bands is unusual for recent prices."},
                    {"title": "Never trust one signal",
                     "body": "Every indicator gives false alarms. Traders look for several pointing the same way: "
                             "trend up, price above its averages, MACD positive, RSI not extreme.\n\nThat's what the "
                             "AI analyst does. It checks them together and tells you how much they agree "
                             "([[confidence]])."},
                ],
                "takeaway": "Bollinger Bands show how jumpy the price is. The best signals are several indicators "
                            "agreeing, not one shouting.",
                "quiz": [
                    _q("The Bollinger Bands suddenly widen. What's happening?",
                       ["The price is getting more jumpy", "The price is frozen", "Volume is zero"], 0,
                       "Wider bands mean bigger recent swings: higher volatility."),
                    _q("Which is the stronger buy setup?",
                       ["RSI is low", "Uptrend, price above its averages and MACD positive",
                        "One green candle"], 1,
                       "Several signals agreeing beats any single one."),
                ],
            },
        ],
        "challenges": [
            _ch("indicator-on", "Turn on an indicator",
                "On Dashboard, tick SMA20, SMA50 or BB above the chart, or switch the lower panel between RSI and MACD.",
                "indicator"),
            _ch("ask-ai", "Ask the AI analyst for its view",
                "On Dashboard, press 'Analyze now' on the AI analysis card and read why it says what it says.",
                "analyze"),
        ],
    },
    {
        "n": 5, "title": "Strategies and backtesting",
        "summary": "Turning ideas into rules, testing them on past prices, and spotting results that are too good to be true.",
        "unlocks": ["Strategy settings for each coin", "The backtester"],
        "lessons": [
            {
                "id": "l5-strategies", "title": "What a strategy is", "minutes": 3,
                "cards": [
                    {"title": "Rules instead of gut feel",
                     "body": "A [[strategy]] is a fixed set of rules for when to buy and when to sell. Rules can be "
                             "tested, they don't get scared, and they don't get greedy."},
                    {"title": "Two families",
                     "body": "[[trend-following|Trend-following]] strategies buy after a move up has started and ride it. "
                             "[[mean-reversion|Mean-reversion]] strategies buy after a sharp drop, betting on a bounce.\n\n"
                             "Trend followers do well in trends and badly in choppy markets. Mean reversion is the "
                             "opposite."},
                    {"title": "The four in this app",
                     "body": "SMA cross: buy when SMA20 crosses above SMA50.\nEMA + MACD: buy when MACD turns up in "
                             "an uptrend.\nRSI reversion: buy when RSI is very low, sell when it's high.\nBollinger "
                             "breakout: buy when the price closes above the upper band."},
                ],
                "takeaway": "A strategy is written rules. Trend following rides moves; mean reversion bets on bounces. "
                            "Each suits different market conditions.",
                "quiz": [
                    _q("What makes something a strategy rather than a guess?",
                       ["Fixed rules for buying and selling", "A feeling that the price will rise",
                        "Following a famous trader"], 0,
                       "Rules can be written down and tested. Feelings can't."),
                    _q("'Buy when RSI drops below 30' is which kind of strategy?",
                       ["Trend following", "Mean reversion", "Buy & hold"], 1,
                       "It buys after a sharp fall, betting the price returns toward normal."),
                    _q("The market is choppy, going sideways with no direction. Which usually struggles most?",
                       ["Trend following", "Mean reversion", "Neither"], 0,
                       "Trend followers keep buying breakouts that fail when there's no trend."),
                ],
            },
            {
                "id": "l5-backtests", "title": "Reading a backtest", "minutes": 4,
                "cards": [
                    {"title": "Testing on the past",
                     "body": "A [[backtest]] runs a strategy over past prices and records every trade it would have "
                             "made, fees included."},
                    {"title": "The first question: did it beat doing nothing?",
                     "body": "[[total-return|Total return]] is how much the account grew. Compare it with "
                             "[[buy-and-hold|buy & hold]]: buying at the start and never trading. If the strategy "
                             "can't beat that, it isn't helping. The difference is called [[alpha]]."},
                    {"title": "How painful was it?",
                     "body": "[[drawdown|Max drawdown]] is the worst fall from a high point along the way. A strategy "
                             "that made 20% but fell 40% first would be very hard to stick with in real life."},
                    {"title": "The other numbers",
                     "body": "[[win-rate|Win rate]]: share of winning trades. [[profit-factor|Profit factor]]: money won ÷ "
                             "money lost (above 1 is profitable). [[sharpe|Sharpe ratio]]: return compared with how "
                             "bumpy the ride was (higher is smoother).\n\nA low win rate is fine if wins are big; check "
                             "profit factor."},
                ],
                "takeaway": "Compare with buy & hold first, then check the worst drawdown. Win rate means little "
                            "without profit factor.",
                "quiz": [
                    _q("A strategy returned +8%. Buy & hold returned +15% over the same time. Is the strategy good?",
                       ["Yes, it made money", "No, just holding would have made more", "Can't tell"], 1,
                       "It made money, but less than doing nothing. That's negative alpha."),
                    _q("Win rate 35%, profit factor 1.8. What does that tell you?",
                       ["It loses money", "It loses often, but its wins are much bigger", "It's broken"], 1,
                       "Profit factor above 1 means it made money overall; the wins outweigh the many small losses."),
                    _q("What does max drawdown measure?",
                       ["The worst fall from a high point", "The biggest single win", "The number of trades"], 0,
                       "It shows the most painful stretch you'd have had to sit through."),
                ],
            },
            {
                "id": "l5-overfitting", "title": "Why backtests lie", "minutes": 2,
                "cards": [
                    {"title": "Too good to be true",
                     "body": "Tweak the settings enough and almost any strategy looks amazing on past data. That's "
                             "[[overfitting]]: you've fitted the rules to old noise, and new data won't have the same "
                             "noise."},
                    {"title": "How to stay honest",
                     "body": "Test on more than one coin and more than one timeframe. Prefer simple rules with few "
                             "settings. Be suspicious of results with only a handful of trades.\n\nAnd remember: past "
                             "results never guarantee future ones."},
                ],
                "takeaway": "A great backtest can be luck. Test widely, keep rules simple and distrust small samples.",
                "quiz": [
                    _q("You tweak 6 settings until a backtest shows +300%. The most likely problem is:",
                       ["Overfitting to past data", "Fees are too low", "Nothing, it's a great strategy"], 0,
                       "Lots of tuning finds settings that fit the past by luck. They usually fail going forward."),
                    _q("Which result is more trustworthy?",
                       ["+40% from 3 trades on one coin", "+12% from 80 trades across several coins", "They're the same"], 1,
                       "More trades across more markets is much less likely to be luck."),
                ],
            },
        ],
        "challenges": [
            _ch("run-backtest", "Run a backtest",
                "On Dashboard, open the Backtest tab at the bottom, pick a strategy and press 'Run backtest'.", "backtest"),
            _ch("compare-strategies", "Backtest two different strategies",
                "Run backtests with two different strategies on the same coin and compare them with buy & hold.",
                "backtest_strategy", 2, distinct=True),
            _ch("beat-hold", "Find a backtest that beats buy & hold",
                "Try different strategies, coins and timeframes. Then ask yourself if it's overfitted.",
                "beat_hold", bonus=True),
        ],
    },
    {
        "n": 6, "title": "Automation and your trading plan",
        "summary": "Letting a strategy trade for you, using the AI filter, and the habits that matter most.",
        "unlocks": ["Auto-trading", "The AI filter"],
        "lessons": [
            {
                "id": "l6-auto", "title": "Letting the robot trade", "minutes": 3,
                "cards": [
                    {"title": "Auto-trading",
                     "body": "With [[auto-trading]] on, the app checks each coin's strategy every time a 1-minute "
                             "candle closes. If the strategy says buy, it buys; if it says sell, it sells."},
                    {"title": "Your safety net still applies",
                     "body": "Every automatic trade still uses your [[position-size|position size]], "
                             "[[stop-loss]], [[take-profit]] and [[daily-loss-limit|daily loss limit]]. Automation "
                             "follows rules perfectly, including bad rules, so pick a strategy you've backtested."},
                    {"title": "The AI filter",
                     "body": "Turn on the [[ai-filter|AI filter]] and auto-trading only takes a buy signal when the AI "
                             "analyst also likes the coin. Fewer trades, ideally better ones. The AI can be wrong too, "
                             "so check what it does."},
                ],
                "takeaway": "Auto-trading follows your strategy and your safety net exactly. Choose and test the rules "
                            "first, then keep an eye on it.",
                "quiz": [
                    _q("Auto-trading is on and the strategy signals a buy. What limits how much it spends?",
                       ["Nothing", "Your position size setting", "The AI"], 1,
                       "Every automatic trade is sized by your position-size setting and protected by your stop-loss."),
                    _q("What does the AI filter do?",
                       ["Only takes buy signals the AI also agrees with", "Guarantees profits", "Turns off stop-losses"], 0,
                       "It's a second opinion on buy signals, not a guarantee."),
                    _q("Before turning on auto-trading, you should:",
                       ["Backtest the strategy you're automating", "Raise your position size", "Turn off the daily loss limit"], 0,
                       "Automation repeats your rules perfectly, so make sure the rules are worth repeating."),
                ],
            },
            {
                "id": "l6-plan", "title": "Your plan and your head", "minutes": 3,
                "cards": [
                    {"title": "Write a trading plan",
                     "body": "A [[trading-plan|trading plan]] answers four questions before you trade: What do I trade? "
                             "When do I buy? When do I sell? How much do I risk?\n\nIf you can't answer all four, "
                             "don't place the trade."},
                    {"title": "Review every trade",
                     "body": "Look back at each trade: did you follow the plan? Was the loss a planned one? The "
                             "'Explain' button on your trades helps with exactly this."},
                    {"title": "Know your enemies",
                     "body": "[[fomo|FOMO]]: buying because a price is flying and you feel left out. "
                             "[[revenge-trading|Revenge trading]]: rushing in to win back a loss. Both feel urgent, "
                             "and both usually cost money."},
                    {"title": "Practice is not real",
                     "body": "Real trading has real emotions, and fills can be worse than here. If you ever trade "
                             "real money, start tiny and only use money you can afford to lose completely."},
                ],
                "takeaway": "Plan before you trade, review after, and watch for FOMO and revenge trading. Real money "
                            "feels very different from practice.",
                "quiz": [
                    _q("Bitcoin just jumped 8% in an hour and you feel you must buy now. This is most likely:",
                       ["A great opportunity", "FOMO", "A trading plan"], 1,
                       "Feeling you'll miss out is FOMO. Check your plan before acting."),
                    _q("You just lost $1,000 and want to make a big trade to win it back. You should:",
                       ["Do it quickly", "Stop: that's revenge trading", "Double your position size"], 1,
                       "Trying to win losses back fast is revenge trading. Step away and follow your plan."),
                    _q("Moving from practice to real money, it's sensible to:",
                       ["Start very small with money you can afford to lose", "Use your savings", "Borrow to trade bigger"], 0,
                       "Real money feels different. Start small enough that a loss doesn't matter."),
                ],
            },
        ],
        "challenges": [
            _ch("auto-on", "Turn on auto-trading",
                "On Dashboard, flip the Auto-trading switch at the top. You can turn it off any time.", "auto_on"),
            _ch("explain-trade", "Review one of your trades",
                "On Practice, press 'Explain' next to a trade in 'Your recent trades'.", "explain"),
            _ch("auto-trade", "Let auto-trading make a trade",
                "Leave auto-trading on and wait for your strategy to signal.", "auto_trade", bonus=True),
        ],
    },
]

LESSONS: dict[str, dict] = {lesson["id"]: lesson for lvl in LEVELS for lesson in lvl["lessons"]}
LESSON_LEVEL: dict[str, int] = {lesson["id"]: lvl["n"] for lvl in LEVELS for lesson in lvl["lessons"]}
MAX_LEVEL = len(LEVELS)
TERM_RE = re.compile(r"\[\[([a-z0-9-]+)(?:\|([^\]]+))?\]\]")


def term_slugs(text: str) -> list[str]:
    return [m.group(1) for m in TERM_RE.finditer(text or "")]


# --------------------------------------------------------------------------------------------- progress
class Learning:
    """Tracks course progress in the kv table. All methods are synchronous and cheap."""

    KEY = "learn"

    def __init__(self, db: Database):
        self.db = db
        self.p = self._load()

    def _load(self) -> dict:
        raw = self.db.get_kv(self.KEY)
        p = raw if isinstance(raw, dict) else {}
        return {
            "answered": {k: sorted(set(v)) for k, v in (p.get("answered") or {}).items() if k in LESSONS},
            "counts": dict(p.get("counts") or {}),
            "sets": {k: list(v) for k, v in (p.get("sets") or {}).items()},
            "unlock_all": bool(p.get("unlock_all", False)),
            "started": p.get("started") or time.time(),
        }

    def _save(self) -> None:
        self.db.set_kv(self.KEY, self.p)

    # ---------------------------------------------------------------- status
    def lesson_done(self, lesson_id: str) -> bool:
        return len(self.p["answered"].get(lesson_id, [])) >= len(LESSONS[lesson_id]["quiz"])

    def challenge_progress(self, ch: dict) -> int:
        if ch["distinct"]:
            return len(set(self.p["sets"].get(ch["event"], [])))
        return int(self.p["counts"].get(ch["event"], 0))

    def challenge_done(self, ch: dict) -> bool:
        return self.challenge_progress(ch) >= ch["times"]

    def level_complete(self, lvl: dict) -> bool:
        return all(self.lesson_done(lesson["id"]) for lesson in lvl["lessons"]) and all(
            self.challenge_done(ch) for ch in lvl["challenges"] if not ch["bonus"])

    def finished(self) -> bool:
        return all(self.level_complete(lvl) for lvl in LEVELS)

    def level(self) -> int:
        """The highest unlocked level: the first incomplete one (or the last when everything is done)."""
        if self.p["unlock_all"]:
            return MAX_LEVEL
        for lvl in LEVELS:
            if not self.level_complete(lvl):
                return lvl["n"]
        return MAX_LEVEL

    def summary(self) -> dict:
        current = self.level()
        levels = []
        total = done = 0
        for lvl in LEVELS:
            complete = self.level_complete(lvl)
            status = "done" if complete else ("current" if lvl["n"] <= current else "locked")
            lessons = [{"id": l["id"], "title": l["title"], "minutes": l["minutes"], "done": self.lesson_done(l["id"])}
                       for l in lvl["lessons"]]
            challenges = [{"id": c["id"], "title": c["title"], "how": c["how"], "bonus": c["bonus"],
                           "done": self.challenge_done(c), "progress": min(self.challenge_progress(c), c["times"]),
                           "times": c["times"]} for c in lvl["challenges"]]
            for item in lessons + [c for c in challenges if not c["bonus"]]:
                total += 1
                done += bool(item["done"])
            levels.append({"n": lvl["n"], "title": lvl["title"], "summary": lvl["summary"], "unlocks": lvl["unlocks"],
                           "status": status, "lessons": lessons, "challenges": challenges})
        return {"level": current, "max_level": MAX_LEVEL, "finished": self.finished(),
                "unlock_all": self.p["unlock_all"], "percent": round(100 * done / total) if total else 100,
                "levels": levels}

    def lesson(self, lesson_id: str) -> dict:
        if lesson_id not in LESSONS:
            raise KeyError(f"Unknown lesson '{lesson_id}'")
        lesson = LESSONS[lesson_id]
        n = LESSON_LEVEL[lesson_id]
        return {
            "id": lesson_id, "level": n, "level_title": LEVELS[n - 1]["title"], "title": lesson["title"],
            "minutes": lesson["minutes"], "cards": lesson["cards"], "takeaway": lesson["takeaway"],
            "locked": n > self.level(), "done": self.lesson_done(lesson_id),
            "answered": self.p["answered"].get(lesson_id, []),
            "quiz": [{"q": q["q"], "choices": q["choices"]} for q in lesson["quiz"]],
        }

    # ---------------------------------------------------------------- actions
    def answer(self, lesson_id: str, question: int, choice: int) -> dict:
        if lesson_id not in LESSONS:
            raise KeyError(f"Unknown lesson '{lesson_id}'")
        n = LESSON_LEVEL[lesson_id]
        before = self.level()
        if n > before:
            raise ValueError(f"Finish level {before} first to unlock this lesson.")
        quiz = LESSONS[lesson_id]["quiz"]
        if not 0 <= question < len(quiz):
            raise ValueError("That question doesn't exist.")
        q = quiz[question]
        if not 0 <= choice < len(q["choices"]):
            raise ValueError("That answer doesn't exist.")
        correct = choice == q["answer"]
        if correct:
            got = set(self.p["answered"].get(lesson_id, []))
            got.add(question)
            self.p["answered"][lesson_id] = sorted(got)
            self._save()
        after = self.level()
        return {"correct": correct, "why": q["why"] if correct else "", "hint": "" if correct else q.get("hint", ""),
                "lesson_done": self.lesson_done(lesson_id), "level": after, "leveled_up": after > before}

    def record(self, event: str, value: Any = None) -> None:
        """Count an action. With a value, also remember it in a set (for 'distinct' challenges)."""
        self.p["counts"][event] = int(self.p["counts"].get(event, 0)) + 1
        if value is not None:
            values = self.p["sets"].setdefault(event, [])
            if value not in values:
                values.append(value)
        self._save()

    def record_client(self, event: str, value: Any = None) -> dict:
        if event not in CLIENT_EVENTS:
            raise ValueError(f"Unknown learning event '{event}'")
        before = self.level()
        self.record(event, str(value)[:40] if value is not None else None)
        return {"level": self.level(), "leveled_up": self.level() > before}

    def record_fill(self, fill: dict) -> None:
        side, reason = fill.get("side"), fill.get("reason")
        if side == "buy":
            self.record("buy")
            if reason == "manual":
                self.record("manual_buy")
        elif side == "sell":
            self.record("sell")
            if reason == "manual":
                self.record("manual_sell")
            if (fill.get("pnl") or 0) > 0:
                self.record("win")
            if reason in PROTECTIVE_EXITS:
                self.record("protect_exit")
        if reason == "signal":
            self.record("auto_trade")

    def record_backtest(self, strategy: str, metrics: dict) -> None:
        self.record("backtest")
        self.record("backtest_strategy", strategy)
        ret, hold = metrics.get("total_return_pct"), metrics.get("buy_hold_return_pct")
        if isinstance(ret, (int, float)) and isinstance(hold, (int, float)) and metrics.get("num_trades") and ret > hold:
            self.record("beat_hold")

    def set_unlock_all(self, on: bool) -> dict:
        self.p["unlock_all"] = bool(on)
        self._save()
        return self.summary()

    def reset(self) -> dict:
        self.p = {"answered": {}, "counts": {}, "sets": {}, "unlock_all": False, "started": time.time()}
        self._save()
        return self.summary()


# --------------------------------------------------------------------------------------------- trade explanations
def _money(x: float) -> str:
    v = abs(x)
    return f"${v:,.2f}" if v < 100 else f"${v:,.0f}"


def _held(seconds: float) -> str:
    minutes = seconds / 60
    if minutes < 1:
        return "less than a minute"
    if minutes < 90:
        return f"{minutes:.0f} minute{'s' if round(minutes) != 1 else ''}"
    hours = minutes / 60
    if hours < 36:
        return f"{hours:.1f} hours"
    return f"{hours / 24:.1f} days"


def trade_facts(trade: dict, entry: dict | None, coin: str) -> dict:
    """Plain numbers about one trade (and, for a sell, the buy it closed) used for explanations."""
    facts: dict[str, Any] = {
        "coin": coin, "side": trade["side"], "reason": trade.get("reason") or "manual",
        "strategy": trade.get("strategy"), "qty": trade["qty"], "price": trade["price"], "fee": trade["fee"],
        "value": trade["qty"] * trade["price"],
    }
    if trade["side"] == "sell" and entry:
        cost = entry["price"] * trade["qty"]
        gross = (trade["price"] - entry["price"]) * trade["qty"]
        entry_fee = entry["fee"] * (trade["qty"] / entry["qty"]) if entry["qty"] else entry["fee"]
        pnl = trade["pnl"] if trade.get("pnl") is not None else gross - entry_fee - trade["fee"]
        facts.update({
            "entry_price": entry["price"], "exit_price": trade["price"], "cost": cost, "gross": gross,
            "fees_total": entry_fee + trade["fee"], "pnl": pnl, "pnl_pct": 100 * pnl / cost if cost else 0.0,
            "move_pct": 100 * (trade["price"] / entry["price"] - 1) if entry["price"] else 0.0,
            "held_seconds": max(0.0, trade["ts"] - entry["ts"]),
        })
    elif trade["side"] == "sell" and trade.get("pnl") is not None:
        facts.update({"pnl": trade["pnl"]})
    return facts


def rule_explanation(f: dict) -> dict:
    """Deterministic beginner explanation. Always accurate on the numbers; used as the AI fallback."""
    coin = f["coin"]
    reason = f["reason"]
    strat = (f.get("strategy") or "your").replace("_", " ")
    if f["side"] == "buy":
        how = "by hand" if reason == "manual" else f"automatically, because the {strat} strategy gave a buy signal"
        return {
            "headline": f"You bought {_money(f['value'])} of {coin}",
            "what": f"You bought {f['qty']:.6g} {coin} at {_money(f['price'])} {how}. The fee was {_money(f['fee'])}.",
            "why": "A buy is only half a trade. Until you sell, any profit or loss is unrealized and can still change. "
                   "Your stop-loss and take-profit decide where it ends if you don't step in.",
            "lesson": "Before every buy, know where you'll get out if you're wrong (stop-loss) and if you're right "
                      "(take-profit).",
            "lesson_id": "l2-stops",
        }
    pnl = f.get("pnl")
    if pnl is None or "entry_price" not in f:
        return {
            "headline": f"You sold {coin}" + (f" for a {'gain' if (pnl or 0) >= 0 else 'loss'} of {_money(pnl)}" if pnl is not None else ""),
            "what": f"You sold {f['qty']:.6g} {coin} at {_money(f['price'])}.",
            "why": "The matching buy for this sale isn't in your history any more, so the full story isn't available.",
            "lesson": "Reviewing each trade soon after it closes is the quickest way to improve.",
            "lesson_id": "l6-plan",
        }
    won = pnl > 0
    headline = f"You {'made' if won else 'lost'} {_money(pnl)} ({f['pnl_pct']:+.2f}%) on {coin}"
    direction = "rose" if f["move_pct"] >= 0 else "fell"
    what = (f"You bought at {_money(f['entry_price'])} and sold at {_money(f['exit_price'])} after "
            f"{_held(f['held_seconds'])}. The price {direction} {abs(f['move_pct']):.2f}% in that time.")
    fee_note = f" Fees for buying and selling came to {_money(f['fees_total'])}"
    if f["gross"] > 0 >= pnl:
        fee_note += ", which turned a small price gain into a loss."
    else:
        fee_note += "."
    if reason == "stop_loss":
        why = ("The price fell to your stop-loss, so it sold automatically. That's the safety net working: the loss "
               "stayed small instead of growing.")
        lesson, lid = ("Small, planned losses are normal. What matters is that your wins end up bigger than your "
                       "losses."), "l2-sizing"
    elif reason == "trailing_stop":
        why = ("The price moved, then dropped back by your trailing-stop distance, so it sold to protect what you "
               "had." if won else "The price dropped by your trailing-stop distance, so it sold to limit the loss.")
        lesson, lid = "A trailing stop lets a winner run while protecting part of the gain.", "l2-stops"
    elif reason == "take_profit":
        why = "The price rose to your take-profit target, so it sold automatically and locked in the gain."
        lesson, lid = ("Taking profit at a planned level stops a winning trade from turning into a losing one while "
                       "you're not watching."), "l2-stops"
    elif reason == "signal":
        why = f"The {strat} strategy gave a sell signal, so auto-trading sold."
        lesson, lid = ("One trade says little about a strategy. Judge it over many trades, like a backtest does."
                       ), "l5-backtests"
    else:
        why = f"You sold by hand after the price {direction} from {_money(f['entry_price'])} to {_money(f['exit_price'])}."
        if won:
            lesson = ("Ask yourself: did you sell because of a plan or a feeling? Writing your reason down before you "
                      "trade makes you better over time.")
        else:
            lesson = ("Selling at a loss by hand is fine if your reason for buying no longer holds. If it was fear, a "
                      "stop-loss set in advance can decide for you.")
        lid = "l6-plan"
    return {"headline": headline, "what": what + fee_note, "why": why, "lesson": lesson, "lesson_id": lid}
