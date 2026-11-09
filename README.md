# Quant Path: zero to AI-powered quant trader

A local learning app that takes a complete beginner to quant trader who uses AI. You learn with short lessons and
quizzes, then practise with **pretend money and real prices**: US stocks and ETFs (Yahoo Finance) and crypto
(Binance). A Quant Lab measures and tests ideas the way a quant would, and a local LLM (Ollama) explains trades,
gives suggestions and coaches you.

> **Paper trading only.** No broker or exchange keys are used and no real orders are ever placed. Nothing here is
> financial advice. Backtest and paper results do not predict live performance. US stock prices can be delayed by up
> to 15 minutes.

## The course

The app opens on **Learn**. Each level unlocks the part of the app it teaches. Levels 1-7 are built; 8-13 are
shown as "coming soon".

| Level | Topic | Unlocks |
|---|---|---|
| 1 | How markets work: stocks, ETFs, crypto, bid/ask/spread, market vs limit orders, market hours, costs | Practice with $100k pretend money; limit orders |
| 2 | Risk first: stops, sizing by the dollars at risk (size = risk ÷ stop), the maths of losing | Safety net, size-by-risk buying, editing exits |
| 3 | Charts and classic signals: candles, trends, gaps and earnings, moving averages, RSI, MACD | Charts screen, indicators, AI analyst |
| 4 | Returns and volatility: compounding, CAGR, volatility, Sharpe, drawdowns, fat tails | Lab → Stats |
| 5 | How assets move together: correlation, beta and the market factor, diversification | Lab → Correlation, Mixer |
| 6 | Edge or luck? Randomness, random-trader baselines, sample size, data snooping | Lab → Luck test, best of 20 random strategies |
| 7 | Testing strategies properly: strategy types, costs, look-ahead/survivorship bias, overfitting, walk-forward | Lab → Backtest with costs and walk-forward test |
| 8 | Python for quants *(coming soon)* | Code Lab with an AI pair-programmer |
| 9 | Your strategy in code *(coming soon)* | Custom strategies |
| 10 | Portfolios and factors *(coming soon)* | Portfolio builder, factor breakdown |
| 11 | Machine-learning signals *(coming soon)* | ML Lab |
| 12 | LLMs as research analysts *(coming soon)* | Research agent (SEC filings, news) |
| 13 | Agents and the capstone *(coming soon)* | Auto-trading agent |

Each level has 2-4 short lessons with quizzes (choices are shuffled; the answer stays on the server) and practice
challenges that tick off automatically when you do them in the app. Every trading word is tappable for a
plain-English definition, and the **Glossary** lists them all. On Practice, **Explain** on any trade reviews what
happened and the lesson in it. Experienced users can "Unlock everything" at the bottom of the Learn page.

### Screens

- **Practice:** stocks/ETFs and crypto in two lists. Open one to get an AI suggestion, place a market or limit order,
  size a buy by the dollars you'd accept losing, and change the exits on what you own. While the US market is closed,
  practice market orders fill at the last price (with a note about what a real broker would do); limit orders wait
  for the open. US stocks and ETFs have no commission; crypto pays 0.1% a trade; everything pays a little slippage.
- **Charts:** live candles with indicators, order ticket, AI analyst, positions and logs.
- **Lab:** five years of daily data. Stats (volatility, Sharpe, drawdown, return histogram vs the bell curve),
  correlation heatmap with beta to SPY, a portfolio mixer, a luck test (your strategy vs 500 coin-flip traders that
  trade as often) plus "best of 20 random strategies", and a backtester with costs and a walk-forward test (tune on
  70%, test once on the unseen 30%).

### AI coach

**Ask coach** (bottom right, on every screen) opens a chat with a tutor that knows your level, your practice
account, your recent trades and the screen you're on (`backend/app/coach.py`).

- **Ask anything:** answers use only words you've been taught (new ones get explained), point to the lesson that
  covers the topic, and suggest follow-up questions. Asked "should I buy?", it walks you through the checks for
  your level instead of answering yes or no.
- **Review my week:** your closed trades, wins and losses, result after fees, and habits it spotted: fees bigger
  than the price moves, very short trades, buying again right after a loss, losses bigger than wins, a stop-loss
  that's switched off or too much in each trade. Each point links the lesson to re-read.
