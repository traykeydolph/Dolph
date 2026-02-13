"""Base parser — ParsedSignal dataclass and enums."""

from dataclasses import dataclass
from enum import Enum


class SignalAction(str, Enum):
    ENTRY = "entry"
    EXIT = "exit"
    TRIM = "trim"
    STOP_HIT = "stop_hit"
    INFO = "info"


class AssetType(str, Enum):
    OPTION = "option"
    CRYPTO = "crypto"
    STOCK = "stock"


@dataclass
class ParsedSignal:
    analyst: str              # "grizzlies" | "waxui" | "enhanced_market"
    action: str               # SignalAction value
    asset_type: str           # AssetType value
    ticker: str               # "SPY", "BTC", "AAPL" etc.
    direction: str | None     # "call" | "put" | "long" | "short"
    strike: float | None      # Option strike price
    expiry: str | None        # Option expiry "2026-02-14"
    entry_price: float | None
    trim_fraction: float | None  # 0.2 = sell 20% of position
    confidence: float         # 0.0-1.0 parser confidence
    raw_message: str          # Original Discord message
    message_id: str           # Discord message ID (dedup)
    timestamp: str            # ISO timestamp
