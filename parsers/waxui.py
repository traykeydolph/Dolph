"""Waxui-specific parser enhancements and post-processing."""

import logging
import re
from typing import Optional
from datetime import datetime, timedelta

from parsers.base import ParsedSignal, SignalAction, AssetType

logger = logging.getLogger(__name__)


class WaxuiParser:
    """Waxui-specific parsing logic and validation.
    
    VALIDATED PATTERNS (from Tray's 100-message audit, 60% original accuracy):
    
    ENTRY: "[Risk] [TICKER] here [EXPIRY] [STRIKE][C/P] Avg[,.] [PRICE]"
      - Risk labels: *Riskier*, **High Risk**, **HIGH RISK**, **LOTTO**, *Lotto*
      - "Added to [TICKER] @[PRICE] New Avg is [PRICE]" = avg down entry
      
    TRIM: "[TICKER] [PRICE1] - [PRICE2] ✅ [PCT]% Holding [most/majority/half/runners/last]"
      - KEY: ✅ with "Holding X" = TRIM, NOT EXIT
      - Obfuscated tickers: SPXXX, SPYYY, SPXXXXXXX = SPX/SPY with excitement
      
    EXIT: "Closed [TICKER] here" or "Stopped out of [TICKER] 🔻" or "NOW im out"
      - Only full closes, NO "Holding" language
      
    NOISE: BULLPRINTER planning messages, market analysis, trail stops, daily summaries
    """
    
    # Entry detection regex
    ENTRY_RE = re.compile(
        r'(?:\*{1,2}(?:Riskier|High Risk|HIGH RISK|LOTTO|Lotto)\*{1,2}\s+)?'
        r'([A-Z]{2,5})\s+here\s+'
        r'(\d{1,2}/\d{1,2})\s+'
        r'(\d+(?:\.\d+)?)(C|P)\s+'
        r'Avg[,.]?\s*(\d+(?:\.\d+)?)',
        re.IGNORECASE
    )
    
    # Avg down entry
    AVG_DOWN_RE = re.compile(
        r'Added\s+to\s+([A-Z]{2,5})\s+@\s*(\d+(?:\.\d+)?)',
        re.IGNORECASE
    )
    
    # Trim detection: ✅ with "Holding"
    TRIM_RE = re.compile(
        r'(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)\s*✅\s*(\d+)%',
        re.IGNORECASE
    )
    
    # Holding language (distinguishes TRIM from EXIT)
    HOLDING_RE = re.compile(
        r'Holding\s+(most|majority|half|1/2|runners?\s*only|last\s*cons?\.?)',
        re.IGNORECASE
    )
    
    # BULLPRINTER noise detection
    BULLPRINTER_RE = re.compile(r'(?:BULLPRINTER|<:BULLPRINTER:\d+>)')
    
    # Noise patterns
    NOISE_PATTERNS = [
        re.compile(r'Done for (?:today|the day)', re.IGNORECASE),
        re.compile(r'Trail stops? set', re.IGNORECASE),
        re.compile(r'Enjoy the weekend', re.IGNORECASE),
        re.compile(r'Reduced risk', re.IGNORECASE),
        re.compile(r'Pillow secured', re.IGNORECASE),
        re.compile(r'\d+%\*$', re.IGNORECASE),  # "50%*" correction messages
    ]
    
    # Exit patterns (only these are real exits)
    EXIT_PATTERNS = [
        re.compile(r'Closed\s+\w+\s+here', re.IGNORECASE),
        re.compile(r'Stopped\s+(?:out\s+)?(?:of\s+)?(?:on\s+)?\w+\s*🔻', re.IGNORECASE),
        re.compile(r"NOW\s+i['\u2019]?m?\s+out", re.IGNORECASE),
    ]

    @staticmethod
    def is_noise(message: str) -> bool:
        """Quick noise check — can be called before library matching."""
        if WaxuiParser.BULLPRINTER_RE.search(message):
            return True
        for pattern in WaxuiParser.NOISE_PATTERNS:
            if pattern.search(message):
                return True
        # Image-only messages (Discord CDN links with no signal text)
        if message.strip().startswith('https://cdn.discordapp.com/'):
            return True
        return False

    @staticmethod
    def extract_details(message: str, message_id: str = "", 
                       timestamp: str = "") -> Optional[ParsedSignal]:
        """Extract signal details via regex — no Gemini needed.
        
        Returns ParsedSignal if extraction succeeds, None if Gemini fallback needed.
        """
        # Check for entry
        match = WaxuiParser.ENTRY_RE.search(message)
        if match:
            ticker = match.group(1).upper()
            expiry_raw = match.group(2)
            strike = float(match.group(3))
            direction = 'call' if match.group(4).upper() == 'C' else 'put'
            price = float(match.group(5))
            
            # Convert MM/DD to YYYY-MM-DD
            year = datetime.now().year
            month, day = expiry_raw.split('/')
            expiry = f"{year}-{month.zfill(2)}-{day.zfill(2)}"
            
            return ParsedSignal(
                analyst="waxui",
                action=SignalAction.ENTRY.value,
                asset_type=AssetType.OPTION.value,
                ticker=ticker,
                direction=direction,
                strike=strike,
                expiry=expiry,
                entry_price=price,
                trim_fraction=None,
                confidence=0.95,
                raw_message=message,
                message_id=message_id,
                timestamp=timestamp,
            )
        
        # Check avg down
        match = WaxuiParser.AVG_DOWN_RE.search(message)
        if match:
            return ParsedSignal(
                analyst="waxui",
                action=SignalAction.ENTRY.value,
                asset_type=AssetType.OPTION.value,
                ticker=match.group(1).upper(),
                direction=None,
                strike=None,
                expiry=None,
                entry_price=float(match.group(2)),
                trim_fraction=None,
                confidence=0.85,
                raw_message=message,
                message_id=message_id,
                timestamp=timestamp,
            )
        
        # Check trim (✅ with Holding)
        trim_match = WaxuiParser.TRIM_RE.search(message)
        if trim_match:
            entry_price = float(trim_match.group(1))
            exit_price = float(trim_match.group(2))
            pct = int(trim_match.group(3))
            
            # Extract ticker from message
            ticker = WaxuiParser._extract_ticker(message)
            
            # Holding = TRIM, no Holding = EXIT
            has_holding = WaxuiParser.HOLDING_RE.search(message)
            action = SignalAction.TRIM.value if has_holding else SignalAction.EXIT.value
            
            return ParsedSignal(
                analyst="waxui",
                action=action,
                asset_type=AssetType.OPTION.value,
                ticker=ticker,
                direction=None,
                strike=None,
                expiry=None,
                entry_price=exit_price,  # Current price for trim/exit
                trim_fraction=1.0,  # We run 1 contract, any trim = full exit
                confidence=0.95,
                raw_message=message,
                message_id=message_id,
                timestamp=timestamp,
            )
        
        # Check exits
        for pattern in WaxuiParser.EXIT_PATTERNS:
            if pattern.search(message):
                ticker = WaxuiParser._extract_ticker(message)
                return ParsedSignal(
                    analyst="waxui",
                    action=SignalAction.EXIT.value,
                    asset_type=AssetType.OPTION.value,
                    ticker=ticker,
                    direction=None,
                    strike=None,
                    expiry=None,
                    entry_price=None,
                    trim_fraction=1.0,
                    confidence=0.90,
                    raw_message=message,
                    message_id=message_id,
                    timestamp=timestamp,
                )
        
        return None  # Need Gemini fallback

    @staticmethod
    def _extract_ticker(message: str) -> str:
        """Extract ticker from message, handling obfuscated tickers."""
        # Obfuscated SPX/SPY
        if re.search(r'SPX{2,}', message, re.IGNORECASE):
            return 'SPX'
        if re.search(r'SPY{2,}', message, re.IGNORECASE):
            return 'SPY'
        
        # Standard ticker extraction — skip role mentions and action words
        action_words = {'Trim', 'More', 'Closed', 'Stopped', 'Holding', 'Trail', 'NOW', 
                       'Done', 'Enjoy', 'VIX', 'here', 'Avg', 'New', 'Added'}
        tickers = re.findall(r'\b([A-Z]{2,5})\b', message)
        for t in tickers:
            if t not in action_words and (not t.startswith('SP') or t in ('SPX', 'SPY')):
                return t
        
        # Fallback: check common Waxui tickers
        for t in ['SPX', 'SPY', 'HOOD', 'MU', 'ARM', 'FSLR', 'PLTR', 'RDDT', 'SNDK']:
            if t.lower() in message.lower():
                return t
        
        return 'SPX'  # Default for Waxui

    @staticmethod
    def enhance_prompt(base_prompt: str, message: str) -> str:
        """Add Waxui-specific context to parsing prompt."""
        
        waxui_context = """
WAXUI ANALYST SPECIFIC RULES:

ENTRY FORMAT (CRITICAL — these are ALWAYS entries):
- "[Risk Level] [TICKER] here [MM/DD] [STRIKE][C/P] Avg[,.] [PRICE]"
- Risk labels: *Riskier*, **High Risk**, **HIGH RISK**, **LOTTO**, *Lotto*
- Example: "**HIGH RISK** SPX here 02/23 6860C Avg. 6.00"
- Example: "*Riskier* SPY here 02/23 683C Avg. 1.40"
- "Added to [TICKER] @[PRICE] New Avg is [PRICE]" = ENTRY (averaging down)
- These are ALWAYS action:"entry", NEVER "info"!

TRIM FORMAT (CRITICAL — ✅ with "Holding" = TRIM not EXIT):
- "[TICKER] [PRICE1] - [PRICE2] ✅ [PCT]% Holding [most/majority/half/runners/last]"
- If "Holding" is present = action:"trim"
- "Holding most" → 20% trimmed. "Holding runners only" → 80% trimmed. "Holding last cons" → 95% trimmed.
- Obfuscated tickers: SPXXX/SPYYY/SPXXXXXXX = SPX/SPY (the more X's, more excitement)

EXIT FORMAT:
- "Closed [TICKER] here" = action:"exit"
- "Stopped out of [TICKER] 🔻" = action:"exit"
- "NOW im out" = action:"exit"
- ONLY these patterns are exits. Everything with "Holding" is a TRIM!

NOISE (action:"info"):
- BULLPRINTER emoji messages (long nightly planning) = ALWAYS noise, NEVER entry
- "Done for today" / "Enjoy the weekend" = daily summary, noise
- "Trail stops set @B/E" = trail stop update, noise
- Market commentary ("VIX trying to work back under 20") = noise
- Chart images (Discord CDN links) = noise
- "Reduced risk @X.XX" = risk management, noise
"""
        return base_prompt + waxui_context

    @staticmethod
    def post_process_signal(signal: ParsedSignal, original_message: str) -> ParsedSignal:
        """Apply Waxui-specific post-processing and validation."""
        
        if not signal:
            return signal
        
        # BULLPRINTER = always noise
        if WaxuiParser.BULLPRINTER_RE.search(original_message):
            signal.action = SignalAction.INFO.value
            signal.confidence = 0.0
            return signal
        
        # Known noise patterns
        if WaxuiParser.is_noise(original_message):
            signal.action = SignalAction.INFO.value
            signal.confidence = 0.0
            return signal
            
        # Waxui primarily trades SPX/SPY options
        signal.asset_type = AssetType.OPTION.value
        
        # Fix ✅ with "Holding" = TRIM not EXIT
        if '✅' in original_message and WaxuiParser.HOLDING_RE.search(original_message):
            signal.action = SignalAction.TRIM.value
            signal.trim_fraction = 1.0  # 1 contract = any trim is full exit
            
        # For exit/trim signals, inherit asset type from existing position
        if signal.action in [SignalAction.TRIM.value, SignalAction.EXIT.value, 
                            SignalAction.TRIM, SignalAction.EXIT]:
            try:
                from storage.database import Database
                import os
                db_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "trading_bot.db")
                db = Database(db_path)
                existing_position = db.get_position_by_ticker(signal.ticker, signal.analyst)
                if existing_position:
                    asset_type_str = existing_position.get('asset_type', '')
                    if "OPTION" in asset_type_str:
                        signal.asset_type = AssetType.OPTION.value
                    elif "STOCK" in asset_type_str:
                        signal.asset_type = AssetType.STOCK.value
                    elif "CRYPTO" in asset_type_str:
                        signal.asset_type = AssetType.CRYPTO.value
            except Exception as e:
                logger.warning("Could not look up existing position for Waxui signal: %s", e)
            
        # Extract option details (expiry, strike)
        option_details = WaxuiParser._extract_option_details(original_message)
        if option_details:
            signal.expiry = option_details.get('expiry')
            signal.strike = option_details.get('strike')
            signal.direction = option_details.get('direction')
            
        return signal
    
    @staticmethod
    def _extract_option_details(message: str) -> Optional[dict]:
        """Extract expiry, strike, and direction from Waxui option format."""
        pattern = r'(\d{1,2}/\d{1,2})\s+(\d+(?:\.\d+)?)(C|P)'
        match = re.search(pattern, message, re.IGNORECASE)
        
        if match:
            expiry_str = match.group(1)
            strike = float(match.group(2))
            direction = 'call' if match.group(3).upper() == 'C' else 'put'
            
            try:
                current_year = datetime.now().year
                month, day = expiry_str.split('/')
                expiry = f"{current_year}-{month.zfill(2)}-{day.zfill(2)}"
                return {'expiry': expiry, 'strike': strike, 'direction': direction}
            except Exception:
                logger.warning("Failed to parse expiry: %s", expiry_str)
                
        return None
