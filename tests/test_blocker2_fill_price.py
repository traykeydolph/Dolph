"""Blocker 2 regression — exits/trims must book the ACTUAL fill, not the signal price.

The bug: a `try/except/else` in _handle_trim / _handle_exit whose `else` ran on
SUCCESS overwrote the real fill price (order_result['filled_price']) with
signal.entry_price. Result: every exit booked at the analyst's signal price, not
the fill — e.g. CSCO 07-28 filled $1.00 (a −$15 loss) but booked $1.30 (+$15), a
sign flip. These tests drive the handlers with a fill price that DIFFERS from the
signal price and assert the fill wins everywhere it's recorded.

Run: ./venv/bin/python -m pytest tests/test_blocker2_fill_price.py -v
"""

import asyncio
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from parsers.base import ParsedSignal, SignalAction, AssetType

SIGNAL_PRICE = 1.30   # what the analyst said (the OLD, wrong booking)
FILL_PRICE = 1.00     # what Alpaca actually filled (the CORRECT booking)
ENTRY_PRICE = 1.15    # position entry fill


class _AsyncNoop:
    """Every attribute is an async no-op — stands in for the Telegram alerter."""
    def __getattr__(self, _):
        async def _f(*a, **k):
            return True
        return _f


def _position():
    return SimpleNamespace(
        id=1, analyst="eva", ticker="CSCO", direction="put",
        strike=110.0, expiry="2026-07-31", asset_type=AssetType.OPTION.value,
        current_quantity=1, entry_price=ENTRY_PRICE, trim_count=0,
        stop_price=None, target_prices=None)


def _signal(action):
    return ParsedSignal(
        analyst="eva", action=action, asset_type=AssetType.OPTION.value,
        ticker="CSCO", direction="put", strike=110.0, expiry="2026-07-31",
        entry_price=SIGNAL_PRICE, trim_fraction=1.0, confidence=0.95,
        raw_message="STC CSCO 07/31 110P @ 1.30", message_id="m", timestamp="")


@pytest.fixture
def bot(monkeypatch):
    import main as main_module
    # Journal writers touch the filesystem — stub them out.
    for fn in ("open_trade", "record_trim", "close_trade"):
        monkeypatch.setattr(main_module, fn, lambda *a, **k: None)
    # Skip the handler's real 2s post-exit-verification sleep.
    async def _fast_sleep(*a, **k):
        return None
    monkeypatch.setattr(main_module.asyncio, "sleep", _fast_sleep)

    b = object.__new__(main_module.TradingBot)
    b.captured = {}

    # Alpaca: the order fills at FILL_PRICE (≠ the signal price).
    def execute_exit_order(signal, qty):
        return {"filled_price": FILL_PRICE, "filled_qty": qty, "status": "filled"}
    b._alpaca = SimpleNamespace(
        execute_exit_order=execute_exit_order,
        list_open_positions=lambda: [],          # post-exit check: confirmed flat
    )
    b._coinbase = None

    # position_manager: capture the price handed to trim/close.
    def trim_position(signal, price):
        b.captured["trim_price"] = price
        # P&L computed off the price we were given (that's the point).
        return {"trim_quantity": 1, "remaining_quantity": 0,
                "trim_pnl": round((price - ENTRY_PRICE) * 100, 2)}

    def close_position(signal, price):
        b.captured["exit_price"] = price
        return {"exit_quantity": 1, "total_pnl": round((price - ENTRY_PRICE) * 100, 2)}

    b.position_mgr = SimpleNamespace(
        find_position=lambda ticker, analyst: _position(),
        trim_position=trim_position,
        close_position=close_position)

    # db: capture what gets written as the executed trade.
    def create_trade(**kw):
        b.captured["trade"] = kw
    b.db = SimpleNamespace(create_trade=create_trade, update_position=lambda *a, **k: None)

    b.alerter = _AsyncNoop()
    b.drawdown_mgr = SimpleNamespace(
        check_risk_limits=lambda: (True, ""),
        get_risk_summary=lambda: {"daily_pnl": 0.0, "total_pnl": 0.0})

    async def _noop(*a, **k):
        return None
    b._sync_sheets = _noop
    return b


class TestTrimBooksFill:
    def test_trim_uses_fill_price_not_signal_price(self, bot):
        asyncio.run(bot._handle_trim(_signal(SignalAction.TRIM.value)))
        assert bot.captured["trim_price"] == FILL_PRICE          # 1.00, not 1.30
        assert bot.captured["trade"]["executed_price"] == FILL_PRICE

    def test_trim_pnl_reflects_the_real_loss(self, bot):
        asyncio.run(bot._handle_trim(_signal(SignalAction.TRIM.value)))
        # fill 1.00 vs entry 1.15 = −$15 (a loss), NOT the +$15 the signal implied.
        assert bot.captured["trade"]["pnl"] == -15.0


class TestExitBooksFill:
    def test_exit_uses_fill_price_not_signal_price(self, bot):
        asyncio.run(bot._handle_exit(_signal(SignalAction.EXIT.value)))
        assert bot.captured["exit_price"] == FILL_PRICE
        assert bot.captured["trade"]["executed_price"] == FILL_PRICE

    def test_exit_pnl_reflects_the_real_loss(self, bot):
        asyncio.run(bot._handle_exit(_signal(SignalAction.EXIT.value)))
        assert bot.captured["trade"]["pnl"] == -15.0


class TestSignalPriceNeverLeaksIn:
    """The exact sign-flip that shipped: booked +$15 on a real −$15 trade."""
    def test_no_sign_flip(self, bot):
        asyncio.run(bot._handle_exit(_signal(SignalAction.EXIT.value)))
        pnl = bot.captured["trade"]["pnl"]
        assert pnl < 0, f"loss booked as {pnl} — sign flip regressed"
        assert bot.captured["exit_price"] != SIGNAL_PRICE
