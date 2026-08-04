"""Restart-recovery — missed-exit-on-restart (LIVE_SAFETY.md Blocker 1).

The hole: on restart the poll cursor reset to None and reseeded to the *latest*
message, so any close posted while the bot was down was skipped permanently —
and even if it were refetched, the action-blind stale guard would drop it as
">10 min old". With no broker/option stop, the position sat open, unwatched.

Two guarantees close it, both required:
  A. The poll cursor is PERSISTED, so a restart resumes with `after=<last seen>`
     (refetching what was missed) instead of reseeding to latest.
  B. The stale-signal guard is action-aware: a stale EXIT/TRIM/STOP for a
     currently-open position still executes (a late close protects principal),
     while stale ENTRIES are still skipped (don't open old positions).

Run: ./venv/bin/python -m pytest tests/test_restart_recovery.py -v
"""

import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from config import Config
from storage.database import Database
from discord_poller import DiscordPoller
from parsers.base import ParsedSignal, SignalAction, AssetType

EVA_CHANNEL = "1035245170582626334"


# ═══════════════════════════════════════════════════════════════════
# Part A — the poll cursor survives a restart
# ═══════════════════════════════════════════════════════════════════

class TestCursorPersistence:
    def test_set_get_roundtrip(self, tmp_path):
        db = Database(str(tmp_path / "t.db"))
        assert db.get_cursor(EVA_CHANNEL) is None          # unknown channel → None
        db.set_cursor(EVA_CHANNEL, "111")
        assert db.get_cursor(EVA_CHANNEL) == "111"
        db.set_cursor(EVA_CHANNEL, "222")                  # updates in place
        assert db.get_cursor(EVA_CHANNEL) == "222"
        db.close()

    def test_poller_resumes_from_persisted_cursor_instead_of_reseeding(self, tmp_path):
        # THE regression guard: a fresh poller (i.e. a restart) must load the
        # saved cursor, not reset to None (which would reseed to latest and skip
        # everything posted during downtime).
        db = Database(str(tmp_path / "t.db"))
        db.set_cursor(EVA_CHANNEL, "999")
        poller = DiscordPoller(Config(discord_channel_eva=EVA_CHANNEL), cursor_store=db)
        assert poller.last_message_id.get(EVA_CHANNEL) == "999"
        db.close()

    def test_poller_persists_cursor_after_a_successful_fetch(self, tmp_path, monkeypatch):
        db = Database(str(tmp_path / "t.db"))
        db.set_cursor(EVA_CHANNEL, "100")                  # last saw message 100
        poller = DiscordPoller(Config(discord_channel_eva=EVA_CHANNEL), cursor_store=db)

        class FakeResp:
            status_code = 200
            def json(self):
                return [
                    {"id": "101", "content": "a", "timestamp": "2026-01-01T00:00:00+00:00"},
                    {"id": "102", "content": "b", "timestamp": "2026-01-01T00:01:00+00:00"},
                ]
        monkeypatch.setattr(poller, "_request_with_backoff", lambda *a, **k: FakeResp())

        msgs = poller._fetch_channel(EVA_CHANNEL)
        assert [m.message_id for m in msgs] == ["101", "102"]
        assert db.get_cursor(EVA_CHANNEL) == "102"         # advanced AND persisted
        db.close()

    def test_first_ever_seed_is_persisted(self, tmp_path, monkeypatch):
        # No saved cursor → seed to latest, and persist the seed so the *next*
        # restart resumes from it rather than re-seeding again.
        db = Database(str(tmp_path / "t.db"))
        poller = DiscordPoller(Config(discord_channel_eva=EVA_CHANNEL), cursor_store=db)
        assert poller.last_message_id.get(EVA_CHANNEL) is None

        class FakeSeed:
            status_code = 200
            def json(self):
                return [{"id": "500", "content": "", "timestamp": "2026-01-01T00:00:00+00:00"}]
        monkeypatch.setattr(poller, "_request_with_backoff", lambda *a, **k: FakeSeed())

        poller._fetch_channel(EVA_CHANNEL)                 # first poll seeds
        assert db.get_cursor(EVA_CHANNEL) == "500"
        db.close()


# ═══════════════════════════════════════════════════════════════════
# Part B — a stale close still fires; a stale entry does not
# ═══════════════════════════════════════════════════════════════════

def _sig(action, ticker="IWM"):
    return ParsedSignal(
        analyst="eva", action=action, asset_type=AssetType.OPTION.value, ticker=ticker,
        direction="call", strike=300.0, expiry="2026-07-24", entry_price=0.30,
        trim_fraction=None, confidence=0.95, raw_message="x", message_id="m",
        timestamp="2026-07-24T00:00:00+00:00",
    )


