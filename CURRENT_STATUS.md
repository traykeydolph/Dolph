# Current Status — Trading Bot

*Single orientation doc. Last updated: 2026-07-28 (EOD). Read this + `CLAUDE.md` +
`RESTART_PLAN.md` + `LIVE_SAFETY.md` (pre-live blockers) at the start of any session. Keep the
"Where we are" and "Open items" sections current — this file, not chat history, is the source of
truth for continuity.*

---

## Where we are (TL;DR)
- 07-28 session on branch `fix/missed-exit-on-restart`. Three analysts live: Eva + Ace
  **execute** on Alpaca paper; Waxui **shadow / log-only**. Clean SIGTERM stop 07-28 19:09 CT.
- **Gate 1: 5/5 clean market days ✅ · 4/5 lifecycles · zero parser errors (clock never reset).**
  Clean days: 07-22 ✓, **07-23 ✓ (Tray's call 07-28 — counts moving forward)**, 07-24 ✓,
  07-27 ✓\* (caveated), 07-28 ✓ (pristine). **Clean-day criterion is MET;** need 1 more complete
  lifecycle. BUT the real go-live gate is track-record *integrity*, not the day count — see
  Blocker 2 (booked P&L is sign-flipping). Do not read "5/5 clean" as "ready."
- **07-28 was a clean, busy day:** Eva ran **3 ideas** — CSCO (full lifecycle), OKLO + RKLB (both
  still open). Zero parser errors, zero silent failures. Also **validated Blocker 1 live across a
  real restart**: the bot **resumed from persisted cursors** (no re-seed), zero persist errors —
  the market-hours-restart check that was pending is now done. Action-aware stale guard also fired
  (skipped a 14 h-old message, no open position).
- **Complete lifecycles (4):** IWM 300C (07-22, real −$17), GOOGL 325C (07-24, real +$31),
  USO 100P (07-27, real +$85), **CSCO 110P (07-28 — booked +$15 but REAL −$15, a loss).**
- **⚠️⚠️ Blocker 2 is worse than "optimistic" — it FLIPS SIGNS.** CSCO 07-28: entry filled $1.15,
  exit market-fallback filled **$1.00** (real −$15), but booked at Eva's signal price **$1.30**
  (+$15). A losing trade recorded as a winner. Reconciled against Alpaca: **all-time real +$198 vs
  booked +$233** (Δ −$35). The booked track record is not trustworthy trade-by-trade — this is now
  the top pre-live fix. (Blocker 3's 15s→market escalation caused the bad CSCO fill.)
- **Open carried positions (2):** Eva **OKLO 43C** (opened 07-28 @ $0.68) and **RKLB 70C**
  (opened 07-28 @ $0.72), both exp 07-31. Account NOT flat into 07-29.
- **🟢 Blocker 1 fixed + live-verified** (`9dafff2`, pushed). `check_same_thread=False` + per-instance
  RLock on all DB methods; `poll_cursor` persists and reloads on restart. Confirmed across the
  07-28 restart (resumed, not re-seeded).
- **🆕 Dashboard tooling built (07-27, read-only, UNCOMMITTED):** `daily_report.py` (self-contained
  HTML → `reports/latest.html`; Daily / All-time / **Calendar** tabs), `reconcile_fills.py` (real
  Alpaca fills vs booked — how CSCO was caught), `shadow_pnl.py` (Waxui hypothetical P&L: first-trim
  / laddered / hold-to-exit / peak, SPY market-verified, SPX unverified). Touches no exec logic.
- **⚠️ Gemini fallback is DOWN — invalid API key (found 07-28).** `GEMINI_API_KEY` → `400
  API_KEY_INVALID`. No realized impact yet: Eva/Ace are 100% regex-parsed (every signal logs "NO
  Gemini needed"), so nothing has actually fallen to Gemini. But the LLM safety net is gone — a
  future regex miss would now **silently drop** (parse throws → caught → returns None → treated as
  noise). Also means "would-hit-Gemini" shadow counts are **unverifiable** until a fresh key is set.
  Today's 5 unparsed Waxui msgs, checked by eye: 3 genuine noise (/ES commentary, "nothing today",
  a GIF); 2 "Day Trade idea" watchlist posts (CRWV, FIG) — **NOT entries** (Waxui says "will alert
  entry" later), but entry-shaped (ticker + "Love the 07/31 70Cs") → a false-entry risk IF Gemini
  ever fires on them. **Action: set a valid `GEMINI_API_KEY`.**
- **Ace: STILL pending first live signal.** 0 messages *ever* 07-21→07-28 (7 quiet days); cursor
  unchanged (`1527352026764148916`). ~1.8 entries/wk → quiet, not a fault. First Ace lifecycle = milestone.
- **Waxui shadow, 0 orders (isolation holds):** 07-28 → 21 real obs (regex 10 / would-Gemini 5 /
  noise 6). Parsed split: 3 executable (SPY) / **7 SPX index** (unexecutable) — SPX-heavy day,
  reconfirms shadow-only. Hypothetical P&L (6 ideas to date): first-trim +$111 / laddered +$134 /
  hold-to-exit +$47 / peak +$501 — SPY needs trim discipline to be profitable.
- **Landed:** PR #1 (`feat/ace-parser-and-waxui-shadow`) open into `main`. Blocker 1 fix pushed to
  `fix/missed-exit-on-restart` (`9dafff2`, PR #2). Project skills (start/stop/eod) committed.
- **🎯 Plan (Tray, 07-28): 30-day clean streak on FIXED code.** Re-baselined (the 5 Gate-1 days were
  on old wrong-P&L code). Sequence: **#1 Blocker 2 🟢 DONE** → **#2 VPS kit 🟢 BUILT** (deploy/,
  health_monitor.py; Tray provisioning the box) → **#3 daily verification gate 🟢 DONE**
  (`daily_verify.py`: refreshes reconcile+shadow_pnl, checks parser-errors/exec-failures/
  position-sync/pnl-integrity, CLEAN/DIRTY verdict + streak N/30 to Telegram, Alpaca-calendar
  market days only) → **#4 Blockers 3 & 4 (next).** Discipline: NO new analysts / NO Waxui
  execution during the streak; Waxui accrues an as-if-live record via shadow_pnl for the day-30
  all-3 production call. **The verify gate flags 07-28 DIRTY** (CSCO Δ−$30, old-code booking) — so
  the streak correctly starts at 0 and only counts clean, fixed-code days.
- **Gemini key fixed (07-28)** — valid key in .env; health probe passes. Also added
  `tests/conftest.py` stubbing the live Gemini API off in the suite (a valid key made tests hit the
  real API — 14s + a flaky failure; now deterministic/offline again).
- **Next:** manage OKLO/RKLB exits; stand up the VPS (#2); build the daily verification gate (#3);
  then Blockers 3 & 4. Catch the **first Ace lifecycle** whenever Ace posts.

## Config (live)
- `.env`: `ENABLED_ANALYSTS=eva,ace`, `SHADOW_ANALYSTS=waxui`, `CONTRACTS_ACE=1`
- Channels (guild 697936741117460640):
  - eva `1035245170582626334` → **EXECUTE**
  - ace `1478050123786485831` → **EXECUTE**
  - waxui `1347238168109387857` → **SHADOW** (no orders)
- Alpaca paper account `PA3OQ9Y8K2X7` (~$250K).
- Scheduled task `trading-bot-paper-start`: weekday ~6:37am CT auto-launch. **Only fires while
  the Claude app is open**; may hit the `~/Desktop/trading` file-access wall — if it does, it's
  built to stop and report. Fallback = manual launch (below).

## Run / stop
```bash
# launch (or use "Run now" on the scheduled task)
cd ~/Desktop/trading && ./venv/bin/python main.py
# pre-flight
./venv/bin/python -m pytest tests/ -q          # expect ~381 passed
# stop (from ~/Desktop/trading; wait for "Bot stopped.")
kill -TERM $(cat trading_bot.pid)
```
Startup must show the health check passing (6 items) and routing: `eva → execute`,
`ace → execute`, `waxui → SHADOW`.

## What to watch today (first Ace session)
- **First Ace signal end-to-end** — the lifecycle proof the dry-run couldn't give.
- **`[SHADOW]` Waxui alerts** — log only, never an order.
- **Any parser error on Eva/Ace resets the Gate-1 clock** → bring it to the next session.

## Gate 1 (paper validation)
5 clean market days · 5+ complete trade lifecycles · zero parser errors · zero silent failures.
**Any parser error resets the clock.**

## Why Ace is analyst #2 (bake-off, 2026-07-21)
Fresh 1,000-msg pulls of Waxui/Ace/Luigi through the deterministic tiers. Parse-ability ranked
Waxui > Ace > Luigi, but **Ace chosen**: cleanest greenfield format, all Alpaca-executable equity
options. Waxui has the best raw numbers but **doesn't convert on Alpaca** (SPX index options
unexecutable; 0DTE latency hostile to a poller) → shadow now. Luigi is complete-lifecycle but
prose-heavy (46.6% ambiguity) → analyst #3.

## What was built (2026-07-21 night)
- `parsers/ace.py` — deterministic entries (100% on 383-msg corpus), prose exits via a bounded
  verb table, **position-aware exit resolution** (`resolve_sole_position`, ON by default, wired
  through the router). Refuses ambiguous/mismatched exits (won't close the wrong contract).
- `signal_router.py` — `_run_regex_extractor()` + `_open_tickers()` (DB lookup for Ace's
  tickerless exits); `classify_shadow()` (mirrors tiers, stops before Gemini).
- `shadow_logger.py` — writes `logs/waxui_shadow.jsonl` with an is_index/executable flag.
- `main.py` — shadow gate in `_process_message` before dispatch; **proven load-bearing** (7 tests
  fail if removed).
- `config.py` — `shadow_analysts` as a separate axis from `enabled_analysts` (observation wins).
- tests: `test_ace.py`, `test_ace_corpus.py`, `test_shadow_mode.py` — 367 total pass.

## 🔴 PRE-LIVE BLOCKERS → see `LIVE_SAFETY.md`
Four defects that **paper trading hides** and that **must be fixed + verified before real
money** now live in **`LIVE_SAFETY.md`** (the go-live checklist — none may be waived):
1. ~~Missed exit on restart~~ **🟢 FIXED + LIVE-VERIFIED (07-27, `fix/missed-exit-on-restart`,
   PR #2).** Poll cursor persisted (`poll_cursor` table) + reloaded on startup; stale guard
   action-aware. The 07-24 fix had a **cross-thread bug that silently defeated persistence**
   (`set_cursor` on the `to_thread` poll worker vs a main-thread SQLite conn → `ProgrammingError`
   every write); fixed 07-27 (`9dafff2`: `check_same_thread=False` + per-instance RLock on all DB
   methods) and **verified live** — `poll_cursor` populates, zero persist errors, healthy poll
   loop across a full session. Regression test `tests/test_database_threading.py` (reproduces the
   bug + the concurrent-write hazard). Remaining → optional: a true market-hours restart drill +
   the default-stop backstop.
2. ~~P&L recorded off signal price, not fill~~ **🟢 FIXED (07-28).** Root cause: a `try/except/else`
   in _handle_trim/_handle_exit whose `else` ran on SUCCESS and overwrote the real
   `order_result['filled_price']` with `signal.entry_price` — booking every exit at the analyst's
   signal price (07-28 CSCO: real −$15 booked as +$15, a sign flip). The `else` is gone; exits/trims
   now book the actual fill. Regression test `tests/test_blocker2_fill_price.py` (5 tests, proven
   load-bearing). **Forward-looking:** trades made BEFORE the fix stay mis-booked in the DB (that's
   why `reconcile_fills.py` still shows historical deltas); new trades will reconcile to ~zero Δ.
3. **Limit→market escalation after 15s** — bleeds edge on fast fills.
4. **External calls have no timeout** (07-24) — the Gemini fallback blocks on connect with no
   timeout; a network blip can hang message processing. Surfaced as a >2min test-suite hang
   (normally 0.89s) on a live Gemini call stuck in `socket.create_connection`.
Do **not** fix these during Gate-1 (execution logic frozen); `LIVE_SAFETY.md` is the plan for
the paper→live transition.

## Open items / decisions (none block paper)
0b. **Network/DNS resilience (07-23).** ~40-min outage (14:16–14:58 CT): local `gaierror`
   → Discord unreachable, Telegram sends failed. Bot survived (retries + skipped cycles, no
   crash); cursor polling refetches gap messages on recovery **so long as it stays up** (if it
   restarts, see Blocker 1). Fine on a workstation with slow Eva; needs connection hardening +
   alert-retry/queue before a VPS/live 0DTE path.
0c. **Single-contract trim == full close (behavior note, not a bug).** With `CONTRACTS=1` a "trim"
   can only sell the whole 1-lot, so trim and exit collapse: the trim closes the position and the
   later exit is correctly skipped. Expected; documented so it isn't mistaken for a miss.
2. **Double teardown on SIGTERM** — CONFIRMED still present (2026-07-22: "Bot stopped." logged
   twice on clean shutdown). Harmless/idempotent now; fix before unattended VPS.
3. ~~Startup Telegram banner doesn't tag Waxui as `[SHADOW]`.~~ **DONE (2026-07-22):** banner now
   splits `Executing:` vs `Observing (shadow — logged, NO orders):`. Cosmetic only; routing
   unchanged.
4. **Ace expiry-correction in a later message** (2/383): parser holds the first contract. Watch
   via max-verbosity alerts; future cross-message correction handler.
5. **No Ace signal library** in `obsidian_matcher` yet (harmless; regex covers it).
6. **Logging path consolidation:** the live canonical log is `trading_bot.log` (project root,
   `RotatingFileHandler` in `main.py`). The old `logs/bot.log` was orphaned (nothing wrote it
   since Feb 19) and has been **deleted** — there is now ONE log. `.gitignore` covers it.
7. **Timezone convention (audit note):** `trading_bot.log` is in **CT** (local), `trading_bot.db`
   timestamps are **UTC**. E.g. today's Eva entry: log `08:39 CT` = DB `13:39 UTC`. Left as-is
   deliberately — changing timestamp formats mid-validation would split the streak's dataset.
   **DEFERRED:** normalize to one zone (likely UTC everywhere) after Gate 1.

## 3-month goal (~Oct 2026)
All three trading **real money**. Eva + Ace realistic; **Waxui is the long pole** — needs a
second broker (Tastytrade per roadmap, or IBKR) AND a fix for 0DTE fill latency (move that
analyst from periodic polling to near-real-time ingestion). Sequence Eva → Ace → Waxui; start the
broker + latency work in parallel while Eva/Ace validate.

**Income principle:** monetize the bot's **own verified track record** / trade own or prop
capital — do **not** resell the analysts' signals rebranded (misappropriation + regulatory
exposure + it kills the source feeds the bot depends on).

## Workflow going forward
- **One root: `~/Desktop/trading`.** VS Code + the Claude Code extension (files + terminal +
  Claude in one window), or the desktop app opened to this folder. This directory + `CLAUDE.md`
  **is** the project — there's no separate planning thread.
- Keep this file current (state + next action). Start fresh sessions freely; they read the docs
  and resume with zero loss. Don't rely on chat history for continuity.
- User is on **US Central** time.
