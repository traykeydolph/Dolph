"""Waxui parser hardening — the 5 edge cases caught in the 08-04 validation pass.

Each maps to a real Waxui message that was misclassified (all shadow-only, so no
execution/streak impact, but real go-live blockers). See ACTION_ITEMS.md §1.

Run: ./venv/bin/python -m pytest tests/test_waxui_hardening.py -v
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from parsers.waxui import WaxuiParser as W


def _action(m):
    if W.is_noise(m):
        return "noise"
    s = W.extract_details(m, "x", "2026-01-01T00:00:00")
    return "gemini" if s is None else s.action


class TestClosedTickerIsExit:
    """Fix 1: `Closed {TICKER}` is an exit regardless of what follows (here/@price/@B/E)."""

    @pytest.mark.parametrize("m", [
        "Closed SPX @B/E\nEntries are everything.",   # break-even close (was missed)
        "Closed SPY @2.50\nWe were early before",      # @price close (was missed)
        "Closed SPX here\nSolid catch",                # the "here" form (regression)
        "Closed AAPL",                                 # bare
    ])
    def test_closes_are_exits(self, m):
        assert _action(m) == "exit"

    def test_lowercase_non_ticker_does_not_match(self):
        # "Closed the position…" must NOT be read as an exit (no uppercase ticker)
        assert _action("Closed the position for the day") != "exit"


class TestHoldingFractionIsTrim:
    """Fix 2: any `Holding N/N` keeps a sell a TRIM, not a full exit."""

    @pytest.mark.parametrize("m,expected", [
        ("SPXXX\n4.60 - 7.50 ✅ 63%\nHolding 2/2!", "trim"),   # was exit
        ("SPY\n1.30 - 2.20 ✅ 69%\nHolding 1/2!", "trim"),      # regression
        ("SPX\n4.60 - 5.80 ✅ 26%\nHolding most.", "trim"),     # regression
        ("SPXXX\n4.60 - 7.50 ✅ 63%", "exit"),                  # no "Holding" = exit
    ])
    def test_holding(self, m, expected):
        assert _action(m) == expected


class TestReducedRiskIsTrim:
    """Fix 3: `Reduced risk @X` is a partial de-risk sell = TRIM (not noise)."""

    @pytest.mark.parametrize("m", ["Reduced risk @6.50", "Reduced risk @2.25", "Reduced risk @1.90"])
    def test_reduced_risk(self, m):
        assert _action(m) == "trim"
        s = W.extract_details(m, "x", "t")
        assert s.entry_price == float(m.split("@")[1])   # captures the price


class TestTrailCommentaryIsNoise:
    """Fix 4: trail/stop commentary is caught by regex noise, never Gemini."""

    @pytest.mark.parametrize("m", [
        "Trail stops set @B/E",              # regression
        "Using /ES 7630 as trail.",          # was Gemini → info
        "Trailing stop under the lows",
    ])
    def test_trail_is_noise(self, m):
        assert _action(m) == "noise"


class TestAddedToIsInfoNotEntry:
    """Fix 5: `Added to X` is a scale-in → INFO, never an entry that could open a
    2nd phantom position."""

    @pytest.mark.parametrize("m", [
        "Added to SPY @1.30\nNew Avg. is 1.60",
        "Added to SPY @0.75\nNew Avg. is 0.90.\nStops over HOD",
    ])
    def test_added_to_is_info(self, m):
        assert _action(m) == "info"          # NOT "entry"


class TestNoRegressionOnRealEntriesExits:
    @pytest.mark.parametrize("m,expected", [
        ("**LOTTO**\nSPY here\n07/22 747P\nAvg. 1.25", "entry"),
        ("*Riskier*\nSPX here\n07/28 7430P\nAvg. 4.60", "entry"),
        ("Stopped out of SPY 🔻", "exit"),
    ])
    def test_real_signals_unaffected(self, m, expected):
        assert _action(m) == expected
