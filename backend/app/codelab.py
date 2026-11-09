"""Code Lab: run the learner's Python in a sandbox, check exercises, and evaluate custom strategies.

Code runs in a separate process (`python -I`) with a time limit, in a fresh folder. On macOS it is wrapped in
`sandbox-exec`: no network, no reading the home folder (so no API keys or personal files), no writing outside the run
folder. Prices come from CSV files the app writes beforehand, read through a tiny `qp` helper module:

    import qp
    spy = qp.prices("SPY")          # pandas DataFrame: open, high, low, close, volume (daily, indexed by date)
    qp.chart(spy["close"], "SPY")   # line chart (Series, DataFrame or dict of Series)
    qp.table(spy.tail())            # table
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from app import assets, prices

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data" / "codelab"
TIMEOUT = 20.0
MAX_OUTPUT = 20_000
CHECK_MARK = "__QP_CHECK__"
RESULT_MARK = "__QP_RESULT__"
SANDBOX = shutil.which("sandbox-exec") if sys.platform == "darwin" else None
DATA_TTL = 6 * 3600

QP_MODULE = r'''
"""Quant Path helper: prices, charts and tables for the Code Lab."""
import json as _json, os as _os
import numpy as _np
import pandas as _pd

_DATA = _os.environ.get("QP_DATA", ".")
_OUT = open("qp_out.jsonl", "a")
_COUNT = {"chart": 0, "table": 0}
SYMBOLS = _os.environ.get("QP_SYMBOLS", "").split(",")


def symbols():
    """Every symbol you can load."""
    return list(SYMBOLS)


def prices(symbol, years=5):
    """Daily prices for `symbol` as a DataFrame with open, high, low, close, volume, indexed by date."""
    symbol = symbol.upper()
    path = _os.path.join(_DATA, symbol + ".csv")
    if not _os.path.exists(path):
        raise ValueError(f"No data for {symbol}. Try one of: {', '.join(SYMBOLS)}")
    df = _pd.read_csv(path, parse_dates=["date"], index_col="date")
    per_year = 365 if symbol.endswith("USDT") else 252
    return df.iloc[-int(years * per_year) - 1:]


def _points(s):
    s = _pd.Series(s).replace([_np.inf, -_np.inf], _np.nan).dropna()
    if len(s) > 1500:
        s = s.iloc[:: int(_np.ceil(len(s) / 1500))]
    out = []
    for k, v in s.items():
        t = int(_pd.Timestamp(k).timestamp()) if not isinstance(k, (int, _np.integer)) else int(k)
        out.append([t, float(v)])
    return out


def chart(data, title=""):
    """Draw a line chart of a Series, a DataFrame (one line per column) or a dict of Series."""
    if isinstance(data, _pd.DataFrame):
        series = {str(c): data[c] for c in data.columns}
    elif isinstance(data, dict):
        series = {str(k): v for k, v in data.items()}
    else:
        series = {str(getattr(data, "name", None) or "value"): data}
    payload = {"type": "chart", "title": str(title), "series": [{"name": k, "points": _points(v)} for k, v in series.items()]}
    _OUT.write(_json.dumps(payload) + "\n"); _OUT.flush()
    _COUNT["chart"] += 1


def table(df, title="", rows=30):
    """Show a table (the first `rows` rows)."""
    df = _pd.DataFrame(df).head(rows)
    cols = [str(c) for c in df.columns]
    body = [[str(i)] + [("" if _pd.isna(v) else (round(float(v), 6) if isinstance(v, (int, float, _np.number)) else str(v)))
                        for v in row] for i, row in zip(df.index, df.values.tolist())]
    _OUT.write(_json.dumps({"type": "table", "title": str(title), "columns": [""] + cols, "rows": body}) + "\n"); _OUT.flush()
    _COUNT["table"] += 1
'''

# ------------------------------------------------------------------------------------------ exercises
EXERCISES: list[dict] = [
    {
        "id": "ex-load", "level": 8, "title": "Load prices and read the last close",
        "task": "Load SPY with qp.prices and store the most recent closing price in a variable called last_close.",
        "starter": 'import qp\n\nspy = qp.prices("SPY")\nprint(spy.tail())\n\n# The last row\'s close. Hint: spy["close"].iloc[-1]\nlast_close = None\nprint("Last close:", last_close)\n',
        "check": 'ok = isinstance(last_close, (int, float)) and abs(float(last_close) - qp.prices("SPY")["close"].iloc[-1]) < 1e-6\n'
                 'msg = "Right: that\'s the latest close." if ok else "last_close should be the final value of the close column."',
    },
    {
        "id": "ex-returns", "level": 8, "title": "Daily returns with pct_change",
        "task": "Make a Series called returns holding SPY's daily returns (use .pct_change()), and print the average daily return.",
        "starter": 'import qp\n\nspy = qp.prices("SPY")\nclose = spy["close"]\n\n# Daily return = today\'s close / yesterday\'s close - 1\nreturns = None\n\nif returns is not None:\n    print("Average daily return:", returns.mean())\n    qp.chart(returns, "SPY daily returns")\n',
        "check": 'import pandas as pd\nc = qp.prices("SPY")["close"].pct_change()\n'
                 'ok = isinstance(returns, pd.Series) and (returns.dropna() - c.dropna()).abs().max() < 1e-9 and len(returns.dropna()) == len(c.dropna())\n'
                 'msg = "Correct. Each value is that day\'s change as a fraction (0.01 = 1%)." if ok else "returns should equal close.pct_change()."',
    },
    {
        "id": "ex-vol", "level": 8, "title": "Yearly volatility",
        "task": "Compute SPY's yearly volatility: the standard deviation of daily returns × √252. Store it in vol (as a fraction, e.g. 0.17).",
        "starter": 'import qp\nimport numpy as np\n\nreturns = qp.prices("SPY")["close"].pct_change().dropna()\n\nvol = None  # daily std × sqrt(252)\nprint("Yearly volatility:", vol)\n',
        "check": 'import numpy as np\nr = qp.prices("SPY")["close"].pct_change().dropna()\nwant = r.std() * np.sqrt(252)\n'
                 'ok = isinstance(vol, (int, float, np.floating)) and abs(float(vol) - want) / want < 0.01\n'
                 'msg = f"Correct: {want:.1%} a year." if ok else "Not quite. Take the standard deviation of the daily returns, then multiply by np.sqrt(252)."',
    },
    {
        "id": "ex-drawdown", "level": 8, "title": "Max drawdown in code",
        "task": "Compute SPY's max drawdown: the biggest fall from a running peak. Use cummax(). Store it in max_dd as a positive fraction (0.25 = 25%).",
        "starter": 'import qp\n\nclose = qp.prices("SPY")["close"]\npeak = close.cummax()          # highest price so far\ndrawdown = 1 - close / peak    # how far below that peak\n\nmax_dd = None\nprint("Max drawdown:", max_dd)\nqp.chart(-drawdown, "SPY drawdown")\n',
        "check": 'c = qp.prices("SPY")["close"]\nwant = float((1 - c / c.cummax()).max())\n'
                 'ok = isinstance(max_dd, (int, float)) and abs(abs(float(max_dd)) - want) < 1e-6\n'
                 'msg = f"Correct: {want:.1%}." if ok else "Not quite. Take the largest value of the drawdown series (drawdown.max())."',
    },
    {
        "id": "ex-rolling", "level": 8, "title": "Rolling volatility chart",
        "task": "Make a DataFrame called rolling_vol with 60-day rolling yearly volatility for SPY and BTCUSDT (columns named SPY and BTCUSDT), and draw it with qp.chart.",
        "starter": 'import qp\nimport numpy as np\nimport pandas as pd\n\ndef rolling_yearly_vol(symbol, days=60, per_year=252):\n    r = qp.prices(symbol)["close"].pct_change()\n    return r.rolling(days).std() * np.sqrt(per_year)\n\nrolling_vol = pd.DataFrame({\n    "SPY": rolling_yearly_vol("SPY"),\n    # add BTCUSDT here (crypto trades 365 days a year)\n})\nqp.chart(rolling_vol, "60-day volatility")\n',
        "check": 'import pandas as pd\nok = isinstance(rolling_vol, pd.DataFrame) and {"SPY", "BTCUSDT"} <= set(rolling_vol.columns) and qp._COUNT["chart"] >= 1\n'
                 'msg = "Nice. Notice how volatility itself moves over time." if ok else "rolling_vol needs SPY and BTCUSDT columns, and call qp.chart(rolling_vol)."',
    },
    {
        "id": "ex-positions", "level": 9, "title": "A signal as positions",
        "task": "Write positions(df) that returns 1 when the close is above its 50-day average and 0 otherwise.",
        "starter": 'import qp\n\ndef positions(df):\n    close = df["close"]\n    sma50 = close.rolling(50).mean()\n    # 1 when close > sma50, else 0 (as integers)\n    return None\n\nspy = qp.prices("SPY")\npos = positions(spy)\nif pos is not None:\n    print(pos.value_counts())\n',
        "check": 'd = qp.prices("SPY"); c = d["close"]; want = (c > c.rolling(50).mean()).astype(int)\ngot = positions(d)\n'
                 'ok = got is not None and (got.astype(int).iloc[60:] == want.iloc[60:]).all()\n'
                 'msg = "Correct: 1 means hold, 0 means stay in cash." if ok else "positions should be (close > close.rolling(50).mean()).astype(int)."',
    },
    {
        "id": "ex-shift", "level": 9, "title": "No look-ahead: shift the positions",
        "task": "A signal from today's close can only trade tomorrow. Make strat_returns = daily returns × positions shifted by 1 day.",
        "starter": 'import qp\n\nd = qp.prices("SPY")\nclose = d["close"]\npos = (close > close.rolling(50).mean()).astype(int)\nreturns = close.pct_change()\n\nstrat_returns = None   # returns * pos.shift(1)\n\nif strat_returns is not None:\n    growth = (1 + strat_returns.fillna(0)).cumprod()\n    hold = (1 + returns.fillna(0)).cumprod()\n    qp.chart({"strategy": growth, "buy & hold": hold}, "Growth of $1")\n',
        "check": 'd = qp.prices("SPY"); c = d["close"]; p = (c > c.rolling(50).mean()).astype(int)\nwant = c.pct_change() * p.shift(1)\n'
                 'ok = strat_returns is not None and (strat_returns.fillna(0) - want.fillna(0)).abs().max() < 1e-12\n'
                 'msg = "Correct. Without .shift(1) you\'d be trading on a close you couldn\'t know yet." if ok else "Use returns * pos.shift(1)."',
    },
    {
        "id": "ex-costs", "level": 9, "title": "Pay for every trade",
        "task": "Subtract costs: every time the position changes, pay 0.1%. Make net_returns = strat_returns − 0.001 × (number of position changes that day).",
        "starter": 'import qp\n\nd = qp.prices("SPY")\nclose = d["close"]\npos = (close > close.rolling(50).mean()).astype(int)\nheld = pos.shift(1).fillna(0)\nstrat_returns = close.pct_change().fillna(0) * held\n\ntrades = held.diff().abs().fillna(0)   # 1 on days you buy or sell\nnet_returns = None                     # strat_returns - 0.001 * trades\n\nprint("Trades:", int(trades.sum()))\nprint("Gross growth:", (1 + strat_returns).prod())\nif net_returns is not None:\n    print("Net growth:", (1 + net_returns).prod())\n',
        "check": 'd = qp.prices("SPY"); c = d["close"]; p = (c > c.rolling(50).mean()).astype(int); h = p.shift(1).fillna(0)\n'
                 'want = c.pct_change().fillna(0) * h - 0.001 * h.diff().abs().fillna(0)\n'
                 'ok = net_returns is not None and (net_returns - want).abs().max() < 1e-12\n'
                 'msg = "Correct. Costs grow with every extra trade." if ok else "net_returns = strat_returns - 0.001 * trades."',
    },
]
EXERCISE_BY_ID = {e["id"]: e for e in EXERCISES}

STRATEGY_TEMPLATE = '''import qp
import pandas as pd

def positions(df):
    """Return a Series of 1 (hold) or 0 (cash) for every day, using only data up to that day.
    df has columns open, high, low, close, volume. The app trades your position on the NEXT day's open."""
    close = df["close"]
    fast = close.rolling(20).mean()
    slow = close.rolling(100).mean()
    return (fast > slow).astype(int)

