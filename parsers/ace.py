"""Ace (Ace ♠) parser — two layers, per the 2026-07-21 bake-off.

Layer 1 — ENTRIES are deterministic. Every entry Ace has ever posted is one
of three surface forms wrapping the same option leg
(`$TICKER <strike><c|p> <MM/DD> @<price>`):

    **BTO $AAPL 267.5c 03/04 @0.11 <@&697950067285295115> **
    **took $AMZN 262.5c 04/27 @0.57 <@&697950067285295115> **
    Entering:\\n**$NVDA 217.5c 05/11 @1.75**        (also "Lotto:")

Layer 2 — EXITS/TRIMS are prose with a bounded vocabulary. The bot holds
ONE contract per trade, so there is nothing to trim: any trim/exit marker on
an open Ace position collapses to a full flatten (trim_fraction=1.0). No
fraction extraction is attempted. Because Ace posts a ladder of "up X% ✅"
messages per trade, the FIRST marker flattens and the rest land on an
already-closed ticker, where main.py's exit handler no-ops with an alert.

Anything this module cannot resolve deterministically returns None and logs
loudly (max-verbosity validation mode) — same discipline as parsers/eva.py.
"""

import logging
import re
from datetime import datetime, timedelta
from typing import Optional

from parsers.base import ParsedSignal, SignalAction, AssetType

logger = logging.getLogger(__name__)


