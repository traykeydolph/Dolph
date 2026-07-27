# Current Status — Trading Bot

*Single orientation doc. Last updated: 2026-07-27 (EOD). Read this + `CLAUDE.md` +
`RESTART_PLAN.md` + `LIVE_SAFETY.md` (pre-live blockers) at the start of any session. Keep the
"Where we are" and "Open items" sections current — this file, not chat history, is the source of
truth for continuity.*

---

## Where we are (TL;DR)
- 07-27 session on branch `fix/missed-exit-on-restart`. Three analysts live: Eva + Ace
  **execute** on Alpaca paper; Waxui **shadow / log-only**. Clean SIGTERM stop 07-27 16:42 CT.
- **Gate 1: 3/5 clean days · 3/5 complete lifecycles · zero parser errors (clock never reset).**
  Counting clean days: 07-22 ✓, 07-24 ✓, **07-27 ✓\*** — **Tray's call 07-27: today COUNTS as a
  success, caveated (✓\*)** for the cursor-persistence regression that fired and needed a
  mid-session code fix (parser + execution ran flawlessly; the one lifecycle verified clean).
  **07-23** (network outage) remains asterisked and undecided. 4 market days elapsed; 2 of 5
  clean days still needed.
- **Complete lifecycles (3):** Eva IWM 300C (07-22, −$17), Eva GOOGL 325C (07-24, **+$22**),
  **Eva USO 100P (closed 07-27, +$80 recorded).** USO: entry 07-23 @ $2.85 → exit 07-27, full
  chain verified in log (parse 0.95 → limit sell $3.77 → 15s timeout → market fallback → fill →
  **post-exit Alpaca verification confirmed closed** → journal → Sheets). Regex-only, no Gemini.
- **No open carried positions** — account is flat (USO was the last one; closed today).
- **🟢 Blocker 1 TRULY fixed today (`9dafff2`, pushed).** The "FIXED 07-24" cursor persistence was
  **silently failing**: `set_cursor` ran on the `to_thread` poll worker but the SQLite conn was
  opened on the main thread → `ProgrammingError` on *every* cursor write (3 `[ERROR]` lines in the
  07:13 pre-fix run). Fix = `check_same_thread=False` + a per-instance RLock on all DB methods
  (serialized mode alone still clashes on shared txn state). Verified live: `poll_cursor` table now
  populates, **zero persist errors** after the 07:20 restart, 24 healthy poll cycles, lifecycle
  executed. New regression test reproduces the bug. This is why 07-27 is asterisked — a real
  regression fired and needed a code fix, though the parser/execution ran flawlessly all day.
- **⚠️ P&L-accuracy finding RECURRED (Blocker 2).** USO exit: Alpaca market order **filled @ $3.70**
  but the bot booked the exit @ **$3.65** (the parsed signal price) → P&L $80 vs a true ~$85. Same
  root cause as 07-24 GOOGL; consistent, known, NOT touched (execution logic frozen in Gate-1).
- **Ace: STILL pending first live signal.** 0 messages *ever* 07-21→07-27 (6 quiet days); cursor
  unchanged (`1527352026764148916`). ~1.8 entries/wk avg → quiet but not a fault. First Ace
  lifecycle = open milestone.
- **Waxui shadow, 0 orders (isolation holds):** 07-27 → 16 real obs (regex-extract 8 /
  would-Gemini 4 / noise-skip 4). Parsed split: 7 executable (all SPY) / 1 SPX index
  (unexecutable). Observed a full SPY cycle (entry→exit→entry→trim×3→exit) + 1 SPX entry.
- **Landed:** PR #1 (`feat/ace-parser-and-waxui-shadow`) open into `main`. Blocker 1 fix
  committed + **pushed** to `fix/missed-exit-on-restart` (`9dafff2`, PR #2). Project skills
  (start/stop/eod) committed.
- **Next:** day 5/5 (the clean-streak gate); decide if the two asterisked days count; catch the
  **first Ace lifecycle** whenever Ace posts; then continue LIVE_SAFETY (Blocker 4 → 2 → 3).

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
./venv/bin/python -m pytest tests/ -q          # expect ~367 passed
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
2. **P&L recorded off signal price, not fill** (07-24 GOOGL: fill $0.79, booked $0.70).
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
