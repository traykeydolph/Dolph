"""Configuration — loads all env vars and defines constants."""

import os
from dataclasses import dataclass, field
from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Config:
    # Discord
    discord_user_token: str = os.getenv("DISCORD_USER_TOKEN", "")
    discord_channel_grizzlies: str = os.getenv("DISCORD_CHANNEL_GRIZZLIES", "")
    discord_channel_waxui: str = os.getenv("DISCORD_CHANNEL_WAXUI", "")
    discord_channel_em: str = os.getenv("DISCORD_CHANNEL_EM", "")
    discord_channel_ecs: str = os.getenv("DISCORD_CHANNEL_ECS", "")
    discord_channel_eva: str = os.getenv("DISCORD_CHANNEL_EVA", "")
    discord_channel_nando: str = os.getenv("DISCORD_CHANNEL_NANDO", "")
    discord_channel_zabes: str = os.getenv("DISCORD_CHANNEL_ZABES", "")

    # Gemini
    gemini_api_key: str = os.getenv("GEMINI_API_KEY", "")

    # Alpaca
    alpaca_api_key: str = os.getenv("ALPACA_API_KEY", "")
    alpaca_secret_key: str = os.getenv("ALPACA_SECRET_KEY", "")
    alpaca_base_url: str = os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")

    # Coinbase (stub)
    coinbase_api_key: str = os.getenv("COINBASE_API_KEY", "")
    coinbase_secret_key: str = os.getenv("COINBASE_SECRET_KEY", "")

    # Telegram (outbound alerts)
    telegram_bot_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    telegram_chat_id: str = os.getenv("TELEGRAM_CHAT_ID", "")

    # Telegram (inbound signal monitoring — user account via Telethon)
    telegram_api_id: str = os.getenv("TELEGRAM_API_ID", "")
    telegram_api_hash: str = os.getenv("TELEGRAM_API_HASH", "")
    telegram_session_file: str = os.getenv("TELEGRAM_SESSION_FILE", "telegram_session")

    @property
    def telegram_signal_channels(self) -> list[str]:
        raw = os.getenv("TELEGRAM_SIGNAL_CHANNELS", "")
        return [ch.strip() for ch in raw.split(",") if ch.strip()]

    @property
    def telegram_channel_to_analyst(self) -> dict[str, str]:
        """Map tg_<channel_id> → analyst name. Configure via TELEGRAM_CHANNEL_ANALYSTS env."""
        # Format: "channel_id:analyst_name,channel_id2:analyst_name2"
        raw = os.getenv("TELEGRAM_CHANNEL_ANALYSTS", "")
        mapping = {}
        for pair in raw.split(","):
            if ":" in pair:
                ch, analyst = pair.strip().split(":", 1)
                mapping[f"tg_{ch.strip()}"] = analyst.strip()
        return mapping

    # Position sizing (legacy defaults — per-analyst overrides below)
    position_size_standard: float = float(os.getenv("POSITION_SIZE_STANDARD", "400"))
    position_size_lotto: float = float(os.getenv("POSITION_SIZE_LOTTO", "200"))
    position_size_crypto: float = float(os.getenv("POSITION_SIZE_CRYPTO", "10"))  # $10/play crypto Phase 1
    account_size: float = float(os.getenv("ACCOUNT_SIZE", "7500"))

    # Per-analyst contract targets (paper trading phase)
    # Enhanced Market: 2 contracts (~$535 avg)
    # Grizzlies options: 3 contracts (~$285 avg)
    # Waxui: 3 contracts (~$615 avg)
    contracts_enhanced_market: int = int(os.getenv("CONTRACTS_ENHANCED_MARKET", "1"))
    contracts_grizzlies: int = int(os.getenv("CONTRACTS_GRIZZLIES", "1"))
    contracts_waxui: int = int(os.getenv("CONTRACTS_WAXUI", "1"))
    contracts_eva: int = int(os.getenv("CONTRACTS_EVA", "1"))
    contracts_nando: int = int(os.getenv("CONTRACTS_NANDO", "1"))
    contracts_zabes: int = int(os.getenv("CONTRACTS_ZABES", "1"))
    position_size_ecs: float = float(os.getenv("POSITION_SIZE_ECS", "10"))  # $10/play ECS crypto

    # Risk management
    default_stop_loss_pct: float = float(os.getenv("DEFAULT_STOP_LOSS_PCT", "0.30"))
    grizzlies_stop_loss_pct: float = float(os.getenv("GRIZZLIES_STOP_LOSS_PCT", "0.25"))
    max_open_positions: int = int(os.getenv("MAX_OPEN_POSITIONS", "10"))

    # Drawdown / prop firm risk management
    account_balance_risk: float = float(os.getenv("ACCOUNT_BALANCE", "100"))
    max_daily_drawdown_pct: float = float(os.getenv("MAX_DAILY_DRAWDOWN_PCT", "5.0"))
    max_total_drawdown_pct: float = float(os.getenv("MAX_TOTAL_DRAWDOWN_PCT", "10.0"))
    max_position_size_pct: float = float(os.getenv("MAX_POSITION_SIZE_PCT", "2.0"))
    max_open_positions_risk: int = int(os.getenv("MAX_OPEN_POSITIONS_RISK", "15"))
    risk_per_trade_pct: float = float(os.getenv("RISK_PER_TRADE_PCT", "1.0"))

    # Polling
    polling_interval: int = int(os.getenv("POLLING_INTERVAL", "15"))
    stale_signal_seconds: int = int(os.getenv("STALE_SIGNAL_SECONDS", "600"))  # 10 min default

    # System
    log_level: str = os.getenv("LOG_LEVEL", "INFO")
    timezone: str = os.getenv("TIMEZONE", "US/Pacific")
    db_path: str = os.getenv("DB_PATH", "trading_bot.db")

    # Channel → analyst mapping
    @property
    def channel_to_analyst(self) -> dict[str, str]:
        mapping = {
            self.discord_channel_grizzlies: "grizzlies",
            self.discord_channel_waxui: "waxui",
            self.discord_channel_em: "enhanced_market",
            self.discord_channel_ecs: "ecs",
            self.discord_channel_eva: "eva",
            self.discord_channel_nando: "nando",
            self.discord_channel_zabes: "zabes",
        }
        # Merge Telegram channel→analyst mappings
        mapping.update(self.telegram_channel_to_analyst)
        # Filter out empty channel IDs
        return {k: v for k, v in mapping.items() if k}

    @property
    def watched_channels(self) -> list[str]:
        discord = [
            self.discord_channel_grizzlies,
            self.discord_channel_waxui,
            self.discord_channel_em,
            self.discord_channel_ecs,
            self.discord_channel_eva,
            self.discord_channel_nando,
            self.discord_channel_zabes,
        ]
        # Include Telegram channels (prefixed with tg_) so signal_router accepts them
        telegram = [f"tg_{ch}" for ch in self.telegram_signal_channels]
        return [ch for ch in discord + telegram if ch and ch != "tg_"]

    @property
    def discord_only_channels(self) -> list[str]:
        """Only Discord channels — used by discord_poller (excludes Telegram)."""
        return [ch for ch in [
            self.discord_channel_grizzlies,
            self.discord_channel_waxui,
            self.discord_channel_em,
            self.discord_channel_ecs,
            self.discord_channel_eva,
            self.discord_channel_nando,
            self.discord_channel_zabes,
        ] if ch]

    # Waxui trim schedule
    WAXUI_TRIM_FRACTIONS: tuple = (0.2, 0.2, 0.2, 0.2, 0.2)

    # Discord API base
    DISCORD_API_BASE: str = "https://discord.com/api/v10"
