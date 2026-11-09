"""FastAPI application: REST API, WebSocket feed and the static dashboard."""
import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Literal

import httpx
import numpy as np
from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app import assets, codelab, indicators, ml, portfolio, prices, quant
from app.backtest import bars_per_year, run_backtest, walk_forward
from app.broker import RiskError
from app.config import settings
from app.engine import TradingEngine
from app.agent import _daily_equity
from app.learn import GLOSSARY
from app.llm import LLMError
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


@app.exception_handler(LLMError)
async def _llm_error(_: Request, exc: LLMError):
    return JSONResponse(status_code=503, content={"detail": str(exc)})


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


class AISettings(BaseModel):
    cloud_enabled: bool | None = None
    cloud_model: str | None = None
    cloud_tasks: list[str] | None = None
    monthly_cap_usd: float | None = None


class CodeRun(BaseModel):
    code: str = Field(max_length=50_000)


class CodeCheck(BaseModel):
    exercise_id: str
    code: str = Field(max_length=50_000)


class CodeAssist(BaseModel):
    code: str = Field(default="", max_length=20_000)
    output: str = Field(default="", max_length=6_000)
    error: str = Field(default="", max_length=6_000)
    question: str = Field(default="", max_length=1_000)
    mode: Literal["explain", "hint", "fix", "ask"] = "explain"
    exercise_id: str | None = None


class StrategyEval(BaseModel):
    code: str = Field(max_length=50_000)
    symbol: str = "SPY"
    fee_pct: float | None = Field(default=None, ge=0, le=5)