# Try it on one asset:
pos = positions(qp.prices("SPY"))
print(pos.tail())
'''


# ------------------------------------------------------------------------------------------ data
async def ensure_data(symbols: list[str], years: int = 6) -> Path:
    """Write daily CSVs for `symbols` into data/codelab (refreshed every few hours)."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    async def one(sym: str):
        path = DATA_DIR / f"{sym}.csv"
        if path.exists() and time.time() - path.stat().st_mtime < DATA_TTL:
            return
        candles = await prices.cached_history(sym, "1d", assets.periods_per_year(sym) * years + 1)
        lines = ["date,open,high,low,close,volume"]
        for c in candles:
            d = datetime.fromtimestamp(c["time"], tz=timezone.utc).strftime("%Y-%m-%d")
            lines.append(f"{d},{c['open']:.6f},{c['high']:.6f},{c['low']:.6f},{c['close']:.6f},{c['volume']:.2f}")
        tmp = path.with_suffix(".tmp")
        tmp.write_text("\n".join(lines) + "\n")
        tmp.replace(path)

    results = await asyncio.gather(*(one(s) for s in symbols), return_exceptions=True)
    missing = [s for s, r in zip(symbols, results) if isinstance(r, BaseException) and not (DATA_DIR / f"{s}.csv").exists()]
    if missing:
        raise ValueError(f"Couldn't load price data for {', '.join(missing)}. Try again in a minute.")
    return DATA_DIR


# ------------------------------------------------------------------------------------------ running code
def _profile(run_dir: Path) -> str:
    home = str(Path.home())
    venv = str(Path(sys.prefix).resolve())
    base = str(Path(sys.base_prefix).resolve())
    data = str(DATA_DIR.resolve())
    run = str(run_dir.resolve())
    return (f'(version 1)(allow default)(deny network*)'
            f'(deny file-read* (subpath "{home}"))(allow file-read-metadata)'
            f'(allow file-read* (subpath "{venv}") (subpath "{base}") (subpath "{run}") (subpath "{data}"))'
            f'(deny file-write*)(allow file-write* (subpath "{run}") (subpath "/dev"))')


def _clean_error(stderr: str) -> str:
    """Keep only the traceback lines that point at the learner's code."""
    lines = stderr.strip().splitlines()
    keep, skip = [], False
    for line in lines:
        m = re.match(r'\s*File "(.*)", line (\d+)(.*)', line)
        if m:
            skip = not m.group(1).endswith("main.py")
            if not skip:
                keep.append(f"  Your code, line {m.group(2)}{m.group(3).replace(', in <module>', '')}")
            continue
        if skip and line.startswith("    "):
            continue
        skip = False
        keep.append(line)
    return "\n".join(keep[-25:])


async def run_code(code: str, *, extra: str = "", timeout: float = TIMEOUT, symbols: list[str] | None = None) -> dict:
    """Run `code` (+ `extra` harness code) in the sandbox. Returns stdout, error, outputs and markers."""
    if len(code) > 50_000:
        raise ValueError("That code is too long (50,000 characters max).")
    symbols = symbols or [a["symbol"] for a in assets.ASSETS]
    await ensure_data(symbols)
    run_dir = Path(tempfile.mkdtemp(prefix="qp-run-"))
    try:
        (run_dir / "qp.py").write_text(QP_MODULE)
        user_lines = len(code.splitlines())
        (run_dir / "main.py").write_text(code + "\n" + extra)
        env = {"PATH": "/usr/bin:/bin", "QP_DATA": str(DATA_DIR), "QP_SYMBOLS": ",".join(symbols),
               "PYTHONPATH": str(run_dir), "MPLBACKEND": "Agg", "HOME": str(run_dir), "TMPDIR": str(run_dir),
               "OMP_NUM_THREADS": "2", "PYTHONDONTWRITEBYTECODE": "1"}
        cmd = [sys.executable, "-B", "main.py"]
        if SANDBOX:
            cmd = [SANDBOX, "-p", _profile(run_dir)] + cmd
        start = time.perf_counter()
        proc = await asyncio.create_subprocess_exec(*cmd, cwd=run_dir, env=env, stdout=asyncio.subprocess.PIPE,
                                                    stderr=asyncio.subprocess.PIPE)
        timed_out = False
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout)
        except asyncio.TimeoutError:
            proc.kill()
            out, err = await proc.communicate()
            timed_out = True
        duration = time.perf_counter() - start
        stdout = out.decode(errors="replace")
        stderr = err.decode(errors="replace")
        outputs = []
        side = run_dir / "qp_out.jsonl"
        if side.exists():
            for line in side.read_text().splitlines()[:40]:
                try:
                    outputs.append(json.loads(line))
                except ValueError:
                    pass
        markers = {}
        printed = []
        for line in stdout.splitlines():
            for mark in (CHECK_MARK, RESULT_MARK):
                if line.startswith(mark):
                    try:
                        markers[mark] = json.loads(line[len(mark):])
                    except ValueError:
                        pass
                    break
            else:
                printed.append(line)
        stdout = "\n".join(printed)
        error = None
        if timed_out:
            error = f"Your code ran for more than {timeout:.0f} seconds, so it was stopped. Look for a loop that never ends."
        elif proc.returncode != 0:
            error = _clean_error(stderr) or f"Your code stopped with exit code {proc.returncode}."
        return {"ok": error is None, "stdout": stdout[-MAX_OUTPUT:], "error": error, "outputs": outputs,
                "duration_ms": int(duration * 1000), "sandboxed": bool(SANDBOX), "user_lines": user_lines,
                "check": markers.get(CHECK_MARK), "result": markers.get(RESULT_MARK)}
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)


def check_harness(exercise: dict) -> str:
    body = "\n".join("    " + line for line in exercise["check"].splitlines())
    return ("\n# ---- checker (added by the app) ----\nimport json as __j, qp\ntry:\n" + body +
            f"\nexcept Exception as __e:\n    ok, msg = False, f\"The checker couldn't verify your answer: {{__e}}\"\n"
            f"print('{CHECK_MARK}' + __j.dumps({{'pass': bool(ok), 'msg': str(msg)}}))\n")


def strategy_harness(symbol: str, cut: float = 0.7) -> str:
    """Call the learner's positions(df) on full and truncated history and report positions + look-ahead check."""
    return f'''
