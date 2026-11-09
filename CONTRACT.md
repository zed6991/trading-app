# Interface contract (all agents MUST follow this exactly)

Project root: `~/ai/trading-app`. Python 3.12 venv at `.venv` (`.venv/bin/python`, deps in requirements.txt:
fastapi, uvicorn, httpx, websockets 14, numpy, pydantic 2, pytest). Do NOT add dependencies.
Backend package: `backend/app` (run as `uvicorn app.main:app --app-dir backend`). Imports are `from app.x import y`.
Tests in `backend/tests`, run with `cd backend && ../.venv/bin/python -m pytest -q`.
Already written (do not modify): `app/config.py` (`settings`), `app/db.py` (`Database`), `app/indicators.py`
(`sma, ema, rsi, macd, bollinger, atr, snapshot(candles)->dict, series(candles)->dict`).

Paper trading only, long-only: US stocks/ETFs (Yahoo Finance) and spot crypto (Binance). No placeholders, TODOs or truncated code.

## Candle dict (used everywhere)
`{"time": int (open time, unix SECONDS), "open": float, "high": float, "low": float, "close": float, "volume": float, "closed": bool}`

## app/strategies.py  (owner: Trading Engine agent)
- `class Strategy`: attrs `name: str`, `description: str`, `default_params: dict` (class-level), `self.params` (defaults merged with overrides),
  property `min_bars: int`, method `signal(self, candles: list[dict]) -> "buy" | "sell" | None` evaluated on the LAST candle of the list
  (caller passes only closed candles).
- Concrete: `sma_cross` (fast/slow SMA cross), `ema_macd` (MACD/signal cross with EMA trend filter), `rsi_reversion`
  (buy RSI < oversold, sell RSI > overbought), `bollinger_breakout` (close above upper band = buy, below mid = sell).
- `STRATEGIES: dict[str, type[Strategy]]`, `make_strategy(name: str, params: dict | None = None) -> Strategy` (KeyError if unknown),
  `list_strategies() -> list[{"name","description","params"}]`.

## app/broker.py  (owner: Trading Engine agent)
- `class RiskError(Exception)`.
- `@dataclass RiskLimits`: `max_position_pct, max_open_positions:int, stop_loss_pct, take_profit_pct, trailing_stop_pct, daily_loss_limit_pct`
  (fractions, 0 = disabled for SL/TP/trailing/daily). `RiskLimits.from_settings()`, `.to_dict()`, `.update(dict) -> RiskLimits` (validates, returns new).
- `@dataclass Position`: `symbol, qty, avg_price, stop_loss: float|None, take_profit: float|None, high_water, strategy: str|None, opened_at: float`.
- Fill dict: `{"ts": float, "symbol", "side": "buy"|"sell", "qty", "price", "fee", "pnl": float|None (None on buys; net of both fees on sells), "reason": str, "strategy": str|None}`.
- `class PaperBroker(cash: float, fee_rate: float, slippage_bps: float, limits: RiskLimits)`:
  - attrs: `cash, starting_cash, realized_pnl, fees_paid, positions: dict[str, Position], limits, halted: bool, halt_reason: str|None, day_start_equity, day: str`
  - `equity(prices: dict[str, float]) -> float`
  - `open_long(symbol, price, ts, strategy=None, qty=None, reason="signal") -> dict(fill)`; qty None => size = max_position_pct*equity/price,
    capped by cash incl. fee. Raises RiskError if halted, position already open, max_open_positions reached, qty*price > max_position_pct*equity*1.0001,
    or insufficient cash. Buy fills at price*(1+slippage). Sets stop_loss/take_profit from limits.
  - `close(symbol, price, ts, reason="signal") -> dict(fill)`; RiskError if no position. Sell fills at price*(1-slippage).
  - `check_exits(symbol, high, low, close, ts) -> list[fill]`: updates high_water + trailing stop, exits at stop price (priority) or take-profit price.
    Live ticks pass high=low=close=price.
  - `update_day(ts, prices)`: rolls `day` (UTC date) resetting day_start_equity; if equity < day_start_equity*(1-daily_loss_limit_pct) sets halted.
  - `resume()`, `reset(cash: float | None = None)`, `set_brackets(symbol, stop_loss, take_profit) -> dict(position)`
  - `snapshot(prices) -> {"cash","equity","starting_cash","realized_pnl","unrealized_pnl","fees_paid","total_return_pct","halted","halt_reason",
    "positions":[{"symbol","qty","avg_price","mark","market_value","unrealized_pnl","pnl_pct","stop_loss","take_profit","strategy","opened_at"}]}`
  - `dump() -> dict` (JSON-serialisable full state) and `load(state: dict)`.

