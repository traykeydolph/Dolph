"""Health-monitor Gemini throttle + non-critical exit (08-03 quota fix).

The monitor's 5-min live Gemini probe (~288/day) exhausted the free-tier quota it
existed to watch, and a down *fallback* was marking the whole systemd run failed.
Fix: probe Gemini at most hourly (reuse cached result otherwise) and keep Gemini
out of the CRITICAL set that drives the exit code.

Run: ./venv/bin/python -m pytest tests/test_health_monitor_throttle.py -v
"""

import os
import sys
import time
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import health_monitor as hm


class TestGeminiThrottle:
    def _counting_probe(self, monkeypatch, result=(True, "ok")):
        calls = {"n": 0}
        def fake(cfg):
            calls["n"] += 1
            return result
        monkeypatch.setattr(hm, "check_gemini", fake)
        return calls

    def test_first_call_probes_live_and_caches(self, monkeypatch):
        calls = self._counting_probe(monkeypatch)
        state = {}
        ok, detail = hm.maybe_check_gemini(None, state)
        assert calls["n"] == 1 and ok is True
        assert "live" in detail
        assert state["gemini_last_ok"] is True and "gemini_last_probe" in state

    def test_second_call_within_interval_uses_cache(self, monkeypatch):
        calls = self._counting_probe(monkeypatch)
        state = {}
        hm.maybe_check_gemini(None, state)      # 1st: live
        ok, detail = hm.maybe_check_gemini(None, state)  # 2nd: cached
        assert calls["n"] == 1, "must NOT re-probe within the hour"
        assert "cached" in detail and ok is True

    def test_probes_again_after_interval(self, monkeypatch):
        calls = self._counting_probe(monkeypatch)
        stale = (datetime.now(timezone.utc)
                 - timedelta(seconds=hm.GEMINI_PROBE_INTERVAL + 60)).isoformat()
        state = {"gemini_last_probe": stale, "gemini_last_ok": True, "gemini_last_detail": "ok"}
        hm.maybe_check_gemini(None, state)
        assert calls["n"] == 1, "must re-probe once the interval has elapsed"

    def test_force_always_probes(self, monkeypatch):
        calls = self._counting_probe(monkeypatch)
        # even with a fresh cache, force=True (manual --dry-run) probes live
        state = {"gemini_last_probe": datetime.now(timezone.utc).isoformat(),
                 "gemini_last_ok": True, "gemini_last_detail": "ok"}
        hm.maybe_check_gemini(None, state, force=True)
        assert calls["n"] == 1

    def test_cached_failure_is_carried_forward(self, monkeypatch):
        calls = self._counting_probe(monkeypatch)
        state = {"gemini_last_probe": datetime.now(timezone.utc).isoformat(),
                 "gemini_last_ok": False, "gemini_last_detail": "429 RESOURCE_EXHAUSTED"}
        ok, detail = hm.maybe_check_gemini(None, state)
        assert calls["n"] == 0 and ok is False
        assert "429" in detail and "cached" in detail


class TestGeminiIsNonCritical:
    def test_gemini_not_in_critical_set(self):
        assert "gemini" not in hm.CRITICAL
        assert hm.CRITICAL == {"heartbeat", "alpaca", "database"}

    def test_only_gemini_failing_is_exit_zero(self):
        # the exit rule main() uses: 0 unless a CRITICAL check is failing
        now = {"gemini"}
        assert (0 if not (now & hm.CRITICAL) else 1) == 0

    def test_a_critical_failing_is_exit_one(self):
        now = {"gemini", "heartbeat"}
        assert (0 if not (now & hm.CRITICAL) else 1) == 1