def _stale_msg(message_id):
    from discord_poller import DiscordMessage
    old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()  # well past 10-min guard
    return DiscordMessage(channel_id=EVA_CHANNEL, message_id=message_id, content="whatever",
                          timestamp=old, embeds=[], referenced_message=None)


@pytest.fixture
def eva_bot(tmp_path, monkeypatch):
    """A TradingBot on the executable Eva channel; dispatch handlers record calls
    instead of hitting Alpaca. Router is stubbed per-test to return a chosen signal
    (keeps these tests off the network and independent of parser internals)."""
    os.environ["ENABLED_ANALYSTS"] = "eva"
    os.environ.pop("SHADOW_ANALYSTS", None)
    import main as main_module

    bot = object.__new__(main_module.TradingBot)
    bot.config = Config(discord_channel_eva=EVA_CHANNEL, db_path=str(tmp_path / "eva.db"))
    from signal_router import SignalRouter
    bot.db = Database(bot.config.db_path)
    bot.router = SignalRouter(bot.config)

    calls = {"entry": 0, "trim": 0, "exit": 0}
    async def _entry(sig): calls["entry"] += 1
    async def _trim(sig): calls["trim"] += 1
    async def _exit(sig): calls["exit"] += 1
    monkeypatch.setattr(bot, "_handle_entry", _entry, raising=False)
    monkeypatch.setattr(bot, "_handle_trim", _trim, raising=False)
    monkeypatch.setattr(bot, "_handle_exit", _exit, raising=False)
    async def _noop(*a, **k): return None
    monkeypatch.setattr(bot, "_alert_no_action", _noop, raising=False)

    class Alerter:
        async def send_message(self, text, **kw): return True
    bot.alerter = Alerter()
    bot.calls = calls
    yield bot
    bot.db.close()


def _run(bot, msg):
    asyncio.run(bot._process_message(msg))


class TestStaleCloseStillFires:
    def test_stale_exit_executes_when_a_position_is_open(self, eva_bot, monkeypatch):
        eva_bot.db.open_position(analyst="eva", ticker="IWM", current_quantity=1)
        monkeypatch.setattr(eva_bot.router, "route_message",
                            lambda *a, **k: _sig(SignalAction.EXIT.value))
        _run(eva_bot, _stale_msg("exit_stale_1"))
        assert eva_bot.calls["exit"] == 1, "a recovered stale close must still execute"

    def test_stale_trim_executes_when_a_position_is_open(self, eva_bot, monkeypatch):
        eva_bot.db.open_position(analyst="eva", ticker="IWM", current_quantity=1)
        monkeypatch.setattr(eva_bot.router, "route_message",
                            lambda *a, **k: _sig(SignalAction.TRIM.value))
        _run(eva_bot, _stale_msg("trim_stale_1"))
        assert eva_bot.calls["trim"] == 1

    def test_stale_entry_is_skipped_even_with_a_position_open(self, eva_bot, monkeypatch):
        # A late ENTRY must never open a stale position, even when the position
        # gate is satisfied by an unrelated open holding.
        eva_bot.db.open_position(analyst="eva", ticker="IWM", current_quantity=1)
        monkeypatch.setattr(eva_bot.router, "route_message",
                            lambda *a, **k: _sig(SignalAction.ENTRY.value, ticker="AAPL"))
        _run(eva_bot, _stale_msg("entry_stale_1"))
        assert eva_bot.calls["entry"] == 0, "a stale entry must not open a position"

    def test_stale_message_with_no_open_positions_is_skipped_without_routing(self, eva_bot, monkeypatch):
        # The common case: nothing open → a stale message can't be a needed close,
        # so it is skipped WITHOUT routing (no network, no execution).
        routed = {"n": 0}
        def _spy(*a, **k):
            routed["n"] += 1
            return _sig(SignalAction.EXIT.value)
        monkeypatch.setattr(eva_bot.router, "route_message", _spy)
        _run(eva_bot, _stale_msg("stale_norote"))
        assert routed["n"] == 0, "stale message must not be routed when nothing is open"
        assert eva_bot.calls["exit"] == 0

    def test_replay_dedups_already_processed_message(self, eva_bot, monkeypatch):
        eva_bot.db.open_position(analyst="eva", ticker="IWM", current_quantity=1)
        monkeypatch.setattr(eva_bot.router, "route_message",
                            lambda *a, **k: _sig(SignalAction.EXIT.value))
        _run(eva_bot, _stale_msg("dedup_1"))
        _run(eva_bot, _stale_msg("dedup_1"))               # same id → replayed
        assert eva_bot.calls["exit"] == 1, "a replayed message must execute exactly once"
