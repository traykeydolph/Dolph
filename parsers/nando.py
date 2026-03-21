"""Nando (TradesWithNando) parser — conversational options signals."""

import logging
import re
from typing import Optional

from parsers.base import ParsedSignal, SignalAction, AssetType

logger = logging.getLogger(__name__)


class NandoParser:
    """Nando-specific parsing logic.
    
    Nando posts conversational signals with clear entry/exit info:
    - Entry: "BTO 1 BABA 150P 3/6 @ 5.2" or "Swing Trade: BTO 1 XOM 140C 2/27 @ 3.65"
    - Exit: "Sold some at 0.88", "cutting the rest of msft", "Out on AMD at $8.75"
    - Recap: Weekly recaps with emoji: 🟢 = win, 🔴 = loss, ✅ = win
    
    Key patterns:
    - Uses BTO for entries with contract count, ticker, strike+direction, expiry, price
    - Exits are conversational — "Sold", "Out on", "cutting"
    - Posts weekly recaps (info only, not signals)
    - Does live VC Fridays (text recap follows)
    - Tickers: QQQ, AMD, TSLA, AAPL, SPY, BABA, XOM, NFLX, MSTR
    """

    @staticmethod
    def enhance_prompt(base_prompt: str, message: str) -> str:
        """Add Nando-specific context to parsing prompt."""
        
        nando_context = """
NANDO (TRADESWITHNANDO) ANALYST SPECIFIC RULES:
- Entry format: "BTO [QTY] [TICKER] [STRIKE][C/P] [MM/DD] @ [PRICE]"
  Example: "BTO 1 BABA 150P 3/6 @ 5.2"
  Example: "Swing Trade: BTO 1 XOM 140C 2/27 @ 3.65"
- Exit phrases: "Sold at", "Out on", "cutting the rest", "Contracts Sitting"
- "Payday" or "Sitting at $X from $Y" = update showing current P&L, NOT an exit unless "sold" mentioned
- Weekly recaps with 🟢/🔴 emojis = info/recap only, NOT new signals
- "Free VC Friday" recaps = info only
- "Share your Profit" = info only
- "Swing Trade:" prefix = longer hold, still valid entry
- "targeting" numbers = take profit levels
- He trades primarily QQQ, AMD, TSLA, AAPL, SPY, BABA, XOM, NFLX
- Asset type is ALWAYS options
- Personal messages/jokes (Sam Darnold, Yorkie, etc.) = info only
"""
        return base_prompt + nando_context

    @staticmethod
    def post_process_signal(signal: ParsedSignal, original_message: str) -> ParsedSignal:
        """Apply Nando-specific post-processing."""
        
        if not signal:
            return signal
        
        # Nando trades options exclusively
        signal.asset_type = AssetType.OPTION
        
        msg_lower = original_message.lower()
        
        # Skip recaps and commentary
        if any(term in msg_lower for term in ['recap', 'free vc friday', 'share your profit', 
                                                'started his beginner', 'merry christmas',
                                                'shout out', 'appreciate']):
            signal.action = SignalAction.INFO if hasattr(SignalAction, 'INFO') else 'info'
            signal.confidence = 0.0
            return signal
        
        # Detect entries
        if 'bto' in msg_lower or 'buy to open' in msg_lower:
            signal.action = SignalAction.ENTRY
        
        # Detect exits
        elif any(term in msg_lower for term in ['sold', 'out on', 'cutting', 'closed']):
            if any(term in msg_lower for term in ['some', 'half', 'few']):
                signal.action = SignalAction.TRIM
                signal.trim_fraction = 1.0  # Sell all when holding 1 contract
            else:
                signal.action = SignalAction.EXIT
        
        # For exit/trim, inherit asset type from position
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
            except Exception:
                pass
        
        return signal