class AceParser:
    """Ace-specific parsing logic."""

    # ------------------------------------------------------------------
    # Layer 1: entries
    # ------------------------------------------------------------------

    # The option leg. Strike allows .5 (Ace trades 247.5 / 252.5 / 267.5
    # strikes constantly). Right is glued to the strike ("267.5c") in every
    # corpus sample; the optional \s* is defensive only.
    _LEG = (
        r'\$([A-Z]{1,5})\s+'                 # $TICKER
        r'(\d+(?:\.\d+)?)\s*([CP])\s+'       # strike + right
        r'(\d{1,2}/\d{1,2})\s*'              # MM/DD expiry
        r'@\s*(\d+(?:\.\d+)?)'               # @limit price
    )

    # "BTO $AAPL 267.5c 03/04 @0.11" / "took $AMZN 262.5c 04/27 @0.57"
    ENTRY_VERB_RE = re.compile(r'\b(?:BTO|took)\s+' + _LEG, re.IGNORECASE)

    # "Entering:" / "Lotto:" header on its own line, leg on the next line
    ENTRY_HEADER_RE = re.compile(r'^\s*(?:Entering|Lotto)\s*:', re.IGNORECASE | re.MULTILINE)
    ENTRY_LEG_RE = re.compile(_LEG, re.IGNORECASE)

    # Entry that names a strike but NO right (c/p) — Ace has done this twice
    # ("BTO $AAPL 247.5 03/30 @0.22", corrected two messages later with
    # "calls btw"). Unexecutable without the right: skip loudly.
    RIGHTLESS_ENTRY_RE = re.compile(
        r'\b(?:BTO|took)\s+\$([A-Z]{1,5})\s+(\d+(?:\.\d+)?)\s+(\d{1,2}/\d{1,2})\s*@\s*(\d+(?:\.\d+)?)',
        re.IGNORECASE,
    )

    # ------------------------------------------------------------------
    # Layer 2: exits / trims
    # ------------------------------------------------------------------

    # Built from the corpus, not from memory. Grouped by the phrasing family
    # each one came from; every pattern below has at least one real message
    # behind it in data/history_20260721/ace.json.
    EXIT_TRIGGERS = [
        re.compile(r'✅'),                                        # "$AAPL up 20% ✅"
        re.compile(r'🛑'),                                        # "cutting here -30% 🛑"
        re.compile(r'\ball\s+out\b', re.IGNORECASE),              # "all out. hit target at EMA."
        re.compile(r'\bout\s+(?:on|here|at|of)\b', re.IGNORECASE),  # "out on $AAPL", "out here @0.54",
                                                                  # "out at BE", "out of the rest of $AAPL"
        re.compile(r"\b(?:im|i'm)\s+out\b", re.IGNORECASE),       # "im out of remaining cons"
        re.compile(r'\bclos(?:e|ed|ing)\b', re.IGNORECASE),       # "closed $AMZN here", "closing most of $AMZN"
        re.compile(r'\bcut(?:s|ting)?\b', re.IGNORECASE),         # "im cutting $AMZN guys -50%", "i cut AAPL here"
        re.compile(r'\btrim(?:s|ming|med)?\b', re.IGNORECASE),    # "trimming here. cons at 1.52"
        re.compile(r'\b(?:sold|sell|selling)\b', re.IGNORECASE),  # "sold half here", "selling more here"
        re.compile(r'\bstopped\s+out\b', re.IGNORECASE),          # "stopped out here @0.65"
        re.compile(r'\bstop\s+loss\b', re.IGNORECASE),            # "and theres the stop loss 🛑"
        re.compile(r'\bdamn\s+SL\b', re.IGNORECASE),              # "damn SL. bad timing again"
        re.compile(r'\b(?:took|taking|take)\s+(?:all\s+|half\s+|some\s+|more\s+|the\s+)*profit',
                   re.IGNORECASE),                                # "took all profit there", "taking half profit"
        re.compile(r'\bfull\s+TP\b', re.IGNORECASE),              # "full TP, cons at @0.27"
        re.compile(r'\bTP\s+(?:here|now)\b', re.IGNORECASE),      # "TP here, leave a runner"
        re.compile(r'\bhit\s+(?:the\s+)?(?:final\s+)?target\b', re.IGNORECASE),  # "$AMZN hit target for 150%"
    ]

    # Phrasings that LOOK like triggers but are not actions Ace took.
    # Each is anchored to a real corpus message.
    EXIT_NEGATIONS = [
        re.compile(r"\b(?:will\s+not|won'?t|do\s+not|don'?t|did\s*n'?t|could\s*n'?t|"
                   r"unable\s+to|too\s+fast\s+to)\s+\w*\s*"
                   r"(?:sell|sold|trim|close|closed|cut|take\s+profit|tp)\b", re.IGNORECASE),
        # "set stops at entry" / "setting stops" / "set your stops" — risk
        # management chatter, never an exit on its own.
        re.compile(r'\bset(?:ting)?\s+(?:your\s+|my\s+|the\s+)?stops?\b', re.IGNORECASE),
        # "might just hit SL here" / "SL will be a close below this low of day"
        re.compile(r'\bSL\s+(?:will|would|is|at)\b', re.IGNORECASE),
        # "a close under it, maybe 294.50" — candle close, not a position close.
        re.compile(r'\b(?:a|the)\s+close\s+(?:under|below|above|over|at)\b', re.IGNORECASE),
    ]

    # Recaps re-list every closed trade with its ✅/🛑 marker. They must never
    # be read as fresh exits.
    RECAP_RE = re.compile(r'\brecap\b', re.IGNORECASE)
    RECAP_LINE_RE = re.compile(r'^\s*\$?[A-Z]{1,5}\b.{0,30}?[+-]?\d+%.{0,20}?[✅🛑⏸️]', re.MULTILINE)

    # Pure commentary — "watching $AMZN puts", "$AAPL calls look good as well"
    WATCHING_RE = re.compile(r'\b(?:watching|watch|eyeing|looking at)\b', re.IGNORECASE)

    TICKER_RE = re.compile(r'\$([A-Z]{1,5})\b')

    # Every ticker Ace has traded or named in the corpus. Used ONLY as a guard:
    # if a message names one of these and it isn't open, we refuse to fall back
    # to the sole open position — "closed AAPL" must never close AMZN. Matched
    # case-sensitively, because Ace writes tickers in caps and several of these
    # are also English words (NOW, CAR).
    KNOWN_TICKERS = frozenset({
        "AAPL", "AMZN", "AMD", "GOOGL", "TSLA", "WMT", "NVDA", "SPY", "QQQ",
        "IWM", "HOOD", "SOXL", "NBIS", "SPCX", "CAR", "INTC", "NOW", "CRM",
        "ENPH", "IREN", "SPX",
    })

    # ==================================================================

    @staticmethod
    def is_noise(message: str) -> bool:
        """True for messages that are definitively not actionable.

        Entries are never noise. Recaps always are.
        """
        if AceParser.ENTRY_VERB_RE.search(message):
            return False
        if AceParser.ENTRY_HEADER_RE.search(message) and AceParser.ENTRY_LEG_RE.search(message):
            return False
        if AceParser._is_recap(message):
            return True
        return False

    @staticmethod
    def _is_recap(message: str) -> bool:
        if AceParser.RECAP_RE.search(message):
            return True
        # Three or more "$TICKER +80% ✅" lines is a recap even without the word.
        return len(AceParser.RECAP_LINE_RE.findall(message)) >= 3

    # ------------------------------------------------------------------

    @staticmethod
    def extract_details(message: str, message_id: str = "", timestamp: str = "",
                        open_tickers: Optional[set] = None,
                        resolve_sole_position: bool = True) -> Optional[ParsedSignal]:
        """Extract an Ace signal. Returns None (loudly) when unresolvable.

        open_tickers: optional set of tickers with an open Ace position. When
        supplied, exits may resolve a bare (un-$-prefixed) ticker against it —
        "closed AAPL." only means AAPL if AAPL is actually open. Without it,
        only explicit $TICKER exits resolve.

        resolve_sole_position: a tickerless exit marker ("cutting here -30% 🛑")
        resolves to the single open position when there is exactly one. ON by
        default; still refuses when 2+ are open. Requires open_tickers to be
        supplied — with no position context there is nothing to resolve against.
        """

        if AceParser._is_recap(message):
            logger.info("Ace recap message — not a signal: %s", message_id)
            return None

        entry = AceParser._extract_entry(message, message_id, timestamp)
        if entry is not None:
            return entry

        # An entry-shaped message that failed validation must not fall through
        # to the exit layer (its "@0.22" is a limit price, not a fill).
        if AceParser._looks_like_entry(message):
            return None

        return AceParser._extract_exit(message, message_id, timestamp, open_tickers,
                                       resolve_sole_position)

    # ------------------------------------------------------------------
    # Entries
    # ------------------------------------------------------------------

    @staticmethod
    def _looks_like_entry(message: str) -> bool:
        return bool(
            AceParser.ENTRY_VERB_RE.search(message)
            or AceParser.RIGHTLESS_ENTRY_RE.search(message)
            or (AceParser.ENTRY_HEADER_RE.search(message)
                and AceParser.ENTRY_LEG_RE.search(message))
        )

    @staticmethod
    def _extract_entry(message: str, message_id: str, timestamp: str) -> Optional[ParsedSignal]:
        match = AceParser.ENTRY_VERB_RE.search(message)
        if not match and AceParser.ENTRY_HEADER_RE.search(message):
            match = AceParser.ENTRY_LEG_RE.search(message)

        if not match:
            # Strike present, right (c/p) missing — unexecutable, skip loudly.
            rightless = AceParser.RIGHTLESS_ENTRY_RE.search(message)
            if rightless:
                logger.error(
                    "⚠️ Ace entry $%s %s %s @%s has NO c/p — cannot build an option "
                    "symbol, skipping: %s",
                    rightless.group(1).upper(), rightless.group(2),
                    rightless.group(3), rightless.group(4), message_id,
                )
            return None

        ticker = match.group(1).upper()
        strike = float(match.group(2))
        direction = 'call' if match.group(3).upper() == 'C' else 'put'
        expiry_raw = match.group(4)
        price = float(match.group(5))

        expiry = AceParser._parse_expiry(expiry_raw, timestamp)
        if not expiry:
            logger.error(
                "⚠️ Ace entry $%s %s%s exp %s @%s — unusable expiry (stale or "
                "malformed), skipping: %s",
                ticker, match.group(2), match.group(3).upper(), expiry_raw, price, message_id,
            )
            return None

        return ParsedSignal(
            analyst="ace",
            action=SignalAction.ENTRY.value,
            asset_type=AssetType.OPTION.value,
            ticker=ticker, direction=direction,
            strike=strike, expiry=expiry,
            entry_price=price, trim_fraction=None,
            confidence=0.95,
            raw_message=message, message_id=message_id, timestamp=timestamp,
        )

    @staticmethod
    def _parse_expiry(raw: str, timestamp: str = "") -> Optional[str]:
        """MM/DD -> YYYY-MM-DD, anchored to the message date.

        Year rollover: Ace posts 0-30 DTE, so an MM/DD that lands far in the
        past against the message date is next year's (a 12/28 post with an
        01/02 expiry). But an expiry only slightly in the past is a TYPO, not a
        rollover — Ace really did post "BTO $AMD 195p 03/18" on 03/19 and
        follow it with "sorry guys meant 03/20 exp". Rolling that to 2027 would
        buy a contract a year out; we refuse it instead.
        """
        try:
            month, day = (int(p) for p in raw.split('/'))
        except (ValueError, TypeError):
            return None
        if not (1 <= month <= 12 and 1 <= day <= 31):
            return None

        anchor = AceParser._message_date(timestamp)

        try:
            expiry = datetime(anchor.year, month, day).date()
        except ValueError:
            return None  # e.g. 02/30

        delta = (expiry - anchor).days
        if delta < -180:
            # Genuine year rollover (Dec post, Jan expiry).
            try:
                expiry = datetime(anchor.year + 1, month, day).date()
            except ValueError:
                return None
        elif delta < 0:
            # Stale/typo expiry — refuse rather than guess.
            return None

        return expiry.isoformat()

    @staticmethod
    def _message_date(timestamp: str):
        """Date the message was posted; today's date when unparseable."""
        if timestamp:
            try:
                return datetime.fromisoformat(timestamp.replace('Z', '+00:00')).date()
            except ValueError:
                pass
        return datetime.now().date()

    # ------------------------------------------------------------------
    # Exits / trims
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_exit(message: str, message_id: str, timestamp: str,
                      open_tickers: Optional[set] = None,
                      resolve_sole_position: bool = False) -> Optional[ParsedSignal]:
        trigger = AceParser._exit_trigger(message)
        if not trigger:
            return None

        # Pure commentary that happens to contain a trigger word
        # ("watching $AMZN puts") is not an exit.
        if AceParser.WATCHING_RE.search(message) and not re.search(r'✅|🛑', message):
            logger.info("Ace watchlist chatter, not an exit: %s", message_id)
            return None

        ticker = AceParser._resolve_exit_ticker(message, message_id, open_tickers,
                                                resolve_sole_position)
        if not ticker:
            return None

        return ParsedSignal(
            analyst="ace",
            action=SignalAction.EXIT.value,
            asset_type=AssetType.OPTION.value,
            ticker=ticker, direction=None,
            strike=None, expiry=None,
            entry_price=None,
            # One contract per trade: any trim marker is a full flatten.
            trim_fraction=1.0,
            confidence=0.9,
            raw_message=message, message_id=message_id, timestamp=timestamp,
        )

    @staticmethod
    def _exit_trigger(message: str) -> Optional[str]:
        """Return the matched exit marker, or None."""
        for negation in AceParser.EXIT_NEGATIONS:
            if negation.search(message):
                # Strip the negated span and re-test the remainder, so
                # "set stops closer" inside a "$AAPL up 100% ✅" message
                # doesn't suppress the ✅.
                message = negation.sub(' ', message)
        for pattern in AceParser.EXIT_TRIGGERS:
            match = pattern.search(message)
            if match:
                return match.group(0)
        return None

    @staticmethod
    def _resolve_exit_ticker(message: str, message_id: str,
                             open_tickers: Optional[set] = None,
                             resolve_sole_position: bool = False) -> Optional[str]:
        """Map an exit to a ticker. Never guesses between candidates.

        The hard case is the tickerless stop-loss: Ace names the ticker on
        winners ("$AAPL up 40% ✅") but frequently omits it when cutting a
        loser ("cutting here -30% 🛑", "and theres the stop loss 🛑",
        "stopped out here @0.65"). Five such messages in the 383-message corpus
        are the ONLY exit for their position, so refusing them means holding
        losers to expiry AND tripping main.py's duplicate-position guard on the
        next call for that ticker. With exactly one Ace position open there is
        nothing to guess between, so we resolve; with 2+ we still refuse.
        See tests/test_ace_corpus.py.
        """
        explicit = {t.upper() for t in AceParser.TICKER_RE.findall(message)}

        if len(explicit) == 1:
            return explicit.pop()
        if len(explicit) > 1:
            # If exactly one of them is actually open, that's not a guess.
            if open_tickers:
                overlap = explicit & {t.upper() for t in open_tickers}
                if len(overlap) == 1:
                    return overlap.pop()
            logger.error(
                "⚠️ Ace exit names %d tickers (%s) — ambiguous, NOT guessing: %s",
                len(explicit), ", ".join(sorted(explicit)), message_id,
            )
            return None

        # No $TICKER. Only a bare ticker that matches a known open position
        # counts — "closed AAPL." is an exit, "hit the EMA" is not.
        if open_tickers:
            bare = {t.upper() for t in open_tickers
                    if re.search(rf'\b{re.escape(t.upper())}\b', message, re.IGNORECASE)}
            if len(bare) == 1:
                return bare.pop()
            if len(bare) > 1:
                logger.error(
                    "⚠️ Ace exit matches %d open positions (%s) — ambiguous, NOT "
                    "guessing: %s", len(bare), ", ".join(sorted(bare)), message_id,
                )
                return None

            if resolve_sole_position and len(open_tickers) == 1:
                # Only when the message names NO ticker at all. If it names one
                # we don't hold, that is a mismatch, not a tickerless exit —
                # closing the sole position would close the WRONG contract.
                named = {t for t in AceParser.KNOWN_TICKERS
                         if re.search(rf'\b{t}\b', message)}
                if named:
                    logger.error(
                        "⚠️ Ace exit names %s but only %s is open — mismatch, NOT "
                        "closing the sole position: %s",
                        ", ".join(sorted(named)), ", ".join(sorted(open_tickers)), message_id,
                    )
                    return None
                sole = next(iter(open_tickers)).upper()
                logger.warning(
                    "Ace tickerless exit resolved to the sole open position %s: %s",
                    sole, message_id,
                )
                return sole

        logger.error(
            "⚠️ Ace exit marker with no resolvable ticker — skipping, position may "
            "still be OPEN: %s", message_id,
        )
        return None

    # ------------------------------------------------------------------
    # Gemini fallback support
    # ------------------------------------------------------------------

    @staticmethod
    def enhance_prompt(base_prompt: str, message: str) -> str:
        """Add Ace-specific context to the Gemini fallback prompt."""

        ace_context = """
ACE (ACE ♠) ANALYST SPECIFIC RULES:
- Entry format: "BTO $TICKER [STRIKE][c/p] [MM/DD] @[PRICE]" — sometimes
  "took $TICKER ..." or an "Entering:" / "Lotto:" header with the leg on the
  next line, wrapped in ** bold **.
- Expiry is MM/DD only (no year) and is always within ~30 days of the message.
- Exits/trims are prose: "✅", "all out", "out on $TICKER @price", "up X%",
  "trim"/"trimming"/"trimmed", "sold", "closing", "cut", "🛑" (stop loss).
- The bot holds ONE contract, so ANY trim or exit marker = full exit. Never
  return a partial trim_fraction for Ace.
- Recap messages ("WEEKLY RECAP", "daily recap:") re-list already-closed
  trades with ✅/🛑 markers — these are NOISE, not exits.
- "watching $TICKER puts", "set stops", "SL will be ...", targets and EMA
  commentary = NOT signals.
- Asset type is ALWAYS equity options.
- Tickers seen: AAPL, AMZN, AMD, GOOGL, TSLA, NVDA, SPY, QQQ, IWM, WMT, HOOD,
  SOXL, NBIS, SPCX, CAR, INTC.
"""
        return base_prompt + ace_context

    @staticmethod
    def post_process_signal(signal: ParsedSignal, original_message: str) -> ParsedSignal:
        """Apply Ace-specific post-processing to a Gemini-parsed signal."""

        if not signal:
            return signal

        # Ace trades equity options exclusively.
        signal.asset_type = AssetType.OPTION.value

        # Collapse any trim Gemini returns into a full exit — one contract.
        action = getattr(signal.action, "value", signal.action)
        if action == SignalAction.TRIM.value:
            signal.action = SignalAction.EXIT.value
            signal.trim_fraction = 1.0

        return signal