# ---- strategy harness (added by the app) ----
import json as __j, qp as __qp, pandas as __pd
if "positions" not in globals():
    raise NameError("Define a function called positions(df) that returns 1 (hold) or 0 (cash) for each day.")
__df = __qp.prices("{symbol}", years=6)
__full = __pd.Series(positions(__df.copy()), index=__df.index).astype(float).fillna(0).clip(0, 1)
__peek = []
def __changed(__alt, __n):
    __pos = __pd.Series(positions(__alt.copy()), index=__alt.index).astype(float).fillna(0).clip(0, 1).iloc[:__n]
    __d = (__pos - __full.iloc[:__n]).abs()
    return str(__d[__d > 1e-9].index[0].date()) if (__d > 1e-9).any() else None
for __frac in (0.5, 0.65, 0.8, 0.9):
    __n = int(len(__df) * __frac)
    # 1) Cut off the future. 2) Mirror the future around the last known price, so any use of where prices go next
    # flips. Either way, positions up to the cut must not change.
    __mir = __df.copy()
    __p0 = float(__df["close"].iloc[__n - 1])
    for __c in ("open", "high", "low", "close"):
        __mir.iloc[__n:, __mir.columns.get_loc(__c)] = (2 * __p0 - __df[__c].iloc[__n:]).clip(lower=__p0 * 0.01)
    __mir.iloc[__n:, __mir.columns.get_loc("high")], __mir.iloc[__n:, __mir.columns.get_loc("low")] = (
        __mir["low"].iloc[__n:].to_numpy(), __mir["high"].iloc[__n:].to_numpy())
    for __alt in (__df.iloc[:__n], __mir):
        __hit = __changed(__alt, __n)
        if __hit and __hit not in __peek:
            __peek.append(__hit)
