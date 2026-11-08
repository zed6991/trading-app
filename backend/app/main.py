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

from app import indicators
from app.backtest import bars_per_year, run_backtest
from app.broker import RiskError
from app.config import settings
from app.engine import TradingEngine
from app.market import fetch_history
from app.strategies import STRATEGIES, list_strategies

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("app.main")

engine = TradingEngine()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await engine.start()
    try:
        yield
    finally:
        await engine.stop()


app = FastAPI(title="AI Crypto Paper Trader", version="1.0.0", lifespan=lifespan)


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


class BracketsRequest(BaseModel):
    stop_loss: float | None = None
    take_profit: float | None = None


class ResetRequest(BaseModel):
    cash: float | None = Field(default=None, gt=0)


class BacktestRequest(BaseModel):
    symbol: str
    interval: Literal["1m", "5m", "15m", "1h", "4h", "1d"] = "1h"
    bars: int = Field(default=1000, ge=50, le=5000)
    strategy: str
    params: dict[str, Any] = Field(default_factory=dict)
    risk: RiskUpdate | None = None
    cash: float | None = Field(default=None, gt=0)


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
    return await engine.place_order(body.symbol, body.side, body.qty)


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


@app.post("/api/backtest")
async def backtest(body: BacktestRequest):
    if body.strategy not in STRATEGIES:
        raise KeyError(f"Unknown strategy '{body.strategy}'. Available: {', '.join(STRATEGIES)}")
    symbol = body.symbol.upper()
    limits = engine.broker.limits.update(body.risk.model_dump(exclude_none=True) if body.risk else {})
    try:
        data = await fetch_history(symbol, body.interval, body.bars)
    except httpx.HTTPError as exc:
        raise HTTPException(400, f"Could not load history for {symbol}: {exc}") from exc
    if not data:
        raise ValueError(f"No history returned for {symbol} {body.interval}")
    result = await asyncio.to_thread(
        run_backtest, data, body.strategy, body.params, limits,
        body.cash or settings.starting_cash, settings.fee_rate, settings.slippage_bps,
        bars_per_year(body.interval), symbol,
    )
    result["candles"] = data
    return result


@app.post("/api/ai/analyze/{symbol}")
async def ai_analyze(symbol: str):
    return await engine.analyze(symbol)


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
app.mount("/", StaticFiles(directory=str(settings.frontend_dir), html=True), name="frontend")
