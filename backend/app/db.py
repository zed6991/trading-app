"""SQLite persistence. All access happens on the single asyncio thread, so one connection is enough."""
import json
import sqlite3
import time
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS account (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    cash REAL NOT NULL,
    starting_cash REAL NOT NULL,
    realized_pnl REAL NOT NULL DEFAULT 0,
    fees_paid REAL NOT NULL DEFAULT 0,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS positions (
    symbol TEXT PRIMARY KEY,
    qty REAL NOT NULL,
    avg_price REAL NOT NULL,
    stop_loss REAL,
    take_profit REAL,
    high_water REAL,
    strategy TEXT,
    opened_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    qty REAL NOT NULL,
    price REAL NOT NULL,
    fee REAL NOT NULL,
    pnl REAL,
    reason TEXT,
    strategy TEXT
);
CREATE INDEX IF NOT EXISTS idx_trades_ts ON trades(ts);
CREATE TABLE IF NOT EXISTS equity (
    ts REAL PRIMARY KEY,
    equity REAL NOT NULL,
    cash REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    level TEXT NOT NULL,
    message TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS analyses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    symbol TEXT NOT NULL,
    source TEXT NOT NULL,
    bias TEXT NOT NULL,
    confidence REAL NOT NULL,
    payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS kv (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class Database:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        return self.conn.execute(sql, params)

    def query(self, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    def one(self, sql: str, params: tuple = ()) -> dict[str, Any] | None:
        row = self.conn.execute(sql, params).fetchone()
        return dict(row) if row else None

    def get_kv(self, key: str, default: Any = None) -> Any:
        row = self.one("SELECT value FROM kv WHERE key = ?", (key,))
        return json.loads(row["value"]) if row else default

    def set_kv(self, key: str, value: Any) -> None:
        self.execute(
            "INSERT INTO kv(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, json.dumps(value)),
        )

    def log(self, level: str, message: str) -> dict[str, Any]:
        ts = time.time()
        self.execute("INSERT INTO logs(ts, level, message) VALUES(?, ?, ?)", (ts, level, message))
        return {"ts": ts, "level": level, "message": message}