- **Facts first:** the numbers and habits come from fixed rules. The local LLM only rewords them; a reworded
  review whose numbers don't match, or that drops a section, is thrown away. Without Ollama the coach still
  defines glossary words, gives your next step and reviews your trades.
- API: `POST /api/coach/ask` `{message, history, view, symbol}`, `GET /api/coach/review?days=7`, `GET /api/coach/next`.

## Architecture

```
 Binance REST + WS     Yahoo Finance chart API    Ollama (local LLM, optional)
   (crypto)              (US stocks/ETFs, polled)            |
      |                        |                             v
   market.py             stocks.py (StockFeed,       +-------------+
   MarketData            US market hours)            |  engine.py  |<---- ai.py (AIAnalyst, rule fallback)
      +---- CombinedMarket ----+ ------------------> | TradingEngine|<---- coach.py, learn.py + curriculum.py
                                on_update/on_close   +------+------+
                                                 | uses
             strategies.py (signals) <-----------+-----------> broker.py (PaperBroker, RiskLimits)
                                                 |
                       db.py (SQLite: trades, equity, logs, analyses, kv state)
                                                 |
                                 hub.py (WebSocket fan-out)
                                                 |
   main.py (FastAPI): REST /api/*  +  WS /ws  +  static frontend/ at /
                                                 |
                                   Browser dashboard (frontend/, vanilla JS
                                   + TradingView lightweight-charts)

 assets.py: the tradable universe (names, kinds, fees)     prices.py: history for any asset (+ daily cache)
 quant.py: stats, correlation/beta, portfolio mix, luck test, best-of-N random strategies
 backtest.py: run_backtest, walk_forward (tune in-sample, test once out-of-sample)
```

Limit orders are held by the engine and fill when a price update reaches the limit (stocks only while the US market
is open). Signal flow: each **closed** candle -> daily-loss check -> strategy signal -> (if auto-trading on, and the AI filter
agrees when enabled) open/close a paper position. Every price tick checks stop-loss / take-profit / trailing stop.
All state (account, positions, engine config, risk limits) survives restarts via SQLite.

## Quick start

```bash
cd ~/ai/trading-app
./start.sh                 # creates .venv (uv, Python 3.12), installs deps, copies .env, starts on :8000
open http://127.0.0.1:8000
PORT=8001 ./start.sh       # another port (ComfyUI already uses 8188)
```

For AI analysis, run Ollama (`ollama serve`) with the model in `OLLAMA_MODEL` pulled. Without it the app still works
and uses a deterministic rule-based analysis.

Tests: `cd backend && ../.venv/bin/python -m pytest -q`

## Configuration (`.env`)

| Key | Default | Meaning |
|---|---|---|
| `SYMBOLS` | 13 US stocks/ETFs + `BTCUSDT,ETHUSDT,SOLUSDT` | Symbols to trade; crypto pairs end in `USDT`, everything else is a US ticker (see `assets.py`) |
| `INTERVAL` | `1m` | Candle interval for live trading |
| `HISTORY_BARS` | `500` | Bars loaded per symbol at startup (max 1000) |
| `BINANCE_REST` | `https://api.binance.com` | REST base URL |
| `BINANCE_WS` | `wss://stream.binance.com:9443` | WebSocket base URL |
| `STARTING_CASH` | `100000` | Paper account starting balance (USDT) |
| `FEE_RATE` | `0.001` | Crypto fee per fill (0.1%). US stocks/ETFs have no commission |
| `SLIPPAGE_BPS` | `2` | Simulated slippage in basis points |
| `MAX_POSITION_PCT` | `0.20` | Max position size as a fraction of equity |
| `MAX_OPEN_POSITIONS` | `3` | Max simultaneous positions |
| `STOP_LOSS_PCT` | `0.02` | Default stop-loss (0 = off) |
| `TAKE_PROFIT_PCT` | `0.04` | Default take-profit (0 = off) |
| `TRAILING_STOP_PCT` | `0` | Trailing stop (0 = off) |
| `DAILY_LOSS_LIMIT_PCT` | `0.05` | Halt trading after this daily loss (0 = off) |
| `OLLAMA_URL` | `http://localhost:11434` | Ollama server |
| `OLLAMA_MODEL` | `qwen3.6-moe-uncensored` | Model used for analysis |
| `OLLAMA_TIMEOUT` | `90` | Seconds before falling back to rules |
| `DB_PATH` | `data/trading.db` | SQLite file |
| `MARKET_DISABLED` | unset | `1` skips the Binance and Yahoo connections (tests/offline) |
| `PORT` | `8000` | Port used by `start.sh` |

