"""Gemini Flash LLM parser — Tier 3 fallback parsing engine.

Uses Gemini for signals that regex + library matching can't handle.
Gracefully degrades if API key is missing/invalid.
"""

import logging
import time
from typing import Optional

from config import Config
from parsers.base import ParsedSignal, SignalAction, AssetType
from parsers.waxui import WaxuiParser
from parsers.grizzlies import GrizzliesParser
from parsers.enhanced_market import EnhancedMarketParser
from parsers.ecs import ECSParser
from parsers.eva import EvaParser
from parsers.ace import AceParser
from parsers.nando import NandoParser
from parsers.zabes import ZabesParser

logger = logging.getLogger(__name__)

# Try new SDK first, fall back to deprecated
_genai = None
_genai_version = None
try:
    from google import genai as _genai
    _genai_version = "new"
except ImportError:
    try:
        import google.generativeai as _genai
        _genai_version = "legacy"
    except ImportError:
        logger.warning("No Gemini SDK installed — Tier 3 parsing disabled")


class GeminiParser:
    def __init__(self, config: Config):
        self.config = config
        self.model = None
        self._available = False

        if not config.gemini_api_key:
            logger.warning("No GEMINI_API_KEY — Tier 3 parsing disabled")
            return

        if _genai is None:
            logger.warning("Gemini SDK not installed — Tier 3 parsing disabled")
            return

        # Blocker 4: a network blip that resolves DNS but can't complete the
        # connection would otherwise hang the blocking genai call indefinitely
        # (froze the test suite >2min once). A hard timeout converts that hang
        # into a fast failure that parse()'s except already handles.
        self._timeout_s = float(getattr(config, "gemini_timeout_seconds", 10) or 10)

        try:
            if _genai_version == "new":
                http_options = None
                try:
                    # HttpOptions.timeout is in MILLISECONDS.
                    http_options = _genai.types.HttpOptions(
                        timeout=int(self._timeout_s * 1000)
                    )
                except Exception:
                    logger.warning("google.genai HttpOptions unavailable — client timeout not set")
                self._client = _genai.Client(
                    api_key=config.gemini_api_key,
                    http_options=http_options,
                ) if http_options else _genai.Client(api_key=config.gemini_api_key)
                self._available = True
                logger.info("Gemini parser ready (google.genai SDK, timeout=%.0fs)", self._timeout_s)
            else:
                _genai.configure(api_key=config.gemini_api_key)
                self.model = _genai.GenerativeModel('gemini-2.5-flash')
                self._available = True
                logger.info("Gemini parser ready (legacy SDK — consider upgrading to google.genai)")
        except Exception:
            logger.exception("Gemini initialization failed — Tier 3 parsing disabled")

    def health_check(self) -> tuple[bool, str]:
        """Actually probe the API. `_available` only means the client object
        constructed — an INVALID key fails only on the first real call, so a
        live probe is the only way to know the fallback works."""
        if not self._available:
            return False, "unavailable (no key or SDK)"
        try:
            if _genai_version == "new":
                self._client.models.generate_content(model="gemini-2.5-flash", contents="ping")
            else:
                self.model.generate_content("ping", request_options={"timeout": self._timeout_s})
            return True, "ok"
        except Exception as e:  # noqa: BLE001 — surface the reason
            return False, str(e).splitlines()[0][:180]

    @staticmethod
    def _is_transient(exc: Exception) -> bool:
        """A retry-worthy failure: a 5xx / timeout / 'unavailable' from Google's
        side (e.g. the 504 Gateway Timeout that tripped the gate on 07-30), not a
        4xx we caused. Best-effort match on the SDK error's code/text."""
        s = f"{getattr(exc, 'code', '')} {getattr(exc, 'status', '')} {exc}".lower()
        return any(t in s for t in
                   ("500", "502", "503", "504", "timeout", "deadline",
                    "unavailable", "gateway", "overloaded"))

    def _generate_text(self, prompt: str, message_id: str) -> str:
        """Call Gemini once, retrying ONCE on a transient server error. The
        per-call timeout (Blocker 4) still bounds each attempt, so worst case is
        two bounded waits, never a hang."""
        last_exc = None
        for attempt in (1, 2):
            try:
                if _genai_version == "new":
                    resp = self._client.models.generate_content(
                        model="gemini-2.5-flash", contents=prompt)
                else:
                    resp = self.model.generate_content(
                        prompt, request_options={"timeout": self._timeout_s})
                return resp.text
            except Exception as e:  # noqa: BLE001
                last_exc = e
                if attempt == 1 and self._is_transient(e):
                    logger.warning("Gemini transient error on msg %s (%s) — retrying once",
                                   message_id, str(e).splitlines()[0][:120])
                    time.sleep(0.75)
                    continue
                raise
        raise last_exc

    def parse(self, message: str, channel_id: str, message_id: str,
              timestamp: str, hint_action: str = None) -> Optional[ParsedSignal]:
        """Parse Discord message using Gemini Flash."""
        if not self._available:
            logger.debug("Gemini unavailable — skipping Tier 3 for %s", message_id)
            return None

        try:
            analyst = self.config.channel_to_analyst.get(channel_id, "unknown")

            # Get analyst-specific prompt
            prompt = self._build_prompt(message, analyst, hint_action=hint_action)

            # Generate response (both SDK versions; retries once on a transient 5xx)
            response_text = self._generate_text(prompt, message_id)

            # Parse structured response
            parsed_signal = self._extract_signal(
                response_text, message, analyst, message_id, timestamp
            )

            return parsed_signal

        except Exception:
            logger.exception("Gemini parsing failed for message %s", message_id)
            return None

    def _build_prompt(self, message: str, analyst: str, hint_action: str = None) -> str:
        """Build analyst-specific parsing prompt."""
        hint_text = ""
        if hint_action:
            hint_text = f"""
IMPORTANT: This signal has been pre-classified as "{hint_action}" by our signal library 
with high confidence. Do NOT override this classification. Your job is only to extract 
the details (ticker, strike, expiry, price, direction). Set action to "{hint_action}".
"""
        base_prompt = f"""You are a trading signal parser for the {analyst} analyst Discord channel.
{hint_text}

Parse this message and extract trading signal information. Respond with ONLY a JSON object containing:
{{
    "action": "entry|exit|trim|stop_hit|info",
    "asset_type": "option|crypto|stock", 
    "ticker": "ticker_symbol",
    "direction": "call|put|long|short|null",
    "strike": number_or_null,
    "expiry": "YYYY-MM-DD_or_null",
    "entry_price": number_or_null,
    "trim_fraction": number_or_null,
    "confidence": 0.0_to_1.0
}}

CRITICAL ENTRY DETECTION RULES:
- "**LOTTO**" or "**HIGH RISK**" followed by a ticker = ENTRY signal (action: "entry")
- "[TICKER] here [DATE] [STRIKE][C/P] Avg. [PRICE]" = ENTRY signal
- These are ALWAYS entries even if the word "entry" is not used
- For options: extract the strike price and C/P direction from patterns like "6860P" (strike=6860, direction=put) or "682C" (strike=682, direction=call)
- For expiry: "02/13" means 2026-02-13 (current year)
- SPX strikes are typically 4000-7000 range; SPY strikes are 400-700 range

TRIM DETECTION:
- "Trim [TICKER] here [price1] - [price2] ✅ [pct]%" = TRIM signal
- "More [TICKER] here" with prices = TRIM signal  
- "Holding most/majority/half/runners" = context for trim amount
- trim_fraction: 0.2 for each mechanical trim step

CANCEL / RETRACT DETECTION (CRITICAL):
- "cancel bid" or "cancelling" or "scratch that" or "nvm" or "pulling" = action "info" (NOT an entry!)
- If a message references a previous entry AND contains cancel language, it is NOT a new entry
- "didn't fill" or "no fill" = info only

EXIT DETECTION:
- "Closed [TICKER]" = EXIT signal
- "Done for today" = info only, not an exit

Message to parse:
{message}

If this is not a tradeable signal (just commentary, chat, etc.), respond with: {{"action": "info", "confidence": 0.0}}
"""
        # Add analyst-specific context
        if analyst == "waxui":
            base_prompt = WaxuiParser.enhance_prompt(base_prompt, message)
        elif analyst == "grizzlies":
            base_prompt = GrizzliesParser.enhance_prompt(base_prompt, message)
        elif analyst == "enhanced_market":
            base_prompt = EnhancedMarketParser.enhance_prompt(base_prompt, message)
        elif analyst == "ecs":
            base_prompt = ECSParser.enhance_prompt(base_prompt, message)
        elif analyst == "eva":
            base_prompt = EvaParser.enhance_prompt(base_prompt, message)
        elif analyst == "ace":
            base_prompt = AceParser.enhance_prompt(base_prompt, message)
        elif analyst == "nando":
            base_prompt = NandoParser.enhance_prompt(base_prompt, message)
        elif analyst == "zabes":
            base_prompt = ZabesParser.enhance_prompt(base_prompt, message)
        
        return base_prompt

    def _extract_signal(self, response_text: str, original_message: str, 
                       analyst: str, message_id: str, timestamp: str) -> Optional[ParsedSignal]:
        """Extract ParsedSignal from Gemini response."""
        try:
            import json
            
            # Clean response - extract JSON
            response_text = response_text.strip()
            if response_text.startswith('```json'):
                response_text = response_text[7:]
            if response_text.endswith('```'):
                response_text = response_text[:-3]
            
            data = json.loads(response_text.strip())
            
            # Validate required fields
            if data.get("confidence", 0) < 0.3:
                return None
                
            signal = ParsedSignal(
                analyst=analyst,
                action=data.get("action", "info"),
                asset_type=data.get("asset_type", "stock"),
                ticker=data.get("ticker", "").upper(),
                direction=data.get("direction"),
                strike=data.get("strike"),
                expiry=data.get("expiry"),
                entry_price=data.get("entry_price"),
                trim_fraction=data.get("trim_fraction"),
                confidence=data.get("confidence", 0.0),
                raw_message=original_message,
                message_id=message_id,
                timestamp=timestamp,
                stop_price=data.get("stop_price"),
                target_prices=data.get("target_prices"),
            )
            
            # Fix stale expiry years (Gemini sometimes returns 2024/2025 instead of 2026)
            if signal.expiry:
                try:
                    from datetime import datetime, timezone
                    exp_date = datetime.fromisoformat(signal.expiry).date()
                    today = datetime.now(timezone.utc).date()
                    if exp_date < today:
                        exp_date = exp_date.replace(year=today.year)
                        # If still in the past (e.g., Jan date parsed in Dec), try next year
                        if exp_date < today:
                            exp_date = exp_date.replace(year=today.year + 1)
                        signal.expiry = exp_date.isoformat()
                        logger.warning("Fixed stale expiry year → %s for %s", signal.expiry, signal.ticker)
                except Exception:
                    logger.warning("Could not fix expiry date: %s", signal.expiry)

            # Run analyst-specific post-processing (fixes expiry years, trim fractions, etc.)
            if analyst == "waxui":
                signal = WaxuiParser.post_process_signal(signal, original_message)
            elif analyst == "grizzlies":
                signal = GrizzliesParser.post_process_signal(signal, original_message)
            elif analyst == "enhanced_market":
                signal = EnhancedMarketParser.post_process_signal(signal, original_message)
            elif analyst == "ecs":
                signal = ECSParser.post_process_signal(signal, original_message)
            elif analyst == "eva":
                signal = EvaParser.post_process_signal(signal, original_message)
            elif analyst == "nando":
                signal = NandoParser.post_process_signal(signal, original_message)
            elif analyst == "zabes":
                signal = ZabesParser.post_process_signal(signal, original_message)
            
            return signal
            
        except Exception:
            logger.exception("Failed to extract signal from Gemini response: %s", response_text[:200])
            return None