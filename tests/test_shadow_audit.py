"""Full-parse shadow audit — the dataset for trusting Waxui execution.

Covers the three 2026-07-28 additions:
  1. Waxui "Day Trade idea" watchlist posts are noise (false-entry guard).
  2. would_execute() — would a parse place an order in production?
  3. shadow_audit() — captures BOTH the regex and Gemini verdicts, and the
     _process_shadow_message flow logs them + flags would_execute while still
     placing NO order (even when Gemini would parse an executable entry).

Run: ./venv/bin/python -m pytest tests/test_shadow_audit.py -v
"""

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from config import Config
from parsers.base import ParsedSignal, SignalAction, AssetType
from parsers.waxui import WaxuiParser
from shadow_logger import would_execute, INDEX_TICKERS

WAXUI_CHANNEL = "1347238168109387857"


@pytest.fixture(autouse=True)
def _isolate_env():
    saved = {k: os.environ.get(k) for k in ("SHADOW_ANALYSTS", "ENABLED_ANALYSTS")}
    os.environ["SHADOW_ANALYSTS"] = "waxui"
    yield
    for k, v in saved.items():
        os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)


def _sig(ticker, action="entry", conf=0.95, asset=AssetType.OPTION.value,
         strike=700.0, direction="call"):
    return ParsedSignal(
        analyst="waxui", action=action, asset_type=asset, ticker=ticker,
        direction=direction, strike=strike, expiry="2026-07-31", entry_price=1.5,
        trim_fraction=None, confidence=conf, raw_message="", message_id="", timestamp="")


# ═══════════════════════════════════════════════════════════════════
# 1. Watchlist "Day Trade idea" posts → noise
# ═══════════════════════════════════════════════════════════════════

class TestWatchlistNoise:
    IDEAS = [
        "*CRWV*, Day Trade idea, Possible Swing. Looking for strength over 66. "
        "Love the 07/31 70Cs. Will alert any entry!",
        "*FIG*, Day Trade idea, Possible Swing. Love the 07/31 25Cs. "
        "Stay posted for any entry alert!",
        "AMD Day Trade idea — watching for a break. Will alert an entry.",
    ]
    REAL = [
        "*Riskier* SPY here 02/23 683C Avg. 1.40",
        "**LOTTO** SPY here 02/27 682P Avg. 2.00",
        "SPY here\n07/22 747P\nAvg. 1.25",
        "Trim SPY here 1.70 - 2.05 ✅ 21% Holding most",
        "Closed SPY here VIX failing yesterday's high",
        "Stopped out of SPY 🔻",
    ]

    @pytest.mark.parametrize("msg", IDEAS)
    def test_watchlist_idea_is_noise(self, msg):
        assert WaxuiParser.is_noise(msg) is True

    @pytest.mark.parametrize("msg", REAL)
    def test_real_signal_is_not_noise(self, msg):
        assert WaxuiParser.is_noise(msg) is False


# ═══════════════════════════════════════════════════════════════════
# 2. would_execute()
# ═══════════════════════════════════════════════════════════════════

class TestWouldExecute:
    def test_executable_entry_fires(self):
        assert would_execute(_sig("SPY")) is True

    def test_index_never_fires(self):
        for tk in ("SPX", "XSP", "NDX", "RUT", "VIX"):
            assert would_execute(_sig(tk)) is False, tk

    def test_low_confidence_blocked(self):
        assert would_execute(_sig("SPY", conf=0.7)) is False
        assert would_execute(_sig("SPY", conf=0.79)) is False
        assert would_execute(_sig("SPY", conf=0.80)) is True

    def test_info_action_never_fires(self):
        assert would_execute(_sig("SPY", action="info")) is False

    def test_none_never_fires(self):
        assert would_execute(None) is False

    def test_crypto_not_alpaca_executable(self):
        assert would_execute(_sig("BTC", asset=AssetType.CRYPTO.value, strike=None,
                                  direction="long")) is False


