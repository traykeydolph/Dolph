"""Ace parser unit tests.

Companion to tests/test_ace_corpus.py (which runs the whole 383-message
history). This file pins the individual behaviours — surface-form variants,
expiry rollover, the exit vocabulary, and the negation guards — so a
regression names the rule it broke instead of just moving a corpus count.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from parsers.ace import AceParser
from parsers.base import AssetType, SignalAction

TS = "2026-03-04T15:00:00+00:00"


def extract(msg, ts=TS, **kw):
    return AceParser.extract_details(msg, "test_id", ts, **kw)


# ═══════════════════════════════════════════════════════════════════
# ENTRIES — the three surface forms, all verbatim from the corpus
# ═══════════════════════════════════════════════════════════════════

class TestAceEntryForms:
    def test_bto_bold_with_role_mention_inside(self):
        sig = extract("**BTO $AAPL 267.5c 03/04 @0.11 <@&697950067285295115> **\n\n"
                      "looking for it to come up retest EMAs.")
        assert sig is not None
        assert sig.analyst == "ace"
        assert sig.action == SignalAction.ENTRY.value
        assert sig.asset_type == AssetType.OPTION.value
        assert sig.ticker == "AAPL"
        assert sig.strike == 267.5
        assert sig.direction == "call"
        assert sig.expiry == "2026-03-04"
        assert sig.entry_price == 0.11

    def test_bto_bold_closed_before_mention(self):
        # "**BTO $AMZN 215p 03/06 @0.75**<@&...>" — no space before the mention
        sig = extract("**BTO $AMZN 215p 03/06 @0.75**<@&697950067285295115> \n\ncalling top here.")
        assert sig is not None
        assert sig.ticker == "AMZN"
        assert sig.strike == 215.0
        assert sig.direction == "put"
        assert sig.entry_price == 0.75

    def test_bto_with_everyone_mention_does_not_eat_the_price(self):
        # "@everyone" sits right after "@2.60" — the price capture must not slide.
        sig = extract("**BTO $TSLA 355c 04/10 @2.60 @everyone **\n\nlooking for a bounce here.",
                      ts="2026-04-08T15:00:00+00:00")
        assert sig is not None
        assert sig.ticker == "TSLA"
        assert sig.entry_price == 2.60
        assert sig.expiry == "2026-04-10"

    def test_took_verb(self):
        sig = extract("**took $AMZN 262.5c 04/27 @0.57 <@&697950067285295115> **\n\n"
                      "this ran as I was typing it out, cons @0.73 right now for 30%",
                      ts="2026-04-27T15:00:00+00:00")
        assert sig is not None
        assert sig.action == SignalAction.ENTRY.value
        assert sig.ticker == "AMZN"
        assert sig.strike == 262.5
        assert sig.entry_price == 0.57

    def test_entering_header_form(self):
        sig = extract("Entering:\n**$NVDA 217.5c 05/11 @1.75**\n\nlotto targeting 217, 218.",
                      ts="2026-05-08T15:00:00+00:00")
        assert sig is not None
        assert sig.ticker == "NVDA"
        assert sig.strike == 217.5
        assert sig.expiry == "2026-05-11"
        assert sig.entry_price == 1.75

    def test_lotto_header_form(self):
        sig = extract("Lotto:\n**$QQQ 709c 07/16 @1.05**\n\nlight.",
                      ts="2026-07-16T15:00:00+00:00")
        assert sig is not None
        assert sig.ticker == "QQQ"
        assert sig.strike == 709.0
        assert sig.expiry == "2026-07-16"

    def test_bare_leg_without_entry_marker_is_not_an_entry(self):
        # No BTO / took / Entering: header — must not fire.
        assert extract("watching $AAPL 267.5c 03/04 @0.11 here") is None

    def test_integer_and_half_strikes(self):
        assert extract("**BTO $CAR 100p 05/08 @0.65**", ts="2026-04-23T15:00:00+00:00").strike == 100.0
        assert extract("**BTO $AAPL 247.5p 04/01 @1.34**", ts="2026-03-31T15:00:00+00:00").strike == 247.5


class TestAceEntrySkips:
    """Entry-shaped messages the parser refuses. Each returns None loudly."""

    def test_missing_call_put_right_is_skipped(self):
        # Real: "BTO $AAPL 247.5 03/30 @0.22", corrected 2 messages later
        # with "calls btw". No right = no option symbol = no trade.
        sig = extract("**BTO $AAPL 247.5 03/30 @0.22 <@&697950067285295115> **\n\n"
                      "looking for a quick scalp, light size.",
                      ts="2026-03-30T15:00:00+00:00")
        assert sig is None

    def test_stale_expiry_is_skipped_not_rolled_to_next_year(self):
        # Real: "BTO $AMD 195p 03/18" posted 03/19, followed by "sorry guys
        # meant 03/20 exp". Must NOT become 2027-03-18.
        sig = extract("**BTO $AMD 195p 03/18 @0.88 <@&697950067285295115> **\n\ncalling top. medium size.",
                      ts="2026-03-19T15:00:00+00:00")
        assert sig is None

    def test_prose_add_without_strike_is_not_an_entry(self):
        assert extract("entered 2 more cons into AMD @1.08") is None
        assert extract("lets see how these play out tomorrow...\n\n"
                       "entered in on INTC 06/18 call light sized.") is None


class TestAceExpiry:
    def test_same_day_expiry_is_valid(self):
        assert AceParser._parse_expiry("03/04", "2026-03-04T15:00:00+00:00") == "2026-03-04"

    def test_forward_expiry_same_year(self):
        assert AceParser._parse_expiry("05/08", "2026-04-23T15:00:00+00:00") == "2026-05-08"

    def test_year_rollover_december_to_january(self):
        assert AceParser._parse_expiry("01/02", "2026-12-28T15:00:00+00:00") == "2027-01-02"

    def test_year_rollover_only_when_gap_is_large(self):
        # 1 day stale = typo, not a rollover.
        assert AceParser._parse_expiry("03/18", "2026-03-19T15:00:00+00:00") is None
        # 179 days stale is still refused; 181 rolls forward.
        assert AceParser._parse_expiry("01/05", "2026-06-30T15:00:00+00:00") is None
        assert AceParser._parse_expiry("01/01", "2026-12-31T15:00:00+00:00") == "2027-01-01"

    def test_impossible_dates_rejected(self):
        assert AceParser._parse_expiry("02/30", TS) is None
        assert AceParser._parse_expiry("13/01", TS) is None
        assert AceParser._parse_expiry("garbage", TS) is None


# ═══════════════════════════════════════════════════════════════════
# EXITS — bounded vocabulary, collapsed to a full flatten
# ═══════════════════════════════════════════════════════════════════

class TestAceExitVocabulary:
    """Every message below is verbatim from data/history_20260721/ace.json."""

    @pytest.mark.parametrize("message,ticker", [
        ("$AAPL up 20% at 0.13 ✅ <@&697950067285295115>", "AAPL"),
        ("out on $AAPL for around 20% @0.13 ✅\n\ncons not moving much.", "AAPL"),
        ("$AMZN up 50% ✅\n\nall out. hit target at EMA.", "AMZN"),
        ("$AMZN up 15% ✅\n\n0.74-0.85. Trim.", "AMZN"),
        ("$AMD up 25% ✅\n\ntrimming. heres a retest of EMA.", "AMD"),
        ("closed $AMD here. barely any profit.", "AMD"),
        ("closing most of $AMZN here.\nwill hold one runner into tomorrow.", "AMZN"),
        ("im cutting $AMZN guys -50% 🛑\n\nterrible trade.", "AMZN"),
        ("closed $AMZN here -25% 🛑\n\nwill look for reentry.", "AMZN"),
        ("$AAPL just flew 25% ✅\n\ndon't chase. sold half here.", "AAPL"),
        ("out of the rest of $AAPL for a small loss,\ndon't like this bear flag", "AAPL"),
        ("out of the last cons on $SPY. happy to time that bounce well.", "SPY"),
        ("$AMZN hit target for 150% ✅\n\ncons at 1.45 and 263.30 level hit.", "AMZN"),
        ("$GOOGL up 85% ✅\n\nout of runners at 5.20", "GOOGL"),
        ("$SOXL up 50% ✅\n\ntaking profit here, maybe leave a runner", "SOXL"),
        ("closed most of $NBIS sorry guys.", "NBIS"),
        ("$QQQ up 30% ✅\n\ncons at 1.35, trimmed half", "QQQ"),
    ])
    def test_exit_markers(self, message, ticker):
        sig = extract(message)
        assert sig is not None, f"missed exit: {message[:60]}"
        assert sig.action == SignalAction.EXIT.value
        assert sig.ticker == ticker

    def test_any_trim_marker_is_a_full_flatten(self):
        # One contract per trade — there is nothing to trim.
        sig = extract("$AMZN up 15% ✅\n\n0.74-0.85. Trim.")
        assert sig.action == SignalAction.EXIT.value
        assert sig.trim_fraction == 1.0

    def test_exit_carries_no_strike_or_expiry(self):
        # Ace never restates the contract on exit; execution resolves the
        # OCC symbol from the open position instead.
        sig = extract("$AAPL up 20% ✅")
        assert sig.strike is None and sig.expiry is None and sig.direction is None


class TestAceExitNegations:
    """Trigger words that are NOT exits. All verbatim from the corpus."""

    @pytest.mark.parametrize("message", [
        "have two options here.\ni'm very light sized so i personally will not sell.",
        "still holding $GOOGL, big level here and RSI is low.\n\nSL would be a close under it, maybe 294.50",
        "SL will be a close below this low of day. risky play but bounce still looks on.",
        "might just hit SL here, cons got wrecked.",
        "watching $AMZN puts\n\nfilling a gap right now.",
        "watching $AMD 220c 👀",
        "$AAPL calls look good as well",
        "cons back at 0.20 on $AAPL, if it closes above both EMAs, looks good to go higher.",
        "target is now lower than it was. will be reducing risk if it comes to breakeven.",
        "damn AAPL coming back down, if it does not hold this LOD i will be out",
    ])
    def test_not_an_exit(self, message):
        assert extract(message) is None, f"false exit on: {message[:60]}"

    def test_set_stops_does_not_suppress_a_real_marker(self):
        # "set stops closer" is chatter, but the ✅ in the same message is real.
        sig = extract("$AAPL up 100% ✅\n\ncons @0.50 🚀\ntrimming more. still have a some left, set stops closer.")
        assert sig is not None
        assert sig.ticker == "AAPL"


class TestAceRecaps:
    """Recaps re-list closed trades with ✅/🛑. Never fresh exits."""

    @pytest.mark.parametrize("message", [
        "WEEKLY RECAP ✨\n\n$AMZN calls 160% ✅  (sold at 110%)\n$AAPL calls 50% ✅\n$AMD puts 30% ✅",
        "daily recap:\n$AMD 20% ✅\n$AAPL 140% ✅ (sold at 120%)\n\nsolid day!",
        "**APRIL TRADES RECAP**\n\nfull transparency guys.\n\n$AAPL +700% ✅\n$CAR +200% ✅\n$AMZN +150%✅",
        "MARCH RECAP ✨\n\n$AAPL calls 50% ✅\n$AMZN puts 50% ✅\n$AAPL calls 75% ✅",
    ])
    def test_recap_is_noise(self, message):
        assert extract(message) is None
        assert AceParser.is_noise(message) is True

    def test_entry_is_never_noise(self):
        assert AceParser.is_noise("**BTO $AAPL 267.5c 03/04 @0.11**") is False
        assert AceParser.is_noise("Entering:\n**$NVDA 217.5c 05/11 @1.75**") is False


# ═══════════════════════════════════════════════════════════════════
# TICKER RESOLUTION — never guess
# ═══════════════════════════════════════════════════════════════════

class TestAceTickerResolution:
    def test_tickerless_exit_is_skipped_when_no_positions_are_known(self):
        # No position context to resolve against — refuse, don't invent.
        assert extract("cutting here -30% 🛑\n\nnot a good trade sorry guys.") is None
        assert extract("and theres the stop loss 🛑\n\nhopefully no one was heavy on that.") is None
        assert extract("stopped out here @0.65\n\n-35% 🛑") is None
        assert extract("cutting here -30% 🛑", open_tickers=set()) is None

    @pytest.mark.parametrize("message", [
        "cutting here -30% 🛑\n\nnot a good trade sorry guys.",
        "and theres the stop loss 🛑\n\nhopefully no one was heavy on that.",
        "stopped out here @0.65\n\n-35% 🛑",
        "out at BE guys too risky to play",
        "damn SL. bad timing again\n\n-30% 🛑",
    ])
    def test_tickerless_exit_resolves_to_the_sole_open_position(self, message):
        # ON by default: with one position open there is nothing to guess
        # between. These five are the real corpus stop-losses.
        sig = extract(message, open_tickers={"AAPL"})
        assert sig is not None, f"missed sole-position exit: {message[:50]}"
        assert sig.ticker == "AAPL"
        assert sig.action == SignalAction.EXIT.value
        assert sig.trim_fraction == 1.0

    def test_tickerless_exit_with_two_open_positions_never_guesses(self):
        # The guardrail that still matters — refuse when it IS a guess.
        assert extract("cutting here -30% 🛑", open_tickers={"AAPL", "AMZN"}) is None
        assert extract("and theres the stop loss 🛑",
                       open_tickers={"AAPL", "AMZN", "GOOGL"}) is None

    def test_sole_position_resolution_can_be_disabled(self):
        assert extract("cutting here -30% 🛑", open_tickers={"AAPL"},
                       resolve_sole_position=False) is None

    def test_sole_position_fallback_does_not_fire_on_non_exit_prose(self):
        # An open position must not turn ordinary commentary into an exit.
        assert extract("patience here. we have time to hold these,",
                       open_tickers={"AAPL"}) is None
        assert extract("targeting 312.15", open_tickers={"AAPL"}) is None
        assert extract("needs some volume.\ndouble bottom formed",
                       open_tickers={"AAPL"}) is None

    def test_bare_ticker_resolves_against_open_positions(self):
        sig = extract("closed AAPL. sorry about that guys. too early of an entry.",
                      open_tickers={"AAPL"})
        assert sig is not None and sig.ticker == "AAPL"

    def test_bare_ticker_ignored_when_not_open(self):
        assert extract("closed AAPL. sorry about that guys.", open_tickers={"AMZN"}) is None

    def test_multiple_dollar_tickers_are_ambiguous(self):
        # "that move down on SPY is killing us / i cut AAPL here" with both
        # written as $-tickers: refuse rather than pick.
        assert extract("that move down on $SPY is killing us\ni cut $AAPL here.") is None

    def test_multiple_dollar_tickers_resolve_when_exactly_one_is_open(self):
        sig = extract("that move down on $SPY is killing us\ni cut $AAPL here.",
                      open_tickers={"AAPL"})
        assert sig is not None and sig.ticker == "AAPL"


# ═══════════════════════════════════════════════════════════════════
# OCC SYMBOL — Ace entries must be Alpaca-executable
# ═══════════════════════════════════════════════════════════════════

class TestAceOptionSymbols:
    """Feed Ace signals through the same builder Eva's signals use
    (AlpacaClient._build_option_symbol) and check the OCC strings."""

    @staticmethod
    def build(message, ts):
        from execution.alpaca_client import AlpacaClient
        sig = AceParser.extract_details(message, "test_id", ts)
        assert sig is not None
        return AlpacaClient._build_option_symbol(None, sig)

    @pytest.mark.parametrize("message,ts,expected", [
        ("**BTO $AAPL 267.5c 03/04 @0.11**", "2026-03-04T15:00:00+00:00", "AAPL260304C00267500"),
        ("**BTO $AMZN 215p 03/06 @0.75**", "2026-03-05T15:00:00+00:00", "AMZN260306P00215000"),
        ("**BTO $AMD 210p 03/27 @1.07**", "2026-03-25T15:00:00+00:00", "AMD260327P00210000"),
        ("**BTO $GOOGL 347.5p 04/27 @0.60**", "2026-04-27T15:00:00+00:00", "GOOGL260427P00347500"),
        ("**BTO $TSLA 377.5c 03/20 @0.73**", "2026-03-20T15:00:00+00:00", "TSLA260320C00377500"),
    ])
    def test_occ_symbols(self, message, ts, expected):
        assert self.build(message, ts) == expected

    def test_occ_strike_field_is_always_eight_digits(self):
        # 709 strike (QQQ) and 100 strike (CAR) must both pad correctly.
        assert self.build("Lotto:\n**$QQQ 709c 07/16 @1.05**",
                          "2026-07-16T15:00:00+00:00") == "QQQ260716C00709000"
        assert self.build("**BTO $CAR 100p 05/08 @0.65**",
                          "2026-04-23T15:00:00+00:00") == "CAR260508P00100000"


class TestAcePostProcess:
    def test_gemini_trim_collapses_to_exit(self):
        from parsers.base import ParsedSignal
        sig = ParsedSignal(
            analyst="ace", action=SignalAction.TRIM.value,
            asset_type=AssetType.STOCK.value, ticker="AAPL", direction="call",
            strike=250.0, expiry="2026-07-24", entry_price=1.0, trim_fraction=0.5,
            confidence=0.9, raw_message="", message_id="", timestamp="",
        )
        out = AceParser.post_process_signal(sig, "trimming half here")
        assert out.action == SignalAction.EXIT.value
        assert out.trim_fraction == 1.0
        assert out.asset_type == AssetType.OPTION.value

    def test_enhance_prompt_mentions_ace_rules(self):
        prompt = AceParser.enhance_prompt("BASE", "msg")
        assert prompt.startswith("BASE")
        assert "ACE" in prompt and "BTO $TICKER" in prompt