## app/backtest.py  (owner: Trading Engine agent)
`run_backtest(candles, strategy_name, params, limits: RiskLimits, cash, fee_rate, slippage_bps) -> dict`:
signal on close of bar i, entry/exit fill at OPEN of bar i+1 (no look-ahead); SL/TP checked using each bar's high/low; open position closed at the end.
Returns `{"metrics": {"total_return_pct","buy_hold_return_pct","max_drawdown_pct","sharpe","win_rate_pct","profit_factor","num_trades","final_equity"},
"equity_curve": [{"time","value"}], "trades": [fill + "time": int], "markers": [{"time","position":"belowBar"|"aboveBar","color","shape":"arrowUp"|"arrowDown","text"}]}`.
Sharpe annualised from per-bar returns (pass bars_per_year based on interval; helper `bars_per_year(interval: str)`). Use profit_factor = None if no losses.

## app/market.py  (owner: Market+AI agent)
- `async fetch_klines(client: httpx.AsyncClient | None, symbol, interval, limit=1000, end_ms=None) -> list[candle]` (Binance REST /api/v3/klines, all closed except possibly the last: set closed = close_time < now).
- `async fetch_history(symbol, interval, bars) -> list[candle]` paginates backwards, up to 5000 bars, ascending, de-duplicated.
- `class MarketData(symbols, interval, history_bars, on_update, on_close)`; `on_update(symbol, candle)` and `on_close(symbol, candle)` are async callbacks.
  attrs `candles: dict[str, list[candle]]` (keep last 1000, last element may be the forming candle), `last_price: dict[str,float]`, `connected: bool`.
  `async start()` bootstraps history via REST then launches a background task on Binance combined stream
  `{settings.binance_ws}/stream?streams=btcusdt@kline_1m/...`, reconnects with exponential backoff (max 30s), updates/append candles,
  calls on_update for every message and on_close when `k.x` is true. `async stop()`. `closed_candles(symbol) -> list[candle]`.
  Callback exceptions must be caught and logged, never kill the loop.

## app/ai.py  (owner: Market+AI agent)
`class AIAnalyst(url, model, timeout)`: `async analyze(symbol, candles, indicators: dict, position: dict | None) -> analysis`;
`async health() -> {"available": bool, "model": str, "error": str|None}`.
Uses Ollama `/api/chat` with `stream: false, format: "json", think: false, options: {temperature: 0.2, num_ctx: 8192}`; compact prompt
(last ~30 candles OHLC + indicator snapshot + position). Falls back to deterministic rule-based analysis on any error/timeout/invalid JSON.
analysis = `{"ts": float, "symbol", "bias": "bullish"|"bearish"|"neutral", "confidence": 0..1, "recommendation": "buy"|"sell"|"hold",
"summary": str (<= 3 sentences), "key_levels": {"support": float|None, "resistance": float|None}, "source": "ollama:<model>" | "rules", "latency_ms": int}`.

## app/engine.py + app/main.py  (owner: Integration agent)
Engine owns MarketData, PaperBroker, AIAnalyst, Database, WebSocket hub. Engine config:
`{"auto_trading": bool, "ai_filter": bool, "strategies": {SYMBOL: {"name": str, "params": dict}}}` persisted in kv.
Broker state persisted in kv `broker` after every fill; fills inserted in `trades`; equity snapshot each closed candle (and at most 1/min).
On update: check_exits on price, broadcast `tick`, broadcast `account` at most once per second. On close: update_day; run strategy on closed candles;
buy -> if auto_trading and no position (and if ai_filter: analysis.recommendation=="buy" or bias=="bullish") open_long; sell -> close if position.

### WebSocket `/ws` — messages `{"type": str, "data": ...}`
`snapshot` (= GET /api/state, sent on connect), `tick` `{"symbol","candle"}`, `account` (broker snapshot), `trade` (fill + "id"),
`log` `{"ts","level","message"}`, `analysis`, `engine` (engine config), `risk` (limits dict), `status` `{"market_connected","ai_available","ai_model"}`.
Client may send `{"type":"ping"}` -> server replies `{"type":"pong"}`.