# ═══════════════════════════════════════════════════════════════════
# 3. Index-skip set (broadened from SPX-only)
# ═══════════════════════════════════════════════════════════════════

class TestIndexSet:
    def test_index_set_covers_waxui_products(self):
        for tk in ("SPX", "SPXW", "XSP", "NDX", "RUT", "VIX"):
            assert tk in INDEX_TICKERS

    def test_equities_and_etfs_are_not_index(self):
        for tk in ("SPY", "QQQ", "IWM", "AAPL", "CRWV", "FIG"):
            assert tk not in INDEX_TICKERS


# ═══════════════════════════════════════════════════════════════════
# 4. shadow_audit — both verdicts, Gemini only on regex-miss
# ═══════════════════════════════════════════════════════════════════

def _router():
    from signal_router import SignalRouter
    return SignalRouter(Config(discord_channel_waxui=WAXUI_CHANNEL))


class TestShadowAudit:
    def test_regex_hit_does_not_run_gemini(self, monkeypatch):
        router = _router()
        monkeypatch.setattr(router.gemini_parser, "parse",
                            lambda *a, **k: pytest.fail("Gemini called on a regex hit"))
        r = router.shadow_audit(WAXUI_CHANNEL, "m", "*Riskier* SPY here 02/23 683C Avg. 1.40",
                                "2026-07-28T15:00:00Z", run_gemini=True)
        assert r["tier"] == "regex-extract"
        assert r["regex_signal"] is not None and r["regex_signal"].ticker == "SPY"
        assert r["gemini_ran"] is False and r["gemini_signal"] is None

    def test_regex_miss_runs_gemini_when_enabled(self, monkeypatch):
        router = _router()
        fake = _sig("CRWV")
        called = {}
        def fake_parse(content, *a, **k):
            called["yes"] = True
            return fake
        monkeypatch.setattr(router.gemini_parser, "parse", fake_parse)
        # an unrecognised prose message that regex won't parse and isn't noise
        r = router.shadow_audit(WAXUI_CHANNEL, "m", "some unstructured musing about CRWV strength",
                                "2026-07-28T15:00:00Z", run_gemini=True)
        assert r["regex_signal"] is None
        assert r["gemini_ran"] is True and called.get("yes")
        assert r["gemini_signal"] is fake

    def test_regex_miss_does_not_run_gemini_when_disabled(self, monkeypatch):
        router = _router()
        monkeypatch.setattr(router.gemini_parser, "parse",
                            lambda *a, **k: pytest.fail("Gemini called with run_gemini=False"))
        r = router.shadow_audit(WAXUI_CHANNEL, "m", "some unstructured musing about strength",
                                "2026-07-28T15:00:00Z", run_gemini=False)
        assert r["gemini_ran"] is False

    def test_watchlist_idea_is_noise_before_gemini(self, monkeypatch):
        # The false-entry guard: a "Day Trade idea" is caught as noise, so it
        # never even reaches Gemini — can't misfire.
        router = _router()
        monkeypatch.setattr(router.gemini_parser, "parse",
                            lambda *a, **k: pytest.fail("Gemini called on a watchlist idea"))
        r = router.shadow_audit(
            WAXUI_CHANNEL, "m",
            "*CRWV*, Day Trade idea. Love the 07/31 70Cs. Will alert any entry!",
            "2026-07-28T15:00:00Z", run_gemini=True)
        assert r["tier"] == "noise-skip"
        assert r["regex_signal"] is None and r["gemini_ran"] is False

    def test_classify_shadow_still_regex_only(self, monkeypatch):
        router = _router()
        monkeypatch.setattr(router.gemini_parser, "parse",
                            lambda *a, **k: pytest.fail("classify_shadow must never call Gemini"))
        sig, tier = router.classify_shadow(WAXUI_CHANNEL, "m",
                                           "unstructured musing", "2026-07-28T15:00:00Z")
        assert sig is None


