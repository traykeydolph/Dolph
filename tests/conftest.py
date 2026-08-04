"""Shared test fixtures.

The suite must never touch the live Gemini API — it has to be deterministic,
free, and runnable offline / in CI without a valid key. Before a real
GEMINI_API_KEY was in .env this happened by accident (every call errored to
None); with a valid key the router's Tier-3 fallback would start making live
calls (slow, non-deterministic, and it broke regex-refusal tests). This fixture
stubs the LLM tier off by default.

Tests that want a specific Gemini verdict override it with their own
`monkeypatch.setattr(router.gemini_parser, "parse", ...)` — an instance
attribute shadows this class-level patch, so those tests keep working.
"""

import pytest


@pytest.fixture(autouse=True)
def _no_live_gemini(monkeypatch):
    from parsers.gemini_parser import GeminiParser
    monkeypatch.setattr(GeminiParser, "parse", lambda self, *a, **k: None)
