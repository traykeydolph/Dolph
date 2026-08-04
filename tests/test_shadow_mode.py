"""Shadow / log-only mode — Waxui observation without execution.

The load-bearing test here is TestShadowNeverExecutes: it drives real
historical Waxui signals (one SPX, one SPY) through the actual
TradingBot._process_message and asserts that no Alpaca/Coinbase order method
is ever called. Everything else supports that claim.

Run: ./venv/bin/python -m pytest tests/test_shadow_mode.py -v
"""

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from config import Config
from parsers.base import ParsedSignal, SignalAction, AssetType
from shadow_logger import classify_instrument, format_shadow_alert, log_shadow_observation

WAXUI_CHANNEL = "1347238168109387857"


@pytest.fixture(autouse=True)
def _isolate_analyst_env():
    """These tests flip SHADOW_ANALYSTS/ENABLED_ANALYSTS. Restore them so the
    settings can't leak into other test modules."""
    saved = {k: os.environ.get(k) for k in ("SHADOW_ANALYSTS", "ENABLED_ANALYSTS")}
    yield
    for key, value in saved.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value

# Real Waxui messages (format matches tests/test_replay.py's validated set).
SPX_ENTRY = "*Riskier* SPX here 02/23 6830C Avg. 1.40"
SPY_ENTRY = "*Riskier* SPY here 02/23 683C Avg. 1.40"


def shadow_config(**overrides):
    """Config with Waxui in shadow and Eva/Ace still executable."""
    os.environ["SHADOW_ANALYSTS"] = "waxui"
    os.environ["ENABLED_ANALYSTS"] = "eva,ace"
    return Config(discord_channel_waxui=WAXUI_CHANNEL, **overrides)


# ═══════════════════════════════════════════════════════════════════
# Config: shadow is a separate axis from execution
# ═══════════════════════════════════════════════════════════════════

class TestShadowConfig:
    def test_shadow_channel_is_polled_but_not_executable(self):
        cfg = shadow_config()
        assert WAXUI_CHANNEL in cfg.discord_only_channels, "poller must fetch it"
        assert WAXUI_CHANNEL in cfg.watched_channels
        assert cfg.channel_to_analyst[WAXUI_CHANNEL] == "waxui"
        assert cfg.is_shadow_channel(WAXUI_CHANNEL) is True
        assert cfg._analyst_enabled("waxui") is False, "waxui must NOT be executable"

    def test_enabled_analysts_is_untouched_by_shadow(self):
        cfg = shadow_config()
        assert cfg.enabled_analysts == {"eva", "ace"}
        assert cfg._analyst_enabled("eva") is True
        assert cfg._analyst_enabled("ace") is True

    def test_shadow_wins_if_an_analyst_is_in_both_lists(self):
        # Belt and braces: observation must beat execution, never the reverse.
        os.environ["SHADOW_ANALYSTS"] = "waxui"
        os.environ["ENABLED_ANALYSTS"] = "eva,waxui"
        cfg = Config(discord_channel_waxui=WAXUI_CHANNEL)
        assert cfg._analyst_enabled("waxui") is False
        assert cfg.is_shadow_channel(WAXUI_CHANNEL) is True

    def test_no_shadow_env_means_no_shadow_channels(self):
        os.environ["SHADOW_ANALYSTS"] = ""
        cfg = Config(discord_channel_waxui=WAXUI_CHANNEL)
        assert cfg.shadow_channels == []
        assert cfg.is_shadow_channel(WAXUI_CHANNEL) is False

    def test_eva_channel_is_not_shadow(self):
        cfg = shadow_config()
        if cfg.discord_channel_eva:
            assert cfg.is_shadow_channel(cfg.discord_channel_eva) is False


# ═══════════════════════════════════════════════════════════════════
# Instrument classification — the Tastytrade decision input
# ═══════════════════════════════════════════════════════════════════

def _sig(ticker, asset_type=AssetType.OPTION.value):
    return ParsedSignal(
        analyst="waxui", action=SignalAction.ENTRY.value, asset_type=asset_type,
        ticker=ticker, direction="call", strike=683.0, expiry="2026-02-23",
        entry_price=1.40, trim_fraction=None, confidence=0.9,
        raw_message="", message_id="", timestamp="",
    )


class TestInstrumentClassification:
    @pytest.mark.parametrize("ticker", ["SPX", "SPXW", "XSP", "NDX", "RUT", "VIX"])
    def test_index_products_are_not_executable(self, ticker):
        info = classify_instrument(_sig(ticker))
        assert info["is_index"] is True
        assert info["executable_on_alpaca"] is False
        assert "index" in info["not_executable_reason"]

    @pytest.mark.parametrize("ticker", ["SPY", "QQQ", "IWM", "AAPL", "HOOD"])
    def test_etfs_and_equities_are_executable(self, ticker):
        info = classify_instrument(_sig(ticker))
        assert info["is_index"] is False
        assert info["executable_on_alpaca"] is True

    def test_crypto_is_not_alpaca_executable(self):
        info = classify_instrument(_sig("BTC", AssetType.CRYPTO.value))
        assert info["executable_on_alpaca"] is False

    def test_unparsed_signal_is_not_executable(self):
        info = classify_instrument(None)
        assert info["executable_on_alpaca"] is False
        assert info["instrument"] is None