# ═══════════════════════════════════════════════════════════════════
# 5. THE GATE: a Gemini-parsed executable entry in shadow → flagged,
#    logged with both verdicts, but STILL no order.
# ═══════════════════════════════════════════════════════════════════

class ExplodingBroker:
    def __getattr__(self, name):
        def _boom(*a, **k):
            raise AssertionError(f"SHADOW PLACED AN ORDER: {name}()")
        return _boom


@pytest.fixture
def shadow_bot(tmp_path, monkeypatch):
    os.environ["SHADOW_ANALYSTS"] = "waxui"
    os.environ["ENABLED_ANALYSTS"] = "eva,ace"
    monkeypatch.setattr("shadow_logger.LOG_DIR", str(tmp_path))
    import main as main_module
    from storage.database import Database
    from signal_router import SignalRouter

    bot = object.__new__(main_module.TradingBot)
    bot.config = Config(discord_channel_waxui=WAXUI_CHANNEL,
                        db_path=str(tmp_path / "audit.db"))
    bot.db = Database(bot.config.db_path)
    bot.router = SignalRouter(bot.config)
    bot._gemini_ok = True            # pretend Gemini is reachable
    bot._gemini_detail = "test"
    bot._alpaca = ExplodingBroker()
    bot._coinbase = ExplodingBroker()
    for h in ("_handle_entry", "_handle_trim", "_handle_exit"):
        def _boom(*a, _h=h, **k):
            raise AssertionError(f"SHADOW REACHED EXECUTION: {_h}()")
        monkeypatch.setattr(bot, h, _boom, raising=False)
    sent = []

    class Alerter:
        async def send_message(self, text, **kw):
            sent.append(text); return True
    bot.alerter = Alerter()
    bot.sent = sent
    bot.log_path = tmp_path / "waxui_shadow.jsonl"
    yield bot
    bot.db.close()


def _msg(content, mid):
    from discord_poller import DiscordMessage
    from datetime import datetime, timezone
    return DiscordMessage(channel_id=WAXUI_CHANNEL, message_id=mid, content=content,
                          timestamp=datetime.now(timezone.utc).isoformat(),
                          embeds=[], referenced_message=None)


class TestGeminiEntryInShadowNeverExecutes:
    def test_gemini_executable_entry_is_flagged_but_not_traded(self, shadow_bot, monkeypatch):
        # Force Gemini to (mis)parse a fallback message as an executable SPY entry.
        monkeypatch.setattr(shadow_bot.router.gemini_parser, "parse",
                            lambda *a, **k: _sig("SPY", action="entry", conf=0.95))
        asyncio.run(shadow_bot._process_message(
            _msg("some prose the regex can't parse but gemini thinks is a buy", "g1")))
        rec = json.loads(shadow_bot.log_path.read_text().strip().split("\n")[-1])
        assert rec["gemini_ran"] is True
        assert rec["gemini_action"] == "entry" and rec["gemini_ticker"] == "SPY"
        assert rec["would_execute"] is True          # correctly flagged
        assert rec["executed"] is False              # but NOT traded
        assert any("WOULD EXECUTE" in t for t in shadow_bot.sent)
        assert shadow_bot.db.get_open_positions() == []

    def test_gemini_index_entry_would_not_execute(self, shadow_bot, monkeypatch):
        # Even if Gemini parses an SPX entry, would_execute stays False (index).
        monkeypatch.setattr(shadow_bot.router.gemini_parser, "parse",
                            lambda *a, **k: _sig("SPX", action="entry", conf=0.95))
        asyncio.run(shadow_bot._process_message(_msg("prose about spx", "g2")))
        rec = json.loads(shadow_bot.log_path.read_text().strip().split("\n")[-1])
        assert rec["gemini_ticker"] == "SPX"
        assert rec["would_execute"] is False
        assert rec["executed"] is False
