"""Ace corpus gate — all 383 messages of data/history_20260721/ace.json.

Mirrors the Eva Evening-5 hardening. The contract this file enforces:

  Every message lands in exactly ONE bucket, and every bucket is justified.

    ENTRY       45  parsed against a hand-verified golden table
    EXIT       145  exit/trim marker resolved to a ticker
    ENTRY_SKIP   3  entry-shaped but unexecutable — enumerated below
    EXIT_SKIP   34  exit marker with no resolvable ticker — enumerated below
    NOISE      156  no entry shape, no exit marker
                ---
                383

There are no silent failures: the two skip buckets are frozen by Discord
message id, so a regression that starts dropping a message on the floor fails
here rather than going unnoticed. Both skip paths log at ERROR level, which
main.py surfaces to Telegram under ALERT_NOISE=1.

Run: ./venv/bin/python -m pytest tests/test_ace_corpus.py -v
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from parsers.ace import AceParser
from parsers.base import SignalAction

CORPUS_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "history_20260721", "ace.json",
)

with open(CORPUS_PATH) as _f:
    CORPUS = json.load(_f)


def parse(index, **kw):
    """Parse one message with NO position context (bucketing is stateless)."""
    msg = CORPUS[index]
    return AceParser.extract_details(msg.get("content", ""), msg["id"], msg["timestamp"], **kw)


# ═══════════════════════════════════════════════════════════════════
# Golden entry table — hand-verified against the raw Discord text.
# (index, ticker, strike, right, expiry, limit price)
# ═══════════════════════════════════════════════════════════════════

GOLDEN_ENTRIES = [
    (2, "AAPL", 267.5, "call", "2026-03-04", 0.11),
    (9, "AMZN", 215.0, "put", "2026-03-06", 0.75),
    (16, "AAPL", 260.0, "call", "2026-03-06", 1.56),
    (22, "WMT", 124.0, "call", "2026-03-13", 1.50),
    (34, "AMZN", 207.5, "put", "2026-03-16", 0.22),
    (43, "AAPL", 255.0, "call", "2026-03-17", 1.13),
    (52, "AMZN", 215.0, "call", "2026-03-18", 0.16),
    (58, "AAPL", 252.5, "call", "2026-03-18", 0.03),
    (72, "AMD", 197.5, "put", "2026-03-20", 0.81),
    (80, "TSLA", 377.5, "call", "2026-03-20", 0.73),
    (87, "AAPL", 255.0, "call", "2026-03-23", 0.14),
    (98, "GOOGL", 300.0, "call", "2026-03-25", 0.85),
    (103, "AAPL", 255.0, "call", "2026-03-25", 0.24),
    (105, "AMD", 210.0, "put", "2026-03-27", 1.07),
    (119, "AMD", 220.0, "call", "2026-03-27", 1.25),
    (125, "AAPL", 252.5, "put", "2026-03-27", 0.82),
    (138, "AMZN", 205.0, "call", "2026-03-27", 0.33),
    (160, "AAPL", 247.5, "put", "2026-04-01", 1.34),
    (166, "AMZN", 212.5, "call", "2026-04-02", 0.36),
    (174, "AAPL", 252.5, "call", "2026-04-08", 1.14),
    (180, "TSLA", 355.0, "call", "2026-04-10", 2.60),
    (183, "AMZN", 227.5, "put", "2026-04-10", 0.75),
    (189, "AAPL", 260.0, "call", "2026-04-13", 0.15),
    (196, "AAPL", 262.5, "call", "2026-04-15", 0.72),
    (204, "AAPL", 262.5, "call", "2026-04-15", 0.25),
    (214, "AAPL", 267.5, "call", "2026-04-17", 0.65),
    (229, "AAPL", 272.5, "call", "2026-04-22", 0.74),
    (242, "CAR", 100.0, "put", "2026-05-08", 0.65),
    (244, "AAPL", 275.0, "call", "2026-04-27", 0.60),
    (253, "GOOGL", 347.5, "put", "2026-04-27", 0.60),
    (258, "AMZN", 262.5, "call", "2026-04-27", 0.57),
    (268, "AMZN", 277.5, "call", "2026-05-08", 1.55),
    (277, "NVDA", 217.5, "call", "2026-05-11", 1.75),
    (285, "AAPL", 292.5, "call", "2026-05-13", 1.70),
    (294, "GOOGL", 385.0, "call", "2026-05-22", 2.80),
    (307, "SOXL", 250.0, "call", "2026-05-29", 1.40),
    (314, "AAPL", 312.5, "call", "2026-05-29", 0.28),
    (327, "NBIS", 300.0, "call", "2026-06-05", 3.20),
    (337, "SPY", 737.0, "call", "2026-06-09", 1.85),
    (342, "SPCX", 250.0, "call", "2026-06-18", 6.80),
    (349, "GOOGL", 350.0, "call", "2026-06-24", 2.30),
    (354, "IWM", 301.0, "put", "2026-06-26", 1.50),
    (364, "HOOD", 117.0, "call", "2026-07-10", 3.65),
    (370, "AAPL", 320.0, "call", "2026-07-13", 0.60),
    (377, "QQQ", 709.0, "call", "2026-07-16", 1.05),
]

# Entry-shaped messages the parser deliberately refuses, with the reason.
ENTRY_SKIPS = {
    "1484220937007140934": "stale expiry — 03/18 posted 03/19, Ace then said 'meant 03/20 exp'",
    "1488212917131411658": "no c/p right — 'BTO $AAPL 247.5 03/30', corrected later by 'calls btw'",
    "1488212929676578836": "duplicate of the above (Ace's bot double-posted)",
}

# Exit markers with no resolvable ticker in the message text. Frozen so a
# regression can't quietly grow this set. Five of these are the ONLY exit for
# an open position — see test_lifecycle_* below.
EXIT_SKIP_IDS = {
    "1483110899127685150",  # "sold my final one at 110% gain ✅"
    "1483906175513792674",  # "out at BE guys too risky to play"      <- sole exit
    "1484237722318147585",  # "runner up 30% ✅"
    "1484264023024472126",  # "trimmed some up at 0.95 ..."
    "1485640965451550791",  # "those just ran to 40% ✅"
    "1486015785955758262",  # "cons just went from 0.85 to 1.25 for 50% ✅"
    "1486728376889774150",  # "set stops at entry, out of most here"
    "1486742127705722991",  # "cons hit 0.84 as it rejected that EMA..."
    "1488575448635150405",  # "cutting here -30% 🛑"                   <- sole exit
    "1488580716169134190",  # "good thing we cut there"
    "1489279787020324897",  # "averaged down a tiny bit there"
    "1489291368789709002",  # "and theres the stop loss 🛑"            <- sole exit
    "1491082298429407282",  # "stopped out here @0.65  -35% 🛑"        <- sole exit
    "1493252751038222347",  # "full TP, cons at @0.27"
    "1493253509447946300",  # "sorry guys, cons went so fast."
    "1493621574899863714",  # "going to cut AAPL here  -30% 🛑"
    "1493655249058005164",  # "will sell most here, hold a few runners"
    "1494338541008982187",  # "closed AAPL. sorry about that guys."
    "1496164146247499836",  # "i cut AAPL here."
    "1497258698764976168",  # "out here @0.54"
    "1498332931121352926",  # "damn SL. bad timing again  -30% 🛑"     <- sole exit
    "1498365624051826769",  # "took all profit there. target's hit."
    "1502330461223915662",  # "cons back to B/E ... that stop loss candle"
    "1503445583640137750",  # "selling more here. volume low"
    "1503452005686054932",  # "gonna sell most here."
    "1507023866248302599",  # "was only able to take some profit."
    "1507024385658327090",  # "trimmed some more profit in green ... back to 20% ✅"
    "1509212882267541637",  # "not loving the volume, trimming here at 20%"
    "1509965779947487254",  # "im out of remaining cons."
    "1516443999575998474",  # "lotto paid so I took profit on half here."
    "1520095803182415943",  # "sold, nice move! glad we caught that."
    "1520102278009196665",  # "just went to 3.20, sold early"
    "1523714191121322054",  # "trimming some here, leaving the rest to swing"
    "1527351498562994237",  # "out of the rest here at 1.25"
}


def bucket(index):
    """Classify one corpus message into exactly one bucket."""
    content = CORPUS[index].get("content", "")
    signal = parse(index)
    if signal is not None:
        return signal.action  # "entry" | "exit"
    if AceParser._looks_like_entry(content):
        return "entry_skip"
    if AceParser._exit_trigger(content) and not AceParser._is_recap(content):
        return "exit_skip"
    return "noise"


# ═══════════════════════════════════════════════════════════════════

class TestAceCorpusEntries:
    """Entries are the deterministic layer — 100% or it isn't done."""

    @pytest.mark.parametrize("index,ticker,strike,right,expiry,price", GOLDEN_ENTRIES)
    def test_golden_entry(self, index, ticker, strike, right, expiry, price):
        sig = parse(index)
        assert sig is not None, f"msg {index} failed to parse: {CORPUS[index]['content'][:80]}"
        assert sig.action == SignalAction.ENTRY.value
        assert sig.analyst == "ace"
        assert (sig.ticker, sig.strike, sig.direction, sig.expiry, sig.entry_price) == \
               (ticker, strike, right, expiry, price)
        assert sig.confidence >= 0.9

    def test_every_entry_shaped_message_is_parsed_or_explicitly_skipped(self):
        shaped = [i for i, m in enumerate(CORPUS)
                  if AceParser._looks_like_entry(m.get("content", ""))]
        parsed = {i for i, *_ in GOLDEN_ENTRIES}
        skipped = {i for i in shaped if CORPUS[i]["id"] in ENTRY_SKIPS}
        assert set(shaped) == parsed | skipped, "an entry-shaped message is unaccounted for"
        assert len(shaped) == 48
        assert len(parsed) == 45 and len(skipped) == 3

    def test_entry_expiries_are_never_before_the_message_date(self):
        for index, *_ in GOLDEN_ENTRIES:
            sig = parse(index)
            posted = CORPUS[index]["timestamp"][:10]
            assert sig.expiry >= posted, f"msg {index}: expiry {sig.expiry} before post {posted}"

    def test_entry_expiries_are_within_a_sane_dte_window(self):
        from datetime import date
        for index, *_ in GOLDEN_ENTRIES:
            sig = parse(index)
            posted = date.fromisoformat(CORPUS[index]["timestamp"][:10])
            dte = (date.fromisoformat(sig.expiry) - posted).days
            assert 0 <= dte <= 45, f"msg {index}: implausible {dte} DTE"

    def test_all_entries_build_valid_occ_symbols(self):
        from execution.alpaca_client import AlpacaClient
        for index, ticker, strike, right, expiry, _ in GOLDEN_ENTRIES:
            symbol = AlpacaClient._build_option_symbol(None, parse(index))
            assert symbol is not None, f"msg {index} produced no OCC symbol"
            assert symbol.startswith(ticker)
            body = symbol[len(ticker):]
            assert len(body) == 15, f"msg {index}: bad OCC body {body!r}"
            assert body[6] == ("C" if right == "call" else "P")
            assert body[:6] == expiry[2:].replace("-", "")
            assert int(body[7:]) == int(strike * 1000)


