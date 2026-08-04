"""Hardening fixes from the 07-30 DIRTY day.

That day a bare Discord role-ping ("<@&697950067285295115>", no text) fell through
to the Gemini tier and a Google 504 tripped the daily gate. The message was
harmless noise, but it exposed two gaps, fixed here:

  1. Mention/emoji-only messages must be dropped as noise BEFORE any LLM call.
  2. Gemini must retry ONCE on a transient 5xx before giving up.

Run: ./venv/bin/python -m pytest tests/test_noise_and_gemini_retry.py -v
"""

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from signal_router import _content_is_noise_only
from parsers.gemini_parser import GeminiParser


# ── Fix 1: mention/emoji-only noise guard ────────────────────────────────────

class TestNoiseOnlyGuard:
    def test_bare_role_ping_is_noise(self):
        # the exact 07-30 message
        assert _content_is_noise_only("<@&697950067285295115>") is True

    @pytest.mark.parametrize("text", [
        "",
        "   ",
        "<@123>",
        "<@!456>",
        "<#789>",
        "<@123> <@!456> @here",
        "@everyone",
        "<:rocket:12345>",
        "<a:spin:999>",
        "🚀🚀",
        "<@&1> 🚀",
    ])
    def test_things_with_no_real_text_are_noise(self, text):
        assert _content_is_noise_only(text) is True

    @pytest.mark.parametrize("text", [
        "BTO SPY 720P @ 3.20",
        "<@&697950067285295115>  Closed SPX here",   # ping + real signal
        "<@&1> IREN 34.5p entry",
        "Trim half here",
        "720",                                        # a lone number is still text
    ])
    def test_real_signals_survive(self, text):
        assert _content_is_noise_only(text) is False


# ── Fix 2: Gemini retries once on a transient 5xx ────────────────────────────

class _Resp:
    def __init__(self, text):
        self.text = text


class _Transient(Exception):
    def __init__(self):
        super().__init__("504 Gateway Timeout")
        self.code = 504


class _Permanent(Exception):
    def __init__(self):
        super().__init__("400 INVALID_ARGUMENT")
        self.code = 400


def _parser():
    p = object.__new__(GeminiParser)
    p._timeout_s = 10
    return p


class TestTransientDetection:
    def test_5xx_and_timeouts_are_transient(self):
        assert GeminiParser._is_transient(_Transient()) is True
        assert GeminiParser._is_transient(Exception("Deadline exceeded")) is True
        assert GeminiParser._is_transient(Exception("503 UNAVAILABLE")) is True
        assert GeminiParser._is_transient(Exception("model is overloaded")) is True

    def test_4xx_is_not_transient(self):
        assert GeminiParser._is_transient(_Permanent()) is False


class TestGenerateRetry:
    def test_retries_once_then_succeeds(self, monkeypatch):
        import parsers.gemini_parser as gp
        monkeypatch.setattr(gp, "_genai_version", "new")
        monkeypatch.setattr(gp.time, "sleep", lambda *_: None)   # no real wait
        p = _parser()

        calls = {"n": 0}

        def flaky(model, contents):
            calls["n"] += 1
            if calls["n"] == 1:
                raise _Transient()          # first attempt: 504
            return _Resp("OK")              # retry succeeds

        p._client = SimpleNamespace(models=SimpleNamespace(generate_content=flaky))
        assert p._generate_text("prompt", "msg1") == "OK"
        assert calls["n"] == 2              # exactly one retry

    def test_gives_up_after_one_retry(self, monkeypatch):
        import parsers.gemini_parser as gp
        monkeypatch.setattr(gp, "_genai_version", "new")
        monkeypatch.setattr(gp.time, "sleep", lambda *_: None)
        p = _parser()
        calls = {"n": 0}

        def always_504(model, contents):
            calls["n"] += 1
            raise _Transient()

        p._client = SimpleNamespace(models=SimpleNamespace(generate_content=always_504))
        with pytest.raises(_Transient):
            p._generate_text("prompt", "msg2")
        assert calls["n"] == 2              # original + one retry, no more

    def test_does_not_retry_a_4xx(self, monkeypatch):
        import parsers.gemini_parser as gp
        monkeypatch.setattr(gp, "_genai_version", "new")
        monkeypatch.setattr(gp.time, "sleep", lambda *_: None)
        p = _parser()
        calls = {"n": 0}

        def bad_request(model, contents):
            calls["n"] += 1
            raise _Permanent()

        p._client = SimpleNamespace(models=SimpleNamespace(generate_content=bad_request))
        with pytest.raises(_Permanent):
            p._generate_text("prompt", "msg3")
        assert calls["n"] == 1              # no retry on a permanent error
