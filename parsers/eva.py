"""Eva (EvaPanda) parser — embed-based structured signals."""

import logging
import re
from typing import Optional

from parsers.base import ParsedSignal, SignalAction, AssetType

logger = logging.getLogger(__name__)


class EvaParser:
    """Eva-specific parsing logic for Discord embed signals.
    
    Eva signals come via the 'EvaPanda Alerts' bot in structured embeds:
    - Entry: [EMBED: Open | BTO PEP 03/20/26 175C @ 0.74 (risky day trade possible swing), TP: 170, 175 (Stop loss under 164)]
    - Exit:  [EMBED: Close | STC PEP 03/20/26 175C @ 0.95 (Good spot to exit half here, holding 1/2 left)]
    - Update: [EMBED: Update: | holding 2 left. Stop loss at break even]
    """

    @staticmethod
    def enhance_prompt(base_prompt: str, message: str) -> str:
        """Add Eva-specific context to parsing prompt."""
        
        eva_context = """
EVA (EVAPANDA) ANALYST SPECIFIC RULES:
- Signals come via embed bot with tags: "Open", "Close", "Update:"
- Entry format: "BTO [TICKER] [MM/DD/YY] [STRIKE][C/P] @ [PRICE]"
- Exit format: "STC [TICKER] [MM/DD/YY] [STRIKE][C/P] @ [PRICE]"
- BTO = Buy To Open (entry), STC = Sell To Close (exit)
- "challenge" tag means she's trading a challenge account — still valid signal
- "risky" or "lotto" = higher risk, still valid
- "holding 1/2 left" or "holding 1/4th" = partial exit (trim)
- Updates about watchlists, market commentary, or flow data = NOT signals (info only)
- "I like XC" or "watching" = NOT an entry, just commentary
- Only parse explicit BTO/STC as entry/exit signals
- Asset type is ALWAYS options (she trades stock options exclusively)
- Tickers: SPX, NVDA, GOOGL, TSLA, MU, PEP, SPY, QQQ and other large caps
"""
        return base_prompt + eva_context

    @staticmethod
    def post_process_signal(signal: ParsedSignal, original_message: str) -> ParsedSignal:
        """Apply Eva-specific post-processing."""
        
        if not signal:
            return signal
        
        # Eva trades options exclusively
        signal.asset_type = AssetType.OPTION
        
        # Detect if this is an embed-style message and extract BTO/STC
        msg_lower = original_message.lower()
        
        if 'bto' in msg_lower or 'buy to open' in msg_lower:
            signal.action = SignalAction.ENTRY
        elif 'stc' in msg_lower or 'sell to close' in msg_lower:
            # Check if partial exit
            if any(term in msg_lower for term in ['half', '1/2', '1/4', 'holding', 'some']):
                signal.action = SignalAction.TRIM
                signal.trim_fraction = 1.0  # Sell all when holding 1 contract
            else:
                signal.action = SignalAction.EXIT
        
        # For exit/trim signals, inherit asset type from existing position
        if signal.action in [SignalAction.TRIM, SignalAction.EXIT]:
            try:
                from storage.database import Database
                from config import Config
                config = Config()
                db = Database(config.db_path if hasattr(config, "db_path") else "trading_bot.db")
                existing = db.get_position_by_ticker(signal.ticker, signal.analyst)
                if existing:
                    asset_type_str = existing.get('asset_type', '')
                    if "OPTION" in asset_type_str:
                        signal.asset_type = AssetType.OPTION
                    logger.info("Eva %s signal confirmed asset_type from position", signal.action.name)
            except Exception:
                pass  # Already defaulting to OPTION
        
        return signal
