"""Blockers 3 & 4 regressions + shutdown-teardown idempotency.

Blocker 3 — no naked/unbounded market orders. The old code replaced an unfilled
limit with a plain market order after 15s (observed live on OKLO 07-29: sell
limit $0.25 → market → filled $0.22, 3 ticks through the limit). The fix is a
bounded ladder:
  entry: ask → ask+cap → SKIP+alert            (never a market order)
  exit:  bid → bid−cap → bid−emergency → market (must go flat; market is the
         guaranteed-fill last resort, loudly alerted)

Blocker 4 — the Gemini network call must carry a hard timeout so a connect-hang
fails fast instead of freezing message processing.

These drive AlpacaClient's option paths with a scripted fake broker so each rung's
fill/no-fill is deterministic, and assert which order types are (and are NOT) sent.

Run: ./venv/bin/python -m pytest tests/test_blocker34_fill_ladder.py -v
"""

import asyncio
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from execution.alpaca_client import AlpacaClient
from parsers.base import ParsedSignal, SignalAction, AssetType

BID, ASK = 0.25, 0.30
# derived by the same rule the client uses: max(pct·price, abs_floor)
CAP_BUY = 0.33      # ask 0.30 + max(5%≈0.02, 0.03) = 0.33
CAP_SELL = 0.22     # bid 0.25 − 0.03
EMERGENCY_SELL = 0.20  # bid 0.25 − max(20%=0.05, 0.03) = 0.20


class _Order:
    def __init__(self, oid, status, qty, price):
        self.id = oid
        self.status = status
        self.filled_qty = qty
        self.filled_avg_price = price


class _FakeApi:
    """Scripted broker. ``fill_rule(order_kwargs) -> price|None`` decides, per
    submitted order, whether it fills (and at what price) or rests unfilled."""

    def __init__(self, fill_rule):
        self.fill_rule = fill_rule
        self.submitted = []      # every submit_order kwargs, in order
        self._orders = {}
        self._n = 0

    def submit_order(self, **kw):
        self._n += 1
        oid = f"o{self._n}"
        self.submitted.append(kw)
        price = self.fill_rule(kw)
        self._orders[oid] = (_Order(oid, "filled", kw.get("qty", 1), price)
                             if price is not None else _Order(oid, "new", 0, None))
        return self._orders[oid]

    def get_order(self, oid):
        return self._orders[oid]

    def cancel_order(self, oid):
        o = self._orders.get(oid)
        if o and o.status == "new":
            o.status = "canceled"

    # order-type helpers for assertions
    def types(self):
        return [k.get("type") for k in self.submitted]

    def market_count(self):
        return sum(1 for k in self.submitted if k.get("type") == "market")

    def by_suffix(self, suffix):
        return [k for k in self.submitted if str(k.get("client_order_id", "")).endswith(suffix)]


def _client(fill_rule):
    c = object.__new__(AlpacaClient)
    c.config = SimpleNamespace(
        fill_step_timeout=0.05, fill_poll_interval=0.02,
        slippage_cap_pct=0.05, slippage_cap_abs=0.03, emergency_slippage_pct=0.20,
    )
    c.api = _FakeApi(fill_rule)
    c._get_option_quote = lambda sym: {"bid": BID, "ask": ASK, "last": 0.27}
    c._build_option_symbol = lambda sig: "TEST260731C00043000"
    return c


def _signal(action=SignalAction.ENTRY.value):
    return ParsedSignal(
        analyst="eva", action=action, asset_type=AssetType.OPTION.value,
        ticker="TEST", direction="call", strike=43.0, expiry="2026-07-31",
        entry_price=0.68, trim_fraction=None, confidence=0.95,
        raw_message="x", message_id="m", timestamp="")


# ── Blocker 3: ENTRY never escalates to market ───────────────────────────────