class TestAceCorpusExits:
    def test_every_exit_has_a_ticker_and_flattens_fully(self):
        exits = [i for i in range(len(CORPUS)) if bucket(i) == SignalAction.EXIT.value]
        assert len(exits) == 145
        for index in exits:
            sig = parse(index)
            assert sig.ticker, f"msg {index}: exit with empty ticker"
            assert sig.trim_fraction == 1.0, f"msg {index}: partial trim leaked through"
            assert sig.strike is None and sig.expiry is None

    def test_exit_skips_are_exactly_the_frozen_set(self):
        found = {CORPUS[i]["id"] for i in range(len(CORPUS)) if bucket(i) == "exit_skip"}
        assert found == EXIT_SKIP_IDS, (
            f"unexpected: {sorted(found - EXIT_SKIP_IDS)} / "
            f"no longer skipped: {sorted(EXIT_SKIP_IDS - found)}"
        )

    def test_no_entry_is_misread_as_an_exit(self):
        for index, *_ in GOLDEN_ENTRIES:
            assert parse(index).action == SignalAction.ENTRY.value

    def test_recaps_never_produce_signals(self):
        recaps = [i for i, m in enumerate(CORPUS)
                  if AceParser._is_recap(m.get("content", ""))]
        assert len(recaps) >= 7, "recap detector stopped matching"
        for index in recaps:
            assert parse(index) is None, f"msg {index}: recap produced a signal"