class TestShadowAlertFormat:
    def test_alert_is_always_shadow_prefixed(self):
        assert format_shadow_alert("waxui", SPY_ENTRY, _sig("SPY"), "regex-extract").startswith("[SHADOW]")
        assert format_shadow_alert("waxui", "noise", None, "noise-skip").startswith("[SHADOW]")

    def test_alert_says_no_order(self):
        body = format_shadow_alert("waxui", SPY_ENTRY, _sig("SPY"), "regex-extract")
        assert "NO ORDER" in body

    def test_alert_flags_index_as_not_executable(self):
        body = format_shadow_alert("waxui", SPX_ENTRY, _sig("SPX"), "regex-extract")
        assert "NOT executable" in body


class TestShadowLogFile:
    def test_writes_one_json_object_per_line(self, tmp_path):
        for ticker in ("SPX", "SPY"):
            log_shadow_observation("waxui", WAXUI_CHANNEL, f"id_{ticker}",
                                   f"{ticker} msg", "2026-02-23T15:00:00+00:00",
                                   _sig(ticker), "regex-extract", log_dir=str(tmp_path))
        lines = (tmp_path / "waxui_shadow.jsonl").read_text().strip().split("\n")
        assert len(lines) == 2
        records = [json.loads(line) for line in lines]
        assert [r["ticker"] for r in records] == ["SPX", "SPY"]
        assert all(r["executed"] is False for r in records)

    def test_record_has_every_required_field(self, tmp_path):
        log_shadow_observation("waxui", WAXUI_CHANNEL, "id1", SPY_ENTRY,
                               "2026-02-23T15:00:00+00:00", _sig("SPY"),
                               "regex-extract", log_dir=str(tmp_path))
        record = json.loads((tmp_path / "waxui_shadow.jsonl").read_text().strip())
        for field in ("observed_at", "message_timestamp", "raw_text", "action",
                      "ticker", "strike", "expiry", "price", "instrument",
                      "is_index", "executable_on_alpaca", "tier", "executed"):
            assert field in record, f"missing field: {field}"

    def test_logging_failure_is_swallowed(self):
        # A bad path must not raise into the polling loop.
        assert log_shadow_observation("waxui", "c", "m", "t", "ts", None, "noise-skip",
                                      log_dir="/nonexistent/nope/deeper") is None


# ═══════════════════════════════════════════════════════════════════
# THE GATE: shadow messages must never reach execution
# ═══════════════════════════════════════════════════════════════════

class ExplodingBroker:
    """Any attribute access that looks like trading blows up the test."""

    def __getattr__(self, name):
        def _boom(*args, **kwargs):
            raise AssertionError(f"SHADOW PATH PLACED AN ORDER: {name}() was called")
        return _boom


@pytest.fixture
def shadow_bot(tmp_path, monkeypatch):
    """A TradingBot wired for shadow Waxui, with brokers that explode on use."""
    os.environ["SHADOW_ANALYSTS"] = "waxui"
    os.environ["ENABLED_ANALYSTS"] = "eva,ace"
    monkeypatch.setattr("shadow_logger.LOG_DIR", str(tmp_path))

    import main as main_module

    bot = object.__new__(main_module.TradingBot)
    bot.config = Config(discord_channel_waxui=WAXUI_CHANNEL,
                        db_path=str(tmp_path / "shadow_test.db"))
    bot._gemini_ok = False          # audit runs regex-only unless a test flips this
    bot._gemini_detail = "test"

    from storage.database import Database
    from signal_router import SignalRouter
    bot.db = Database(bot.config.db_path)
    bot.router = SignalRouter(bot.config)

    # Brokers and every execution handler are booby-trapped.
    bot._alpaca = ExplodingBroker()
    bot._coinbase = ExplodingBroker()
    for handler in ("_handle_entry", "_handle_trim", "_handle_exit"):
        def _boom(*args, _h=handler, **kwargs):
            raise AssertionError(f"SHADOW PATH REACHED EXECUTION: {_h}() was called")
        monkeypatch.setattr(bot, handler, _boom, raising=False)

    sent = []

    class Alerter:
        async def send_message(self, text, **kw):
            sent.append(text)
            return True

    bot.alerter = Alerter()
    bot.sent = sent
    bot.log_path = tmp_path / "waxui_shadow.jsonl"
    yield bot
    bot.db.close()


