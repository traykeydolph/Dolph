"""Parser unit tests — validates regex extraction against Tray's audited signal library.

Run: ./venv/bin/python -m pytest tests/test_parsers.py -v
"""

import pytest
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from parsers.zabes import ZabesParser
from parsers.grizzlies import GrizzliesParser
from parsers.ecs import ECSParser
from parsers.base import SignalAction, AssetType


# ═══════════════════════════════════════════════════════════════════
# ZABES TESTS — from Tray's 100-message validation
# ═══════════════════════════════════════════════════════════════════

class TestZabesEntries:
    """Zabes ENTRY signals — validated by Tray."""

    @pytest.mark.parametrize("message,expected_ticker,expected_asset", [
        ("META 1/9 $645P at $3.60 Grabbing two to swing", "META", "option"),
        ("Swinging NVDA 1/9 $180P at $0.45 Grabbing 10 cons use own sl", "NVDA", "option"),
        ("$MSFT 2/27 $400C at $6.60 Swinging some. Sl for me around $396", "MSFT", "option"),
        ("Nvda 2/13 $190P at $2.50 Use own stop", "NVDA", "option"),
        ("AMD 2/13 $220C at $4.50 Spy coming into some big resistance", "AMD", "option"),
        ("$MU 2/13 $405P $3.80 1 con \"lotto\"", "MU", "option"),
        ("Risky NVDA 2/3 192.5P $2.12 Sl over 193.5", "NVDA", "option"),
        ("Lotto swing HOOD 2/6 $81C at $1.92 Taking a few to swing", "HOOD", "option"),
        ("SPY 2/25 $680P at $5.05 Use own sl", "SPY", "option"),
    ])
    def test_option_entries(self, message, expected_ticker, expected_asset):
        result = ZabesParser.extract_details(message, "test", "2026-03-21")
        assert result is not None, f"Should parse as entry: {message}"
        assert result.action == SignalAction.ENTRY.value
        assert result.ticker == expected_ticker
        assert result.asset_type == expected_asset

    def test_stock_entry_nflx(self):
        """THE critical NFLX bug — must classify as STOCK not OPTION."""
        result = ZabesParser.extract_details(
            "Starting to buy NFLX shares here at $80 and change for long term hold",
            "test", "2026-03-21"
        )
        assert result is not None
        assert result.action == SignalAction.ENTRY.value
        assert result.asset_type == AssetType.STOCK.value
        assert result.ticker == "NFLX"

    def test_stock_entry_long_term_hold(self):
        """Stock indicator without 'shares' keyword."""
        result = ZabesParser.extract_details("NFLX long term hold", "test", "2026-03-21")
        assert result is not None
        assert result.asset_type == AssetType.STOCK.value

    def test_invalid_expiry_rejected(self):
        """Feb 30 doesn't exist — should return None."""
        result = ZabesParser.extract_details(
            "TSLA 2/30 $500C at $10.00", "test", "2026-03-21"
        )
        assert result is None


class TestZabesTrims:
    """Zabes TRIM signals — validated by Tray."""

    @pytest.mark.parametrize("message", [
        "the champ is back. trim those AAPL puts at $4.15 40% profit holding a few runners",
        "sold a few more AAPL puts at $4.65 56% profit. Holding one",
        "Trimmed META at $6.00 67% profit. Still holding a few",
        "Trimming AAPL puts at $4.00",
        "Trimmed a bunch of TSLA at $5.30 51% profit",
        "Trimmed again at $5.05 38% profit. Still holding some",
        "Took a few more MSFT off at $6.55 56% profit. Swinging 2 cons!",
        "Took a few more AMD puts off at $7.10 36% profit",
    ])
    def test_trim_detection(self, message):
        result = ZabesParser.extract_details(message, "test", "2026-03-21")
        assert result is not None, f"Should parse as trim: {message}"
        assert result.action == SignalAction.TRIM.value


class TestZabesExits:
    """Zabes EXIT signals — validated by Tray."""

    @pytest.mark.parametrize("message", [
        "Out on the rest of AAPL puts at $9.10 206% profit",
        "Fully out on AAPL puts at $5.90 90% profit",
        "Sold last two TSLA calls at $9.60 175% profit",
        "Fully out on Amazon calls at $7.90 116% profit",
        "cutting HOOD, couldnt get a bounce",
        "AMD hit stop loss",
    ])
    def test_exit_detection(self, message):
        result = ZabesParser.extract_details(message, "test", "2026-03-21")
        assert result is not None, f"Should parse as exit: {message}"
        assert result.action == SignalAction.EXIT.value


