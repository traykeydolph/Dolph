# Changelog

Running tracking log of changes to the trading bot — newest first. Each entry
records **what** changed, **why**, and **how it was verified**. Operational
day-by-day results live in `CURRENT_STATUS.md`; pre-live safety gates in
`LIVE_SAFETY.md`. This file tracks *code/behavior* changes.

---

## 2026-08-01 — requirements.txt fix (clean-deploy correctness)

Found while rebuilding the Hetzner VPS from a clean `git clone`: `requirements.txt`
did not match what the code actually imports, so a fresh install was broken.

- Replaced `alpaca-py` → **`alpaca-trade-api==3.2.0`** (code imports `alpaca_trade_api`,
  not the `alpaca` module; local venv had the right one, requirements didn't).
- Added **`telethon`** (inbound Telegram client) and **`coinbase-advanced-py`**
  (crypto client) — both imported at runtime, both were missing.
- Added **`google-genai`** (the new Gemini SDK the code prefers; `google-generativeai`
  stays as the documented fallback).

*Why:* the local dev venv worked only because these were installed ad-hoc; a
reproducible deploy (VPS, CI, DEPLOY.md) needs them declared. *Verified:* fresh
`pip install -r requirements.txt` + `import main` clean on the box (Python 3.12).

---

## 2026-08-01 — Noise + Gemini-retry hardening

Prompted by the **07-30 DIRTY day**: a bare Discord role-ping
(`<@&697950067285295115>`, no text) fell through to the Gemini tier and a Google
**504 Gateway Timeout** logged a parser ERROR, resetting the clean-day streak.
The message was harmless noise, but it exposed two real gaps.

- **Mention/emoji-only messages are now dropped as noise before any LLM call.**
  New global guard in `signal_router.route_message` (`_content_is_noise_only`):
  after stripping Discord mentions, custom emoji, and `@everyone/@here`, if no
  alphanumeric text remains, the message short-circuits as noise. Conservative —
  anything with real text (including a ping *plus* a signal) still parses.
- **Gemini now retries once on a transient 5xx before giving up.** New
  `GeminiParser._generate_text` wraps the SDK call and retries a single time on a
  5xx / timeout / "unavailable" / "overloaded" error (`_is_transient`), with the
  Blocker-4 per-call timeout still bounding each attempt (worst case: two bounded
  waits, never a hang). Permanent 4xx errors are not retried.

*Why:* stop benign noise from ever reaching the LLM, and make the LLM tier
resilient to a single transient Google hiccup — both reduce false DIRTY days
without weakening any safety check.

*Verified:* `tests/test_noise_and_gemini_retry.py` (22 tests) — the exact 07-30
message classifies as noise, real signals survive, retry fires once on 504 and
not at all on 400. Full suite green (see below). No execution/order logic touched.

---

## 2026-07-29 — Blockers 3 & 4 + shutdown teardown (`58bf09f`)

- **Blocker 3:** replaced the naked limit→market escalation with a bounded fill
  ladder on both entry and exit paths. Entry: ask → capped `ask+max(5%,$0.03)` →
  skip+alert (never markets). Exit: bid → capped → emergency `bid−20%` → true
  market + loud alert (guaranteed flat). Fill detection polls every 0.5s / ~3s
  per rung (was one 15s wait). *First live firing 07-31 on SPY 720P — worked.*
- **Blocker 4:** hard 10s timeout on the Gemini client so a connect-hang fails
  fast instead of freezing message processing.
- **Teardown:** `stop()` is idempotent (`self._stopped`), fixing the
  double-teardown that dropped the shutdown Telegram alert and double-logged
  "Bot stopped." *Confirmed single clean stop 08-01.*

## 2026-07-28 — Blocker 2: book the real fill (`d77df8f`)

Exits/trims now record the actual Alpaca fill price and P&L, not the analyst's
signal price (which had sign-flipped a −$15 loss into a booked +$15 win).
*Verified live daily via `reconcile_fills.py` — Δ0 on every fixed-code trade.*

## 2026-07-27 — Blocker 1: missed exit on restart (`7c839d4`, `9dafff2`)

Poll cursor persists to the DB and resumes on restart (no reseed-to-latest);
action-aware stale guard runs stale exits/trims for open positions while still
skipping stale entries. Cross-thread SQLite fixed with `check_same_thread=False`
+ per-instance `RLock`. *Endurance-tested across the 07-30→08-01 continuous run.*

## Earlier

VPS deploy kit + health monitor + daily verification gate (`1430f57`,
`a013816`); Ace parser + Waxui shadow mode (`c207792`); read-only reporting
tools — dashboard, fill reconciliation, Waxui shadow P&L (`0667775`).
