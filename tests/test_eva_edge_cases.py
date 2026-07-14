"""Eva parser edge cases — from the 10 real messages (of 2,000 pulled
2026-07-13) that the deterministic tiers couldn't handle. Every message
below is verbatim from Eva's channel. See ANALYST_RANKING.md."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from parsers.base import AssetType, SignalAction
from parsers.eva import EvaParser


def extract(msg):
    return EvaParser.extract_details(msg, "test_id", "2026-07-13T12:00:00Z")


class TestEvaShareTrades:
    def test_share_add_amzn(self):
        sig = extract("Title: Open\nDescription: BTO AMZN @ 204.65 (Adding shares into IRA), there's flow into 230C in May exp,")
        assert sig is not None
        assert sig.action == SignalAction.ENTRY.value
        assert sig.asset_type == AssetType.STOCK.value
        assert sig.ticker == "AMZN"
        assert sig.entry_price == 204.65
        assert sig.strike is None and sig.expiry is None

    def test_share_add_rivn(self):
        sig = extract("Title: Open\nDescription: BTO RIVN @ 16.38 (Adding shares into IRA)")
        assert sig is not None
        assert sig.action == SignalAction.ENTRY.value
        assert sig.asset_type == AssetType.STOCK.value
        assert sig.ticker == "RIVN"

    def test_share_add_ibit_stays_stock(self):
        # IBIT is a BTC ETF — must classify as STOCK, never crypto
        sig = extract("Title: Open\nDescription: BTO IBIT @ 42.31 (Adding into IRA), liking BTC reclaiming 74k, it just needs to hold it. 1/4th position add, will add more if we drop under 40")
        assert sig is not None
        assert sig.asset_type == AssetType.STOCK.value
        assert sig.ticker == "IBIT"

    def test_share_exit_hims(self):
        sig = extract("Title: Close\nDescription: STC HIMS @ 16.98 (Exiting shares, this news is not great for their largest source of income being blocked)")
        assert sig is not None
        assert sig.action == SignalAction.EXIT.value
        assert sig.asset_type == AssetType.STOCK.value
        assert sig.ticker == "HIMS"
        assert sig.trim_fraction == 1.0

    def test_option_alert_not_misread_as_shares(self):
        # Regular option alert must still parse as option, not shares
        sig = extract("Title: Open\nDescription: BTO PEP 03/20/26 175C @ 0.74 (risky day trade possible swing)")
        assert sig is not None
        assert sig.asset_type == AssetType.OPTION.value
        assert sig.strike == 175.0
        assert sig.expiry == "2026-03-20"


class TestEvaExpiryQuirks:
    def test_typo_year_20026_entry(self):
        sig = extract("Title: Open\nDescription: BTO RIVN 05/15/20026 20C @ 0.33 (Swing), taking 8 contracts, TP: 17, 20 (SL: Under 14 daily close), following the whale")
        assert sig is not None
        assert sig.action == SignalAction.ENTRY.value
        assert sig.expiry == "2026-05-15"
        assert sig.strike == 20.0

    def test_typo_year_20026_trim(self):
        sig = extract("Title: Close\nDescription: STC RIVN 05/15/20026 20C @ 0.44 (Good spot to scale out 3/4, set the remaining at break even or running stop loss)")
        assert sig is not None
        assert sig.action == SignalAction.TRIM.value
        assert sig.expiry == "2026-05-15"

    def test_typo_year_20026_exit(self):
        sig = extract("Title: Close\nDescription: STC RIVN 05/15/20026 20C @ 0.39 (all out, running stop loss hit on this one as well), don't forget we own shares and CCs on it as well. shares are up!")
        assert sig is not None
        assert sig.expiry == "2026-05-15"

    def test_month_year_expiry_third_friday(self):
        # "03/2026" = monthly expiration = third Friday of March 2026 (the 20th)
        sig = extract("Title: Open\nDescription: BTO ONDS 03/2026 12C @ 1.39 (day trade/possible swing), TP: 12, 13, 15 (SL: under 10.40)")
        assert sig is not None
        assert sig.action == SignalAction.ENTRY.value
        assert sig.ticker == "ONDS"
        assert sig.expiry == "2026-03-20"
        assert sig.strike == 12.0

    def test_two_digit_year(self):
        sig = extract("Title: Open\nDescription: BTO PEP 03/20/26 175C @ 0.74 (day trade)")
        assert sig is not None
        assert sig.expiry == "2026-03-20"

    def test_absurd_year_rejected(self):
        assert EvaParser._normalize_year("99999") is None
        assert EvaParser._normalize_year("2205") is None


class TestEvaStrikelessSkip:
    """Explicit skip decision: option alerts with expiry but no strike are
    unexecutable (can't build an option symbol) — parser returns None and
    logs an error so the miss is loud, not silent."""

    def test_strikeless_open_skipped(self):
        sig = extract("Title: Open\nDescription: BTO UNH 01/15/27 @ 4.10 (Long Swing), TP: 376, 400, 420, 464, 500, 526 (SL: Under daily close under 322), IWM still remains strong even after spy and qqq dipped yesterday.")
        assert sig is None

    def test_strikeless_close_skipped(self):
        sig = extract("Title: Close\nDescription: STC UNH 01/15/27 @ 1.30 (all out, the news killed this play), hard to predict new headlines like this")
        assert sig is None


class TestThirdFriday:
    @pytest.mark.parametrize("year,month,expected", [
        (2026, 3, "2026-03-20"),
        (2026, 1, "2026-01-16"),
        (2026, 12, "2026-12-18"),
        (2027, 6, "2027-06-18"),
    ])
    def test_third_fridays(self, year, month, expected):
        assert EvaParser._third_friday(year, month) == expected