def _msg(content, message_id):
    from discord_poller import DiscordMessage
    from datetime import datetime, timezone
    return DiscordMessage(
        channel_id=WAXUI_CHANNEL, message_id=message_id, content=content,
        # "now" so the stale-signal guard doesn't short-circuit the test
        timestamp=datetime.now(timezone.utc).isoformat(),
        embeds=[], referenced_message=None,
    )


class TestShadowNeverExecutes:
    """Replay real Waxui signals through the live _process_message path."""

    def test_spx_signal_is_logged_and_places_no_order(self, shadow_bot):
        asyncio.run(shadow_bot._process_message(_msg(SPX_ENTRY, "spx_1")))
        record = json.loads(shadow_bot.log_path.read_text().strip())
        assert record["ticker"] == "SPX"
        assert record["is_index"] is True
        assert record["executable_on_alpaca"] is False
        assert record["executed"] is False
        assert record["tier"] == "regex-extract"

    def test_spy_signal_is_logged_and_places_no_order(self, shadow_bot):
        asyncio.run(shadow_bot._process_message(_msg(SPY_ENTRY, "spy_1")))
        record = json.loads(shadow_bot.log_path.read_text().strip())
        assert record["ticker"] == "SPY"
        assert record["is_index"] is False
        assert record["executable_on_alpaca"] is True
        assert record["executed"] is False

    def test_both_signals_land_in_one_jsonl_with_correct_classification(self, shadow_bot):
        asyncio.run(shadow_bot._process_message(_msg(SPX_ENTRY, "spx_2")))
        asyncio.run(shadow_bot._process_message(_msg(SPY_ENTRY, "spy_2")))
        records = [json.loads(l) for l in
                   shadow_bot.log_path.read_text().strip().split("\n")]
        assert len(records) == 2
        assert {r["ticker"]: r["executable_on_alpaca"] for r in records} == \
               {"SPX": False, "SPY": True}

    def test_alerts_are_shadow_tagged(self, shadow_bot):
        asyncio.run(shadow_bot._process_message(_msg(SPX_ENTRY, "spx_3")))
        assert shadow_bot.sent, "no Telegram alert fired"
        assert all(text.startswith("[SHADOW]") for text in shadow_bot.sent)
        assert "NO ORDER" in shadow_bot.sent[0]

    def test_noise_is_logged_too(self, shadow_bot):
        asyncio.run(shadow_bot._process_message(
            _msg("Trail stops set @B/E", "noise_1")))
        record = json.loads(shadow_bot.log_path.read_text().strip())
        assert record["parsed"] is False
        assert record["executed"] is False

    def test_no_trade_or_position_row_is_created(self, shadow_bot):
        asyncio.run(shadow_bot._process_message(_msg(SPY_ENTRY, "spy_4")))
        assert shadow_bot.db.get_open_positions() == []

    def test_shadow_message_is_marked_processed(self, shadow_bot):
        asyncio.run(shadow_bot._process_message(_msg(SPY_ENTRY, "spy_5")))
        assert shadow_bot.db.is_message_processed("spy_5") is True


class TestShadowClassifier:
    """classify_shadow reports the tier and never calls Gemini."""

    @staticmethod
    def _router():
        os.environ["SHADOW_ANALYSTS"] = "waxui"
        from signal_router import SignalRouter
        return SignalRouter(Config(discord_channel_waxui=WAXUI_CHANNEL))

    def test_regex_tier_for_a_real_entry(self):
        signal, tier = self._router().classify_shadow(
            WAXUI_CHANNEL, "m1", SPY_ENTRY, "2026-02-23T15:00:00+00:00")
        assert tier == "regex-extract"
        assert signal is not None and signal.ticker == "SPY"

    def test_noise_tier(self):
        signal, tier = self._router().classify_shadow(
            WAXUI_CHANNEL, "m2", "Trail stops set @B/E", "2026-02-23T15:00:00+00:00")
        assert signal is None
        assert tier in ("noise-skip", "unparsed")

    def test_empty_message_is_noise(self):
        signal, tier = self._router().classify_shadow(
            WAXUI_CHANNEL, "m3", "   ", "2026-02-23T15:00:00+00:00")
        assert signal is None and tier == "noise-skip"

    def test_gemini_is_never_invoked(self, monkeypatch):
        router = self._router()
        monkeypatch.setattr(
            router.gemini_parser, "parse",
            lambda *a, **kw: (_ for _ in ()).throw(
                AssertionError("shadow path called Gemini")),
        )
        for message in (SPX_ENTRY, SPY_ENTRY, "Trail stops set @B/E",
                        "some totally unrecognisable prose here"):
            router.classify_shadow(WAXUI_CHANNEL, "m", message,
                                   "2026-02-23T15:00:00+00:00")