Risk limits and engine settings set in the dashboard are stored in the database and override the `.env` defaults.

## API summary

Errors return HTTP 400 `{"detail": "..."}`.

| Method & path | Purpose |
|---|---|
| `GET /api/health` | `{ok, market_connected, ai_available}` |
| `GET /api/state` | Symbols, assets, interval, account, waiting orders, engine config, risk, status (incl. `us_market`), prices |
| `GET /api/assets` / `GET /api/market/status` | Asset list with kinds and fees / US market open?, next open/close |
| `GET /api/candles/{symbol}?limit=500` | Candles + indicator series + latest indicator snapshot |
| `GET /api/strategies` | Available strategies and default params |
| `GET /api/trades?limit=200` / `GET /api/logs?limit=200` | Newest first |
| `GET /api/equity?limit=2000` | Equity curve `[{time, value}]` |
| `GET /api/analyses?symbol=&limit=50` | Stored AI analyses, newest first |
| `PUT /api/engine` | Partial `{auto_trading, ai_filter, strategies: {SYM: {name, params}}}` |
| `PUT /api/risk` | Partial risk limits |
| `POST /api/orders` | `{symbol, side, qty?, type: market\|limit, limit_price?, risk_amount?}`. `risk_amount` sizes a buy so the stop-loss loses about that much |
| `GET /api/orders` / `DELETE /api/orders/{id}` | Waiting limit orders / cancel one |
| `POST /api/positions/{symbol}/close` | Close a position |
| `PUT /api/positions/{symbol}` | Set `{stop_loss, take_profit}` |
| `POST /api/account/reset` / `POST /api/account/resume` | Reset paper account (`{cash?}`) / lift a halt |
| `POST /api/backtest` | `{symbol, interval, bars<=5000, strategy, params, risk?, cash?, fee_pct?, slippage_bps?}` -> metrics, equity curve, trades, markers, candles, costs |
| `POST /api/backtest/walkforward` | Same body + `split` (default 0.7): best settings from a small grid on the tuning part, run once on the rest |
| `GET /api/lab/stats/{symbol}?years=5` | Daily return stats, drawdown, histogram vs bell curve, growth curve |
| `POST /api/lab/correlation?years=3` | `{symbols}` -> correlation matrix, beta to SPY, volatility |
| `POST /api/lab/mix?years=3` | `{weights: {SYM: w}}` -> daily-rebalanced portfolio vs each asset |
| `POST /api/lab/luck` / `POST /api/lab/many` | `{symbol, strategy, params, years, trials}` -> strategy vs random traders / best of 20 random strategies |
| `POST /api/ai/analyze/{symbol}` | Run an AI analysis now |
| `POST /api/advice/{symbol}` | One plain buy/sell/wait suggestion from hourly candles; powers the Simple view |
| `GET /api/learn` | Course progress: current level, built levels with lessons/challenges, and the roadmap |
| `GET /api/learn/glossary` | All glossary terms |
| `GET /api/learn/lessons/{id}` | Lesson cards + quiz questions (answers stay on the server) |
| `POST /api/learn/lessons/{id}/answer` | `{question, choice}` -> `{correct, why, lesson_done, level, leveled_up}` |
| `POST /api/learn/events` | `{name: chart\|indicator\|ticket, value?}` browser-only challenge actions |
| `POST /api/learn/unlock-all` / `POST /api/learn/reset` | `{on}` skip the gating / restart the course |
| `POST /api/trades/{id}/explain` | Beginner explanation of a trade `{headline, what, why, lesson, lesson_id, source}` |
| `WS /ws` | Messages `{type, data}`: `snapshot` on connect, then `tick`, `account`, `trade`, `log`, `analysis`, `engine`, `risk`, `status`; send `{"type":"ping"}` for `{"type":"pong"}` |

Strategies: `sma_cross`, `ema_macd`, `rsi_reversion`, `bollinger_breakout`, `momentum`.