print("{RESULT_MARK}" + __j.dumps({{"dates": [str(d.date()) for d in __df.index], "positions": [float(x) for x in __full.round(4)],
                                   "lookahead": __peek}}))
'''


# ------------------------------------------------------------------------------------------ custom strategies
async def evaluate_strategy(code: str, symbol: str, fee_rate: float, slippage_bps: float, split: float = 0.7) -> dict:
    """Run the learner's positions(df) on `symbol`, then backtest it in-sample / out-of-sample and luck-test it."""
    import numpy as np

    from app.backtest import run_backtest
    from app.broker import RiskLimits
    from app.quant import luck_from_positions

    run = await run_code(code, extra=strategy_harness(symbol), symbols=[a["symbol"] for a in assets.ASSETS])
    if not run["ok"] or not run["result"]:
        return {"ok": False, "run": run}
    res = run["result"]
    candles = await prices.cached_history(symbol, "1d", assets.periods_per_year(symbol) * 6 + 1)
    by_day = {d: p for d, p in zip(res["dates"], res["positions"])}
    days = [datetime.fromtimestamp(c["time"], tz=timezone.utc).strftime("%Y-%m-%d") for c in candles]
    keep = [i for i, d in enumerate(days) if d in by_day]
    candles = [candles[i] for i in keep]
    target = np.array([by_day[days[i]] for i in keep], dtype=float)
    if len(candles) < 150:
        raise ValueError("Not enough matching history to test this strategy.")
    signals = ["buy" if t >= 0.5 else "sell" for t in target]
    limits = RiskLimits(max_position_pct=1.0, max_open_positions=1, stop_loss_pct=0, take_profit_pct=0,
                        trailing_stop_pct=0, daily_loss_limit_pct=0)
    bpy = assets.periods_per_year(symbol)
    cut = int(len(candles) * split)
    full = run_backtest(candles, "custom", None, limits, 10_000, fee_rate, slippage_bps, bpy, symbol, signals=signals)
    ins = run_backtest(candles[:cut], "custom", None, limits, 10_000, fee_rate, slippage_bps, bpy, symbol,
                       signals=signals[:cut])
    outs = run_backtest(candles, "custom", None, limits, 10_000, fee_rate, slippage_bps, bpy, symbol,
                        trade_from=cut, signals=signals)
    held = np.concatenate([[0.0], (target[:-1] >= 0.5).astype(float)])  # decided at close i, held during bar i+1
    try:
        luck = luck_from_positions(candles, held, fee_rate + slippage_bps / 10_000, 300)
    except ValueError as exc:
        luck = {"error": str(exc)}
    return {
        "ok": True, "run": {k: run[k] for k in ("stdout", "outputs", "duration_ms", "sandboxed")},
        "symbol": symbol, "lookahead": res["lookahead"], "split": split,
        "time_in_market_pct": round(float((target >= 0.5).mean() * 100), 1),
        "full": {"metrics": full["metrics"], "equity_curve": full["equity_curve"]},
        "in_sample": {"metrics": ins["metrics"], "start": int(candles[0]["time"]), "end": int(candles[cut - 1]["time"])},
        "out_sample": {"metrics": outs["metrics"], "equity_curve": outs["equity_curve"],
                       "start": int(candles[cut]["time"]), "end": int(candles[-1]["time"])},
        "luck": luck,
        "candles": [{"time": c["time"], "open": c["open"], "close": c["close"]} for c in candles],
    }
