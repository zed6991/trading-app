"""Trading engine: wires market data, the paper broker, strategies, the AI analyst, persistence and the WebSocket hub."""
import asyncio
import json
import logging
import os
import time
from typing import Any

import httpx

from app import assets, indicators, prices as price_data
from app.ai import AIAnalyst, coin_name
from app.coach import Coach
from app.broker import PaperBroker, RiskError, RiskLimits
from app.config import Settings, settings as default_settings
from app.db import Database
from app.hub import Hub
from app.learn import Learning, rule_explanation, trade_facts
from app.market import MarketData, fetch_klines
from app.stocks import StockFeed, market_status
from app.strategies import STRATEGIES, Strategy, make_strategy

logger = logging.getLogger("app.engine")

DEFAULT_STRATEGY = "sma_cross"
STATUS_INTERVAL = 15.0
ACCOUNT_THROTTLE = 1.0
EQUITY_MIN_SPACING = 60.0
AI_CANDLES = 100
MARKET_RETRY = 30.0
ADVICE_INTERVAL = "1h"  # hourly candles, so beginner advice doesn't flip every few minutes


class CombinedMarket:
    """Crypto (Binance stream) and US stocks (Yahoo polling) behind the MarketData interface."""

    def __init__(self, symbols: list[str], interval: str, history_bars: int, on_update, on_close):
        crypto = [s for s in symbols if assets.is_crypto(s)]
        stocks = [s for s in symbols if not assets.is_crypto(s)]
        self.crypto = MarketData(crypto, interval, history_bars, on_update, on_close)
        self.stocks = StockFeed(stocks, history_bars, on_update, on_close)
        # Both feeds write into the same dicts, so callers (and tests) see one view of every symbol.
        self.candles: dict[str, list[dict]] = {**self.crypto.candles, **self.stocks.candles}
        self.last_price: dict[str, float] = {}
        for feed in (self.crypto, self.stocks):
            feed.candles = self.candles
            feed.last_price = self.last_price

    def _feed(self, symbol: str):
        return self.crypto if assets.is_crypto(symbol) else self.stocks

    @property
    def connected(self) -> bool:
        return self.crypto_connected or self.stocks_connected

    @property
    def crypto_connected(self) -> bool:
        return bool(self.crypto.symbols) and bool(self.crypto.connected)

    @property
    def stocks_connected(self) -> bool:
        return bool(self.stocks.symbols) and self.stocks.connected

    def closed_candles(self, symbol: str) -> list[dict]:
        return self._feed(symbol).closed_candles(symbol)

    async def start(self) -> None:
        results = await asyncio.gather(
            *(f.start() for f in (self.crypto, self.stocks) if f.symbols), return_exceptions=True)
        errors = [r for r in results if isinstance(r, BaseException)]
        if errors and len(errors) == len(results):
            raise errors[0]
        for err in errors:
            logger.error("Market feed failed to start: %r", err)

    async def stop(self) -> None:
        for feed in (self.crypto, self.stocks):
            try:
                await feed.stop()
            except Exception as exc:  # noqa: BLE001
                logger.warning("Feed stop failed: %r", exc)


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
        self.learn = Learning(self.db)
        self.market = CombinedMarket(
            self.symbols, self.interval, self.settings.history_bars, self.on_update, self.on_close
        )
        self.ai = AIAnalyst(self.settings.ollama_url, self.settings.ollama_model, self.settings.ollama_timeout)
        self.lock = asyncio.Lock()
        self.coach = Coach(self)
        self.broker = self._load_broker()
        self.config = self._load_config()
        self.orders: list[dict] = self._load_orders()
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
        broker = self._new_broker(limits)
        state = self.db.get_kv("broker")
        if isinstance(state, dict):
            try:
                broker.load(state)
            except Exception as exc:  # noqa: BLE001
                logger.error("Could not restore broker state, starting fresh: %s", exc)
                broker = self._new_broker(limits)
        broker.limits = limits  # kv 'risk' is authoritative for limits
        return broker

    def _new_broker(self, limits: RiskLimits) -> PaperBroker:
        s = self.settings
        return PaperBroker(cash=s.starting_cash, fee_rate=s.fee_rate, slippage_bps=s.slippage_bps, limits=limits,
                           fee_rate_for=lambda sym: assets.fee_rate(sym, s.fee_rate))

    def _load_orders(self) -> list[dict]:
        stored = self.db.get_kv("orders")
        return [o for o in stored if isinstance(o, dict) and o.get("symbol") in self.symbols] \
            if isinstance(stored, list) else []

    def _save_orders(self) -> None:
        self.db.set_kv("orders", self.orders)

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
        on = self.enable_market
        return {
            "market_connected": bool(self.market.connected) if on else False,
            "crypto_connected": bool(self.market.crypto_connected) if on else False,
            "stocks_connected": bool(self.market.stocks_connected) if on else False,
            "us_market": market_status(),
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
            "assets": assets.public_list(self.symbols),
            "interval": self.interval,
            "orders": self.open_orders(),
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
        if assets.is_crypto(symbol):
            return await fetch_klines(None, symbol, self.interval, limit=limit)
        return await price_data.history(symbol, "1m", limit)

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
        self.learn.record_fill(fill)
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
        if any(o["symbol"] == symbol for o in self.orders):
            await self.check_orders(symbol, price)
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
        if new["auto_trading"] and not old["auto_trading"]:
            self.learn.record("auto_on")
        if new["ai_filter"] and not old["ai_filter"]:
            self.learn.record("ai_filter_on")
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
            self.learn.record("risk")
            self.persist_broker()
        await self.hub.broadcast("risk", limits.to_dict())
        await self.log("info", "Risk limits updated: " + ", ".join(f"{k}={v}" for k, v in partial.items()))
        return limits.to_dict()

    # ------------------------------------------------------------------ manual actions
    def market_open_for(self, symbol: str) -> bool:
        return assets.is_crypto(symbol) or market_status()["open"]

    def _closed_note(self, symbol: str) -> str | None:
        if self.market_open_for(symbol):
            return None
        return ("The US market is closed, so this practice trade used the last price. With a real broker a market "
                "order would wait for the next open and could fill at a different price (a gap).")

    def _risk_qty(self, symbol: str, price: float, risk_amount: float) -> float:
        stop = self.broker.limits.stop_loss_pct
        if stop <= 0:
            raise RiskError("Turn on a stop-loss first: risk-based sizing needs to know where you'd get out.")
        qty = risk_amount / (price * stop)
        equity = self.broker.equity(self.prices())
        cap = self.broker.limits.max_position_pct * equity
        if qty * price > cap * 1.0001:
            raise RiskError(
                f"To risk ${risk_amount:,.0f} with a {stop * 100:g}% stop-loss you'd need to buy ${qty * price:,.0f}, "
                f"more than your position-size limit of ${cap:,.0f}. Risk less, or use a wider stop-loss.")
        return qty

    async def place_order(self, symbol: str, side: str, qty: float | None = None, *,
                          order_type: str = "market", limit_price: float | None = None,
                          risk_amount: float | None = None) -> dict:
        """Market order (fills now), or limit order (waits until the price reaches `limit_price`).
        `risk_amount` sizes a buy so that hitting the stop-loss loses about that many dollars."""
        symbol = self.check_symbol(symbol)
        if side not in ("buy", "sell"):
            raise ValueError(f"Invalid side {side!r}")
        if order_type == "limit":
            return await self.place_limit(symbol, side, limit_price, qty, risk_amount)
        if order_type != "market":
            raise ValueError("order type must be 'market' or 'limit'")
        async with self.lock:
            price = self.price(symbol)
            ts = time.time()
            if side == "buy":
                if risk_amount is not None:
                    qty = self._risk_qty(symbol, price, float(risk_amount))
                fill = self.broker.open_long(symbol, price, ts, strategy=None, qty=qty, reason="manual")
            else:
                fill = self.broker.close(symbol, price, ts, reason="manual")
            out = await self._record_fill(fill)
            if side == "buy" and risk_amount is not None:
                self.learn.record("risk_sized")
        note = self._closed_note(symbol)
        return {**out, "note": note} if note else out

    # ------------------------------------------------------------------ limit orders
    def open_orders(self) -> list[dict]:
        return [{**o, "name": assets.name(o["symbol"])} for o in self.orders]

    async def place_limit(self, symbol: str, side: str, limit_price: float | None, qty: float | None,
                          risk_amount: float | None) -> dict:
        if not limit_price or limit_price <= 0:
            raise ValueError("A limit order needs a limit price above zero.")
        price = self.price(symbol)
        if side == "sell" and symbol not in self.broker.positions:
            raise RiskError(f"You don't own any {assets.name(symbol)} to sell.")
        if side == "buy" and risk_amount is not None:
            qty = self._risk_qty(symbol, float(limit_price), float(risk_amount))
        if any(o["symbol"] == symbol and o["side"] == side for o in self.orders):
            raise RiskError(f"You already have a {side} limit order waiting on {assets.name(symbol)}. Cancel it first.")
        order = {"id": int(time.time() * 1000), "symbol": symbol, "side": side, "limit_price": float(limit_price),
                 "qty": qty, "created": time.time(), "price_when_placed": price}
        self.orders.append(order)
        self._save_orders()
        self.learn.record("limit_order")
        await self.log("info", f"LIMIT {side.upper()} {assets.name(symbol)} at {limit_price:.6g} (now {price:.6g})")
        await self.hub.broadcast("orders", self.open_orders())
        # A limit that's already reachable fills straight away (if the market is open).
        if self.market_open_for(symbol):
            await self.check_orders(symbol, price)
        filled = next((o for o in self.orders if o["id"] == order["id"]), None) is None
        return {**order, "status": "filled" if filled else "waiting", "name": assets.name(symbol),
                "note": None if filled else self._limit_note(symbol, side, float(limit_price), price)}

    def _limit_note(self, symbol: str, side: str, limit: float, price: float) -> str:
        if not self.market_open_for(symbol):
            return "Waiting. The US market is closed, so it can only fill after the next open."
        if side == "buy":
            return f"Waiting until the price falls to {limit:,.2f} or lower (it's {price:,.2f} now)."
        return f"Waiting until the price rises to {limit:,.2f} or higher (it's {price:,.2f} now)."

    async def check_orders(self, symbol: str, price: float) -> None:
        if not self.market_open_for(symbol):
            return
        async with self.lock:
            for order in [o for o in self.orders if o["symbol"] == symbol]:
                hit = price <= order["limit_price"] if order["side"] == "buy" else price >= order["limit_price"]
                if not hit:
                    continue
                self.orders.remove(order)
                self._save_orders()
                try:
                    if order["side"] == "buy":
                        fill = self.broker.open_long(symbol, price, time.time(), qty=order.get("qty"), reason="limit")
                    else:
                        fill = self.broker.close(symbol, price, time.time(), reason="limit")
                except RiskError as exc:
                    await self.log("warning", f"Limit {order['side']} on {symbol} couldn't fill: {exc}")
                else:
                    await self._record_fill(fill)
                await self.hub.broadcast("orders", self.open_orders())

    async def cancel_order(self, order_id: int) -> list[dict]:
        before = len(self.orders)
        self.orders = [o for o in self.orders if o["id"] != int(order_id)]
        if len(self.orders) == before:
            raise KeyError(f"Order {order_id} not found")
        self._save_orders()
        await self.hub.broadcast("orders", self.open_orders())
        return self.open_orders()

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
            self.learn.record("brackets")
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
            candles = await price_data.history(symbol, ADVICE_INTERVAL, AI_CANDLES)
        except (httpx.HTTPError, ValueError) as exc:
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

    async def explain_trade(self, trade_id: int) -> dict:
        """Beginner explanation of one trade: rule-based numbers, wording improved by the AI when available."""
        trade = self.db.one("SELECT * FROM trades WHERE id = ?", (int(trade_id),))
        if not trade:
            raise KeyError(f"Trade {trade_id} not found")
        cached = self.db.get_kv(f"explain:{trade['id']}")
        if isinstance(cached, dict):
            self.learn.record("explain")
            return cached
        entry = None
        if trade["side"] == "sell":
            entry = self.db.one("SELECT * FROM trades WHERE symbol = ? AND side = 'buy' AND id < ? ORDER BY id DESC LIMIT 1",
                                (trade["symbol"], trade["id"]))
        facts = trade_facts(trade, entry, coin_name(trade["symbol"]))
        base = rule_explanation(facts)
        result = {**base, "trade_id": trade["id"], "source": "rules"}
        if self.ai_health.get("available"):
            improved = await self.ai.explain_trade(facts, base, self.learn.level())
            if improved:
                result.update(improved)
                result["source"] = f"ollama:{self.ai.model}"
        self.db.set_kv(f"explain:{trade['id']}", result)
        self.learn.record("explain")
        return result

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
