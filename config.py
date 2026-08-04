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
    discord_channel_ace: str = os.getenv("DISCORD_CHANNEL_ACE", "")

    # Gemini
    gemini_api_key: str = os.getenv("GEMINI_API_KEY", "")
    # Blocker 4: hard timeout (seconds) on the Gemini network call so a network
    # blip that can resolve DNS but not complete the connection fails fast
    # (→ except → return None) instead of hanging message processing forever.
    gemini_timeout_seconds: float = float(os.getenv("GEMINI_TIMEOUT_SECONDS", "10"))

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
    contracts_ace: int = int(os.getenv("CONTRACTS_ACE", "1"))
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

    # Blocker 3 — order fill ladder (never send a naked/unbounded market order).
    # Detect non-fill fast, then step the limit toward a bounded cap:
    #   entry: ask → ask+cap → SKIP+alert (skipping an entry costs nothing)
    #   exit:  bid → bid−cap → bid−emergency → true market + LOUD alert (must go flat)
    # cap = max(pct·price, abs) so cheap options can still cross a wide spread.
    fill_poll_interval: float = float(os.getenv("FILL_POLL_INTERVAL", "0.5"))   # status poll cadence (s)
    fill_step_timeout: float = float(os.getenv("FILL_STEP_TIMEOUT", "3"))       # wait per rung (s)
    slippage_cap_pct: float = float(os.getenv("SLIPPAGE_CAP_PCT", "0.05"))      # normal cap: 5%
    slippage_cap_abs: float = float(os.getenv("SLIPPAGE_CAP_ABS", "0.03"))      # or $0.03, whichever larger
    emergency_slippage_pct: float = float(os.getenv("EMERGENCY_SLIPPAGE_PCT", "0.20"))  # exit-only tail: 20%

    # Alerts: notify on every seen-but-skipped message (max-verbosity validation
    # mode). Set ALERT_NOISE=0 to quiet down after trust is built.
    alert_noise: bool = os.getenv("ALERT_NOISE", "1").lower() not in ("0", "false", "no")

    # System
    log_level: str = os.getenv("LOG_LEVEL", "INFO")
    timezone: str = os.getenv("TIMEZONE", "US/Pacific")
    db_path: str = os.getenv("DB_PATH", "trading_bot.db")

    # Analyst gating — ENABLED_ANALYSTS env var (comma-separated, e.g. "eva").
    # Empty/unset = all analysts enabled (backward compatible).
    @property
    def enabled_analysts(self) -> set[str]:
        raw = os.getenv("ENABLED_ANALYSTS", "")
        return {a.strip().lower() for a in raw.split(",") if a.strip()}

    # Shadow / log-only analysts — SHADOW_ANALYSTS env var (comma-separated).
    # These channels ARE polled, parsed, alerted and logged, but their signals
    # NEVER reach order execution. This is a separate axis from
    # ENABLED_ANALYSTS: shadow is observation, enabled is execution.
    @property
    def shadow_analysts(self) -> set[str]:
        raw = os.getenv("SHADOW_ANALYSTS", "")
        return {a.strip().lower() for a in raw.split(",") if a.strip()}

    def is_shadow_analyst(self, analyst: str) -> bool:
        return analyst.lower() in self.shadow_analysts

    def _analyst_enabled(self, analyst: str) -> bool:
        """Execution gate. Shadow analysts are NEVER executable, even if they
        also appear in ENABLED_ANALYSTS — observation always wins."""
        if self.is_shadow_analyst(analyst):
            return False
        enabled = self.enabled_analysts
        return not enabled or analyst in enabled

    def _analyst_observed(self, analyst: str) -> bool:
        """Polling gate: executable analysts plus shadow analysts."""
        return self._analyst_enabled(analyst) or self.is_shadow_analyst(analyst)

    @property
    def shadow_channels(self) -> list[str]:
        """Channel IDs polled for observation only."""
        return [
            ch for ch, analyst in self._discord_channel_analyst_pairs
            if ch and self.is_shadow_analyst(analyst)
        ]

    def is_shadow_channel(self, channel_id: str) -> bool:
        """True if this channel must bypass order execution entirely."""
        for ch, analyst in self._discord_channel_analyst_pairs:
            if ch and ch == channel_id:
                return self.is_shadow_analyst(analyst)
        return False

    @property
    def _discord_channel_analyst_pairs(self) -> list[tuple[str, str]]:
        return [
            (self.discord_channel_grizzlies, "grizzlies"),
            (self.discord_channel_waxui, "waxui"),
            (self.discord_channel_em, "enhanced_market"),
            (self.discord_channel_ecs, "ecs"),
            (self.discord_channel_eva, "eva"),
            (self.discord_channel_nando, "nando"),
            (self.discord_channel_zabes, "zabes"),
            (self.discord_channel_ace, "ace"),
        ]

    # Channel → analyst mapping
    @property
    def channel_to_analyst(self) -> dict[str, str]:
        mapping = {
            ch: analyst
            for ch, analyst in self._discord_channel_analyst_pairs
            if ch and self._analyst_observed(analyst)
        }
        # Merge Telegram channel→analyst mappings (same gating)
        mapping.update({
            ch: analyst
            for ch, analyst in self.telegram_channel_to_analyst.items()
            if self._analyst_enabled(analyst)
        })
        return mapping

    @property
    def watched_channels(self) -> list[str]:
        discord = [
            ch for ch, analyst in self._discord_channel_analyst_pairs
            if ch and self._analyst_observed(analyst)
        ]
        # Telegram channels (prefixed tg_): when analyst gating is active, only
        # watch channels whose mapped analyst is enabled — unmapped channels
        # can't be attributed to an analyst, so they're excluded under gating.
        tg_map = self.telegram_channel_to_analyst
        telegram = []
        for ch in self.telegram_signal_channels:
            key = f"tg_{ch}"
            analyst = tg_map.get(key)
            if analyst is None:
                if not self.enabled_analysts:
                    telegram.append(key)
            elif self._analyst_enabled(analyst):
                telegram.append(key)
        return discord + telegram

    @property
    def discord_only_channels(self) -> list[str]:
        """Only Discord channels — used by discord_poller (excludes Telegram)."""
        return [
            ch for ch, analyst in self._discord_channel_analyst_pairs
            if ch and self._analyst_observed(analyst)
        ]

    # Waxui trim schedule
    WAXUI_TRIM_FRACTIONS: tuple = (0.2, 0.2, 0.2, 0.2, 0.2)

    # Discord API base
    DISCORD_API_BASE: str = "https://discord.com/api/v10"
