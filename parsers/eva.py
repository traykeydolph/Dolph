"""Eva (EvaPanda) parser — embed-based structured signals."""

import logging
import re
from typing import Optional
from datetime import datetime

from parsers.base import ParsedSignal, SignalAction, AssetType

logger = logging.getLogger(__name__)


class EvaParser:
    """Eva-specific parsing logic for Discord embed signals.

    Eva signals come via the 'EvaPanda Alerts' bot in structured embeds:
    - Entry: [EMBED: Open | BTO PEP 03/20/26 175C @ 0.74 (risky day trade possible swing), TP: 170, 175 (Stop loss under 164)]
    - Exit:  [EMBED: Close | STC PEP 03/20/26 175C @ 0.95 (Good spot to exit half here, holding 1/2 left)]
    - Update: [EMBED: Update: | holding 2 left. Stop loss at break even]
    """

    # BTO pattern: "BTO TICKER MM/DD/YY STRIKEC/P @ PRICE"
    # Also handles "BTO TICKER MM/DD/YY TICKER STRIKEC/P @ PRICE" (duplicate ticker)
    BTO_RE = re.compile(
        r'BTO\s+([A-Z]{1,5})\s+(\d{1,2}/\d{1,2}/?\d{0,4})\s+'
        r'(?:(?:[A-Z]{1,5})\s+)?'  # optional duplicate ticker
        r'(\d+(?:\.\d+)?)\s*([CP])\s+'
        r'@\s*(\d+(?:\.\d+)?)',
        re.IGNORECASE
    )

    # STC pattern: "STC TICKER MM/DD/YY STRIKEC/P @ PRICE"
    STC_RE = re.compile(
        r'STC\s+([A-Z]{1,5})\s+(\d{1,2}/\d{1,2}/?\d{0,4})\s+'
        r'(?:(?:[A-Z]{1,5})\s+)?'
        r'(\d+(?:\.\d+)?)\s*([CP])\s+'
        r'@\s*(\d+(?:\.\d+)?)',
        re.IGNORECASE
    )

    # Noise indicators
    NOISE_PATTERNS = [
        re.compile(r'\bflow\b.*\b(?:into|showing|alert)', re.IGNORECASE),
        re.compile(r'\bwatching\b', re.IGNORECASE),
        re.compile(r'\bi like\b.*\b[CP]\b', re.IGNORECASE),
    ]

    @staticmethod
    def is_noise(message: str) -> bool:
        """Check if this is an Update/flow alert (noise)."""
        # BTO/STC are ALWAYS signals, never noise
        if re.search(r'\bBTO\b', message, re.IGNORECASE) or re.search(r'\bSTC\b', message, re.IGNORECASE):
            return False
        # "Open" and "Close" in title/embed are signals, not noise
        if re.search(r'\bOpen\s*\|', message) or re.search(r'\bClose\s*\|', message):
            return False
        # "Update:" without BTO/STC is noise
        if re.search(r'\bUpdate\s*:', message, re.IGNORECASE):
            return True
        for pattern in EvaParser.NOISE_PATTERNS:
            if pattern.search(message):
                return True
        return False

    @staticmethod
    def extract_details(message: str, message_id: str = "",
                       timestamp: str = "") -> Optional[ParsedSignal]:
        """Extract signal details from Eva embed content."""

        if EvaParser.is_noise(message):
            return None

        # Try BTO (entry)
        bto_match = EvaParser.BTO_RE.search(message)
        if bto_match:
            ticker = bto_match.group(1).upper()
            expiry_raw = bto_match.group(2)
            strike = float(bto_match.group(3))
            direction = 'call' if bto_match.group(4).upper() == 'C' else 'put'
            price = float(bto_match.group(5))

            expiry = EvaParser._parse_expiry(expiry_raw)
            if not expiry:
                return None

            return ParsedSignal(
                analyst="eva",
                action=SignalAction.ENTRY.value,
                asset_type=AssetType.OPTION.value,
                ticker=ticker, direction=direction,
                strike=strike, expiry=expiry,
                entry_price=price, trim_fraction=None,
                confidence=0.95,
                raw_message=message, message_id=message_id, timestamp=timestamp,
            )

        # Try STC (exit/trim)
        stc_match = EvaParser.STC_RE.search(message)
        if stc_match:
            ticker = stc_match.group(1).upper()
            expiry_raw = stc_match.group(2)
            strike = float(stc_match.group(3))
            direction = 'call' if stc_match.group(4).upper() == 'C' else 'put'
            price = float(stc_match.group(5))

            expiry = EvaParser._parse_expiry(expiry_raw)

            # Partial exit indicators → TRIM
            msg_lower = message.lower()
            is_partial = any(term in msg_lower for term in [
                'half', '1/2', '1/4', '1/3', 'holding', 'some', 'scale out',
            ])
            action = SignalAction.TRIM.value if is_partial else SignalAction.EXIT.value

            return ParsedSignal(
                analyst="eva",
                action=action,
                asset_type=AssetType.OPTION.value,
                ticker=ticker, direction=direction,
                strike=strike, expiry=expiry,
                entry_price=price,
                trim_fraction=0.5 if is_partial else 1.0,
                confidence=0.95,
                raw_message=message, message_id=message_id, timestamp=timestamp,
            )

        return None  # Fall to Gemini

    @staticmethod
    def _parse_expiry(raw: str) -> Optional[str]:
        """Parse MM/DD or MM/DD/YY into YYYY-MM-DD."""
        parts = raw.split('/')
        if len(parts) == 2:
            month, day = parts
            year = datetime.now().year
        elif len(parts) == 3:
            month, day, year_part = parts
            year = int(year_part)
            if year < 100:
                year += 2000
        else:
            return None

        expiry = f"{year}-{month.zfill(2)}-{day.zfill(2)}"
        try:
            datetime.strptime(expiry, "%Y-%m-%d")
        except ValueError:
            return None
        return expiry

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
