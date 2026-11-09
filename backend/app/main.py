"""FastAPI application: REST API, WebSocket feed and the static dashboard."""
import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Any, Literal

import httpx
from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app import assets, indicators, prices, quant
from app.backtest import bars_per_year, run_backtest, walk_forward
from app.broker import RiskError
from app.config import settings
from app.engine import TradingEngine
from app.learn import GLOSSARY
from app.stocks import market_status
from app.strategies import STRATEGIES, list_strategies

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("app.main")
logging.getLogger("httpx").setLevel(logging.WARNING)  # stock polling would flood the log

engine = TradingEngine()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await engine.start()
    try:
        yield
    finally:
        await engine.stop()


app = FastAPI(title="Quant Trader Academy", version="2.0.0", lifespan=lifespan)


# ---------------------------------------------------------------------- error handling
def _bad_request(exc: Exception) -> JSONResponse:
    msg = exc.args[0] if isinstance(exc, KeyError) and exc.args else str(exc)
    return JSONResponse(status_code=400, content={"detail": str(msg)})


@app.exception_handler(RiskError)
async def _risk_error(_: Request, exc: RiskError):
    return _bad_request(exc)


@app.exception_handler(KeyError)
async def _key_error(_: Request, exc: KeyError):
    return _bad_request(exc)


@app.exception_handler(ValueError)
async def _value_error(_: Request, exc: ValueError):
    return _bad_request(exc)


@app.exception_handler(RequestValidationError)
async def _validation_error(_: Request, exc: RequestValidationError):
    parts = []
    for err in exc.errors():
        loc = ".".join(str(x) for x in err.get("loc", []) if x != "body")
        parts.append(f"{loc}: {err.get('msg')}" if loc else str(err.get("msg")))
    return JSONResponse(status_code=400, content={"detail": "; ".join(parts) or "invalid request"})


# ---------------------------------------------------------------------- request models
class StrategyConf(BaseModel):
    name: str
    params: dict[str, Any] = Field(default_factory=dict)


class EngineUpdate(BaseModel):
    auto_trading: bool | None = None
    ai_filter: bool | None = None
    strategies: dict[str, StrategyConf] | None = None


class RiskUpdate(BaseModel):
    max_position_pct: float | None = None
    max_open_positions: int | None = None
    stop_loss_pct: float | None = None
    take_profit_pct: float | None = None
    trailing_stop_pct: float | None = None
    daily_loss_limit_pct: float | None = None


class OrderRequest(BaseModel):
    symbol: str
    side: Literal["buy", "sell"]
    qty: float | None = Field(default=None, gt=0)
    type: Literal["market", "limit"] = "market"
    limit_price: float | None = Field(default=None, gt=0)
    risk_amount: float | None = Field(default=None, gt=0)


class BracketsRequest(BaseModel):
    stop_loss: float | None = None
    take_profit: float | None = None


class ResetRequest(BaseModel):
    cash: float | None = Field(default=None, gt=0)


class AnswerRequest(BaseModel):
    question: int
    choice: int


class LearnEvent(BaseModel):
    name: str
    value: str | None = None


class UnlockRequest(BaseModel):
    on: bool = True


class CoachTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=4000)