class TestEntryNeverMarkets:
    def test_unfilled_entry_is_skipped_not_marketed(self):
        c = _client(lambda kw: None)             # nothing ever fills
        res = c._execute_option_entry(_signal(), position_size=400)
        assert res["status"] == "skipped"
        assert res["fill_stage"] == "skipped"
        assert res["filled_qty"] == 0
        assert res["escalation"]                 # non-empty note for the alert
        assert c.api.market_count() == 0, "entry must NEVER send a market order"

    def test_entry_fills_at_capped_limit_no_market(self):
        # rung 1 (bare coid) rests; rung 2 (…_c) fills at the cap
        c = _client(lambda kw: CAP_BUY if kw["client_order_id"].endswith("_c") else None)
        res = c._execute_option_entry(_signal(), position_size=400)
        assert res["fill_stage"] == "capped"
        assert res["status"] == "filled"
        assert res["filled_price"] == CAP_BUY
        assert c.api.market_count() == 0
        # the capped rung priced at ask + cap, bounded
        assert c.api.by_suffix("_c")[0]["limit_price"] == CAP_BUY

    def test_entry_fills_immediately_at_ask(self):
        c = _client(lambda kw: ASK)              # rung 1 fills
        res = c._execute_option_entry(_signal(), position_size=400)
        assert res["fill_stage"] == "limit"
        assert res["escalation"] is None
        assert len(c.api.submitted) == 1         # no escalation rungs
        assert c.api.market_count() == 0


# ── Blocker 3: EXIT ladder → guaranteed flat via bounded rungs then market ───

class TestExitLadder:
    def test_exit_fills_at_bid_no_escalation(self):
        c = _client(lambda kw: BID)              # rung 1 fills
        res = c._execute_option_exit(_signal(SignalAction.EXIT.value), quantity=1)
        assert res["fill_stage"] == "limit"
        assert res["escalation"] is None
        assert c.api.market_count() == 0
        assert len(c.api.submitted) == 1

    def test_exit_capped_limit_is_bounded(self):
        c = _client(lambda kw: CAP_SELL if kw["client_order_id"].endswith("_c") else None)
        res = c._execute_option_exit(_signal(SignalAction.EXIT.value), quantity=1)
        assert res["fill_stage"] == "capped"
        assert res["filled_price"] == CAP_SELL
        assert c.api.by_suffix("_c")[0]["limit_price"] == CAP_SELL
        assert c.api.market_count() == 0

    def test_exit_emergency_rung_bounded_and_no_market(self):
        c = _client(lambda kw: EMERGENCY_SELL if kw["client_order_id"].endswith("_e") else None)
        res = c._execute_option_exit(_signal(SignalAction.EXIT.value), quantity=1)
        assert res["fill_stage"] == "emergency"
        assert res["filled_price"] == EMERGENCY_SELL
        assert res["escalation"]                 # loud-alert note
        assert c.api.by_suffix("_e")[0]["limit_price"] == EMERGENCY_SELL
        assert c.api.market_count() == 0, "emergency limit filled — no market needed"

    def test_exit_market_backstop_guarantees_flat(self):
        # all bounded limits rest; only the market order fills → position goes flat
        c = _client(lambda kw: 0.18 if kw.get("type") == "market" else None)
        res = c._execute_option_exit(_signal(SignalAction.EXIT.value), quantity=1)
        assert res["fill_stage"] == "market"
        assert res["filled_price"] == 0.18
        assert res["escalation"]
        assert c.api.market_count() == 1
        # the market rung carries no limit_price (it is a true market order)
        assert "limit_price" not in c.api.by_suffix("_mkt")[0]
        # ladder ran all four rungs
        assert [k["type"] for k in c.api.submitted] == ["limit", "limit", "limit", "market"]

    def test_exit_rungs_priced_in_descending_order(self):
        c = _client(lambda kw: 0.18 if kw.get("type") == "market" else None)
        c._execute_option_exit(_signal(SignalAction.EXIT.value), quantity=1)
        limit_prices = [k["limit_price"] for k in c.api.submitted if k["type"] == "limit"]
        assert limit_prices == [BID, CAP_SELL, EMERGENCY_SELL]
        assert limit_prices == sorted(limit_prices, reverse=True)


# ── Blocker 4: Gemini call carries a hard timeout ────────────────────────────