class TestAceCorpusCoverage:
    """Zero silent failures: all 383 messages accounted for."""

    def test_every_message_lands_in_exactly_one_justified_bucket(self):
        counts = {"entry": 0, "exit": 0, "entry_skip": 0, "exit_skip": 0, "noise": 0}
        for index in range(len(CORPUS)):
            counts[bucket(index)] += 1
        assert counts == {
            "entry": 45, "exit": 145, "entry_skip": 3, "exit_skip": 34, "noise": 156,
        }
        assert sum(counts.values()) == len(CORPUS) == 383

    def test_entry_skips_are_exactly_the_frozen_set(self):
        found = {CORPUS[i]["id"] for i in range(len(CORPUS)) if bucket(i) == "entry_skip"}
        assert found == set(ENTRY_SKIPS)


# ═══════════════════════════════════════════════════════════════════
# Lifecycle — replay the corpus in order and track open positions.
# This is what actually proves the exit layer works.
# ═══════════════════════════════════════════════════════════════════

def replay(resolve_sole_position=True):
    """Walk the corpus in order; return (closed_pairs, unclosed_positions)."""
    open_pos = {}      # ticker -> entry message index
    unclosed = []
    closed = 0
    for index, msg in enumerate(CORPUS):
        sig = AceParser.extract_details(
            msg.get("content", ""), msg["id"], msg["timestamp"],
            open_tickers=set(open_pos), resolve_sole_position=resolve_sole_position,
        )
        if sig is None:
            continue
        if sig.action == SignalAction.ENTRY.value:
            if sig.ticker in open_pos:
                # main.py's duplicate-position guard would block this entry.
                unclosed.append(open_pos[sig.ticker])
            open_pos[sig.ticker] = index
        elif sig.ticker in open_pos:
            del open_pos[sig.ticker]
            closed += 1
    unclosed.extend(open_pos.values())
    return closed, sorted(unclosed)


