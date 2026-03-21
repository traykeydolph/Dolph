"""Zabes parser — concise conviction options signals.

VALIDATED PATTERNS (from Tray's 100-message audit, 86% original accuracy):

ENTRY: "$TICKER MM/DD $STRIKEC/P at $PRICE" or casual variants
  - Sometimes prefixed with $, sometimes without
  - Risk labels: "Risky", "riskier", "lotto", "High risk", "Legit super high risk"
  - "Grabbing X cons", "Taking a few to swing"
  - STOCK entries: "Starting to buy [TICKER] shares" — asset_type = STOCK!
  
TRIM: "Trimmed/Trimming [TICKER] at $X [X%] profit"
  - "Sold a few more... Holding one" = TRIM (still holding)
  - "Took a few more off" = TRIM
  - Profit per contract: "$70 profit a con"
  
EXIT: "Fully out on [TICKER]", "Out on the rest", "cutting [TICKER]"
  - "hit stop loss" = EXIT
  - "Sold at $X" with no "holding" = EXIT
  
NOISE: "Watching", "may hedge", "." dots, personal updates, SL updates
"""

import logging
import re
from typing import Optional
from datetime import datetime

from parsers.base import ParsedSignal, SignalAction, AssetType

logger = logging.getLogger(__name__)


class ZabesParser:
    """Zabes-specific parsing logic."""
    
    # Entry patterns
    ENTRY_RE = re.compile(
        r'(?:\$?)([A-Z]{2,5})\s+'
        r'(\d{1,2}/\d{1,2})\s+'
        r'(?:\$?)(\d+(?:\.\d+)?)(C|P)\s+'
        r'(?:at\s+)?(?:\$?)(\d+(?:\.\d+)?)',
        re.IGNORECASE
    )
    
    # Stock entry (CRITICAL — NFLX bug fix)
    # Must catch: "buying NFLX shares", "Starting to buy NFLX", "grabbing 100 NFLX",
    # "NFLX long term hold", "adding to NFLX position"
    STOCK_ENTRY_RE = re.compile(
        r'(?:buy|buying|bought|grabbing|grab|starting\s+to\s+(?:buy|grab))\s+'
        r'(?:\d+\s+)?'  # optional quantity like "100"
        r'(?:\$?)([A-Z]{2,5})\s+shares',
        re.IGNORECASE
    )

    # Stock indicators — if these appear WITHOUT option details (strike+expiry), it's a stock trade
    STOCK_INDICATOR_RE = re.compile(
        r'(?:long\s+term\s+hold|shares|adding\s+to\s+(?:my\s+)?(?:\$?)[A-Z]{2,5}\s+position|'
        r'buying\s+(?:the\s+)?(?:(?:\$?)[A-Z]{2,5}\s+)?(?:dip|stock)|stock\s+play)',
        re.IGNORECASE
    )

    # Option details pattern — if this is present, it's an option regardless of other keywords
    HAS_OPTION_DETAILS_RE = re.compile(
        r'\d{1,2}/\d{1,2}\s+(?:\$?)\d+(?:\.\d+)?[CP]',
        re.IGNORECASE
    )
    
    # Trim patterns
    TRIM_KEYWORDS = re.compile(
        r'(?:trimm(?:ed|ing)|trim\s+those|took\s+(?:a\s+few\s+)?(?:more\s+)?(?:\w+\s+)*off|sold\s+a\s+few\s+more)',
        re.IGNORECASE
    )
    
    # Exit patterns
    EXIT_KEYWORDS = re.compile(
        r'(?:fully\s+out|out\s+on\s+the\s+rest|sold\s+(?:the\s+)?(?:rest|last)|cutting\b|hit\s+stop\s*loss|hit\s+sl)',
        re.IGNORECASE
    )
    
    # Noise patterns
    NOISE_PATTERNS = [
        re.compile(r'^watching\b', re.IGNORECASE),
        re.compile(r'\bmay\s+hedge\b', re.IGNORECASE),
        re.compile(r'^\.\s*$'),  # Just dots
        re.compile(r'\bon\s+watch\b', re.IGNORECASE),
        re.compile(r'\bswinging\s+my\b', re.IGNORECASE),
        re.compile(r'\bstill\s+in\s+my\b', re.IGNORECASE),
        re.compile(r'\bhope\s+everyone\b', re.IGNORECASE),
        re.compile(r'\bcame\s+down\s+with\b', re.IGNORECASE),
        re.compile(r'\bhaven\'t\s+taken\b', re.IGNORECASE),
    ]
    
    # "Holding" = still has position = TRIM not EXIT
    HOLDING_RE = re.compile(r'holding|still\s+holding|swinging', re.IGNORECASE)

    @staticmethod
    def is_noise(message: str) -> bool:
        """Quick noise check."""
        msg = message.strip()
        if msg == '.':
            return True
        for pattern in ZabesParser.NOISE_PATTERNS:
            if pattern.search(msg):
                return True
        return False

    @staticmethod
    def extract_details(message: str, message_id: str = "",
                       timestamp: str = "") -> Optional[ParsedSignal]:
        """Extract signal details via regex — no Gemini needed."""
        
        # Check noise first
        if ZabesParser.is_noise(message):
            return None  # Will be caught by library matcher as NOISE

        # Determine if message has explicit option details (strike+expiry+C/P)
        has_option_details = bool(ZabesParser.HAS_OPTION_DETAILS_RE.search(message))
        has_stock_indicators = bool(ZabesParser.STOCK_INDICATOR_RE.search(message))

        # Stock entry detection:
        # 1. Explicit "shares" keyword match, OR
        # 2. Stock indicator language (long term hold, buying the dip, etc.) WITHOUT option details
        # Key insight: if strike+expiry+C/P is present, it's ALWAYS an option regardless
        stock_match = ZabesParser.STOCK_ENTRY_RE.search(message)
        if stock_match and not has_option_details:
            ticker = stock_match.group(1).upper()
            price_match = re.search(r'(?:at\s+)?(?:\$?)(\d+(?:\.\d+)?)', message[stock_match.end():])
            price = float(price_match.group(1)) if price_match else None

            return ParsedSignal(
                analyst="zabes",
                action=SignalAction.ENTRY.value,
                asset_type=AssetType.STOCK.value,
                ticker=ticker,
                direction="long",
                strike=None,
                expiry=None,
                entry_price=price,
                trim_fraction=None,
                confidence=0.90,
                raw_message=message,
                message_id=message_id,
                timestamp=timestamp,
            )

        # Stock via indicator patterns (no "shares" keyword but stock language)
        # e.g., "NFLX long term hold", "buying the TSLA dip"
        if has_stock_indicators and not has_option_details:
            # Extract ticker
            ticker = ZabesParser._extract_ticker(message)
            if ticker:
                price_match = re.search(r'(?:at\s+)?(?:\$?)(\d+(?:\.\d+)?)', message)
                price = float(price_match.group(1)) if price_match else None

                return ParsedSignal(
                    analyst="zabes",
                    action=SignalAction.ENTRY.value,
                    asset_type=AssetType.STOCK.value,
                    ticker=ticker,
                    direction="long",
                    strike=None,
                    expiry=None,
                    entry_price=price,
                    trim_fraction=None,
                    confidence=0.85,
                    raw_message=message,
                    message_id=message_id,
                    timestamp=timestamp,
                )

        # Option entry
        match = ZabesParser.ENTRY_RE.search(message)
        if match:
            ticker = match.group(1).upper()
            expiry_raw = match.group(2)
            strike = float(match.group(3))
            direction = 'call' if match.group(4).upper() == 'C' else 'put'
            price = float(match.group(5))
            
            year = datetime.now().year
            month, day = expiry_raw.split('/')
            expiry = f"{year}-{month.zfill(2)}-{day.zfill(2)}"

            # Validate the date is real (catches Feb 30, Apr 31, etc.)
            try:
                datetime.strptime(expiry, "%Y-%m-%d")
            except ValueError:
                logger.warning("Invalid expiry date '%s' from message: %s", expiry, message[:80])
                return None

            return ParsedSignal(
                analyst="zabes",
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
        
        # Exit check (before trim — "fully out" beats "trimmed")
        if ZabesParser.EXIT_KEYWORDS.search(message):
            ticker = ZabesParser._extract_ticker(message)
            return ParsedSignal(
                analyst="zabes",
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
        
        # Trim check
        if ZabesParser.TRIM_KEYWORDS.search(message):
            ticker = ZabesParser._extract_ticker(message)
            # Extract price
            price_match = re.search(r'(?:at\s+)?(?:\$?)(\d+(?:\.\d+)?)\s*(?:\d+%\s+profit)?', message)
            price = float(price_match.group(1)) if price_match else None
            
            return ParsedSignal(
                analyst="zabes",
                action=SignalAction.TRIM.value,
                asset_type=AssetType.OPTION.value,
                ticker=ticker,
                direction=None,
                strike=None,
                expiry=None,
                entry_price=price,
                trim_fraction=1.0,
                confidence=0.90,
                raw_message=message,
                message_id=message_id,
                timestamp=timestamp,
            )
        
        # Check for informal trim: "X% profit" with sell language
        if re.search(r'\d+%?\s*profit\s*(?:selling|sold)?', message, re.IGNORECASE):
            if not ZabesParser.HOLDING_RE.search(message):
                # No "holding" = exit
                pass  # Let Gemini handle ambiguous cases
        
        # Check for informal entry: "Grabbed/Taking/Trying" with ticker
        if re.search(r'(?:grabbed|taking|trying)\s+(?:a\s+few\s+)?(?:\$?)([A-Z]{2,5})', message, re.IGNORECASE):
            # Has option details?
            opt_match = re.search(r'(\d{1,2}/\d{1,2})\s+(?:\$?)(\d+(?:\.\d+)?)(C|P)', message, re.IGNORECASE)
            if opt_match:
                expiry_raw = opt_match.group(1)
                strike = float(opt_match.group(2))
                direction = 'call' if opt_match.group(3).upper() == 'C' else 'put'
                year = datetime.now().year
                month, day = expiry_raw.split('/')
                expiry = f"{year}-{month.zfill(2)}-{day.zfill(2)}"

                # Validate date
                try:
                    datetime.strptime(expiry, "%Y-%m-%d")
                except ValueError:
                    logger.warning("Invalid expiry date '%s' from message: %s", expiry, message[:80])
                    return None

                ticker_match = re.search(r'(?:\$?)([A-Z]{2,5})', message)
                ticker = ticker_match.group(1).upper() if ticker_match else ''
                # Skip action words
                if ticker in ('LOTTO', 'HIGH', 'RISK'):
                    tickers = re.findall(r'(?:\$?)([A-Z]{2,5})', message)
                    ticker = next((t for t in tickers if t not in ('LOTTO', 'HIGH', 'RISK')), '')
                
                price_match = re.search(r'(?:at\s+)?(?:\$?)(\d+(?:\.\d+)?)\s*$', message.split(direction[-1].upper())[-1] if direction else message)
                # Simpler: find price after C/P
                price_match = re.search(r'[CP]\s+(?:at\s+)?(?:\$?)(\d+(?:\.\d+)?)', message, re.IGNORECASE)
                price = float(price_match.group(1)) if price_match else None
                
                return ParsedSignal(
                    analyst="zabes",
                    action=SignalAction.ENTRY.value,
                    asset_type=AssetType.OPTION.value,
                    ticker=ticker,
                    direction=direction,
                    strike=strike,
                    expiry=expiry,
                    entry_price=price,
                    trim_fraction=None,
                    confidence=0.85,
                    raw_message=message,
                    message_id=message_id,
                    timestamp=timestamp,
                )
        
        return None  # Need Gemini fallback

    @staticmethod
    def _extract_ticker(message: str) -> str:
        """Extract ticker from message."""
        skip = {'LOTTO', 'HIGH', 'RISK', 'OUT', 'SL', 'USE', 'NOT', 'NOW'}
        tickers = re.findall(r'(?:\$?)([A-Z]{2,5})\b', message)
        for t in tickers:
            if t not in skip:
                return t
        return ''

    @staticmethod
    def enhance_prompt(base_prompt: str, message: str) -> str:
        """Add Zabes-specific context to parsing prompt."""
        
        zabes_context = """
ZABES ANALYST SPECIFIC RULES:

ENTRY FORMAT:
- "$TICKER MM/DD $STRIKEC/P at $PRICE" or "TICKER MM/DD STRIKEC/P at PRICE"
- Example: "$MSFT 2/27 $400C at $6.60"
- Example: "Nvda 2/13 $190P at $2.50"
- Example: "$MU 2/13 $405P $3.80 1 con 'lotto'"
- "Grabbed a few", "Taking", "Trying" = still entries
- "lotto", "risky", "high risk" = still entries, just smaller size

CRITICAL — STOCK vs OPTION:
- "Starting to buy [TICKER] shares" or "buying [TICKER] shares" = asset_type "stock", NOT option!
- If "shares" appears in the message, it's a STOCK trade
- Everything else = option by default

EXIT PHRASES:
- "Fully out on [TICKER]" = full exit
- "Out on the rest of [TICKER]" = full exit
- "cutting [TICKER]" = exit (cutting losses)
- "[TICKER] hit stop loss" = forced exit
- "Sold the rest" / "Sold last" = full exit

TRIM PHRASES:
- "Trimmed at", "Trimming [TICKER]" = partial exit
- "Sold a few more... Holding one" = TRIM (still has contracts!)
- "Took a few more off" = partial exit
- KEY: If "holding" or "still holding" = TRIM, not EXIT

NOISE:
- "Watching", "[TICKER] on watch" = watchlist, NOT entry
- "may hedge [TICKER] with..." = plan discussion, NOT entry
- "." (dot messages) = noise
- "Swinging my [TICKER]" = holding update, noise
- "my SL for [TICKER] is $X" = SL update, noise
- "moving SL on [TICKER]" = SL update, noise
"""
        return base_prompt + zabes_context

    @staticmethod
    def post_process_signal(signal: ParsedSignal, original_message: str) -> ParsedSignal:
        """Apply Zabes-specific post-processing."""
        
        if not signal:
            return signal
        
        msg_lower = original_message.lower()
        
        # CRITICAL: Stock vs Option classification
        # Rule: if message has option details (strike+expiry+C/P), it's ALWAYS an option.
        # If it has stock language WITHOUT option details, it's a stock.
        has_option_details = bool(ZabesParser.HAS_OPTION_DETAILS_RE.search(original_message))
        has_stock_language = ('shares' in msg_lower or
                              bool(ZabesParser.STOCK_INDICATOR_RE.search(original_message)))

        if has_stock_language and not has_option_details:
            signal.asset_type = AssetType.STOCK.value
            logger.info("Zabes stock signal detected for %s (stock language, no option details)", signal.ticker)
        elif has_option_details:
            signal.asset_type = AssetType.OPTION.value
        elif signal.asset_type != AssetType.STOCK.value:
            signal.asset_type = AssetType.OPTION.value
        
        # Skip commentary
        if any(term in msg_lower for term in ['watching', 'may hedge', 'swinging my', 'on watch']):
            signal.confidence = 0.0
            return signal
        
        # Detect stop loss hit → EXIT
        if 'hit stop loss' in msg_lower or 'hit sl' in msg_lower:
            signal.action = SignalAction.EXIT.value
        
        # "cutting [ticker]" = EXIT
        elif 'cutting' in msg_lower:
            signal.action = SignalAction.EXIT.value
            signal.trim_fraction = 1.0
        
        # Detect exits vs trims
        elif any(term in msg_lower for term in ['fully out', 'out on the rest', 'sold the rest', 'sold last']):
            signal.action = SignalAction.EXIT.value
            signal.trim_fraction = 1.0
        elif any(term in msg_lower for term in ['trimmed', 'trimming', 'took a few more']):
            if ZabesParser.HOLDING_RE.search(original_message):
                signal.action = SignalAction.TRIM.value
            else:
                signal.action = SignalAction.TRIM.value  # Default to trim for partial sells
            signal.trim_fraction = 1.0  # 1 contract = any trim is full exit
        
        # For exit/trim, inherit asset type from position
        if signal.action in [SignalAction.TRIM.value, SignalAction.EXIT.value,
                            SignalAction.TRIM, SignalAction.EXIT]:
            try:
                from storage.database import Database
                from config import Config
                config = Config()
                db = Database(config.db_path if hasattr(config, "db_path") else "trading_bot.db")
                existing = db.get_position_by_ticker(signal.ticker, signal.analyst)
                if existing:
                    asset_type_str = existing.get('asset_type', '')
                    if "OPTION" in asset_type_str:
                        signal.asset_type = AssetType.OPTION.value
                    elif "STOCK" in asset_type_str:
                        signal.asset_type = AssetType.STOCK.value
            except Exception:
                pass
        
        return signal