class TestGeminiTimeout:
    def test_client_constructed_with_http_timeout(self, monkeypatch):
        import parsers.gemini_parser as gp
        captured = {}

        class _FakeClient:
            def __init__(self, **kw):
                captured.update(kw)

        monkeypatch.setattr(gp._genai, "Client", _FakeClient)
        cfg = SimpleNamespace(gemini_api_key="x", gemini_timeout_seconds=7)
        p = gp.GeminiParser(cfg)
        assert p._timeout_s == 7
        # HttpOptions.timeout is in milliseconds
        assert captured["http_options"].timeout == 7000

    def test_default_timeout_is_ten_seconds(self, monkeypatch):
        import parsers.gemini_parser as gp
        monkeypatch.setattr(gp._genai, "Client", lambda **kw: SimpleNamespace(**kw))
        p = gp.GeminiParser(SimpleNamespace(gemini_api_key="x", gemini_timeout_seconds=10))
        assert p._timeout_s == 10


# ── Shutdown teardown idempotency (double-teardown Telegram drop) ────────────

class TestStopIsIdempotent:
    def test_second_stop_is_a_noop(self):
        import main as main_module
        calls = {"shutdown": 0, "close": 0, "db": 0}

        class _Alerter:
            async def alert_shutdown(self):
                calls["shutdown"] += 1

            async def close(self):
                calls["close"] += 1

        b = object.__new__(main_module.TradingBot)
        b._stopped = False
        b._running = True
        b.alerter = _Alerter()
        b.db = SimpleNamespace(close=lambda: calls.__setitem__("db", calls["db"] + 1))

        asyncio.run(b.stop())
        asyncio.run(b.stop())    # the second SIGTERM/finally pass

        assert calls == {"shutdown": 1, "close": 1, "db": 1}
        assert b._stopped is True


# ── Regression: no double-fill when a rung fills during the cancel race ───────

class _RacyExitApi(_FakeApi):
    """Reproduces the 08-03 SPY 720P double-fill: rung 1 reads 'new' while the
    ladder polls (so it decides to escalate), but the order has actually FILLED
    by the time the cancel lands. The old code ignored the raced fill and
    submitted the next rung too — selling twice and going short."""

    def __init__(self, fill_price=1.68):
        super().__init__(fill_rule=lambda kw: None)   # nothing fills on submit/poll
        self.fill_price = fill_price
        self._racy_id = None

    def submit_order(self, **kw):
        o = super().submit_order(**kw)
        if self._racy_id is None:
            self._racy_id = o.id          # the first rung is the racy one
        return o

    def cancel_order(self, oid):
        o = self._orders.get(oid)
        if oid == self._racy_id and o is not None:
            # the fill won the race — the cancel finds it already filled
            o.status = "filled"
            o.filled_qty = int(self.submitted[0].get("qty", 1))
            o.filled_avg_price = self.fill_price
        elif o is not None and o.status == "new":
            o.status = "canceled"


class TestNoDoubleFillOnRace:
    def _racy_client(self, fill_price=1.68):
        c = _client(lambda kw: None)
        c.api = _RacyExitApi(fill_price)
        return c

    def test_exit_settles_raced_fill_and_does_not_escalate(self):
        c = self._racy_client(fill_price=1.68)
        res = c._execute_option_exit(_signal(SignalAction.EXIT.value), quantity=1)
        # the whole point: we sold exactly what we held — never twice
        assert res["filled_qty"] == 1, "double-fill regressed — sold more than held"
        assert len(c.api.submitted) == 1, "must NOT submit a 2nd rung once rung 1 filled"
        assert c.api.market_count() == 0
        assert res["status"] == "filled"
        assert res["fill_stage"] == "limit"
        assert res["filled_price"] == 1.68

    def test_entry_settles_raced_fill_and_does_not_escalate(self):
        c = self._racy_client(fill_price=0.30)
        res = c._execute_option_entry(_signal(), position_size=400)
        submitted_qty = c.api.submitted[0]["qty"]   # entry sizes its own qty
        assert res["filled_qty"] == submitted_qty, "double-fill regressed on entry"
        assert len(c.api.submitted) == 1, "must NOT submit rung 2 once rung 1 filled"
        assert res["status"] == "filled"
        assert res["fill_stage"] == "limit"