class StrategySave(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    code: str = Field(max_length=50_000)
    symbol: str | None = None


class PortfolioRequest(BaseModel):
    symbols: list[str] = Field(min_length=2, max_length=10)
    years: int = Field(default=5, ge=2, le=10)
    rebalance: Literal["daily", "weekly", "monthly", "quarterly", "never"] = "monthly"
    split: float = Field(default=0.5, ge=0.3, le=0.8)
    target_vol_pct: float | None = Field(default=None, ge=2, le=40)
    cost_pct: float = Field(default=0.05, ge=0, le=2)


class KellyRequest(BaseModel):
    symbol: str
    years: int = Field(default=5, ge=1, le=10)


class FactorRequest(BaseModel):
    target: str = "SPY"  # a symbol, "mix" (with weights) or "account"
    weights: dict[str, float] | None = None
    years: int = Field(default=5, ge=1, le=10)


class ApplyPortfolio(BaseModel):
    weights: dict[str, float]
    invest_pct: float = Field(default=0.9, ge=0.05, le=1.0)
    fit_limits: bool = False


class MLRequest(BaseModel):
    symbol: str = "SPY"
    model: Literal["logistic", "boosting"] = "boosting"
    horizon: Literal[1, 5, 20] = 5
    years: int = Field(default=10, ge=4, le=15)
    shuffle: bool = False
    leak: bool = False
    threshold: float = Field(default=0.5, ge=0.3, le=0.7)


class FilingRequest(BaseModel):
    ticker: str
    accession: str
    anonymous: bool = False


class MemoryRequest(BaseModel):
    ticker: str
    date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")


class StudyRequest(BaseModel):
    ticker: str
    count: int = Field(default=6, ge=3, le=12)
    horizon: int = Field(default=5, ge=1, le=20)
    anonymous: bool = True


class AgentUpdate(BaseModel):
    enabled: bool | None = None
    mode: Literal["approve", "auto"] | None = None
    watchlist: list[str] | None = None
    strategy: dict[str, Any] | None = None
    interval_min: int | None = None
    risk_pct: float | None = None
    use_research: bool | None = None


class Decision(BaseModel):
    approve: bool


class KillRequest(BaseModel):
    sell_positions: bool = False


class CapstoneStart(BaseModel):
    plan: str = Field(max_length=4000)


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


# ---------------------------------------------------------------------- AI settings (local first, cloud optional)
@app.get("/api/ai/settings")
async def ai_settings():
    return engine.llm.public()


@app.put("/api/ai/settings")
async def ai_settings_update(body: AISettings):
    return engine.llm.update(body.model_dump(exclude_none=True))


# ---------------------------------------------------------------------- Code Lab (levels 8-9)
def _exercises() -> list[dict]:
    done = set(engine.learn.p["sets"].get("ex8", [])) | set(engine.learn.p["sets"].get("ex9", []))
    return [{**{k: e[k] for k in ("id", "level", "title", "task", "starter", "hints")}, "done": e["id"] in done}
            for e in codelab.EXERCISES]


@app.get("/api/code/exercises")
async def code_exercises():
    return {"exercises": _exercises(), "strategy_template": codelab.STRATEGY_TEMPLATE,
            "sandboxed": bool(codelab.SANDBOX), "symbols": engine.symbols}


@app.post("/api/code/run")
async def code_run(body: CodeRun):
    result = await codelab.run_code(body.code, symbols=engine.symbols)
    engine.learn.record("code_run")
    return result


@app.post("/api/code/check")
async def code_check(body: CodeCheck):
    ex = codelab.EXERCISE_BY_ID.get(body.exercise_id)
    if not ex:
        raise KeyError(f"Unknown exercise '{body.exercise_id}'")
    result = await codelab.run_code(body.code, extra=codelab.check_harness(ex), symbols=engine.symbols)
    engine.learn.record("code_run")
    passed = bool(result["check"] and result["check"]["pass"])
    if passed:
        engine.learn.record("ex8" if ex["level"] == 8 else "ex9", ex["id"])
    result["passed"] = passed
    return result


CODE_SYSTEM = (
    "You are a patient Python pair-programmer inside a course that teaches a beginner to become a quant trader. "
    "They are on level {level}. They write pandas code with a helper module `qp`: qp.prices(symbol, years) returns a "
    "DataFrame of daily open/high/low/close/volume indexed by date; qp.chart(series_or_df_or_dict, title) draws lines; "
    "qp.table(df) shows a table; qp.symbols() lists symbols. Available libraries: pandas, numpy, scikit-learn. There is "
    "no internet. Explain in plain English, short sentences, no jargon without a 5-word definition. Mode: {mode}. "
    "explain = explain what the code or error means. hint = give 1-3 hints that lead them to the answer WITHOUT writing "
    "the solution. fix = return corrected full code. ask = answer their question. Never claim a strategy will make money. "
    "If the code peeks at the future (e.g. shift(-1) in a signal), point it out. Respond with ONLY JSON: "
    '{{"explanation": "...", "hints": ["..."], "code": "full corrected code, or null"}}'
)


@app.post("/api/code/assist")
async def code_assist(body: CodeAssist):
    task = ""
    if body.exercise_id and body.exercise_id in codelab.EXERCISE_BY_ID:
        task = f"Exercise: {codelab.EXERCISE_BY_ID[body.exercise_id]['task']}\n"
    mode = body.mode
    if body.exercise_id and mode == "fix":
        mode = "hint"  # exercises are for learning: hints, not answers
    if not engine.llm.enabled:
        engine.learn.record("code_assist")
        return codelab.rule_assist(body.code, body.error, "", codelab.EXERCISE_BY_ID.get(body.exercise_id or ""),
                                   mode, body.question)
    user = (f"{task}Their question: {body.question or '(none)'}\n\nCODE:\n{body.code[:12000]}\n\n"
            f"OUTPUT:\n{body.output[:3000]}\n\nERROR:\n{body.error[:3000] or '(none)'}")
    reply = await engine.llm.chat("code", [{"role": "system", "content": CODE_SYSTEM.format(level=engine.learn.level(), mode=mode)},
                                           {"role": "user", "content": user}], max_tokens=1800, temperature=0.2)
    j = reply["json"] or {}
    engine.learn.record("code_assist")
    code = j.get("code") if mode == "fix" and isinstance(j.get("code"), str) else None
    return {"explanation": str(j.get("explanation", ""))[:3000], "hints": [str(h)[:400] for h in (j.get("hints") or [])][:4],
            "code": code, "mode": mode, "source": reply["source"], "cost_usd": reply["cost_usd"]}


@app.post("/api/code/strategy/evaluate")
async def code_strategy_evaluate(body: StrategyEval):
    symbol = engine.check_symbol(body.symbol)
    fee = body.fee_pct / 100 if body.fee_pct is not None else assets.fee_rate(symbol, settings.fee_rate)
    result = await codelab.evaluate_strategy(body.code, symbol, fee, settings.slippage_bps)
    engine.learn.record("code_run")
    if result["ok"]:
        engine.learn.record("custom_backtest")
        if not result["lookahead"]:
            engine.learn.record("lookahead_clean")
        outs = result["out_sample"]["metrics"]
        if outs["num_trades"] and outs["total_return_pct"] > outs["buy_hold_return_pct"]:
            engine.learn.record("custom_oos_beat")
    return result


@app.get("/api/code/strategies")
async def code_strategies():
    return engine.custom_strategies()


@app.post("/api/code/strategies")
async def code_strategy_save(body: StrategySave):
    return engine.save_custom_strategy(body.name, body.code, body.symbol)


@app.delete("/api/code/strategies/{name}")
async def code_strategy_delete(name: str):
    return engine.delete_custom_strategy(name)


# ---------------------------------------------------------------------- portfolios and factors (level 10)
@app.post("/api/lab/portfolio")
async def lab_portfolio(body: PortfolioRequest):
    symbols = list(dict.fromkeys(engine.check_symbol(s) for s in body.symbols))
    series = await prices.many_daily(symbols, max(_daily_bars(s, body.years) for s in symbols))
    result = await asyncio.to_thread(portfolio.compare, series, None, body.split, body.rebalance, body.cost_pct / 100,
                                     body.target_vol_pct / 100 if body.target_vol_pct else None)
    engine.learn.record("portfolio_compare")
    if body.target_vol_pct:
        engine.learn.record("vol_target")
    return result


@app.post("/api/lab/kelly")
async def lab_kelly(body: KellyRequest):
    symbol = engine.check_symbol(body.symbol)
    data = await prices.cached_history(symbol, "1d", _daily_bars(symbol, body.years))
    engine.learn.record("kelly_run")
    return await asyncio.to_thread(portfolio.kelly, data, symbol)


@app.post("/api/lab/factors")
async def lab_factors(body: FactorRequest):
    bars = 252 * body.years + 1
    factors = await prices.many_daily(portfolio.FACTOR_ETFS, bars)
    if body.target == "account":
        daily = _daily_equity(engine.db.query("SELECT ts, equity FROM equity ORDER BY ts"))
        if len(daily) < 30:
            raise ValueError(f"Your paper account has {len(daily)} days of history. Factor analysis needs at least 30.")
        target = [{"time": int(datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()), "close": v}
                  for d, v in daily]
        name = "Your paper account"
    elif body.target == "mix":
        weights = {engine.check_symbol(s): float(w) for s, w in (body.weights or {}).items() if float(w) > 0}
        if len(weights) < 1:
            raise ValueError("Add some weights for the mix.")
        series = await prices.many_daily(list(weights), bars)
        times, closes = quant.align(series)
        tot = sum(weights.values())
        r = sum((closes[s][1:] / closes[s][:-1] - 1) * (w / tot) for s, w in weights.items())
        value = np.concatenate([[1.0], np.cumprod(1 + r)])
        target = [{"time": t, "close": float(v)} for t, v in zip(times, value)]
        name = "Your mix"
    else:
        sym = engine.check_symbol(body.target)
        target = await prices.cached_history(sym, "1d", _daily_bars(sym, body.years))
        name = assets.name(sym)
    result = await asyncio.to_thread(portfolio.factor_breakdown, factors, name, target)
    engine.learn.record("factor_run")
    return result


@app.post("/api/portfolio/apply")
async def portfolio_apply(body: ApplyPortfolio):
    return await engine.apply_portfolio(body.weights, body.invest_pct, body.fit_limits)


@app.get("/api/portfolio")
async def portfolio_current():
    return engine.db.get_kv("portfolio") or {}


# ---------------------------------------------------------------------- machine learning (level 11)
@app.post("/api/lab/ml")
async def lab_ml(body: MLRequest):
    symbol = engine.check_symbol(body.symbol)
    data = await prices.cached_history(symbol, "1d", _daily_bars(symbol, body.years))
    fee = assets.fee_rate(symbol, settings.fee_rate) + settings.slippage_bps / 10_000
    result = await asyncio.to_thread(ml.train, data, symbol, body.model, body.horizon, 0.7, body.shuffle, body.leak,
                                     fee, body.threshold)
    engine.learn.record("ml_train")
    if body.shuffle:
        engine.learn.record("ml_shuffle")
    if body.leak:
        engine.learn.record("ml_leak")
    if result.get("beat_hold") and not body.leak and not body.shuffle:
        engine.learn.record("ml_beat_hold")
    return result


# ---------------------------------------------------------------------- research (level 12)
@app.get("/api/research/filings/{ticker}")
async def research_filings(ticker: str):
    from app.research import filings

    return await filings(ticker, limit=25)


@app.post("/api/research/filing")
async def research_filing(body: FilingRequest):
    result = await engine.researcher.analyse_filing(body.ticker, body.accession, body.anonymous)
    engine.learn.record("research_filing")
    return result


@app.get("/api/research/news/{ticker}")
async def research_news(ticker: str):
    result = await engine.researcher.news(engine.check_symbol(ticker))
    engine.learn.record("research_news")
    return result


@app.post("/api/research/memory")
async def research_memory(body: MemoryRequest):
    result = await engine.researcher.memory_test(body.ticker.upper(), body.date)
    engine.learn.record("research_memory")
    return result


@app.post("/api/research/study")
async def research_study(body: StudyRequest):
    result = await engine.researcher.event_study(body.ticker, body.count, body.horizon, body.anonymous)
    engine.learn.record("research_study", "anon" if body.anonymous else "named")
    return result


# ---------------------------------------------------------------------- agent + capstone (level 13)
@app.get("/api/agent")
async def agent_state():
    return engine.agent.state()


@app.put("/api/agent")
async def agent_update(body: AgentUpdate):
    return engine.agent.update(body.model_dump(exclude_none=True))


@app.post("/api/agent/run")
async def agent_run():
    await engine.agent.run_once(manual=True)
    return engine.agent.state()


@app.post("/api/agent/proposals/{proposal_id}")
async def agent_decide(proposal_id: int, body: Decision):
    await engine.agent.decide(proposal_id, body.approve)
    return engine.agent.state()


@app.post("/api/agent/kill")
async def agent_kill(body: KillRequest):
    return await engine.agent.kill(body.sell_positions)


@app.get("/api/capstone")
async def capstone_state():
    return {"status": engine.capstone.status(), "days_needed": 28}


@app.post("/api/capstone/start")
async def capstone_start(body: CapstoneStart):
    return {"status": await engine.capstone.start(body.plan), "days_needed": 28}


@app.post("/api/capstone/report")
async def capstone_report():
    return await engine.capstone.report()


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