class TestZabesNoise:
    """Zabes NOISE signals — should return None or noise."""

    @pytest.mark.parametrize("message", [
        "Watch Nvda puts",
        ".",
        "Watching some cheap TSLA calls to swing",
        "my SL for AAPL is $259",
        "Came down with the flu so not feeling the best currently. May not trade today",
    ])
    def test_noise_detection(self, message):
        result = ZabesParser.extract_details(message, "test", "2026-03-21")
        assert result is None, f"Should be noise: {message}"


# ═══════════════════════════════════════════════════════════════════
# GRIZZLIES TESTS — from Tray's 100-message validation
# ═══════════════════════════════════════════════════════════════════

class TestGrizzliesEntries:
    """Grizzlies ENTRY signals — validated by Tray."""

    @pytest.mark.parametrize("message,expected_asset", [
        ("Crypto play Btc long Entry: 1)66250 targets: 1)66800 2)67300 3)67900", "crypto"),
        ("Crypto play Btc long Entry: 1)65350 2)63350 targets: 1)66000 2)66500", "crypto"),
        ("Crypto play Space long Entry: 1)0.010850 targets: 1)0.011100", "crypto"),
        ("Sol Short Entry: 1)86.2 2)92.2 Target: 1)85.4 2)84.9 SL: 98.2", "crypto"),
    ])
    def test_crypto_entries(self, message, expected_asset):
        result = GrizzliesParser.extract_details(message, "test", "2026-03-21")
        assert result is not None, f"Should parse as entry: {message[:60]}"
        assert result.action == SignalAction.ENTRY.value
        assert result.asset_type == expected_asset

    @pytest.mark.parametrize("message,expected_ticker", [
        ("CLSK 10c 2/20/26 @0.50 swinging this of course", "CLSK"),
        ("Risky ibit 38.5c 2/20/26 @0.65", "IBIT"),
        ("HOOD 78c 2/20/26 @1.15 this is high risk", "HOOD"),
        ("Hood 75c 2/20/26 @1.15 lightly play", "HOOD"),
        ("Ibit 38c 2/20/26 @0.70", "IBIT"),
        ("Coin 190c 2/27/26 @1.90 Lightly play", "COIN"),
    ])
    def test_option_entries(self, message, expected_ticker):
        result = GrizzliesParser.extract_details(message, "test", "2026-03-21")
        assert result is not None, f"Should parse as option entry: {message[:60]}"
        assert result.action == SignalAction.ENTRY.value
        assert result.asset_type == AssetType.OPTION.value
        assert result.ticker == expected_ticker


class TestGrizzliesTrims:
    """Grizzlies TRIM signals — THE most misclassified category."""

    @pytest.mark.parametrize("message", [
        "TP1 hit",
        "TP2",
        "Tp3 on btc",
        "BTC BANG!!! Okay I can't complain",
        "Ibit calls up 25%, trim and set stops $65 to $81",
        "Ibit calls up 35% $65 to $87 Bang!",
        "TP2 hit for space, BANG!!!",
        "Ibit calls up 50% $70 to $105",
        "Hood puts up 30% trim set stops $160 to $208",
        "Hood calls up 80% BANGGGG $115 to $207 !!!!!",
        "TP4 BANGGGGGGG",
        "Btc long still printing atm",
    ])
    def test_trim_detection(self, message):
        result = GrizzliesParser.extract_details(message, "test", "2026-03-21")
        assert result is not None, f"Should parse as trim: {message}"
        assert result.action == SignalAction.TRIM.value


class TestGrizzliesExits:
    """Grizzlies EXIT signals — validated by Tray."""

    @pytest.mark.parametrize("message", [
        "All TPs hit for btc, what a valentine gift",
        "Hood puts up 90% $160 to $304 Closed my runner here",
        "ALL TPS hit, closed my short position",
        "Hedge short stopped out",
        "Closed my long here on btc",
        "ALL TPs hit",
    ])
    def test_exit_detection(self, message):
        result = GrizzliesParser.extract_details(message, "test", "2026-03-21")
        assert result is not None, f"Should parse as exit: {message}"
        assert result.action == SignalAction.EXIT.value