### REST (JSON). Errors: HTTP 400 `{"detail": str}`
- `GET /api/health` -> `{"ok": true, "market_connected", "ai_available"}`
- `GET /api/state` -> `{"symbols","interval","account","engine","risk","status","prices": {sym: float}}`
- `GET /api/candles/{symbol}?limit=500` -> `{"candles": [...], "indicators": indicators.series(...), "snapshot": indicators.snapshot(...)}`
- `GET /api/strategies` -> list_strategies()
- `GET /api/trades?limit=200` (newest first, includes "id") ; `GET /api/logs?limit=200` (newest first) ; `GET /api/equity?limit=2000` -> `[{"time": int sec,"value"}]` ascending
- `GET /api/analyses?symbol=&limit=50` (newest first)
- `PUT /api/engine` partial `{"auto_trading"?, "ai_filter"?, "strategies"?: {SYM: {"name","params"}}}` -> engine config
- `PUT /api/risk` partial RiskLimits -> limits dict
- `POST /api/orders` `{"symbol","side":"buy"|"sell","qty": float|null}` -> fill ; `POST /api/positions/{symbol}/close` -> fill
- `PUT /api/positions/{symbol}` `{"stop_loss": float|null, "take_profit": float|null}` -> position
- `POST /api/account/reset` `{"cash"?: float}` -> account ; `POST /api/account/resume` -> account
- `POST /api/backtest` `{"symbol","interval":"1m|5m|15m|1h|4h|1d","bars": int<=5000,"strategy","params": {},"risk"?: partial,"cash"?: float}`
  -> run_backtest result + `"candles"` (the candles used)
- `POST /api/ai/analyze/{symbol}` -> analysis (also stored + broadcast)
- Static frontend served from `frontend/` at `/` (mounted last, html=True).

## frontend/  (owner: Frontend agent)
No build step, no npm. `frontend/index.html`, `frontend/styles.css`, `frontend/app.js` (ES module, vanilla JS).
Charts: `https://unpkg.com/lightweight-charts@4.2.0/dist/lightweight-charts.standalone.production.js` (global `LightweightCharts`).
Same-origin API (`/api/...`, `ws(s)://<host>/ws`).


## v2 additions: stocks, orders and the Quant Lab

- `app/assets.py`: `ASSETS` registry (`symbol, name, kind: stock|etf|crypto, sector`), `asset/kind/is_crypto/name`,
  `fee_rate(symbol, crypto_fee)` (0 for stocks/ETFs), `periods_per_year` (252 or 365), `public_list(symbols)`.
- `app/stocks.py`: `market_status(now?) -> {open, opens_at, closes_at, label}` (NYSE hours, holidays, early closes),
  `parse_chart(payload, interval)` (daily bars split/dividend-adjusted, time = UTC midnight of the NY date),
  `fetch_stock_history(symbol, interval in 1m|5m|15m|1h|1d, bars)`, `StockFeed` (same interface as `MarketData`,
  polls 1m bars every 15 s while open, 5 min while closed).
- `app/prices.py`: `history(symbol, interval, bars)` dispatches by asset; `cached_history` (6 h cache, closed bars
  only); `many_daily(symbols, bars)`.
- `app/engine.py`: `CombinedMarket` (crypto + stocks share one `candles` and `last_price` dict). `place_order(...,
  order_type, limit_price, risk_amount)`; limit orders live in kv `orders` and fill in `check_orders` on price updates
  (stocks only while the US market is open). Market orders on stocks while closed fill at the last price and return
  a `note`. Fill reasons now include `limit`.
- `app/broker.py`: `PaperBroker(..., fee_rate_for=callable)` for per-symbol fees.
- `app/strategies.py`: adds `momentum` (`lookback`, `threshold`) and `GRIDS` (settings tried by walk-forward).
- `app/backtest.py`: `run_backtest(..., trade_from=0)` (earlier bars only warm up indicators) and
  `walk_forward(candles, strategy, limits, cash, fee_rate, slippage_bps, bars_per_year, symbol, split, grid)`.
- `app/quant.py`: `stats`, `align`, `correlation`, `mix`, `luck_test`, `many_random` (daily candles in, JSON out).
- `app/curriculum.py`: `GLOSSARY`, `LEVELS` (built), `ROADMAP` (coming soon), `COURSE_OUTLINE`, `TOTAL_LEVELS`.
- WebSocket adds message type `orders` (list of waiting limit orders).
