"""Grizzlies-specific parser enhancements and post-processing."""

import logging
import re
from typing import Optional
from datetime import datetime, timedelta

from parsers.base import ParsedSignal, SignalAction, AssetType

logger = logging.getLogger(__name__)


class GrizzliesParser:
    """Grizzlies-specific parsing logic and validation."""
    
    # Common Grizzlies format patterns
    CRYPTO_PATTERN = r'(?i)\b(BTC|ETH|SOL|DOGE|SHIB|PEPE|ADA|XRP|AVAX|LINK|HYPE|SPACE|ONE|ATOM|FET)\b'
    # Matches both "TICKER MM/DD STRIKEc" and "TICKER STRIKEc MM/DD"
    OPTION_PATTERN = r'(?i)\b([A-Z]{1,5})\s+(?:(\d+(?:\.\d+)?)\s*[cCpP]\s+(\d{1,2}/\d{1,2})|(\d{1,2}/\d{1,2})\s+(\d+(?:\.\d+)?)\s*[cCpP])'
    ENTRY_PATTERN = r'(?i)(?:entry|entries?)[:=\s]*([0-9.,\s\-and]+)'
    TARGET_PATTERN = r'(?i)(?:targets?|TPs?)[:=\s]*([0-9.,\s\-and]+)'
    STOP_PATTERN = r'(?i)(?:stop\s*loss|stoploss|SL)(?:[:=\s]+(?:is|at|around))?[:=\s]*(?:day\s+(?:high|low)\s+)?\$?(\d+(?:\.\d+)?|\.\d+)(?!\d)'
    
    @staticmethod
    def is_noise(message: str) -> bool:
        """Quick noise check for Grizzlies messages."""
        msg_lower = message.lower()
        # Report cards with 🍏🍎
        if '🍏' in message or '🍎' in message:
            return True
        # Monthly reports
        if 'monthly report' in msg_lower or 'february report' in msg_lower or 'march report' in msg_lower:
            return True
        # GIFs
        if 'tenor.com' in msg_lower:
            return True
        # Giveaway
        if 'giveaway' in msg_lower or 'instagram' in msg_lower:
            return True
        return False
    
    @staticmethod
    def enhance_prompt(base_prompt: str, message: str) -> str:
        """Add Grizzlies-specific context to parsing prompt."""
        
        grizzlies_context = """
GRIZZLIES ANALYST SPECIFIC RULES:

CRYPTO ENTRIES (most common):
- "[TICKER] long" with "Entry: 1)price 2)price" and "targets: 1-5)" = ENTRY signal
- "Grabbed some BTC at 43.2k" = ENTRY, ticker BTC, price 43200, direction long
- "Bought more [TICKER] here" = ENTRY signal
- Crypto tickers: BTC, ETH, SOL, HYPE, DOGE, XRP, AVAX, LINK, etc.
- asset_type MUST be "crypto" for these

OPTIONS ENTRIES:
- "[TICKER] [STRIKE][C/P] [MM/DD/YY]" = ENTRY signal (option)
- "IBIT 39.5c 2/14" = ticker IBIT, strike 39.5, direction call, expiry 2026-02-14
- "CLSK 10c 2/20/26 @0.50" = ticker CLSK, strike 10, direction call, price 0.50
- "Hood 75c 2/20/26 @1.15 lightly play" = ticker HOOD, strike 75, direction call
- ANY message with "[TICKER] [NUMBER][c/C/p/P] [DATE]" is an ENTRY
- "lightly play" or "small play" or "starter" = still an ENTRY

TRIM SIGNALS (CRITICAL — these were heavily misclassified):
- "TP[1-6] hit" replying to entry = TRIM signal
- "BANG!" / "BANGGGG" = take profit, TRIM signal
- "[ticker] calls/puts up X% ... trim and set stops" = TRIM signal
- "[ticker] calls/puts up X%" (substantial gain >20%) = TRIM signal
- "trim and set stops" = TRIM signal
- "$X to $Y" with profit update = TRIM if substantial
- "Still printing" = TRIM/hold signal
- KEY: ALL of these reply to the original entry via Discord reply_to_message_id

EXIT SIGNALS:
- "ALL TPs hit" = full exit
- "Closed my [long/short/runner]" = EXIT
- "Stopped out" = EXIT
- "Going to cut these" = EXIT
- "Sold rest of my [ticker]" = EXIT (NOT an entry!)

NOISE — NOT ENTRIES:
- Report cards with 🍏🍎🥚 = NOISE (weekly/monthly summaries listing tickers)
- "Still holding" = NOISE (position update, not new entry)
- "Going to leave some money" = NOISE (adjusting stops, not entry)
- Giveaways, Instagram links, GIFs = NOISE
- "Cancel bid" = NOISE
- "don't forget to trim" = NOISE (general advice)
- Market commentary = NOISE
- Dashes "—" = NOISE

HEDGING (future consideration):
- Grizzlies sometimes has BTC long AND BTC short simultaneously
- Direction matching is critical — must track direction per position

QUOTED/REPLY CONTEXT:
- Messages starting with "[Replying to: ...]" are replies to a previous message
- Use quoted message to identify ticker, strike, expiry when reply doesn't specify
- This is Grizzlies' primary communication pattern for trims/exits
"""
        
        return base_prompt + grizzlies_context

    # Profit update patterns — these are TRIMS, not entries
    PROFIT_UPDATE_RE = re.compile(
        r'(?:up|running|printing)\s+\d+%|'
        r'\d+%\s+(?:profit|gain|runner)|'
        r'trim\s+(?:and\s+)?set\s+stops?|'
        r'BANG+!*|'
        r'TP\s*[1-6](?:\s|$|[^a-zA-Z])|'  # "TP2", "TP1 hit", "Tp3 on btc"
        r'still\s+printing',
        re.IGNORECASE
    )

    # "Sold rest" / "going to cut" / "closing X" / "cutting X" — EXIT not entry
    EXIT_LANGUAGE_RE = re.compile(
        r'\ball\s+TPs?\s+hit\b|'
        # Present progressive — Grizzlies' dominant pattern: "Closing ibit puts", "Cutting calls"
        r'\b(?:closing|cutting)\b|'
        # Past + bare imperative: "closed the rest", "close here", "closed out", "closed on a -X% loss"
        r'\bclosed?\s+(?:the\s+|my\s+|out\b|here\b|on\s+a\b)|'
        # "Cut -25%", "Cut this", "Cut here", "cut ibit calls"
        r'\bcut\s+(?:-?\d+%|this|here|\w+\s+(?:calls?|puts?))|'
        # Stops — allow "stopped me out"
        r'\bstopped?\s+(?:me\s+)?out\b|'
        # Sold — allow "entire"/"all" not just "rest"
        r'\bsold\s+(?:the\s+)?(?:rest|entire|all)\b|'
        # Intent
        r'\bgoing\s+to\s+(?:cut|close)\b',
        re.IGNORECASE
    )

    # Splits router-prepended "[Replying to: <ref>]\n\n<current>" into (reply_block, current).
    # Action verbs (EXIT/TRIM) and entry patterns (OPTION/CRYPTO) must match in the
    # current message — otherwise replies-to-an-entry get misread as new entries, and
    # replies-to-an-exit get misread as new exits.
    _REPLY_PREFIX_RE = re.compile(r'^\[Replying to:\s*(.*?)\]\s*\n\n', re.DOTALL)

    @staticmethod
    def _split_reply_block(message: str) -> tuple[str, str]:
        m = GrizzliesParser._REPLY_PREFIX_RE.match(message)
        if m:
            return m.group(1), message[m.end():]
        return "", message

    @staticmethod
    def extract_details(message: str, message_id: str = "",
                       timestamp: str = "") -> Optional[ParsedSignal]:
        """Extract signal details via regex — no Gemini needed for clear patterns.

        Action verbs and entry patterns are matched against the CURRENT message only.
        The reply context is used only to inherit ticker when the current message omits it.
        """
        reply_block, current = GrizzliesParser._split_reply_block(message)

        if GrizzliesParser.is_noise(current):
            return None

        msg_lower = current.lower()

        # Exit detection — current message only
        if GrizzliesParser.EXIT_LANGUAGE_RE.search(current):
            ticker = (GrizzliesParser._extract_ticker_from_message(current)
                      or GrizzliesParser._extract_ticker_from_message(reply_block))
            return ParsedSignal(
                analyst="grizzlies",
                action=SignalAction.EXIT.value,
                asset_type=AssetType.CRYPTO.value,
                ticker=ticker or "",
                direction=None, strike=None, expiry=None,
                entry_price=None, trim_fraction=1.0,
                confidence=0.90,
                raw_message=message, message_id=message_id, timestamp=timestamp,
            )

        # Profit update / trim detection — current message only
        if GrizzliesParser.PROFIT_UPDATE_RE.search(current):
            ticker = (GrizzliesParser._extract_ticker_from_message(current)
                      or GrizzliesParser._extract_ticker_from_message(reply_block))
            return ParsedSignal(
                analyst="grizzlies",
                action=SignalAction.TRIM.value,
                asset_type=AssetType.CRYPTO.value,
                ticker=ticker or "",
                direction=None, strike=None, expiry=None,
                entry_price=None, trim_fraction=None,
                confidence=0.88,
                raw_message=message, message_id=message_id, timestamp=timestamp,
            )

        # Option entry — must match in current message (not the prepended reply context)
        opt_match = re.search(GrizzliesParser.OPTION_PATTERN, current)
        if opt_match:
            ticker = opt_match.group(1).upper()
            # Groups 2,3 = "STRIKEc DATE" format; Groups 4,5 = "DATE STRIKEc" format
            if opt_match.group(2):  # TICKER STRIKEc MM/DD
                strike_str = opt_match.group(2)
                expiry_raw = opt_match.group(3)
            else:  # TICKER MM/DD STRIKEc
                expiry_raw = opt_match.group(4)
                strike_str = opt_match.group(5)

            # Determine C or P from the character after strike (in current msg)
            cp_match = re.search(r'(\d+(?:\.\d+)?)\s*([cCpP])', current)
            direction = 'call' if cp_match and cp_match.group(2).lower() == 'c' else 'put'

            year = datetime.now().year
            month, day = expiry_raw.split('/')
            expiry = f"{year}-{month.zfill(2)}-{day.zfill(2)}"
            try:
                datetime.strptime(expiry, "%Y-%m-%d")
            except ValueError:
                return None

            # Extract price if present (from current msg)
            price_match = re.search(r'[@]\s*\$?(\d+(?:\.\d+)?)', current)
            price = float(price_match.group(1)) if price_match else None

            return ParsedSignal(
                analyst="grizzlies",
                action=SignalAction.ENTRY.value,
                asset_type=AssetType.OPTION.value,
                ticker=ticker, direction=direction,
                strike=float(strike_str), expiry=expiry,
                entry_price=price, trim_fraction=None,
                confidence=0.92,
                raw_message=message, message_id=message_id, timestamp=timestamp,
            )

        # Crypto entry — must match in current message
        entry_match = re.search(GrizzliesParser.ENTRY_PATTERN, current)
        if entry_match and re.search(GrizzliesParser.CRYPTO_PATTERN, current):
            ticker_match = re.search(GrizzliesParser.CRYPTO_PATTERN, current)
            ticker = ticker_match.group(1).upper()
            entries = GrizzliesParser._extract_entries(current)
            targets = GrizzliesParser._extract_targets(current)
            stop_match = re.search(GrizzliesParser.STOP_PATTERN, current)

            direction = "short" if "short" in msg_lower else "long"

            return ParsedSignal(
                analyst="grizzlies",
                action=SignalAction.ENTRY.value,
                asset_type=AssetType.CRYPTO.value,
                ticker=ticker, direction=direction,
                strike=None, expiry=None,
                entry_price=entries[0] if entries else None,
                trim_fraction=None, confidence=0.90,
                raw_message=message, message_id=message_id, timestamp=timestamp,
                stop_price=float(stop_match.group(1)) if stop_match else None,
                target_prices=targets if targets else None,
            )

        return None  # Fall to Gemini

    # Known option tickers Grizzlies trades (ETFs + stocks) — case insensitive matching
    KNOWN_TICKERS_RE = re.compile(
        r'\b(IBIT|BITO|MSTR|COIN|GBTC|ETHE|BITX|MARA|RIOT|CLSK|HUT|'
        r'HOOD|SPY|QQQ|AAPL|MSFT|NVDA|AMD|TSLA|META|AMZN|NFLX|GOOGL)\b',
        re.IGNORECASE
    )

    @staticmethod
    def _extract_ticker_from_message(message: str) -> Optional[str]:
        """Extract most likely ticker from a Grizzlies message."""
        # Check crypto tickers first (BTC, ETH, SOL, etc.)
        crypto_match = re.search(GrizzliesParser.CRYPTO_PATTERN, message)
        if crypto_match:
            return crypto_match.group(1).upper()
        # Check known option/ETF tickers (case insensitive — catches "Ibit", "Hood", etc.)
        known_match = re.search(GrizzliesParser.KNOWN_TICKERS_RE, message)
        if known_match:
            return known_match.group(1).upper()
        # Fall back to any uppercase ticker
        skip = {'BANG', 'ALL', 'TPS', 'STILL', 'THE', 'AND', 'FOR', 'NOT'}
        tickers = re.findall(r'\b([A-Z]{2,5})\b', message)
        for t in tickers:
            if t not in skip:
                return t
        return None

    @staticmethod
    def post_process_signal(signal: ParsedSignal, original_message: str) -> ParsedSignal:
        """Apply Grizzlies-specific post-processing and validation."""
        
        if not signal:
            return signal
            
        # Detect crypto vs options
        # Options take priority: if signal has strike/expiry or ticker is a crypto ETF, it's an option
        crypto_etfs = {'IBIT', 'BITO', 'MSTR', 'COIN', 'GBTC', 'ETHE', 'BITX', 'MARA', 'RIOT', 'CLSK', 'HUT'}
        ticker_upper = (signal.ticker or '').upper()
        
        if ticker_upper in crypto_etfs or signal.strike or signal.expiry:
            # ETFs and anything with strike/expiry are options, never crypto
            signal.asset_type = AssetType.OPTION
            if ticker_upper in crypto_etfs:
                logger.info("Classified %s as OPTION (crypto ETF, not raw crypto)", ticker_upper)
        elif GrizzliesParser._is_option_signal(original_message):
            signal.asset_type = AssetType.OPTION
        elif GrizzliesParser._is_crypto_signal(original_message):
            signal.asset_type = AssetType.CRYPTO
            
        # Apply Grizzlies stop loss default (-25%)
        if signal.action == SignalAction.ENTRY:
            # Grizzlies typically uses -25% stop loss
            pass  # This will be handled by position manager
            
        # Detect position sizing hints
        message_lower = original_message.lower()
        if any(word in message_lower for word in ['lotto', 'degen', 'yolo', 'gamble']):
            # This is a high-risk play - position manager should use smaller size
            pass
            
        # Extract multiple entries if present
        entries = GrizzliesParser._extract_entries(original_message)
        if entries and len(entries) > 1:
            # Use first entry as primary, but log multiple entries available
            signal.entry_price = entries[0]
            logger.info("Multiple entries detected for %s: %s", signal.ticker, entries)
            
        # Extract targets
        targets = GrizzliesParser._extract_targets(original_message)
        if targets:
            logger.info("Targets for %s: %s", signal.ticker, targets)
            
        return signal
    
    @staticmethod
    def _is_crypto_signal(message: str) -> bool:
        """Detect if this is a crypto signal."""
        return bool(re.search(GrizzliesParser.CRYPTO_PATTERN, message))
    
    @staticmethod
    def _is_option_signal(message: str) -> bool:
        """Detect if this is an options signal."""
        return bool(re.search(GrizzliesParser.OPTION_PATTERN, message))
    
    @staticmethod
    def _extract_entries(message: str) -> list[float]:
        """Extract multiple entry prices."""
        entries = []
        
        match = re.search(GrizzliesParser.ENTRY_PATTERN, message)
        if match:
            entry_text = match.group(1)
            # Parse comma/space separated prices
            prices = re.findall(r'([0-9]+(?:\.[0-9]+)?)', entry_text)
            entries = [float(p) for p in prices]
            
        return entries
    
    @staticmethod
    def _extract_targets(message: str) -> list[float]:
        """Extract multiple target prices."""
        targets = []
        
        match = re.search(GrizzliesParser.TARGET_PATTERN, message)
        if match:
            target_text = match.group(1)
            # Parse comma/space separated prices
            prices = re.findall(r'([0-9]+(?:\.[0-9]+)?)', target_text)
            targets = [float(p) for p in prices]
            
        return targets