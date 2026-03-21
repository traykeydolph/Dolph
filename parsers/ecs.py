"""ECS (Crypto Signals) analyst parser — structured crypto entries with targets/stops."""

import logging
import re
from typing import Optional

from parsers.base import ParsedSignal, SignalAction, AssetType

logger = logging.getLogger(__name__)


class ECSParser:
    """ECS-specific parsing logic and validation.
    
    ECS posts very structured crypto signals:
      #TICKER/USDT or USDC (exchanges)
      Entry around PRICE
      Targets around T1-T2-T3
      Stop around PRICE
      Expected duration: Short/Medium/Long term
      
    Also has "Crypto Price Alert bot" that confirms target hits — we use these as TRIM signals.
    """
    
    # Patterns for ECS format
    TICKER_PATTERN = r'#([A-Z]+)/(?:USDT|USDC)'
    ENTRY_PATTERN = r'(?i)entry\s+around\s+([0-9]+(?:[.,][0-9]+)?)'
    TARGETS_PATTERN = r'(?i)targets?\s+around\s+([\d.,\-\s]+)'
    STOP_PATTERN = r'(?i)stop\s+around\s*[–:\-]?\s*([0-9]+(?:\.[0-9]+)?)'
    SHORT_PATTERN = r'(?i)\bshort[/\s]*sell\b'
    LEVERAGE_PATTERN = r'(?i)leverage\s*[-–]?\s*(\d+)x'
    
    # Alert bot patterns
    ALERT_BOT_PATTERN = r'(?i)crypto price alert bot|went (?:above|below)|View Alert'
    ALERT_TICKER_PATTERN = r'(?i)(?:^|\s)([A-Za-z][A-Za-z0-9 ]+?)\s+\(([A-Z]+)\)\s+went\s+(above|below)\s+([\d.,]+)\s+USD'
    PROFIT_TARGET_PATTERN = r'(?i)reached the (\d+)(?:st|nd|rd|th) Profit'

    @staticmethod
    def is_alert_bot_message(message: str) -> bool:
        """Check if this is an alert bot confirmation message."""
        return bool(re.search(ECSParser.ALERT_BOT_PATTERN, message))

    @staticmethod
    def parse_alert_bot_message(message: str, message_id: str, timestamp: str) -> Optional[ParsedSignal]:
        """Parse alert bot message into a TRIM signal.
        
        Example: "Solana (SOL) went above 89.10 USD on MEXC - View Alert
                  Note: ✅ #SOLUSDT has reached the 1st Profit Target"
        
        Returns a TRIM signal with the ticker and the target price.
        """
        ticker_match = re.search(ECSParser.ALERT_TICKER_PATTERN, message)
        if not ticker_match:
            return None
        
        ticker = ticker_match.group(2).upper()
        direction_word = ticker_match.group(3).lower()  # "above" or "below"
        price = float(ticker_match.group(4).replace(',', ''))
        
        # Check if it's a profit target confirmation
        target_match = re.search(ECSParser.PROFIT_TARGET_PATTERN, message)
        if not target_match:
            return None
        
        target_num = int(target_match.group(1))
        
        # Dynamic trim fractions — spread evenly across however many targets exist
        # We don't know total targets here, so use conservative scaling:
        # T1 = 25%, T2 = 33% of remaining, T3 = 50% of remaining, T4+ = exit all
        # This way we always have runners for later targets
        trim_map = {1: 0.25, 2: 0.33, 3: 0.50, 4: 1.0, 5: 1.0}
        trim_fraction = trim_map.get(target_num, 1.0)
        
        action = SignalAction.EXIT.value if target_num >= 4 else SignalAction.TRIM.value
        
        logger.info("Alert bot: %s hit target %d @ $%.6f → %s (%.0f%%)",
                    ticker, target_num, price, action, trim_fraction * 100)
        
        return ParsedSignal(
            analyst="ecs",
            action=action,
            asset_type=AssetType.CRYPTO.value,
            ticker=ticker,
            direction="long" if direction_word == "above" else "short",
            strike=None,
            expiry=None,
            entry_price=price,  # This is the target price, used as trim/exit price
            trim_fraction=trim_fraction,
            confidence=0.95,
            raw_message=message,
            message_id=message_id,
            timestamp=timestamp,
        )

    @staticmethod
    def enhance_prompt(base_prompt: str, message: str) -> str:
        """Add ECS-specific context to parsing prompt."""
        
        ecs_context = """
ECS ANALYST SPECIFIC RULES:

This analyst posts STRUCTURED crypto signals. Format is ALWAYS:
  #TICKER/USDT or USDC (Exchange1, Exchange2 etc)
  Entry around PRICE
  Targets around T1-T2-T3
  Stop around – PRICE
  Expected duration: Short/Medium/Long term

PARSING RULES:
- "#SOL/USDT" → ticker "SOL", asset_type "crypto", direction "long"
- "Short/Sell" in the message → direction "short" (rare but does happen)
- "Leverage - 2x" → noted but doesn't change parsing
- ALWAYS asset_type = "crypto" for this analyst
- entry_price = the "Entry around" value
- Direction is "long" by default, "short" only if explicitly stated
- Extract stop_price from "Stop around – PRICE"
- Extract target_prices as a list from "Targets around T1-T2-T3"

IMPORTANT — RETURN EXTRA FIELDS:
In addition to the standard fields, also return:
  "stop_price": number_or_null,
  "target_prices": [T1, T2, T3] or null

IGNORE THESE (action: "info"):
- Messages from "Crypto Price Alert bot" — these are handled separately
- Any message containing "went above" or "went below" with "View Alert"

ROLE MENTIONS:
- <@&NUMBER> patterns are Discord role pings — IGNORE them entirely
"""
        return base_prompt + ecs_context

    @staticmethod
    def post_process_signal(signal: ParsedSignal, original_message: str) -> ParsedSignal:
        """Apply ECS-specific post-processing."""
        if not signal:
            return signal
        
        # Skip alert bot messages — they're handled by parse_alert_bot_message
        if ECSParser.is_alert_bot_message(original_message):
            signal.action = SignalAction.INFO.value
            signal.confidence = 0.0
            return signal
        
        # Force crypto asset type for all ECS signals
        signal.asset_type = AssetType.CRYPTO.value
        
        # Detect short signals
        if re.search(ECSParser.SHORT_PATTERN, original_message):
            signal.direction = "short"
        elif signal.direction is None:
            signal.direction = "long"
        
        # Extract ticker from structured format if Gemini missed it
        ticker_match = re.search(ECSParser.TICKER_PATTERN, original_message)
        if ticker_match and (not signal.ticker or signal.ticker == ""):
            signal.ticker = ticker_match.group(1).upper()
        
        # Extract entry price if missing
        if signal.entry_price is None:
            entry_match = re.search(ECSParser.ENTRY_PATTERN, original_message)
            if entry_match:
                signal.entry_price = float(entry_match.group(1).replace(',', ''))
        
        # Extract stop price
        if signal.stop_price is None:
            stop_match = re.search(ECSParser.STOP_PATTERN, original_message)
            if stop_match:
                signal.stop_price = float(stop_match.group(1).replace(',', ''))
        
        # Extract target prices
        if signal.target_prices is None:
            targets_match = re.search(ECSParser.TARGETS_PATTERN, original_message)
            if targets_match:
                raw = targets_match.group(1)
                prices = re.findall(r'[\d.]+', raw)
                if prices:
                    signal.target_prices = [float(p) for p in prices]
        
        return signal
