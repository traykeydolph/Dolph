# Trading Bot — Master Roadmap

*Created: March 21, 2026*
*Author: Claude Code (with Tray)*

---

## Phase 0: Critical Fixes (Before Any Live Trading)

These bugs will silently lose you money. Fix before turning the bot on.

### 0.1 — Zabes Stock/Option Misclassification ✅ DONE
- **Bug:** "Starting to grab 100 NFLX" (no "shares" keyword) → parsed as option → bought 9x calls → $10K unrealized to $0
- **Root cause:** `STOCK_ENTRY_RE` only matches `buy|buying|bought|grabbing` + `shares`. Misses "grab", "starting to buy NFLX" (no "shares"), "long term hold"
- **Fix:** Expand stock detection patterns, add "long term" and "hold" as stock indicators, add negative lookahead for option patterns (strike+expiry = option regardless)

### 0.2 — Exit Verification (Silent Exit Bug Prevention) ✅ ALREADY IN PLACE
- Was already fixed in a previous session — `_handle_exit()` has fill verification, force-close retry, and post-exit position confirmation (main.py lines 722-815)

### 0.3 — Async Timeout Protection ✅ DONE
- Wrapped ALL `asyncio.to_thread()` calls in `asyncio.wait_for()` with appropriate timeouts
- Discord poll: 60s, signal routing: 30s, entries: 60s, exits: 60s, trims: 60s, startup: 30s
- Timeout alerts sent via Telegram so you know when something hangs

### 0.4 — Discord 403 Circuit Breaker ✅ DONE
- After 5 consecutive 403s on a channel, it's disabled automatically
- Disabled channels retry every 5 minutes
- Analyst name logged with circuit breaker events for easy identification
- `get_disabled_channels()` method for monitoring

### 0.5 — Zero Price Protection ✅ ALREADY IN PLACE
- `alpaca_client.py` already validates `mid_price <= 0` before quantity calculation (lines 142-144, 204-206)
- Quote failures return None, handled by caller

### 0.6 — Invalid Expiry Dates ✅ DONE
- Added `datetime.strptime()` validation in both `zabes.py` (2 locations) and `waxui.py` (2 locations)
- Invalid dates (Feb 30, Apr 31) now return None instead of creating bad option symbols

---

## Phase 1: Hardening (Before Graduating Past $10/trade)

### 1.1 — Duplicate Position Guard Enhancement ✅ DONE
- Now checks strike+expiry, not just ticker+analyst
- Same ticker with different strikes (SPY 580C vs SPY 590C) correctly allowed
- Same exact contract still blocked

### 1.2 — PDT Check Correction ✅ DONE
- Changed threshold from >= 3 to >= 4 (actual PDT rule: 4+ day trades in 5 business days)

### 1.3 — Database Transactions ✅ DONE
- Added `trim_and_log()` method: atomic position update + trade log in one transaction
- Rolls back both on failure (prevents partial state)

### 1.4 — SPX/SPY Ratio
- **Current:** Hardcoded 10.0 with TODO to fetch live
- **Fix:** Implement live fetch from market data, cache 1 minute during market hours
- **Status:** Deferred — SPX disabled until Tastytrade integration anyway

### 1.5 — Log Rotation ✅ DONE
- Switched to `RotatingFileHandler`: 10MB max per file, 5 backups kept

### 1.6 — Stale Signal Guard Configuration ✅ DONE
- Now configurable via `STALE_SIGNAL_SECONDS` env var
- Default increased from 300s (5 min) to 600s (10 min)
- Prevents valid signals from being skipped after brief downtime

---

## Phase 2: Parser Accuracy (The Signal Library Grind)

### 2.1 — Pull Fresh Messages Since Last Validation
- Have Dolph pull all new messages from each analyst channel
- Run through Tier 1/2 matchers, flag non-matches
- Focus validation on the gaps, not re-validating known patterns

### 2.2 — Grizzlies Parser Tightening ✅ DONE
- Added `extract_details()` regex extractor (was missing — only had post_process)
- Profit update patterns ("up X%", "BANG!", "TP hit", "still printing") → correctly classified as TRIM
- Exit language patterns ("All TPs hit", "closed my long", "stopped out") → EXIT
- Option entry format: supports both "TICKER STRIKEc MM/DD" and "TICKER MM/DD STRIKEc"
- Crypto entry extraction with entries/targets/stop prices
- Date validation on option expiries
- Registered in signal_router regex_extractors → Tier 1/2 matches now get free regex extraction
- 11/11 test cases passing

### 2.3 — Waxui Ticker Decoder
- "SPXXX" → "SPX" works, but what about new obfuscations?
- ✅ encoding resilience
- "Holding" vs "held" vs "still in" normalization
- **Status:** Low priority — SPX disabled until Tastytrade, and existing decoder handles known patterns

### 2.4 — Nando Parser Completion
- Only 20/100 rows validated
- Voice chat → needs Whisper transcription for completeness
- **Recommendation:** Deprioritize. Don't go live with Nando until validation is complete.

