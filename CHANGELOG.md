# Changelog

Running tracking log of changes to the trading bot — newest first. Each entry
records **what** changed, **why**, and **how it was verified**. Operational
day-by-day results live in `CURRENT_STATUS.md`; pre-live safety gates in
`LIVE_SAFETY.md`. This file tracks *code/behavior* changes.

---

## 2026-08-04 — Waxui parser hardening (5 validation flukes)

Fixes from the parse-validation pass (all Waxui/shadow — no Eva/Ace or streak
impact). Each was a real misclassification caught before Waxui goes live:

1. **`Closed {TICKER}` → exit** regardless of trailing text. The exit regex
   required the word "here", so `Closed SPX @B/E` / `Closed SPY @2.50` (break-even
   / no-"here" closes) slipped to a flaky Gemini and were logged `info` — a missed
   exit, including on executable tickers. Now matches an uppercase ticker after
   "Closed" ("Closed out"/"Closed the…" still excluded).
2. **`Holding N/N` → trim.** `HOLDING_RE` hardcoded `1/2`; `Holding 2/2!` (still
   holding all) was misread as a full exit. Generalized to any `\d+/\d+`.
3. **`Reduced risk @X` → trim** (was on the noise list). It's a partial de-risk
   SELL; new `REDUCE_RE` captures the price.
4. **Trail/stop commentary → regex noise** (was falling to Gemini). `Using /ES
   7630 as trail` now caught by regex (`as trail` / `trailing stop`), saving a
   scarce free-tier call.
5. **`Added to {TICKER}` → info** (was `entry` with strike=None). As an entry it
   could open a **2nd phantom position** (the duplicate guard can't match a
   strikeless entry). Now non-actionable info; a real size-increase comes with
   quantity-aware trading (post-streak).

Tests: `tests/test_waxui_hardening.py` (20). Suite 479 green.

---

## 2026-08-04 — Obsidian journal path portable (VPS)

Trade-journal writes hardcoded the dev Mac's Obsidian vault path, so on the VPS
every trade logged `[ERROR] Failed to write trade journal` (harmless — the DB and
Sheets are the source of truth — but noisy on every trade).

- `integrations/obsidian_journal.py`: `VAULT_BASE` is now
  `os.getenv("OBSIDIAN_JOURNAL_PATH", <mac default>)`; if the path isn't writable
  the module **disables journaling gracefully** (one warning, then the three write
  functions no-op) instead of erroring per trade. On the box `OBSIDIAN_JOURNAL_PATH`
  points at a gitignored `trade_journal/` dir so journaling works there too.

---

## 2026-08-03 — Fix fill-ladder double-fill race (🚨 critical)

Found in the EOD: Eva's SPY 720P exit **filled twice** (rung 1 limit @1.68 AND the
capped rung @1.60 both filled) — we held 1, sold 2, and went **short 1** on Alpaca
while the DB booked a clean close. Root cause: the ladder `_cancel_quietly`'d the
previous rung and **resubmitted the full quantity** without checking whether the
cancel actually beat the fill. On a marketable limit the fill can win that race.
(The race pre-existed in the old 15s→market path; the 3s escalation window made it
frequent enough to hit.)

- `alpaca_client._cancel_and_settle`: cancels a rung, then reads its **terminal
  fill** (qty, avg price). Both option ladders now accumulate fills and escalate
  only the **unfilled remainder** — a rung that filled during the race is detected
  and the ladder stops instead of double-submitting. The exit's final market
  backstop is never cancelled (guaranteed fill).
- Erroneous short flattened on paper (bounded limit buy queued for the open).
- Tests: `tests/test_blocker34_fill_ladder.py` +2 (`TestNoDoubleFillOnRace`) — a
  rung that fills during the cancel race must not escalate; total filled == held.
  Suite 459 green.

This is a hard go-live gate: a double-fill flips you into an unintended opposite
position (worse than the market-overpay it replaced).

---

## 2026-08-03 — Health monitor: stop burning Gemini quota

The 5-min health timer live-probed Gemini every run (~288 calls/day), which by
itself exhausted the **free-tier daily quota** — so the monitor was *causing* the
`429` it then reported, and (because a down fallback returned exit 1) marking the
systemd unit "failed" every 5 minutes. This also starved the quota that **Waxui's
shadow validation** needs (≈38%, up to ~56% on busy days, of Waxui's actionable
messages route through Gemini).

