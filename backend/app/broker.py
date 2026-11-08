"""Paper broker: long-only spot accounting with fees, slippage, brackets and risk limits.

Accounting identities (exact, no rounding):
  buy:  cash -= qty * fill + fee,               fee = qty * fill * fee_rate
  sell: cash += qty * fill - fee,               pnl = (qty * fill - fee) - (qty * avg_price + entry_fee)
  equity = cash + sum(qty * mark) = starting_cash + realized_pnl + unrealized_pnl
"""
from __future__ import annotations

import math
import time
from dataclasses import asdict, dataclass, fields, replace
from datetime import datetime, timezone
from typing import Any

from app.config import settings

_EPS = 1e-9


class RiskError(Exception):
    """An order was rejected by a risk rule or account state."""


def _utc_day(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")


@dataclass
class RiskLimits:
    """Risk settings. All percentages are fractions; 0 disables SL/TP/trailing/daily-loss."""

    max_position_pct: float = 0.2
    max_open_positions: int = 3
    stop_loss_pct: float = 0.02
    take_profit_pct: float = 0.04
    trailing_stop_pct: float = 0.0
    daily_loss_limit_pct: float = 0.05

    def __post_init__(self) -> None:
        self._validate()

    def _validate(self) -> None:
        if not (0 < self.max_position_pct <= 1):
            raise ValueError("max_position_pct must be in (0, 1]")
        if self.max_open_positions < 1:
            raise ValueError("max_open_positions must be >= 1")
        for key in ("stop_loss_pct", "trailing_stop_pct", "daily_loss_limit_pct"):
            if not (0 <= getattr(self, key) < 1):
                raise ValueError(f"{key} must be in [0, 1)")
        if not (0 <= self.take_profit_pct <= 10):
            raise ValueError("take_profit_pct must be in [0, 10]")

    @classmethod
    def from_settings(cls) -> RiskLimits:
        """Defaults from app.config.settings."""
        return cls(
            max_position_pct=settings.max_position_pct,
            max_open_positions=settings.max_open_positions,
            stop_loss_pct=settings.stop_loss_pct,
            take_profit_pct=settings.take_profit_pct,
            trailing_stop_pct=settings.trailing_stop_pct,
            daily_loss_limit_pct=settings.daily_loss_limit_pct,
        )

    def to_dict(self) -> dict:
        return asdict(self)

    def update(self, changes: dict) -> RiskLimits:
        """Return a new validated RiskLimits with `changes` applied (None values are ignored). ValueError if invalid."""
        valid = {f.name for f in fields(self)}
        unknown = set(changes) - valid
        if unknown:
            raise ValueError(f"unknown risk fields: {sorted(unknown)}")
        coerced: dict[str, Any] = {}
        for key, value in changes.items():
            if value is None:
                continue
            try:
                number = float(value)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{key} must be numeric") from exc
            if not math.isfinite(number):
                raise ValueError(f"{key} must be finite")
            if key == "max_open_positions":
                if not number.is_integer():
                    raise ValueError("max_open_positions must be an integer")
                coerced[key] = int(number)
            else:
                coerced[key] = number
        return replace(self, **coerced)


@dataclass
class Position:
    """An open long position. `entry_fee` is the fee paid on entry (used for exact pnl)."""

    symbol: str
    qty: float
    avg_price: float
    stop_loss: float | None
    take_profit: float | None
    high_water: float
    strategy: str | None
    opened_at: float
    entry_fee: float = 0.0


class PaperBroker:
    """Simulated spot broker. Not thread-safe; use from a single thread / event loop."""

    def __init__(self, cash: float, fee_rate: float, slippage_bps: float, limits: RiskLimits) -> None:
        if cash <= 0:
            raise ValueError("cash must be positive")
        if fee_rate < 0 or slippage_bps < 0:
            raise ValueError("fee_rate and slippage_bps must be >= 0")
        self.fee_rate = float(fee_rate)
        self.slippage_bps = float(slippage_bps)
        self.limits = limits
        self.reset(cash)

    # ------------------------------------------------------------------ state

    def reset(self, cash: float | None = None) -> None:
        """Flatten everything (no fills) and restart with `cash` (default: previous starting cash)."""
        start = float(cash) if cash is not None else self.starting_cash
        if start <= 0:
            raise ValueError("cash must be positive")
        self.cash: float = start
        self.starting_cash: float = start
        self.realized_pnl: float = 0.0
        self.fees_paid: float = 0.0
        self.positions: dict[str, Position] = {}
        self.halted: bool = False
        self.halt_reason: str | None = None
        self.day_start_equity: float = start
        self.day: str = _utc_day(time.time())
        self._marks: dict[str, float] = {}

    def resume(self) -> None:
        """Clear a halt and re-base the daily loss limit on current equity."""
        self.halted = False
        self.halt_reason = None
        self.day_start_equity = self.equity({})

    @property
    def _slip(self) -> float:
        return self.slippage_bps / 10_000.0

    def _mark(self, prices: dict[str, float]) -> None:
        for sym, px in prices.items():
            if px is not None and px > 0:
                self._marks[sym] = float(px)

    def _price_of(self, symbol: str, prices: dict[str, float]) -> float:
        px = prices.get(symbol)
        if px is not None and px > 0:
            return float(px)
        return self._marks.get(symbol, self.positions[symbol].avg_price)

    def equity(self, prices: dict[str, float]) -> float:
        """Cash plus positions marked at `prices` (falls back to last seen price, then entry price)."""
        self._mark(prices)
        return self.cash + sum(p.qty * self._price_of(s, prices) for s, p in self.positions.items())

    # ------------------------------------------------------------------ orders

    def open_long(
        self,
        symbol: str,
        price: float,
        ts: float,
        strategy: str | None = None,
        qty: float | None = None,
        reason: str = "signal",
    ) -> dict:
        """Buy `qty` (default: max_position_pct of equity, capped by cash incl. fee). RiskError if rejected."""
        if not (price > 0 and math.isfinite(price)):
            raise RiskError("invalid price")
        if self.halted:
            raise RiskError(f"trading halted: {self.halt_reason}")
        if symbol in self.positions:
            raise RiskError(f"position already open in {symbol}")
        if len(self.positions) >= self.limits.max_open_positions:
            raise RiskError(f"max open positions ({self.limits.max_open_positions}) reached")
        fill = price * (1 + self._slip)
        max_notional = self.limits.max_position_pct * self.equity({symbol: price})
        if qty is None:
            affordable = self.cash / (fill * (1 + self.fee_rate))
            qty = min(max_notional / price, affordable)
        else:
            qty = float(qty)
            if qty * price > max_notional * 1.0001:
                raise RiskError(
                    f"order notional {qty * price:.2f} exceeds max position size {max_notional:.2f}"
                )
        if not (qty > 0 and math.isfinite(qty)):
            raise RiskError("order quantity must be positive")
        notional = qty * fill
        fee = notional * self.fee_rate
        cost = notional + fee
        if cost > self.cash * (1 + _EPS):
            raise RiskError(f"insufficient cash: need {cost:.2f}, have {self.cash:.2f}")
        self.cash -= cost
        if abs(self.cash) < _EPS * max(1.0, self.starting_cash):
            self.cash = 0.0
        self.fees_paid += fee
        lim = self.limits
        self.positions[symbol] = Position(
            symbol=symbol,
            qty=qty,
            avg_price=fill,
            stop_loss=fill * (1 - lim.stop_loss_pct) if lim.stop_loss_pct > 0 else None,
            take_profit=fill * (1 + lim.take_profit_pct) if lim.take_profit_pct > 0 else None,
            high_water=fill,
            strategy=strategy,
            opened_at=float(ts),
            entry_fee=fee,
        )
        return self._fill(ts, symbol, "buy", qty, fill, fee, None, reason, strategy)

    def close(self, symbol: str, price: float, ts: float, reason: str = "signal") -> dict:
        """Sell the whole position at `price` less slippage. RiskError if there is no position."""
        pos = self.positions.get(symbol)
        if pos is None:
            raise RiskError(f"no open position in {symbol}")
        if not (price > 0 and math.isfinite(price)):
            raise RiskError("invalid price")
        fill = price * (1 - self._slip)
        notional = pos.qty * fill
        fee = notional * self.fee_rate
        pnl = (notional - fee) - (pos.qty * pos.avg_price + pos.entry_fee)
        self.cash += notional - fee
        self.fees_paid += fee
        self.realized_pnl += pnl
        del self.positions[symbol]
        self._marks[symbol] = float(price)
        return self._fill(ts, symbol, "sell", pos.qty, fill, fee, pnl, reason, pos.strategy)

    def check_exits(
        self, symbol: str, high: float, low: float, close: float, ts: float, open_price: float | None = None
    ) -> list[dict]:
        """Update the trailing stop from `high`, then exit at the stop (priority) or take-profit level.

        If the bar gaps through a level (`open_price` beyond it, or a live tick with high == low == close beyond it)
        the exit uses that worse/better price instead of the level.
        """
        self._mark({symbol: close})
        pos = self.positions.get(symbol)
        if pos is None:
            return []
        pos.high_water = max(pos.high_water, high)
        trailing = False
        if self.limits.trailing_stop_pct > 0:
            trail = pos.high_water * (1 - self.limits.trailing_stop_pct)
            if pos.stop_loss is None or trail > pos.stop_loss:
                pos.stop_loss = trail
            trailing = math.isclose(pos.stop_loss, trail)
        ref = open_price if open_price is not None else (close if high == low == close else None)
        if pos.stop_loss is not None and low <= pos.stop_loss:
            px = min(pos.stop_loss, ref) if ref is not None else pos.stop_loss
            return [self.close(symbol, px, ts, reason="trailing_stop" if trailing else "stop_loss")]
        if pos.take_profit is not None and high >= pos.take_profit:
            px = max(pos.take_profit, ref) if ref is not None else pos.take_profit
            return [self.close(symbol, px, ts, reason="take_profit")]
        return []

    def set_brackets(self, symbol: str, stop_loss: float | None, take_profit: float | None) -> dict:
        """Replace a position's stop-loss / take-profit (None removes). Returns the position dict."""
        pos = self.positions.get(symbol)
        if pos is None:
            raise RiskError(f"no open position in {symbol}")
        for label, value in (("stop_loss", stop_loss), ("take_profit", take_profit)):
            if value is not None and not (value > 0 and math.isfinite(value)):
                raise RiskError(f"{label} must be a positive number")
        if stop_loss is not None and take_profit is not None and stop_loss >= take_profit:
            raise RiskError("stop_loss must be below take_profit")
        pos.stop_loss = float(stop_loss) if stop_loss is not None else None
        pos.take_profit = float(take_profit) if take_profit is not None else None
        return self._position_dict(pos, self._price_of(symbol, {}))

    # ------------------------------------------------------------------ risk

    def update_day(self, ts: float, prices: dict[str, float]) -> None:
        """Roll the UTC trading day and enforce the daily loss limit."""
        eq = self.equity(prices)
        day = _utc_day(ts)
        if day != self.day:
            self.day = day
            self.day_start_equity = eq
            if self.halted and self.halt_reason and self.halt_reason.startswith("daily loss"):
                self.halted = False
                self.halt_reason = None
        limit = self.limits.daily_loss_limit_pct
        if limit > 0 and not self.halted and eq < self.day_start_equity * (1 - limit):
            self.halted = True
            self.halt_reason = (
                f"daily loss limit {limit * 100:.2f}% hit "
                f"(equity {eq:.2f} vs day start {self.day_start_equity:.2f})"
            )

    # ------------------------------------------------------------------ reporting

    def _fill(self, ts, symbol, side, qty, price, fee, pnl, reason, strategy) -> dict:
        return {
            "ts": float(ts), "symbol": symbol, "side": side, "qty": qty, "price": price, "fee": fee,
            "pnl": pnl, "reason": reason, "strategy": strategy,
        }

    def _position_dict(self, pos: Position, mark: float) -> dict:
        cost = pos.qty * pos.avg_price + pos.entry_fee
        market_value = pos.qty * mark
        upnl = market_value - cost
        return {
            "symbol": pos.symbol, "qty": pos.qty, "avg_price": pos.avg_price, "mark": mark,
            "market_value": market_value, "unrealized_pnl": upnl, "pnl_pct": upnl / cost * 100 if cost else 0.0,
            "stop_loss": pos.stop_loss, "take_profit": pos.take_profit, "strategy": pos.strategy,
            "opened_at": pos.opened_at,
        }

    def snapshot(self, prices: dict[str, float]) -> dict:
        """Account summary for the API/WebSocket."""
        self._mark(prices)
        positions = [self._position_dict(p, self._price_of(s, prices)) for s, p in self.positions.items()]
        equity = self.cash + sum(p["market_value"] for p in positions)
        return {
            "cash": self.cash,
            "equity": equity,
            "starting_cash": self.starting_cash,
            "realized_pnl": self.realized_pnl,
            "unrealized_pnl": sum(p["unrealized_pnl"] for p in positions),
            "fees_paid": self.fees_paid,
            "total_return_pct": (equity / self.starting_cash - 1) * 100,
            "halted": self.halted,
            "halt_reason": self.halt_reason,
            "day_start_equity": self.day_start_equity,
            "positions": positions,
        }

    def dump(self) -> dict:
        """JSON-serialisable full state."""
        return {
            "cash": self.cash,
            "starting_cash": self.starting_cash,
            "realized_pnl": self.realized_pnl,
            "fees_paid": self.fees_paid,
            "positions": [asdict(p) for p in self.positions.values()],
            "limits": self.limits.to_dict(),
            "halted": self.halted,
            "halt_reason": self.halt_reason,
            "day_start_equity": self.day_start_equity,
            "day": self.day,
            "marks": dict(self._marks),
        }

    def load(self, state: dict) -> None:
        """Restore state produced by `dump()`. Fee rate and slippage keep their configured values."""
        self.cash = float(state["cash"])
        self.starting_cash = float(state["starting_cash"])
        self.realized_pnl = float(state.get("realized_pnl", 0.0))
        self.fees_paid = float(state.get("fees_paid", 0.0))
        names = {f.name for f in fields(Position)}
        self.positions = {}
        for raw in state.get("positions", []):
            data = {k: v for k, v in raw.items() if k in names}
            data.setdefault("entry_fee", data["qty"] * data["avg_price"] * self.fee_rate)
            data.setdefault("high_water", data["avg_price"])
            pos = Position(**data)
            self.positions[pos.symbol] = pos
        if "limits" in state:
            known = {f.name for f in fields(RiskLimits)}
            self.limits = RiskLimits(**{k: v for k, v in state["limits"].items() if k in known})
        self.halted = bool(state.get("halted", False))
        self.halt_reason = state.get("halt_reason")
        self.day_start_equity = float(state.get("day_start_equity", self.cash))
        self.day = state.get("day") or _utc_day(time.time())
        self._marks = {k: float(v) for k, v in state.get("marks", {}).items()}