class CoachAsk(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    history: list[CoachTurn] = Field(default_factory=list, max_length=20)
    view: str | None = None
    symbol: str | None = None


class BacktestRequest(BaseModel):
    symbol: str
    interval: Literal["1m", "5m", "15m", "1h", "4h", "1d"] = "1h"
    bars: int = Field(default=1000, ge=50, le=5000)
    strategy: str
    params: dict[str, Any] = Field(default_factory=dict)
    risk: RiskUpdate | None = None
    cash: float | None = Field(default=None, gt=0)
    fee_pct: float | None = Field(default=None, ge=0, le=5)  # per trade, % of value; default: the asset's fee
    slippage_bps: float | None = Field(default=None, ge=0, le=500)
    split: float = Field(default=0.7, ge=0.3, le=0.9)  # walk-forward only


class SymbolsRequest(BaseModel):
    symbols: list[str] = Field(min_length=2, max_length=16)


class MixRequest(BaseModel):
    weights: dict[str, float]


class LuckRequest(BaseModel):
    symbol: str
    strategy: str = "sma_cross"
    params: dict[str, Any] = Field(default_factory=dict)
    years: int = Field(default=5, ge=1, le=10)
    trials: int = Field(default=500, ge=50, le=2000)


# ---------------------------------------------------------------------- REST
@app.get("/api/health")
async def health():
    status = engine.status()
    return {"ok": True, "market_connected": status["market_connected"], "ai_available": status["ai_available"]}


@app.get("/api/state")
async def state():
    return engine.state()


@app.get("/api/candles/{symbol}")
async def candles(symbol: str, limit: int = Query(500, ge=1, le=1000)):
    try:
        full = await engine.candles(symbol, 1000)
    except httpx.HTTPError as exc:
        raise HTTPException(400, f"Could not load candles for {symbol.upper()}: {exc}") from exc
    # Indicators are computed on the whole buffer (so short windows still get warmed-up values), then trimmed.
    data = full[-limit:]
    start = data[0]["time"] if data else 0
    series = {k: [p for p in v if p["time"] >= start] for k, v in indicators.series(full).items()}
    return {"candles": data, "indicators": series, "snapshot": indicators.snapshot(full)}


@app.get("/api/strategies")
async def strategies():
    return list_strategies()


@app.get("/api/trades")
async def trades(limit: int = Query(200, ge=1, le=5000)):
    return engine.trades(limit)


@app.get("/api/logs")
async def logs(limit: int = Query(200, ge=1, le=5000)):
    return engine.logs(limit)


@app.get("/api/equity")
async def equity(limit: int = Query(2000, ge=1, le=100000)):
    return engine.equity(limit)


@app.get("/api/analyses")
async def analyses(symbol: str | None = None, limit: int = Query(50, ge=1, le=1000)):
    return engine.analyses(symbol or None, limit)


@app.put("/api/engine")
async def update_engine(body: EngineUpdate):
    partial = body.model_dump(exclude_none=True)
    return await engine.update_engine(partial)


@app.put("/api/risk")
async def update_risk(body: RiskUpdate):
    partial = body.model_dump(exclude_none=True)
    if not partial:
        return engine.risk()
    return await engine.update_risk(partial)


@app.post("/api/orders")
async def place_order(body: OrderRequest):
    return await engine.place_order(body.symbol, body.side, body.qty, order_type=body.type,
                                    limit_price=body.limit_price, risk_amount=body.risk_amount)


@app.get("/api/orders")
async def list_orders():
    return engine.open_orders()


@app.delete("/api/orders/{order_id}")
async def cancel_order(order_id: int):
    return await engine.cancel_order(order_id)


@app.get("/api/assets")
async def list_assets():
    return assets.public_list(engine.symbols)


@app.get("/api/market/status")
async def us_market_status():
    return market_status()


@app.post("/api/positions/{symbol}/close")
async def close_position(symbol: str):
    return await engine.close_position(symbol)


@app.put("/api/positions/{symbol}")
async def set_brackets(symbol: str, body: BracketsRequest):
    return await engine.set_brackets(symbol, body.stop_loss, body.take_profit)


@app.post("/api/account/reset")
async def reset_account(body: ResetRequest | None = None):
    return await engine.reset_account(body.cash if body else None)


@app.post("/api/account/resume")
async def resume_account():
    return await engine.resume()


@app.post("/api/advice/{symbol}")
async def advice(symbol: str):
    return await engine.advice(symbol)


async def _load(symbol: str, interval: str, bars: int) -> list[dict]:
    try:
        if interval == "1d":
            data = await prices.cached_history(symbol, "1d", bars)
        else:
            data = await prices.history(symbol, interval, bars)
    except httpx.HTTPError as exc:
        raise HTTPException(400, f"Could not load history for {symbol}: {exc}") from exc
    if not data:
        raise ValueError(f"No history returned for {symbol} {interval}")
    return data


def _bars_per_year(symbol: str, interval: str) -> float:
    """Bars per year for Sharpe: crypto trades all year; stocks ~252 days of 6.5 hours."""
    if assets.is_crypto(symbol):
        return bars_per_year(interval)
    per_day = {"1m": 390, "5m": 78, "15m": 26, "1h": 7, "4h": 2, "1d": 1}[interval]
    return 252.0 * per_day


def _costs(body: BacktestRequest, symbol: str) -> tuple[float, float]:
    fee = body.fee_pct / 100 if body.fee_pct is not None else assets.fee_rate(symbol, settings.fee_rate)
    slip = body.slippage_bps if body.slippage_bps is not None else settings.slippage_bps
    return fee, slip


def _backtest_setup(body: BacktestRequest):
    if body.strategy not in STRATEGIES:
        raise KeyError(f"Unknown strategy '{body.strategy}'. Available: {', '.join(STRATEGIES)}")
    symbol = engine.check_symbol(body.symbol)
    limits = engine.broker.limits.update(body.risk.model_dump(exclude_none=True) if body.risk else {})
    return symbol, limits


@app.post("/api/backtest")
async def backtest(body: BacktestRequest):
    symbol, limits = _backtest_setup(body)
    data = await _load(symbol, body.interval, body.bars)
    fee, slip = _costs(body, symbol)
    result = await asyncio.to_thread(
        run_backtest, data, body.strategy, body.params, limits,
        body.cash or settings.starting_cash, fee, slip, _bars_per_year(symbol, body.interval), symbol,
    )
    engine.learn.record_backtest(body.strategy, result.get("metrics") or {})
    result["candles"] = data
    result["costs"] = {"fee_pct": fee * 100, "slippage_bps": slip}
    return result


@app.post("/api/backtest/walkforward")
async def backtest_walkforward(body: BacktestRequest):
    symbol, limits = _backtest_setup(body)
    data = await _load(symbol, body.interval, body.bars)
    fee, slip = _costs(body, symbol)
    result = await asyncio.to_thread(
        walk_forward, data, body.strategy, limits, body.cash or settings.starting_cash, fee, slip,
        _bars_per_year(symbol, body.interval), symbol, body.split,
    )
    engine.learn.record_backtest(body.strategy, result["out_sample"]["metrics"])
    engine.learn.record_walkforward(result)
    result["candles"] = data
    result["costs"] = {"fee_pct": fee * 100, "slippage_bps": slip}
    return result


# ---------------------------------------------------------------------- quant lab
def _daily_bars(symbol: str, years: int) -> int:
    return assets.periods_per_year(symbol) * years + 1


@app.get("/api/lab/stats/{symbol}")
async def lab_stats(symbol: str, years: int = Query(5, ge=1, le=10)):
    symbol = engine.check_symbol(symbol)
    data = await prices.cached_history(symbol, "1d", _daily_bars(symbol, years))
    result = await asyncio.to_thread(quant.stats, symbol, data)
    engine.learn.record("stats", symbol)
    return result


@app.post("/api/lab/correlation")
async def lab_correlation(body: SymbolsRequest, years: int = Query(3, ge=1, le=10)):
    symbols = list(dict.fromkeys(engine.check_symbol(s) for s in body.symbols))
    wanted = symbols if assets.BENCHMARK in symbols else symbols + [assets.BENCHMARK]
    series = await prices.many_daily(wanted, max(_daily_bars(s, years) for s in wanted))
    result = await asyncio.to_thread(quant.correlation, {s: series[s] for s in symbols})
    if assets.BENCHMARK not in symbols:
        full = await asyncio.to_thread(quant.correlation, series)
        result["beta"] = {s: full["beta"].get(s) for s in symbols}
    engine.learn.record("corr")
    return result


@app.post("/api/lab/mix")
async def lab_mix(body: MixRequest, years: int = Query(3, ge=1, le=10)):
    weights = {engine.check_symbol(s): float(w) for s, w in body.weights.items() if float(w) > 0}
    if len(weights) < 2:
        raise ValueError("Pick at least two assets with a weight above zero.")
    if len(weights) > 8:
        raise ValueError("Use at most 8 assets in a mix.")
    series = await prices.many_daily(list(weights), max(_daily_bars(s, years) for s in weights))
    result = await asyncio.to_thread(quant.mix, series, weights)
    engine.learn.record_mix(result)
    return result


def _cost_of(symbol: str) -> float:
    return assets.fee_rate(symbol, settings.fee_rate) + settings.slippage_bps / 10_000


@app.post("/api/lab/luck")
async def lab_luck(body: LuckRequest):
    if body.strategy not in STRATEGIES:
        raise KeyError(f"Unknown strategy '{body.strategy}'")
    symbol = engine.check_symbol(body.symbol)
    data = await prices.cached_history(symbol, "1d", _daily_bars(symbol, body.years))
    result = await asyncio.to_thread(quant.luck_test, data, body.strategy, body.params, _cost_of(symbol), body.trials)
    result.update({"symbol": symbol, "name": assets.name(symbol)})
    engine.learn.record_luck(result)
    return result


@app.post("/api/lab/many")
async def lab_many(body: LuckRequest):
    symbol = engine.check_symbol(body.symbol)
    data = await prices.cached_history(symbol, "1d", _daily_bars(symbol, body.years))
    result = await asyncio.to_thread(quant.many_random, data, _cost_of(symbol))
    result.update({"symbol": symbol, "name": assets.name(symbol)})
    engine.learn.record("luck_many")
    return result


@app.post("/api/ai/analyze/{symbol}")
async def ai_analyze(symbol: str):
    result = await engine.analyze(symbol)
    engine.learn.record("analyze")
    return result


@app.post("/api/trades/{trade_id}/explain")
async def explain_trade(trade_id: int):
    return await engine.explain_trade(trade_id)


# ---------------------------------------------------------------------- AI coach
@app.post("/api/coach/ask")
async def coach_ask(body: CoachAsk):
    history = [t.model_dump() for t in body.history]
    return await engine.coach.ask(body.message, history, body.view, body.symbol)


@app.get("/api/coach/review")
async def coach_review(days: int = Query(7, ge=1, le=90)):
    return await engine.coach.review(days)


@app.get("/api/coach/next")
async def coach_next():
    review = engine.coach.rule_review(7)
    return engine.coach.next_step(review)


# ---------------------------------------------------------------------- learning path
@app.get("/api/learn")
async def learn_summary():
    return engine.learn.summary()


@app.get("/api/learn/glossary")
async def learn_glossary():
    return GLOSSARY


@app.get("/api/learn/lessons/{lesson_id}")
async def learn_lesson(lesson_id: str):
    return engine.learn.lesson(lesson_id)


@app.post("/api/learn/lessons/{lesson_id}/answer")
async def learn_answer(lesson_id: str, body: AnswerRequest):
    return engine.learn.answer(lesson_id, body.question, body.choice)


@app.post("/api/learn/events")
async def learn_event(body: LearnEvent):
    return engine.learn.record_client(body.name, body.value)


@app.post("/api/learn/unlock-all")
async def learn_unlock_all(body: UnlockRequest):
    return engine.learn.set_unlock_all(body.on)


@app.post("/api/learn/reset")
async def learn_reset():
    return engine.learn.reset()


# ---------------------------------------------------------------------- WebSocket
@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await engine.hub.connect(ws)
    try:
        await engine.hub.send(ws, "snapshot", engine.state())
        while True:
            msg = await ws.receive_json()
            if isinstance(msg, dict) and msg.get("type") == "ping":
                await ws.send_json({"type": "pong"})
    except WebSocketDisconnect:
        pass
    except Exception as exc:  # noqa: BLE001 - malformed client frames etc.
        logger.debug("WebSocket closed: %r", exc)
    finally:
        engine.hub.disconnect(ws)


# ---------------------------------------------------------------------- static dashboard (mounted last)
settings.frontend_dir.mkdir(parents=True, exist_ok=True)


@app.middleware("http")
async def revalidate_frontend(request: Request, call_next):
    """Make browsers check for a newer file each time, so app updates show up on a normal reload."""
    response = await call_next(request)
    if not request.url.path.startswith("/api/"):
        response.headers.setdefault("Cache-Control", "no-cache")
    return response


app.mount("/", StaticFiles(directory=str(settings.frontend_dir), html=True), name="frontend")