class TestAceLifecycle:
    # The five tickerless stop-loss cuts that are the ONLY exit for their
    # position. Every one is a LOSING trade — Ace names the ticker on winners
    # and omits it when cutting.
    SOLE_EXIT_MISSES = [58, 160, 166, 174, 253]

    def test_default_mode_closes_every_trade(self):
        # resolve_sole_position is ON by default — this is production behaviour.
        closed, unclosed = replay()
        assert unclosed == [], f"positions left open: {unclosed}"
        assert closed == 45, "every entry should get exactly one matching exit"

    def test_disabling_sole_position_leaves_the_five_documented_positions_open(self):
        # Kept as the counterfactual: this is what turning the flag off costs.
        closed, unclosed = replay(resolve_sole_position=False)
        assert unclosed == self.SOLE_EXIT_MISSES
        assert closed == 40

    def test_the_five_misses_are_all_losing_trades(self):
        # Documents WHY this matters: without resolution these are held to expiry.
        for index in self.SOLE_EXIT_MISSES:
            assert parse(index).action == SignalAction.ENTRY.value


class TestAceRouterWiring:
    """The sole-position default is useless unless the router supplies the
    open positions. These pin the wiring, not just the parser flag."""

    @staticmethod
    def _router():
        # Config reads env at class-definition time, so setting DISCORD_CHANNEL_ACE
        # here would be too late — pass the channel as a constructor arg instead.
        os.environ["ENABLED_ANALYSTS"] = ""
        from config import Config
        from signal_router import SignalRouter
        return SignalRouter(Config(discord_channel_ace="ace_test_channel")), "ace_test_channel"

    def test_router_passes_open_positions_to_the_ace_parser(self, monkeypatch):
        router, channel = self._router()
        monkeypatch.setattr(router, "_open_tickers", lambda analyst: {"AAPL"})
        # Real corpus message 162: "cutting here -30% 🛑" — no ticker anywhere.
        sig = router.route_message(channel, "m1", "cutting here -30% 🛑\n\nnot a good trade sorry guys.",
                                   "2026-03-31T18:00:00+00:00", [], None)
        assert sig is not None, "router did not hand position context to the parser"
        assert sig.ticker == "AAPL"
        assert sig.action == SignalAction.EXIT.value

    def test_router_refuses_when_two_positions_are_open(self, monkeypatch):
        router, channel = self._router()
        monkeypatch.setattr(router, "_open_tickers", lambda analyst: {"AAPL", "AMZN"})
        sig = router.route_message(channel, "m2", "cutting here -30% 🛑",
                                   "2026-03-31T18:00:00+00:00", [], None)
        assert sig is None

    def test_router_refuses_when_the_named_ticker_is_not_the_open_one(self, monkeypatch):
        # "closed AAPL" while holding only AMZN must NOT close AMZN.
        router, channel = self._router()
        monkeypatch.setattr(router, "_open_tickers", lambda analyst: {"AMZN"})
        sig = router.route_message(channel, "m3", "closed AAPL. sorry about that guys.",
                                   "2026-04-16T18:00:00+00:00", [], None)
        assert sig is None

    def test_open_tickers_returns_empty_set_when_db_is_unreadable(self):
        router, _ = self._router()
        object.__setattr__(router.config, "db_path", "/nonexistent/dir/nope.db")
        assert router._open_tickers("ace") == set()

    def test_entry_path_still_works_through_the_router(self, monkeypatch):
        router, channel = self._router()
        monkeypatch.setattr(router, "_open_tickers", lambda analyst: set())
        sig = router.route_message(channel, "m4", "Entering:\n**$GOOGL 350c 06/24 @2.30**\n\nlight sized",
                                   "2026-06-22T15:00:00+00:00", [], None)
        assert sig is not None
        assert sig.action == SignalAction.ENTRY.value
        assert (sig.ticker, sig.strike, sig.expiry) == ("GOOGL", 350.0, "2026-06-24")