### 2.5 — ECS Crypto Ticker Gaps ✅ NON-ISSUE
- All 9 "missing" tickers (POPCAT, MELANIA, AWE, CHEEMS, BSV, ZEC, POL, BCH, XNO) already in `data/crypto_tickers.json`
- Parser doc was outdated

---

## Phase 3: Testing Infrastructure

### 3.1 — Unit Tests for Every Parser ✅ DONE
- 72 test cases across Zabes, Grizzlies, ECS
- Tests use REAL validated signals from Tray's audit (not synthetic data)
- Coverage: entries, trims, exits, noise, stock/option classification, date validation
- All 72 passing
- Run: `./venv/bin/python -m pytest tests/test_parsers.py -v`

### 3.2 — Replay Testing ✅ DONE
- 104 validated signals fed through full SignalRouter pipeline
- Tests cover Zabes (39), Grizzlies (36), Waxui (15), ECS (3) + meta test
- Added "Tier 2.5" regex-first fallback in signal_router.py — tries regex extraction
  BEFORE falling to Gemini when no library match exists. This means the bot can
  classify signals correctly even when Gemini is offline or API key is dead.
- **100% match rate** — all 104 signals classify correctly
- Run: `./venv/bin/python -m pytest tests/test_replay.py -v`
- Run all: `./venv/bin/python -m pytest tests/ -v` (176 total tests)

### 3.3 — Paper Trading Validation Period
- 5 consecutive market days with zero parser errors
- 5+ complete trade lifecycles (entry → trim/exit)
- Zero silent exit failures
- This is Gate 1 from the graduation plan

---

## Phase 4: Graduation (Scaling Up)

### 4.1 — Gate System (Revived)
- **Gate 1 ($10/trade):** 5 days stability, 5+ lifecycles, 7 days no bugs
- **Gate 2 ($50/trade):** 20+ completed trades, per-analyst win rate tracked
- **Gate 3 ($100/trade):** 30+ trades, positive P&L, best analyst identified
- **Gate 4 (Prop Firm):** 2-3 months track record, apply to TopStep/CFT

### 4.2 — Per-Analyst Confidence Weighting
- Track win rate per analyst over time
- Scale position size by analyst accuracy (Enhanced Market gets more than Nando)
- Auto-demote analysts below 60% accuracy

### 4.3 — Tastytrade Integration
- Required for SPX options (currently disabled)
- Unlocks Waxui's SPX signals

### 4.4 — Discord Bot Account Migration
- Current: user token (TOS violation, gets revoked)
- Target: proper bot account with OAuth2
- Requires: admin access in each Discord server, or the analyst server operators adding the bot
- This solves the recurring token death problem permanently

---

## Phase 5: Kalshi Optimization

### 5.1 — Timeframe Mismatch Resolution
- Grizzlies scalps (7-min holds) but Kalshi bets run multi-day
- Options: (a) use only signals where Grizzlies explicitly states multi-day thesis, (b) use shorter-duration Kalshi markets, (c) track directional accuracy on 24hr+ windows separately

### 5.2 — Multi-Analyst Kalshi Signals
- Once timeframe is resolved, evaluate other analysts for directional accuracy
- ECS crypto direction could map to Kalshi crypto markets

---

## Priority Order

## Infrastructure Improvements (Done)

### Startup Health Check ✅ DONE
- Validates Discord token, Alpaca connection, DB, Telegram, Signal Library on every startup
- Detects position mismatches (DB vs broker) before entering poll loop
- Critical failures → bot refuses to start + Telegram alert
- Non-critical warnings → bot starts but alerts you

### Position Sync Tool ✅ DONE
- `sync_positions.py` — standalone tool to reconcile DB vs Alpaca
- Detects ghost positions (in DB but not on broker)
- Detects orphaned positions (on broker but not in DB)
- Detects quantity mismatches
- Dry run by default, `--fix` flag to resolve
- Run: `./venv/bin/python sync_positions.py` or `--fix`

### Gemini SDK Migration ✅ DONE
- Supports both `google.genai` (new) and `google.generativeai` (deprecated)
- Auto-detects which SDK is installed, prefers new
- Graceful degradation: if no API key or no SDK, Tier 3 is silently disabled
- Bot works correctly without Gemini (Tier 1/2/2.5 handle most signals)

### Tier 2.5 Regex Fallback ✅ DONE
- Added regex extraction BEFORE Gemini in signal_router.py
- Messages that don't match the library still get tried by analyst-specific regex
- Only falls to Gemini (Tier 3) when both library AND regex fail
- Means bot classifies correctly even with dead Gemini API key

```
NOW:        ✅ Phase 0-3 COMPLETE — bot ready for paper testing
NEXT:       Phase 2.1 (pull fresh messages for validation delta)
THEN:       Phase 3.3 (paper trading validation — 5 market days)
MONTH 2+:   Phase 4 (graduation gates)
ONGOING:    Phase 5 (Kalshi), Phase 4.4 (Discord migration)
```

---

*This roadmap lives at `~/Desktop/Trading/ROADMAP.md` and should be updated as items are completed.*
