"""Signal Router — routes Discord messages to appropriate analyst parser."""

import json
import logging
import os
from typing import Optional

from config import Config
from parsers.base import ParsedSignal, AssetType, SignalAction
from parsers.gemini_parser import GeminiParser
from parsers.ticker_decoder import TickerDecoder
from parsers.ecs import ECSParser
from parsers.waxui import WaxuiParser
from parsers.grizzlies import GrizzliesParser
from parsers.zabes import ZabesParser
from parsers.eva import EvaParser
from parsers.ace import AceParser
from parsers.obsidian_matcher import match as obsidian_match, append_to_library

logger = logging.getLogger(__name__)


class SignalRouter:
    def __init__(self, config: Config):
        self.config = config
        self.gemini_parser = GeminiParser(config)
        self.ticker_decoder = TickerDecoder()
        
        # Load crypto ticker set for asset_type correction
        self._crypto_tickers: set[str] = set()
        crypto_path = os.path.join(os.path.dirname(__file__), "data", "crypto_tickers.json")
        try:
            with open(crypto_path) as f:
                self._crypto_tickers = set(json.load(f))
            logger.info("Loaded %d crypto tickers for classification", len(self._crypto_tickers))
        except Exception:
            logger.warning("Could not load crypto_tickers.json — crypto classification disabled")
    
    def route_message(self, channel_id: str, message_id: str, content: str, 
                     timestamp: str, embeds: list = None,
                     referenced_message: str = None) -> Optional[ParsedSignal]:
        """Route message to appropriate parser based on channel."""
        
        if channel_id not in self.config.watched_channels:
            logger.debug("Ignoring message from unwatched channel %s", channel_id)
            return None
            
        if not content.strip() and not embeds:
            logger.debug("Empty message %s, skipping", message_id)
            return None
            
        # For ECS channel, intercept alert bot messages as TRIM signals
        if channel_id == self.config.discord_channel_ecs and ECSParser.is_alert_bot_message(content):
            signal = ECSParser.parse_alert_bot_message(content, message_id, timestamp)
            if signal:
                logger.info("ECS alert bot → %s %s @ $%.6f", signal.action, signal.ticker, signal.entry_price or 0)
                return signal
            # If alert bot but not a profit target, skip
            return None

        # For Enhanced Market, check embeds first (structured signals)
        if channel_id == self.config.discord_channel_em and embeds:
            structured_content = self._extract_embed_content(embeds)
            if structured_content:
                content = structured_content
        
        # For Eva channel, extract embed content (signals come via bot embeds)
        if channel_id == self.config.discord_channel_eva and embeds:
            structured_content = self._extract_embed_content(embeds)
            if structured_content:
                content = structured_content

        # Decode obfuscated tickers (especially for Waxui)
        decoded_content = self.ticker_decoder.decode_message(content, channel_id)
        
        # Prepend referenced (quoted) message for context — critical for Grizzlies
        # where replies like "Trimming half here" reference the original entry
        parse_content = decoded_content
        if referenced_message and referenced_message.strip():
            # Also decode the referenced message
            decoded_ref = self.ticker_decoder.decode_message(referenced_message, channel_id)
            parse_content = f"[Replying to: {decoded_ref.strip()}]\n\n{decoded_content}"
        
        analyst = self.config.channel_to_analyst.get(channel_id, "unknown")
        
        # === NOISE SHORT-CIRCUIT: Skip obvious noise before any parsing ===
        if analyst == "waxui" and WaxuiParser.is_noise(parse_content):
            logger.info("Waxui noise short-circuit: %s", message_id)
            return None
        if analyst == "grizzlies" and GrizzliesParser.is_noise(parse_content):
            logger.info("Grizzlies noise short-circuit: %s", message_id)
            return None
        if analyst == "zabes" and ZabesParser.is_noise(parse_content):
            logger.info("Zabes noise short-circuit: %s", message_id)
            return None
        if analyst == "eva" and EvaParser.is_noise(parse_content):
            logger.info("Eva noise short-circuit: %s", message_id)
            return None
        if analyst == "ace" and AceParser.is_noise(parse_content):
            logger.info("Ace noise short-circuit (recap): %s", message_id)
            return None
        # Image-only messages (Discord CDN with no signal)
        if parse_content.strip().startswith('https://cdn.discordapp.com/') and '\n' not in parse_content.strip():
            logger.debug("Image-only message, skipping: %s", message_id)
            return None
        
        # === TIER 1 & 2: Obsidian Signal Library matching (free, instant) ===
        library_match = obsidian_match(parse_content, analyst)
        if library_match:
            # Library matched — convert to ParsedSignal
            # Map signal_type to action
            action_map = {
                "ENTRY": SignalAction.ENTRY.value,
                "TRIM": SignalAction.TRIM.value,
                "EXIT": SignalAction.EXIT.value,
                "NOISE": SignalAction.INFO.value,
            }
            action = action_map.get(library_match.signal_type, SignalAction.INFO.value)
            
            if action == SignalAction.INFO.value:
                # NOISE — skip entirely, don't waste Gemini call
                logger.info("Library match → NOISE (Tier %d, %.2f) — skipping: %s", 
                           library_match.tier, library_match.score, message_id)
                return None
            
            # For actionable signals (ENTRY/TRIM/EXIT), try regex extraction first (FREE)
            # Only fall back to Gemini if regex can't extract details
            signal = self._run_regex_extractor(analyst, parse_content, message_id, timestamp)
            if signal:
                # Override action with library classification (regex may disagree)
                signal.action = action
                signal.confidence = max(signal.confidence, 0.92)
                logger.info("Library match → %s (Tier %d) + regex extraction — NO Gemini needed: %s",
                           library_match.signal_type, library_match.tier, message_id)
            
            if not signal:
                # Regex couldn't extract — fall back to Gemini with hint
                logger.info("Library match → %s (Tier %d, %.2f) — enriching via Gemini: %s",
                           library_match.signal_type, library_match.tier, library_match.score, message_id)
                signal = self.gemini_parser.parse(parse_content, channel_id, message_id, timestamp,
                                                  hint_action=action)
                if signal:
                    # Override Gemini's action with library's classification
                    signal.action = action
                    # Boost confidence since library confirmed the type
                    signal.confidence = max(signal.confidence, 0.9)
        else:
            # === TIER 2.5: Try regex extraction BEFORE Gemini (free, instant) ===
            signal = self._run_regex_extractor(analyst, parse_content, message_id, timestamp)
            if signal:
                logger.info("Regex extraction (no library match) → %s %s — NO Gemini needed: %s",
                           signal.action, signal.ticker, message_id)

            # === TIER 3: Full Gemini parsing (no library or regex match) ===
            if not signal:
                signal = self.gemini_parser.parse(parse_content, channel_id, message_id, timestamp)
        
        if signal and signal.confidence >= 0.7:
            # Auto-learn: if Tier 3 classified this, add to library for future matching
            # GUARD: Only auto-learn at HIGH confidence (0.9+) to avoid polluting
            # the validated library with Gemini mistakes. We saw 19-40% error rates
            # in the signal audit — don't let bad classifications sneak back in.
            # Also: only auto-learn NOISE (safe) and verified entries/exits (risky).
            auto_learn_threshold = 0.9
            safe_auto_learn = signal.action == SignalAction.INFO.value  # NOISE is safe to auto-learn
            
            if not library_match and signal.confidence >= auto_learn_threshold and signal.action in (
                SignalAction.ENTRY.value, SignalAction.TRIM.value, 
                SignalAction.EXIT.value, SignalAction.INFO.value
            ):
                lib_type_map = {
                    SignalAction.ENTRY.value: "ENTRY",
                    SignalAction.TRIM.value: "TRIM",
                    SignalAction.EXIT.value: "EXIT",
                    SignalAction.INFO.value: "NOISE",
                }
                lib_type = lib_type_map.get(signal.action, "NOISE")
                
                # For actionable signals (ENTRY/TRIM/EXIT), log but DON'T auto-append
                # until we have human validation. Noise is safe to append.
                if safe_auto_learn:
                    try:
                        append_to_library(analyst, lib_type, content)
                        logger.info("Auto-learned NOISE → %s/NOISE library", analyst)
                    except Exception:
                        logger.warning("Failed to auto-learn noise to library")
                else:
                    # Log for manual review instead of auto-appending
                    logger.info("Tier 3 signal (%s, conf=%.2f) NOT auto-learned — needs validation: %s %s",
                               lib_type, signal.confidence, analyst, signal.ticker)
            
            # Post-process: correct asset_type if ticker is a known crypto
            # BUT don't reclassify if it already has option details (strike/expiry) — that means
            # the parser correctly identified it as an option, not crypto
            if (signal.ticker.upper() in self._crypto_tickers 
                    and signal.asset_type != AssetType.CRYPTO
                    and not signal.strike 
                    and not signal.expiry):
                logger.info("Reclassified %s from %s → crypto", signal.ticker, signal.asset_type)
                signal.asset_type = AssetType.CRYPTO
            
            # Hard execution threshold — don't send low-confidence signals to execution
            # Library matches (Tier 1/2) get 0.9+ confidence. Only Tier 3 (Gemini) can be low.
            # Our audit showed 19-60% Gemini error rates — 0.8 minimum prevents the worst misparses.
            MIN_EXECUTION_CONFIDENCE = 0.8
            if signal.confidence < MIN_EXECUTION_CONFIDENCE and signal.action != SignalAction.INFO.value:
                logger.warning("⚠️ LOW CONFIDENCE (%.2f < %.2f) — blocking %s %s %s from execution",
                             signal.confidence, MIN_EXECUTION_CONFIDENCE,
                             analyst, signal.action, signal.ticker)
                return None  # Don't execute, don't return as actionable
            
            logger.info("Parsed %s signal: %s %s %s (%.2f confidence)", 
                       analyst, signal.action, signal.ticker, signal.direction, signal.confidence)
            return signal
        
        # TODO: Add fallback parsers for low-confidence signals
        # if signal and signal.confidence < 0.7:
        #     signal = self.openai_parser.parse(content, channel_id, message_id, timestamp)
        #     if signal and signal.confidence >= 0.6:
        #         return signal
        
        logger.debug("No parseable signal found in message %s", message_id)
        return None
    
    # ── SHADOW / LOG-ONLY classification ──────────────────────────────
    # Mirrors the tier logic of route_message but stops BEFORE Gemini and
    # returns the tier that handled the message. It never touches execution:
    # it returns data, and main.py's shadow branch returns before dispatch.

    TIER_NOISE = "noise-skip"
    TIER_REGEX = "regex-extract"
    TIER_GEMINI = "would-hit-Gemini"
    TIER_UNPARSED = "unparsed"

    def classify_shadow(self, channel_id: str, message_id: str, content: str,
                        timestamp: str, embeds: list = None,
                        referenced_message: str = None) -> tuple[Optional[ParsedSignal], str]:
        """Regex-tier-only observation parse. Returns (signal_or_None, tier).
        Never calls Gemini. Thin wrapper over shadow_audit for callers that only
        want the deterministic verdict (and its tests)."""
        r = self.shadow_audit(channel_id, message_id, content, timestamp,
                              embeds, referenced_message, run_gemini=False)
        return r["regex_signal"], r["tier"]

    def shadow_audit(self, channel_id: str, message_id: str, content: str,
                     timestamp: str, embeds: list = None,
                     referenced_message: str = None, run_gemini: bool = False) -> dict:
        """Full observation parse for the audit trail. Runs the regex tier
        always; when `run_gemini` and regex MISSES, also runs the Gemini tier —
        exactly matching production, where Gemini is consulted only on a regex
        miss. Executes nothing. Returns both verdicts:

            {regex_signal, tier, gemini_signal, gemini_ran, gemini_error}

        This is the dataset for trusting Waxui execution: for every message we
        capture what the deterministic parser said AND what the LLM fallback
        would have said, so misparses (e.g. a watchlist idea read as a BUY) are
        caught in the log before they could ever fire live.
        """
        analyst = self.config.channel_to_analyst.get(channel_id, "unknown")
        result = {"regex_signal": None, "tier": self.TIER_NOISE,
                  "gemini_signal": None, "gemini_ran": False, "gemini_error": None}

        if not content.strip() and not embeds:
            return result

        if embeds:
            structured = self._extract_embed_content(embeds)
            if structured:
                content = structured

        parse_content = self.ticker_decoder.decode_message(content, channel_id)
        if referenced_message and referenced_message.strip():
            decoded_ref = self.ticker_decoder.decode_message(referenced_message, channel_id)
            parse_content = f"[Replying to: {decoded_ref.strip()}]\n\n{parse_content}"

        noise_checks = {
            "waxui": WaxuiParser.is_noise,
            "grizzlies": GrizzliesParser.is_noise,
            "zabes": ZabesParser.is_noise,
            "eva": EvaParser.is_noise,
            "ace": AceParser.is_noise,
        }
        is_noise = noise_checks.get(analyst)
        if is_noise and is_noise(parse_content):
            return result  # tier = noise-skip

        library_match = obsidian_match(parse_content, analyst)
        if library_match and library_match.signal_type == "NOISE":
            return result  # tier = noise-skip

        signal = self._run_regex_extractor(analyst, parse_content, message_id, timestamp)
        if signal:
            if library_match:
                action_map = {
                    "ENTRY": SignalAction.ENTRY.value,
                    "TRIM": SignalAction.TRIM.value,
                    "EXIT": SignalAction.EXIT.value,
                }
                signal.action = action_map.get(library_match.signal_type, signal.action)
            result["regex_signal"] = signal
            result["tier"] = self.TIER_REGEX
            return result

        # Regex missed → production would fall through to Gemini here.
        result["tier"] = self.TIER_GEMINI if library_match else self.TIER_UNPARSED
        if run_gemini:
            result["gemini_ran"] = True
            try:
                result["gemini_signal"] = self.gemini_parser.parse(
                    parse_content, channel_id, message_id, timestamp)
            except Exception as exc:  # noqa: BLE001 — parse usually swallows, belt-and-braces
                result["gemini_error"] = str(exc)[:200]
        return result

    # Analysts with a free deterministic regex parser, tried before Gemini.
    REGEX_EXTRACTORS = {
        "waxui": WaxuiParser.extract_details,
        "zabes": ZabesParser.extract_details,
        "grizzlies": GrizzliesParser.extract_details,
        "eva": EvaParser.extract_details,
        "ace": AceParser.extract_details,
    }

    def _run_regex_extractor(self, analyst: str, content: str, message_id: str,
                             timestamp: str) -> Optional[ParsedSignal]:
        """Run the analyst's regex parser, if it has one.

        Ace is the one parser that needs position context: he names the ticker
        on winners ("$AAPL up 40% ✅") but routinely omits it when cutting a
        loser ("cutting here -30% 🛑"). Those tickerless exits resolve against
        the single open Ace position — so the router has to hand them over.
        """
        extractor = self.REGEX_EXTRACTORS.get(analyst)
        if not extractor:
            return None
        if analyst == "ace":
            return extractor(content, message_id, timestamp,
                             open_tickers=self._open_tickers("ace"))
        return extractor(content, message_id, timestamp)

    def _open_tickers(self, analyst: str) -> set:
        """Tickers this analyst currently holds an open position in.

        Returns an empty set on any failure — a tickerless exit is then skipped
        loudly rather than resolved against stale or missing state.
        """
        try:
            from storage.database import Database
            db = Database(getattr(self.config, "db_path", "trading_bot.db"))
            try:
                return {
                    p["ticker"].upper() for p in db.get_open_positions()
                    if p.get("analyst") == analyst and p.get("ticker")
                }
            finally:
                db.close()
        except Exception:
            logger.warning(
                "Could not read open positions for %s — tickerless exits will be "
                "skipped, positions may stay OPEN", analyst,
            )
            return set()

    def _extract_embed_content(self, embeds: list) -> Optional[str]:
        """Extract structured content from Discord embeds (Enhanced Market)."""
        try:
            for embed in embeds:
                # Look for title + description + fields
                parts = []
                
                if embed.get('title'):
                    parts.append(f"Title: {embed['title']}")
                    
                if embed.get('description'):
                    parts.append(f"Description: {embed['description']}")
                
                if embed.get('fields'):
                    for field in embed['fields']:
                        name = field.get('name', '')
                        value = field.get('value', '')
                        if name and value:
                            parts.append(f"{name}: {value}")
                
                if parts:
                    return "\n".join(parts)
                    
        except Exception:
            logger.exception("Failed to extract embed content")
        
        return None