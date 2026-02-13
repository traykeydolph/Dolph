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

    # Gemini
    gemini_api_key: str = os.getenv("GEMINI_API_KEY", "")

    # Alpaca
    alpaca_api_key: str = os.getenv("ALPACA_API_KEY", "")
    alpaca_secret_key: str = os.getenv("ALPACA_SECRET_KEY", "")
    alpaca_base_url: str = os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")

    # Coinbase (stub)
    coinbase_api_key: str = os.getenv("COINBASE_API_KEY", "")
    coinbase_secret_key: str = os.getenv("COINBASE_SECRET_KEY", "")

    # Telegram
    telegram_bot_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    telegram_chat_id: str = os.getenv("TELEGRAM_CHAT_ID", "")

    # Position sizing
    position_size_standard: float = float(os.getenv("POSITION_SIZE_STANDARD", "400"))
    position_size_lotto: float = float(os.getenv("POSITION_SIZE_LOTTO", "200"))
    position_size_crypto: float = float(os.getenv("POSITION_SIZE_CRYPTO", "200"))
    account_size: float = float(os.getenv("ACCOUNT_SIZE", "4000"))

    # Risk management
    default_stop_loss_pct: float = float(os.getenv("DEFAULT_STOP_LOSS_PCT", "0.30"))
    grizzlies_stop_loss_pct: float = float(os.getenv("GRIZZLIES_STOP_LOSS_PCT", "0.25"))
    max_open_positions: int = int(os.getenv("MAX_OPEN_POSITIONS", "10"))

    # Polling
    polling_interval: int = int(os.getenv("POLLING_INTERVAL", "15"))

    # System
    log_level: str = os.getenv("LOG_LEVEL", "INFO")
    timezone: str = os.getenv("TIMEZONE", "US/Pacific")
    db_path: str = os.getenv("DB_PATH", "trading_bot.db")

    # Channel → analyst mapping
    @property
    def channel_to_analyst(self) -> dict[str, str]:
        return {
            self.discord_channel_grizzlies: "grizzlies",
            self.discord_channel_waxui: "waxui",
            self.discord_channel_em: "enhanced_market",
        }

    @property
    def watched_channels(self) -> list[str]:
        return [
            self.discord_channel_grizzlies,
            self.discord_channel_waxui,
            self.discord_channel_em,
        ]

    # Waxui trim schedule
    WAXUI_TRIM_FRACTIONS: tuple = (0.2, 0.2, 0.2, 0.2, 0.2)

    # Discord API base
    DISCORD_API_BASE: str = "https://discord.com/api/v10"
