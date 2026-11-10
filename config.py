"""Configuration with strict validation. Secrets only in local .env."""
from dataclasses import dataclass
from pathlib import Path
import os
import re
from urllib.parse import urlparse

from dotenv import load_dotenv

load_dotenv()
ROOT = Path(__file__).resolve().parent


def _env_bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in ("true", "1", "yes", "on")


@dataclass(frozen=True)
class Config:
    symbols: tuple[str, ...]
    webhook: str
    console_only: bool
    window_seconds: int
    pressure_threshold: float
    min_trades: int
    cooldown_seconds: int
    evaluation_seconds: int
    confirmations: int
    neutral_windows: int
    min_confirming_factors: int
    min_volume_ratio: float
    rest_url: str
    ws_base: str
    data_dir: Path
    dashboard_host: str
    dashboard_port: int
    large_trade_min_usdt: float = 100000.0
    mtf_gate_enabled: bool = False
    replay_retention_days: int = 14

    @classmethod
    def from_env(cls) -> "Config":
        symbols = tuple(dict.fromkeys(s.strip().upper() for s in os.getenv("SYMBOLS", "BTCUSDT,ETHUSDT").split(",") if s.strip()))
        if not symbols or len(symbols) > 10 or any(not re.fullmatch(r"[A-Z0-9]{5,20}", s) for s in symbols):
            raise ValueError("SYMBOLS: provide 1-10 valid market pairs, e.g. BTCUSDT,ETHUSDT")
        webhook = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
        console_only = _env_bool("CONSOLE_ONLY")
        if not webhook and not console_only:
            raise ValueError("Set DISCORD_WEBHOOK_URL in .env, or set CONSOLE_ONLY=true for testing")
        if webhook:
            parts = urlparse(webhook)
            if parts.scheme != "https" or parts.hostname not in {"discord.com", "discordapp.com", "canary.discord.com", "ptb.discord.com"} or not re.fullmatch(r"/api/webhooks/\d+/[A-Za-z0-9._-]+/?", parts.path):
                raise ValueError("DISCORD_WEBHOOK_URL must be a valid Discord webhook URL")
        root = Path(os.getenv("DATA_DIR", str(ROOT / "data"))).expanduser()
        conf = cls(
            symbols=symbols,
            webhook=webhook,
            console_only=console_only,
            window_seconds=int(os.getenv("WINDOW_SECONDS", "60")),
            pressure_threshold=float(os.getenv("PRESSURE_THRESHOLD", "0.65")),
            min_trades=int(os.getenv("MIN_TRADES", "100")),
            cooldown_seconds=int(os.getenv("COOLDOWN_SECONDS", "180")),
            evaluation_seconds=int(os.getenv("EVALUATION_SECONDS", "10")),
            confirmations=int(os.getenv("CONFIRMATIONS", "2")),
            neutral_windows=int(os.getenv("NEUTRAL_WINDOWS", "2")),
            min_confirming_factors=int(os.getenv("MIN_CONFIRMING_FACTORS", "2")),
            min_volume_ratio=float(os.getenv("MIN_VOLUME_RATIO", "0.65")),
            rest_url=os.getenv("BINANCE_REST_URL", "https://data-api.binance.vision").rstrip("/"),
            ws_base=os.getenv("BINANCE_WS_BASE", "wss://data-stream.binance.vision/stream?streams=").strip(),
            data_dir=root,
            dashboard_host=os.getenv("DASHBOARD_HOST", "127.0.0.1"),
            dashboard_port=int(os.getenv("DASHBOARD_PORT", "8765")),
            large_trade_min_usdt=float(os.getenv("LARGE_TRADE_MIN_USDT", "100000")),
            mtf_gate_enabled=_env_bool("MTF_GATE_ENABLED", False),
            replay_retention_days=int(os.getenv("REPLAY_RETENTION_DAYS", "14")),
        )
        if not (10 <= conf.window_seconds <= 300 and 0.5 < conf.pressure_threshold <= 0.95 and 1 <= conf.min_trades <= 100000):
            raise ValueError("Invalid WINDOW_SECONDS, PRESSURE_THRESHOLD, or MIN_TRADES")
        if not (1 <= conf.evaluation_seconds <= 60 and 0 <= conf.cooldown_seconds <= 86400 and 1 <= conf.confirmations <= 10 and 1 <= conf.neutral_windows <= 20):
            raise ValueError("Invalid evaluation, cooldown, confirmation or neutral-window settings")
        if not (1 <= conf.min_confirming_factors <= 4 and 0 <= conf.min_volume_ratio <= 10):
            raise ValueError("Invalid signal confirmation or volume settings")
        if not (1 <= conf.dashboard_port <= 65535 and conf.rest_url.startswith("https://") and conf.ws_base.startswith("wss://")):
            raise ValueError("Invalid endpoint or dashboard port")
        if not (conf.large_trade_min_usdt > 0 and conf.large_trade_min_usdt < 1e12 and 1 <= conf.replay_retention_days <= 90):
            raise ValueError("Invalid LARGE_TRADE_MIN_USDT or REPLAY_RETENTION_DAYS")
        return conf