- `health_monitor.py`: Gemini is now **probed at most hourly** (`maybe_check_gemini`
  caches the last result in `.health_state.json` between probes; manual `--dry-run`
  still probes live). It is also **non-critical** — a down Gemini still *alerts* on
  state change but no longer drives the exit code (`CRITICAL = {heartbeat, alpaca,
  database}`), so the systemd unit stops flapping "failed".
- Tests: `tests/test_health_monitor_throttle.py` (8) — throttle, cache carry-forward,
  force-on-dry-run, and the critical-only exit rule. Suite 457 green.

*Interim measure* until Gemini API billing is enabled (the free tier only 2x'd via
Google One AI Plus is a **consumer** plan and does not lift the API quota).

---

## 2026-08-02 — Clean rebuild on Hetzner VPS (24/7 cutover)

Replaced OpenClaw's stale pm2/root deploy on `5.78.207.151` with a clean systemd
build: non-root `trader` user, `git clone` on branch `fix/missed-exit-on-restart`,
proper venv, `.env` + `config/` creds + Signal Library + `trading_bot.db` (carrying
the open SPY 720P + poll cursors) copied over. Installed the 5 systemd units
(bot + health timer + verify timer), all boot-persistent. Health check 7-green,
Discord token valid (the old box's was dead → 401s for ~3 months), positions in
sync 1/1, single instance confirmed, pm2 removed.

**Operational change:** the box is now the single live bot; the Mac is dev-only
(ONE-BOT rule — two pollers on one paper account = duplicate orders). Ops are
systemd now, not pm2 (`systemctl {start,stop,restart} trading-bot`,
`journalctl -u trading-bot -f`). The three deploy-hardening commits below made
the clean rebuild possible.

---

## 2026-08-01 — Signal Library path is now portable (VPS parity)

The Obsidian Signal Library (Tier 1/2 matching source) loaded from a **hardcoded
Mac path** (`/Users/tray/Documents/…/Signal Library`). On the VPS that path doesn't
exist, so the library loaded **empty** and the box silently parsed **regex-only** —
a different classifier than we validated (caught by a Waxui replay test that
returned `exit` instead of `trim` on the box).

- `parsers/obsidian_matcher.py`: `SIGNAL_LIBRARY_PATH` is now
  `os.getenv("SIGNAL_LIBRARY_PATH", <mac default>)` — local dev unchanged; any
  non-Mac host sets the env var. The 284K library is deployed to the box and
  `SIGNAL_LIBRARY_PATH` points at it in the box `.env`.

*Why:* the VPS must parse **identically** to what we test locally — an empty
library is a silent behavior change. *Verified:* full suite 449 green on the box
after the library was deployed and the env var set.

*Follow-up to consider:* vendor the library into the repo so deploys carry it
automatically (trade-off: a committed copy can drift from the Obsidian master).

---

## 2026-08-01 — requirements.txt fix (clean-deploy correctness)

Found while rebuilding the Hetzner VPS from a clean `git clone`: `requirements.txt`
did not match what the code actually imports, so a fresh install was broken.

- Dropped `alpaca-py` (never imported). The code uses **`alpaca-trade-api==3.2.0`**
  (`import alpaca_trade_api`), which is the deprecated Alpaca SDK with stale pins
  (`websockets<11`, `urllib3<2`) that make the pip resolver fail against the modern
  stack. It's now installed as a documented **`--no-deps`** second step (we only use
  its REST API, not streaming) — see the note in `requirements.txt` and `DEPLOY.md §3`.
- Added **`telethon`** (inbound Telegram) and **`coinbase-advanced-py`** (crypto) —
  both imported at runtime, both were missing.
- Added **`google-genai`** (the new Gemini SDK the code prefers; `google-generativeai`
  stays as the documented fallback).

*Why:* the local dev venv worked only because these were installed ad-hoc; a
reproducible deploy (VPS, CI, DEPLOY.md) needs them declared. *Verified:* clean
`pip install -r requirements.txt` + the `--no-deps` alpaca step + `import main` OK
on the Hetzner box (Python 3.12), new Gemini SDK loaded.

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
