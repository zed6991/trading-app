# AI Crypto Paper Trader

A local web app that streams live Binance spot prices, runs rule-based strategies, filters them (optionally)
through a local LLM via Ollama, and executes **simulated** long-only trades against a paper account.
It includes a backtester, risk controls (position sizing, SL/TP, trailing stop, daily loss halt) and a live dashboard.

> **Paper trading only.** No exchange API keys are used and no real orders are ever placed. Nothing here is
> financial advice. Backtest and paper results do not predict live performance (real fills, liquidity, fees and
> latency differ).

## Architecture

```
 Binance REST + WS (public)             Ollama (local LLM, optional)
          |                                      |
          v                                      v
   +-------------+   on_update/on_close   +-------------+
   | market.py   |----------------------->|  engine.py  |<---- ai.py (AIAnalyst, rule fallback)
   | MarketData  |                        | TradingEngine|
   +-------------+                        +------+------+
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

 backtest.py: POST /api/backtest -> market.fetch_history -> run_backtest (in a worker thread)
```

Signal flow: each **closed** candle -> daily-loss check -> strategy signal -> (if auto-trading on, and the AI filter
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
| `SYMBOLS` | `BTCUSDT,ETHUSDT,SOLUSDT` | Binance spot symbols to stream and trade |
| `INTERVAL` | `1m` | Candle interval for live trading |
| `HISTORY_BARS` | `500` | Bars loaded per symbol at startup (max 1000) |
| `BINANCE_REST` | `https://api.binance.com` | REST base URL |
| `BINANCE_WS` | `wss://stream.binance.com:9443` | WebSocket base URL |
| `STARTING_CASH` | `100000` | Paper account starting balance (USDT) |
| `FEE_RATE` | `0.001` | Fee per fill (0.1%) |
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
| `MARKET_DISABLED` | unset | `1` skips the Binance connection (tests/offline) |
| `PORT` | `8000` | Port used by `start.sh` |

Risk limits and engine settings set in the dashboard are stored in the database and override the `.env` defaults.

## API summary

Errors return HTTP 400 `{"detail": "..."}`.

| Method & path | Purpose |
|---|---|
| `GET /api/health` | `{ok, market_connected, ai_available}` |
| `GET /api/state` | Symbols, interval, account, engine config, risk, status, prices |
| `GET /api/candles/{symbol}?limit=500` | Candles + indicator series + latest indicator snapshot |
| `GET /api/strategies` | Available strategies and default params |
| `GET /api/trades?limit=200` / `GET /api/logs?limit=200` | Newest first |
| `GET /api/equity?limit=2000` | Equity curve `[{time, value}]` |
| `GET /api/analyses?symbol=&limit=50` | Stored AI analyses, newest first |
| `PUT /api/engine` | Partial `{auto_trading, ai_filter, strategies: {SYM: {name, params}}}` |
| `PUT /api/risk` | Partial risk limits |
| `POST /api/orders` | Manual `{symbol, side: buy\|sell, qty?}` at the live price |
| `POST /api/positions/{symbol}/close` | Close a position |
| `PUT /api/positions/{symbol}` | Set `{stop_loss, take_profit}` |
| `POST /api/account/reset` / `POST /api/account/resume` | Reset paper account (`{cash?}`) / lift a halt |
| `POST /api/backtest` | `{symbol, interval, bars<=5000, strategy, params, risk?, cash?}` -> metrics, equity curve, trades, markers, candles |
| `POST /api/ai/analyze/{symbol}` | Run an AI analysis now |
| `POST /api/advice/{symbol}` | One plain buy/sell/wait suggestion from hourly candles; powers the Simple view |
| `WS /ws` | Messages `{type, data}`: `snapshot` on connect, then `tick`, `account`, `trade`, `log`, `analysis`, `engine`, `risk`, `status`; send `{"type":"ping"}` for `{"type":"pong"}` |

Strategies: `sma_cross`, `ema_macd`, `rsi_reversion`, `bollinger_breakout`.
