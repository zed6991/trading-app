"""Runtime configuration, read from environment variables (and an optional .env file)."""
import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load_dotenv() -> None:
    env_file = ROOT / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv()

DEFAULT_SYMBOLS = "SPY,QQQ,TLT,GLD,AAPL,MSFT,NVDA,AMZN,GOOGL,TSLA,JPM,KO,XOM,BTCUSDT,ETHUSDT,SOLUSDT"


def _env(key: str, default: str) -> str:
    return os.environ.get(key, default)


@dataclass(frozen=True)
class Settings:
    symbols: list[str] = field(
        default_factory=lambda: [s.strip().upper() for s in _env("SYMBOLS", DEFAULT_SYMBOLS).split(",") if s.strip()]
    )
    interval: str = _env("INTERVAL", "1m")
    history_bars: int = int(_env("HISTORY_BARS", "500"))
    binance_rest: str = _env("BINANCE_REST", "https://api.binance.com")
    binance_ws: str = _env("BINANCE_WS", "wss://stream.binance.com:9443")

    starting_cash: float = float(_env("STARTING_CASH", "100000"))
    fee_rate: float = float(_env("FEE_RATE", "0.001"))  # crypto; US stocks/ETFs have no commission
    slippage_bps: float = float(_env("SLIPPAGE_BPS", "2"))

    # Default risk limits (editable at runtime from the dashboard)
    max_position_pct: float = float(_env("MAX_POSITION_PCT", "0.20"))
    max_open_positions: int = int(_env("MAX_OPEN_POSITIONS", "3"))
    stop_loss_pct: float = float(_env("STOP_LOSS_PCT", "0.02"))
    take_profit_pct: float = float(_env("TAKE_PROFIT_PCT", "0.04"))
    trailing_stop_pct: float = float(_env("TRAILING_STOP_PCT", "0"))
    daily_loss_limit_pct: float = float(_env("DAILY_LOSS_LIMIT_PCT", "0.05"))

    ollama_url: str = _env("OLLAMA_URL", "http://localhost:11434")
    ollama_model: str = _env("OLLAMA_MODEL", "qwen3.6-moe-uncensored")
    ollama_timeout: float = float(_env("OLLAMA_TIMEOUT", "90"))

    db_path: Path = Path(_env("DB_PATH", str(ROOT / "data" / "trading.db")))
    frontend_dir: Path = ROOT / "frontend"


settings = Settings()