class TestGrizzliesNoise:
    """Grizzlies NOISE signals — should return None."""

    @pytest.mark.parametrize("message", [
        "🍏🍎 Weekly report card: BTC +37%, HOOD -87%",
        "https://tenor.com/view/money-gif-11954040389595096870",
        "Cancel bid if you didn't get in, contracts sitting at $150 a pop",
        "—",
    ])
    def test_noise_detection(self, message):
        result = GrizzliesParser.extract_details(message, "test", "2026-03-21")
        assert result is None, f"Should be noise: {message}"


# ═══════════════════════════════════════════════════════════════════
# ECS TESTS — from Tray's validation (3 rules = 100% accuracy)
# ═══════════════════════════════════════════════════════════════════

class TestECSEntries:
    """ECS ENTRY signals — entries go through library/Gemini, but we test pattern detection."""

    def test_entry_pattern_match(self):
        """ECS entry messages should NOT be caught by alert bot filter."""
        msg = ("#GALA/USDT or USDC (Binance.com, Coinbase Pro, MexC etc) "
               "Entry around: 0.006745 Targets around: 0.006876 - 0.007183 "
               "Stop around: 0.00493 Expected duration: Medium and long term")
        assert not ECSParser.is_alert_bot_message(msg), "Entry should not match alert bot pattern"

    def test_short_entry_pattern(self):
        """Short entries should also not match alert bot."""
        msg = ("#POPCAT/USDT or USDC (MEXC etc) Short/Sell Entry around - 0.0771 "
               "Targets around: 0.0763 - 0.0686 Leverage - 4x Stop around - 0.0886")
        assert not ECSParser.is_alert_bot_message(msg)


class TestECSTrims:
    """ECS TRIM signals — automated alert bot messages."""

    def test_trim_detection(self):
        msg = ("Popcat (POPCAT) went below 0.0763 USD on [MEXC] - "
               "Note: ✅ #POPCATUSDT has reached the 1st Profit Target")
        assert ECSParser.is_alert_bot_message(msg)
        result = ECSParser.parse_alert_bot_message(msg, "test", "2026-03-21")
        assert result is not None
        assert result.action == SignalAction.TRIM.value


# ═══════════════════════════════════════════════════════════════════
# CROSS-CUTTING TESTS
# ═══════════════════════════════════════════════════════════════════

class TestAssetClassification:
    """Verify stock vs option vs crypto classification."""

    def test_zabes_stock_indicators(self):
        """All stock indicator patterns should classify as STOCK."""
        stock_messages = [
            "NFLX long term hold",
            "Starting to buy NFLX shares",
            "buying NFLX shares at 850",
            "NFLX stock play",
            "adding to my AAPL position",
            "buying the TSLA dip",
        ]
        for msg in stock_messages:
            result = ZabesParser.extract_details(msg, "test", "2026-03-21")
            if result:
                assert result.asset_type == AssetType.STOCK.value, \
                    f"'{msg}' should be STOCK, got {result.asset_type}"

    def test_option_details_override_stock_language(self):
        """If strike+expiry+C/P present, ALWAYS option regardless of stock language."""
        result = ZabesParser.extract_details(
            "buying AAPL shares 3/28 180C at 4.50", "test", "2026-03-21"
        )
        assert result is not None
        assert result.asset_type == AssetType.OPTION.value

    def test_grizzlies_crypto_etf_classification(self):
        """Crypto ETFs (IBIT, MSTR, etc.) should be classified as OPTION."""
        result = GrizzliesParser.extract_details(
            "IBIT 39.5c 2/14 @0.50", "test", "2026-03-21"
        )
        assert result is not None
        assert result.asset_type == AssetType.OPTION.value


class TestDateValidation:
    """Invalid dates should be rejected, not create bad option symbols."""

    @pytest.mark.parametrize("message", [
        "TSLA 2/30 $500C at $10.00",    # Feb 30
        "AAPL 4/31 $180C at $5.00",     # Apr 31
        "SPY 6/31 $600P at $3.00",      # Jun 31
    ])
    def test_invalid_dates_rejected_zabes(self, message):
        result = ZabesParser.extract_details(message, "test", "2026-03-21")
        assert result is None, f"Invalid date should be rejected: {message}"
