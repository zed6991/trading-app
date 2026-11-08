"""Trading engine: wires market data, the paper broker, strategies, the AI analyst, persistence and the WebSocket hub."""
import asyncio
import json
import logging
import os
import time
from typing import Any

import httpx

from app import indicators
from app.ai import AIAnalyst, coin_name
from app.broker import PaperBroker, RiskError, RiskLimits
from app.config import Settings, settings as default_settings
from app.db import Database
from app.hub import Hub
from app.market import MarketData, fetch_history, fetch_klines
from app.strategies import STRATEGIES, Strategy, make_strategy

logger = logging.getLogger("app.engine")

DEFAULT_STRATEGY = "sma_cross"
STATUS_INTERVAL = 15.0
ACCOUNT_THROTTLE = 1.0
EQUITY_MIN_SPACING = 60.0
AI_CANDLES = 100
MARKET_RETRY = 30.0
ADVICE_INTERVAL = "1h"  # hourly candles, so beginner advice doesn't flip every few minutes


def market_disabled() -> bool:
    return os.environ.get("MARKET_DISABLED", "").strip().lower() in {"1", "true", "yes", "on"}


class TradingEngine:
    def __init__(self, cfg: Settings | None = None, enable_market: bool | None = None):
        self.settings = cfg or default_settings
        self.enable_market = (not market_disabled()) if enable_market is None else enable_market
        self.symbols: list[str] = list(self.settings.symbols)
        self.interval: str = self.settings.interval
        self.db = Database(self.settings.db_path)
        self.hub = Hub()
        self.market = MarketData(
            self.symbols, self.interval, self.settings.history_bars, self.on_update, self.on_close
        )
        self.ai = AIAnalyst(self.settings.ollama_url, self.settings.ollama_model, self.settings.ollama_timeout)
        self.lock = asyncio.Lock()
        self.broker = self._load_broker()
        self.config = self._load_config()
        self._strategies: dict[str, Strategy] = {}
        self.ai_health: dict[str, Any] = {"available": False, "model": self.settings.ollama_model, "error": "not checked yet"}
        self._last_account_broadcast = 0.0
        self._last_equity_ts = 0.0
        self._tasks: set[asyncio.Task] = set()
        self._status_task: asyncio.Task | None = None
        self._market_task: asyncio.Task | None = None
        self._running = False

    # ------------------------------------------------------------------ persistence / loading
    def _load_broker(self) -> PaperBroker:
        s = self.settings
        limits = RiskLimits.from_settings()
        stored_risk = self.db.get_kv("risk")
        if isinstance(stored_risk, dict):
            try:
                limits = limits.update(stored_risk)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Ignoring invalid stored risk limits: %s", exc)
        broker = PaperBroker(cash=s.starting_cash, fee_rate=s.fee_rate, slippage_bps=s.slippage_bps, limits=limits)
        state = self.db.get_kv("broker")
        if isinstance(state, dict):
            try:
                broker.load(state)
            except Exception as exc:  # noqa: BLE001
                logger.error("Could not restore broker state, starting fresh: %s", exc)
                broker = PaperBroker(cash=s.starting_cash, fee_rate=s.fee_rate, slippage_bps=s.slippage_bps, limits=limits)
        broker.limits = limits  # kv 'risk' is authoritative for limits
        return broker

    def _default_strategy_conf(self) -> dict:
        return {"name": DEFAULT_STRATEGY, "params": dict(STRATEGIES[DEFAULT_STRATEGY].default_params)}

    def _load_config(self) -> dict:
        stored = self.db.get_kv("engine") or {}
        config = {
            "auto_trading": bool(stored.get("auto_trading", False)),
            "ai_filter": bool(stored.get("ai_filter", False)),
            "strategies": {},
        }
        stored_strats = stored.get("strategies") or {}
        for sym in self.symbols:
            conf = stored_strats.get(sym)
            try:
                if not isinstance(conf, dict):
                    raise KeyError(sym)
                strat = make_strategy(conf["name"], conf.get("params") or {})
                config["strategies"][sym] = {"name": strat.name, "params": dict(strat.params)}
            except Exception:  # noqa: BLE001 - unknown/invalid stored config -> default
                config["strategies"][sym] = self._default_strategy_conf()
        return config

    def persist_broker(self) -> None:
        self.db.set_kv("broker", self.broker.dump())

    # ------------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        self._running = True
        await self.log("info", f"Engine starting: {', '.join(self.symbols)} @ {self.interval}"
                       + ("" if self.enable_market else " (market data disabled)"))
        if self.enable_market:
            self._market_task = asyncio.create_task(self._start_market(), name="engine-market-start")
        self._status_task = asyncio.create_task(self._status_loop(), name="engine-status")

    async def _start_market(self) -> None:
        while self._running:
            try:
                await self.market.start()
                loaded = {s: len(c) for s, c in self.market.candles.items()}
                await self.log("info", "Market data started, history bars: "
                               + ", ".join(f"{s}={n}" for s, n in loaded.items()))
                if self.market.last_price:
                    await self.snapshot_equity(force=True)
                if all(loaded.values()):
                    return
                await self.log("warning", "Some symbols have no history yet; live stream will fill them")
                return
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                await self.log("error", f"Market data start failed: {exc!r}; retrying in {MARKET_RETRY:.0f}s")
                await asyncio.sleep(MARKET_RETRY)

    async def stop(self) -> None:
        self._running = False
        for task in [self._status_task, self._market_task, *self._tasks]:
            if task and not task.done():
                task.cancel()
        for task in [self._status_task, self._market_task, *self._tasks]:
            if task:
                try:
                    await task
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
        self._tasks.clear()
        if self.enable_market:
            try:
                await self.market.stop()
            except Exception as exc:  # noqa: BLE001
                logger.warning("Market stop failed: %r", exc)
        self.persist_broker()
        self.db.log("info", "Engine stopped")
        try:
            self.db.conn.close()
        except Exception:  # noqa: BLE001
            pass

    def _spawn(self, coro) -> asyncio.Task:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def _status_loop(self) -> None:
        while self._running:
            await self.refresh_ai_health()
            await self.hub.broadcast("status", self.status())
            await asyncio.sleep(STATUS_INTERVAL)

    async def refresh_ai_health(self) -> dict:
        try:
            self.ai_health = await asyncio.wait_for(self.ai.health(), 10)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            self.ai_health = {"available": False, "model": self.settings.ollama_model, "error": repr(exc)}
        return self.ai_health

    # ------------------------------------------------------------------ views
    def prices(self) -> dict[str, float]:
        out: dict[str, float] = {}
        for sym, candles in self.market.candles.items():
            if candles:
                out[sym] = float(candles[-1]["close"])
        for sym, price in self.market.last_price.items():
            if price:
                out[sym] = float(price)
        return out

    def price(self, symbol: str) -> float:
        p = self.prices().get(symbol)
        if not p:
            raise ValueError(f"No price available for {symbol}")
        return p

    def status(self) -> dict:
        return {
            "market_connected": bool(self.market.connected) if self.enable_market else False,
            "ai_available": bool(self.ai_health.get("available")),
            "ai_model": self.ai_health.get("model") or self.settings.ollama_model,
        }

    def account(self) -> dict:
        return self.broker.snapshot(self.prices())

    def risk(self) -> dict:
        return self.broker.limits.to_dict()

    def state(self) -> dict:
        return {
            "symbols": self.symbols,
            "interval": self.interval,
            "account": self.account(),
            "engine": self.config,
            "risk": self.risk(),
            "status": self.status(),
            "prices": self.prices(),
        }

    def check_symbol(self, symbol: str) -> str:
        symbol = symbol.upper()
        if symbol not in self.symbols:
            raise ValueError(f"Unknown symbol {symbol}; configured: {', '.join(self.symbols)}")
        return symbol

    async def candles(self, symbol: str, limit: int = 500) -> list[dict]:
        symbol = symbol.upper()
        limit = max(1, min(int(limit), 1000))
        data = self.market.candles.get(symbol)
        if data:
            return list(data[-limit:])
        if not self.enable_market:
            return []
        return await fetch_klines(None, symbol, self.interval, limit=limit)

    # ------------------------------------------------------------------ events
    async def log(self, level: str, message: str) -> dict:
        entry = self.db.log(level, message)
        getattr(logger, level if level in {"debug", "info", "warning", "error"} else "info")(message)
        await self.hub.broadcast("log", entry)
        return entry

    async def broadcast_account(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_account_broadcast < ACCOUNT_THROTTLE:
            return
        self._last_account_broadcast = now
        await self.hub.broadcast("account", self.account())

    async def snapshot_equity(self, force: bool = False) -> None:
        now = time.time()
        if not force and now - self._last_equity_ts < EQUITY_MIN_SPACING:
            return
        prices = self.prices()
        equity = self.broker.equity(prices)
        self.db.execute(
            "INSERT OR REPLACE INTO equity(ts, equity, cash) VALUES(?, ?, ?)", (now, float(equity), float(self.broker.cash))
        )
        self._last_equity_ts = now

    async def _record_fill(self, fill: dict) -> dict:
        cur = self.db.execute(
            "INSERT INTO trades(ts, symbol, side, qty, price, fee, pnl, reason, strategy) VALUES(?,?,?,?,?,?,?,?,?)",
            (fill["ts"], fill["symbol"], fill["side"], fill["qty"], fill["price"], fill["fee"],
             fill.get("pnl"), fill.get("reason"), fill.get("strategy")),
        )
        out = {**fill, "id": cur.lastrowid}
        self.persist_broker()
        await self.hub.broadcast("trade", out)
        await self.broadcast_account(force=True)
        pnl = f", pnl {fill['pnl']:+.2f}" if fill.get("pnl") is not None else ""
        await self.log(
            "info",
            f"{fill['side'].upper()} {fill['qty']:.6g} {fill['symbol']} @ {fill['price']:.6g} "
            f"(fee {fill['fee']:.2f}{pnl}, reason: {fill.get('reason')})",
        )
        return out

    async def on_update(self, symbol: str, candle: dict) -> None:
        price = float(candle["close"])
        if symbol in self.broker.positions:
            async with self.lock:
                if symbol in self.broker.positions:
                    fills = self.broker.check_exits(symbol, price, price, price, time.time())
                    for fill in fills:
                        await self._record_fill(fill)
        await self.hub.broadcast("tick", {"symbol": symbol, "candle": candle})
        await self.broadcast_account()

    async def on_close(self, symbol: str, candle: dict) -> None:
        # Run in its own task so a slow AI call never stalls the market stream.
        self._spawn(self._handle_close(symbol, candle))

    async def _handle_close(self, symbol: str, candle: dict) -> None:
        try:
            await self._process_close(symbol, candle)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.exception("Close handling failed for %s", symbol)
            await self.log("error", f"Close handling failed for {symbol}: {exc!r}")

    async def _process_close(self, symbol: str, candle: dict) -> None:
        async with self.lock:
            was_halted = self.broker.halted
            self.broker.update_day(time.time(), self.prices())
            if self.broker.halted and not was_halted:
                self.persist_broker()
                await self.log("warning", f"Trading halted: {self.broker.halt_reason}")
                await self.broadcast_account(force=True)
        await self.snapshot_equity()

        strategy = self.strategy_for(symbol)
        closed = self.market.closed_candles(symbol)
        if len(closed) < strategy.min_bars:
            return
        signal = strategy.signal(closed)
        if signal is None:
            return
        price = float(candle["close"])
        auto = self.config["auto_trading"]
        has_pos = symbol in self.broker.positions
        await self.log("info", f"{strategy.name} signal {signal.upper()} on {symbol} @ {price:.6g}"
                       + ("" if auto else " (auto-trading off)"))
        if not auto:
            return

        if signal == "buy" and not has_pos:
            if self.config["ai_filter"]:
                analysis = await self.analyze(symbol)
                if not (analysis.get("recommendation") == "buy" or analysis.get("bias") == "bullish"):
                    await self.log("info", f"AI filter blocked BUY on {symbol}: {analysis.get('bias')} / "
                                   f"{analysis.get('recommendation')} ({analysis.get('source')})")
                    return
            async with self.lock:
                if symbol in self.broker.positions:
                    return
                try:
                    fill = self.broker.open_long(symbol, self.prices().get(symbol, price), time.time(),
                                                 strategy=strategy.name, reason="signal")
                except RiskError as exc:
                    await self.log("warning", f"BUY {symbol} rejected: {exc}")
                    return
                await self._record_fill(fill)
        elif signal == "sell" and has_pos:
            async with self.lock:
                if symbol not in self.broker.positions:
                    return
                try:
                    fill = self.broker.close(symbol, self.prices().get(symbol, price), time.time(), reason="signal")
                except RiskError as exc:
                    await self.log("warning", f"SELL {symbol} rejected: {exc}")
                    return
                await self._record_fill(fill)

    # ------------------------------------------------------------------ strategies / config
    def strategy_for(self, symbol: str) -> Strategy:
        strat = self._strategies.get(symbol)
        if strat is None:
            conf = self.config["strategies"].get(symbol) or self._default_strategy_conf()
            strat = make_strategy(conf["name"], conf.get("params") or {})
            self._strategies[symbol] = strat
        return strat

    async def update_engine(self, partial: dict) -> dict:
        new = json.loads(json.dumps(self.config))
        if partial.get("auto_trading") is not None:
            new["auto_trading"] = bool(partial["auto_trading"])
        if partial.get("ai_filter") is not None:
            new["ai_filter"] = bool(partial["ai_filter"])
        changed: list[str] = []
        for sym, conf in (partial.get("strategies") or {}).items():
            sym = self.check_symbol(sym)
            name = conf.get("name")
            if name not in STRATEGIES:
                raise KeyError(f"Unknown strategy '{name}'. Available: {', '.join(STRATEGIES)}")
            params = conf.get("params") or {}
            if not isinstance(params, dict):
                raise ValueError("params must be an object")
            try:
                strat = make_strategy(name, params)
                _ = strat.min_bars
            except KeyError:
                raise
            except Exception as exc:  # noqa: BLE001
                raise ValueError(f"Invalid params for {name}: {exc}") from exc
            new["strategies"][sym] = {"name": strat.name, "params": dict(strat.params)}
            changed.append(sym)
        old = self.config
        self.config = new
        for sym in changed:
            self._strategies.pop(sym, None)
        self.db.set_kv("engine", self.config)
        await self.hub.broadcast("engine", self.config)
        msgs = []
        if new["auto_trading"] != old["auto_trading"]:
            msgs.append(f"auto-trading {'ON' if new['auto_trading'] else 'OFF'}")
        if new["ai_filter"] != old["ai_filter"]:
            msgs.append(f"AI filter {'ON' if new['ai_filter'] else 'OFF'}")
        for sym in changed:
            msgs.append(f"{sym} -> {new['strategies'][sym]['name']} {new['strategies'][sym]['params']}")
        if msgs:
            await self.log("info", "Engine config: " + "; ".join(msgs))
        return self.config

    async def update_risk(self, partial: dict) -> dict:
        async with self.lock:
            limits = self.broker.limits.update(partial)
            self.broker.limits = limits
            self.db.set_kv("risk", limits.to_dict())
            self.persist_broker()
        await self.hub.broadcast("risk", limits.to_dict())
        await self.log("info", "Risk limits updated: " + ", ".join(f"{k}={v}" for k, v in partial.items()))
        return limits.to_dict()

    # ------------------------------------------------------------------ manual actions
    async def place_order(self, symbol: str, side: str, qty: float | None = None) -> dict:
        symbol = self.check_symbol(symbol)
        async with self.lock:
            price = self.price(symbol)
            ts = time.time()
            if side == "buy":
                fill = self.broker.open_long(symbol, price, ts, strategy=None, qty=qty, reason="manual")
            elif side == "sell":
                fill = self.broker.close(symbol, price, ts, reason="manual")
            else:
                raise ValueError(f"Invalid side {side!r}")
            return await self._record_fill(fill)

    async def close_position(self, symbol: str) -> dict:
        symbol = self.check_symbol(symbol)
        async with self.lock:
            fill = self.broker.close(symbol, self.price(symbol), time.time(), reason="manual")
            return await self._record_fill(fill)

    async def set_brackets(self, symbol: str, stop_loss: float | None, take_profit: float | None) -> dict:
        symbol = symbol.upper()
        async with self.lock:
            position = self.broker.set_brackets(symbol, stop_loss, take_profit)
            self.persist_broker()
        await self.broadcast_account(force=True)
        await self.log("info", f"Brackets {symbol}: SL={stop_loss} TP={take_profit}")
        return position

    async def reset_account(self, cash: float | None = None) -> dict:
        async with self.lock:
            self.broker.reset(cash)
            self.persist_broker()
            self.db.execute("DELETE FROM equity")
            self._last_equity_ts = 0.0
            await self.snapshot_equity(force=True)
        account = self.account()
        await self.hub.broadcast("account", account)
        await self.log("warning", f"Account reset to {self.broker.starting_cash:,.2f}")
        return account

    async def resume(self) -> dict:
        async with self.lock:
            self.broker.resume()
            self.persist_broker()
        account = self.account()
        await self.hub.broadcast("account", account)
        await self.log("info", "Trading resumed")
        return account

    # ------------------------------------------------------------------ AI
    async def advice(self, symbol: str) -> dict:
        """One plain recommendation for a beginner: buy, sell or wait, based on hourly candles."""
        symbol = self.check_symbol(symbol)
        try:
            candles = await fetch_history(symbol, ADVICE_INTERVAL, AI_CANDLES)
        except httpx.HTTPError as exc:
            logger.warning("Hourly candles for %s unavailable (%s); using live candles", symbol, exc)
            candles = None
        analysis = await self.analyze(symbol, candles)
        owned = any(p["symbol"] == symbol for p in self.account()["positions"])
        rec = analysis.get("recommendation")
        action = "buy" if rec == "buy" and not owned else "sell" if rec == "sell" and owned else "wait"
        return {**analysis, "name": coin_name(symbol), "action": action, "owned": owned}

    async def analyze(self, symbol: str, candles: list[dict] | None = None) -> dict:
        symbol = symbol.upper()
        candles = candles or self.market.closed_candles(symbol) or list(self.market.candles.get(symbol, []))
        if not candles:
            raise ValueError(f"No candle data for {symbol}")
        candles = candles[-AI_CANDLES:]
        snap = indicators.snapshot(candles)
        position = next((p for p in self.account()["positions"] if p["symbol"] == symbol), None)
        analysis = await self.ai.analyze(symbol, candles, snap, position)
        cur = self.db.execute(
            "INSERT INTO analyses(ts, symbol, source, bias, confidence, payload) VALUES(?,?,?,?,?,?)",
            (analysis.get("ts", time.time()), symbol, analysis.get("source", "unknown"), analysis.get("bias", "neutral"),
             float(analysis.get("confidence") or 0), json.dumps(analysis)),
        )
        analysis = {**analysis, "id": cur.lastrowid}
        await self.hub.broadcast("analysis", analysis)
        await self.log("info", f"AI {symbol}: {analysis.get('bias')} / {analysis.get('recommendation')} "
                       f"conf {float(analysis.get('confidence') or 0):.2f} via {analysis.get('source')} "
                       f"({analysis.get('latency_ms')} ms)")
        return analysis

    # ------------------------------------------------------------------ history queries
    def trades(self, limit: int = 200) -> list[dict]:
        return self.db.query("SELECT * FROM trades ORDER BY id DESC LIMIT ?", (max(1, int(limit)),))

    def logs(self, limit: int = 200) -> list[dict]:
        return self.db.query("SELECT ts, level, message FROM logs ORDER BY id DESC LIMIT ?", (max(1, int(limit)),))

    def equity(self, limit: int = 2000) -> list[dict]:
        rows = self.db.query(
            "SELECT ts, equity FROM (SELECT ts, equity FROM equity ORDER BY ts DESC LIMIT ?) ORDER BY ts ASC",
            (max(1, int(limit)),),
        )
        out: list[dict] = []
        for r in rows:
            t = int(r["ts"])
            if out and out[-1]["time"] == t:
                out[-1]["value"] = r["equity"]  # chart libs need strictly increasing times
            else:
                out.append({"time": t, "value": r["equity"]})
        return out

    def analyses(self, symbol: str | None = None, limit: int = 50) -> list[dict]:
        if symbol:
            rows = self.db.query("SELECT id, payload FROM analyses WHERE symbol = ? ORDER BY id DESC LIMIT ?",
                                 (symbol.upper(), max(1, int(limit))))
        else:
            rows = self.db.query("SELECT id, payload FROM analyses ORDER BY id DESC LIMIT ?", (max(1, int(limit)),))
        return [{**json.loads(r["payload"]), "id": r["id"]} for r in rows]
