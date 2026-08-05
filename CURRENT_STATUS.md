# Current Status — Trading Bot

*Single orientation doc. Last updated: 2026-08-01 (EOD, multi-day). Read this + `CLAUDE.md` +
`RESTART_PLAN.md` + `LIVE_SAFETY.md` (pre-live blockers) at the start of any session. Keep the
"Where we are" and "Open items" sections current — this file, not chat history, is the source of
truth for continuity.*

---

## Where we are (TL;DR)
- **🗓️ 08-04 (Tue) EOD — ✅ CLEAN · streak 1/30 (first clean day on fully-fixed code, 24/7 VPS).**
  - **🎯 Double-fill fix VALIDATED LIVE:** ORCL entry escalated (rung 1 $2.95 unfilled 3s → capped
    $3.13) and filled **exactly 1 contract @ $3.00 — no double-fill.** `_cancel_and_settle` works;
    Alpaca shows no ORCL orphan. Yesterday's bug can't recur.
  - **SPY short flattened at open** (buy 1 @ $0.89, 13:30 UTC) → orphan gone, account reconciled.
  - **ORCL +$50** lifecycle (entry $3.00 → trim/close $3.50, booked = real, Δ0). **NFLX 75C @ $0.61**
    carried open (DB 1 / Alpaca 1 in sync). ORCL exit correctly **skipped** (already closed). Eva
    100% regex, zero Gemini on execute path.
  - **⚠️ Peripheral errors to fix (don't affect trading; daily_verify correctly ignored):** Obsidian
    journal writes FAIL on the box (Mac path missing — same class as the Signal Library portability
    bug; errors on every trade); one Google-Sheets position-update fail; one overnight Telegram fail.
  - Waxui shadow: 18 obs (5 regex / 6 would-Gemini / 7 noise), 0 orders.
- **🗓️ 08-03 (Mon) EOD — 🚨 DIRTY · streak 0/30, but a critical bug was caught & fixed.**
  - **Headline:** Eva's SPY 720P exit **double-filled** — rung 1 (limit @1.68) AND the capped
    rung (@1.60) both filled → sold 2 holding 1 → **went short 1** while the DB booked a clean
    close. Root cause: the fill ladder cancelled a rung and **resubmitted the full qty** without
    confirming the cancel beat the fill. **🟢 FIXED** (`5e98a2f`): `_cancel_and_settle` reads each
    rung's terminal fill; ladders now escalate only the *unfilled remainder* (tests +2, suite 459).
    Deployed; bot restarted on fixed code.
  - **Erroneous short flattened:** bounded limit buy queued (fills at 08-04 open) → confirm it filled.
  - **The good part:** the box **caught Eva's SPY exit across the Mac→box handoff** (knew it held SPY,
    acted on the close). Blocker-1 *seeing* the exit worked; the bug was in *how* it executed.
  - **DIRTY drivers:** parser-errors (5 — all **Waxui shadow-path** Gemini 429s from the quota
    exhaustion, pre-health-fix) + position-sync/pnl (the double-fill). exec-failures ✅.
  - **Health-monitor Gemini quota fix** landed 08-04 00:33 UTC (throttle + non-critical) — Gemini is
    **reachable again** as of the restart. See [[CHANGELOG]].
- **🟢🖥️ LIVE ON HETZNER 24/7 (08-02 night).** The bot now runs on the VPS
  (`5.78.207.151`, Ubuntu, Python 3.12) under **systemd** as a non-root `trader` user —
  a clean rebuild replacing OpenClaw's stale pm2 setup (which had a **dead Discord token**,
  was inert for ~3 months, and ran pre-blocker code). Health check 7-green, Discord token
  **valid**, SPY 720P handed off (DB 1 / Alpaca 1 in sync), health timer (5-min) + verify
  timer (weekdays 21:30 UTC) enabled, boot-persistent. Single instance confirmed.
- **‼️ ONE-BOT RULE — do NOT run the Mac bot anymore.** The box is the single live bot on
  paper account `PA3OQ9Y8K2X7`. Running the local Mac bot at the same time = two pollers →
  duplicate orders. The Mac is now **dev only**; the box is production.
- **Ops moved to systemd (the pm2/rsync Obsidian cheatsheet is OBSOLETE):**
  - deploy code: `git push` → on box `sudo -u trader git -C /home/trader/trading pull` → `sudo systemctl restart trading-bot`
  - status/logs: `ssh root@5.78.207.151 'journalctl -u trading-bot -f'`
  - start/stop: `sudo systemctl {start,stop,restart} trading-bot`
  - EOD/reconcile/verify now run **on the box** (its DB is the live one; the Mac DB is stale).
- **Deploy hardening committed today** (see `CHANGELOG.md`): requirements.txt fixed for clean
  installs (`--no-deps` alpaca; added telethon/coinbase/google-genai); Signal Library path made
  env-configurable (was a hardcoded Mac path → VPS parsed regex-only); `config/` creds + library
  + DB deployed to the box.

- **08-01 (Sat) multi-day EOD.** The bot ran **continuously 07-30 07:27 → 08-01 12:06 CT**
  (one process, ~2 days — a real Blocker-1 endurance test; cursor persistence + resume held
  across two overnights and several Discord 503s). Branch `fix/missed-exit-on-restart`.
  Eva + Ace execute on Alpaca paper; Waxui shadow.
- **🎯 30-day clean streak: back to 1/30.** 07-29 CLEAN → **07-30 DIRTY** → 07-31 CLEAN.
  The 07-30 DIRTY **reset** the streak; 07-31 is the current run's day 1.
- **🟥 07-30 DIRTY — a Gemini `504 Gateway Timeout` (benign cause, real trip).** An Eva
  message fell through to the LLM tier and Google returned 504 → logged a parser ERROR →
  `daily_verify` correctly flagged DIRTY. **The message was harmless:** its content was a
  bare Discord role-ping `<@&697950067285295115>` (no text) — pure noise, no signal missed,
  code caught it cleanly (no crash/freeze). But it exposes two cheap gaps → see Open items:
  (a) bare `<@&…>`-only messages should be caught by the **noise filter** before ever
  reaching Gemini; (b) Gemini has **no retry** on transient 5xx. **🟢 Both fixed 08-01**
  (global mention/emoji-only noise guard in `signal_router`; Gemini retries once on a
  transient 5xx) — `tests/test_noise_and_gemini_retry.py` (22), suite 449 green. See
  `CHANGELOG.md` (new running tracking log of code changes).
- **🟢 07-31 CLEAN — the star day. FIRST LIVE FIRING of the Blocker-3 fill ladder (#4).**
  Eva **SPY 720P** entry: rung 1 limit @ $3.19 unfilled in 3.0s → **repriced to capped limit
  $3.44 (rung 2)** → filled **$3.25** — bounded escalation, **NO naked market order**. This is
  the live validation of #4 that was pending. Also **IREN 34.5P** full lifecycle: entry $0.32
  → trim/exit $0.48 = **+$16 real** (booked +$16, Δ0 — Blocker 2 holding). Safety guards all
  fired right: NVDA trims skipped (no position), 3rd IREN trim skipped (already closed),
  duplicate SPY entry skipped. **Zero parser errors.**
- **🟢 Teardown fix confirmed live:** the 08-01 shutdown logged a **single** `Bot stopped.`
  (was a double before #4). Idempotent `stop()` working.
- **⚠️ OPEN POSITION carried, bot DOWN:** Eva **SPY 720P (#77)**, 1 qty, entry **$3.25**,
  opened 07-31 12:07 CT, exp 2026-08-21 — **still open** (DB 1 / Alpaca 1, in sync). The bot
  stopped 08-01 12:06 CT when the launching Claude Code background task was torn down
  (SIGTERM → clean stop). **✅ RESOLVED:** the position was handed off to the Hetzner box
  (08-02) which now runs 24/7 and will catch the SPY exit — no Monday manual restart needed
  (and the Mac bot must stay OFF, per the ONE-BOT rule above).
- **07-29 was a clean day, account now flat:** Eva ran **2 ideas** — both carried positions closed.
  **OKLO 43C** trim→full-close (entry $0.68 → fill **$0.22**, real **−$46**) and **RKLB 70C**
  trim→full-close (entry $0.72 → fill **$0.33**, real **−$39**). Total real **−$85** (booked −$85,
  Δ0 — Blocker-2 fix confirmed live). Losses, but correctly parsed, executed, booked. Zero parser
  errors. Both parsed by **regex, conf 0.95, NO Gemini**.
- **⚠️ Blocker 3 fired LIVE (07-29 OKLO):** exit limit @ $0.25 didn't fill in 15s → **escalated to
  market → filled $0.22** (3¢ *through* the limit = ~$3 edge-bleed on 1 contract). Even on paper the
  escalation path is real. Direct evidence for **#4**. (RKLB filled @ $0.33 in ~1s, no escalation.)
- **⚠️ New shutdown defect (07-29):** the double-teardown's 2nd pass tried to send the shutdown
  Telegram alert through an already-closed aiohttp session → `ClientConnectionError: Connector is
  closed.` **Market-hours alerting was fine** (no send failures during the day); only the shutdown
  alert was dropped. Elevates the "harmless double-teardown" to *has a visible consequence* — fold
  a teardown-ordering fix into #4 / VPS phase.
- **Discord 503 (07-29 12:09, 1 cycle):** poller logged it and **skipped the cycle**; cursor not
  advanced → next poll refetched. No message loss. Graceful degradation working as designed.
- **Complete lifecycles (6):** IWM 300C (07-22, real −$17), GOOGL 325C (07-24, real +$31),
  USO 100P (07-27, real +$85), CSCO 110P (07-28, real −$15), **OKLO 43C (07-29, real −$46),
  RKLB 70C (07-29, real −$39).** Gate-1's 5-lifecycle count is met on fixed-code days.
- **⚠️⚠️ Blocker 2 is worse than "optimistic" — it FLIPS SIGNS.** CSCO 07-28: entry filled $1.15,
  exit market-fallback filled **$1.00** (real −$15), but booked at Eva's signal price **$1.30**
  (+$15). A losing trade recorded as a winner. Reconciled against Alpaca: **all-time real +$198 vs
  booked +$233** (Δ −$35). The booked track record is not trustworthy trade-by-trade — this is now
  the top pre-live fix. (Blocker 3's 15s→market escalation caused the bad CSCO fill.)
- **Open positions (1): Eva SPY 720P (#77)** — entry $3.25, opened 07-31, exp 08-21. Carried over the
  weekend with the bot DOWN. Restart before Monday 08-03 open (see TL;DR action).
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
- **Ace: STILL pending first live signal.** 0 messages *ever* 07-21→07-29 (8 quiet days); cursor
  unchanged (`1527352026764148916`). ~1.8 entries/wk → quiet, not a fault. First Ace lifecycle = milestone.
- **Waxui shadow, 0 orders (isolation holds):** 07-29 → 13 real obs (regex-extract 5 / would-Gemini 5
  / noise 3). Parsed split: **5 executable-on-alpaca**, 0 index today. as-if-live laddered all-time
  **+$345.59** (accruing toward the day-30 all-3 production call).
- **Landed:** PR #1 (`feat/ace-parser-and-waxui-shadow`) open into `main`. Blocker 1 fix pushed to
  `fix/missed-exit-on-restart` (`9dafff2`, PR #2). Project skills (start/stop/eod) committed.
- **🎯 Plan (Tray, 07-28): 30-day clean streak on FIXED code.** Re-baselined (the 5 Gate-1 days were
  on old wrong-P&L code). Sequence: **#1 Blocker 2 🟢 DONE** → **#2 VPS kit 🟢 BUILT** (deploy/,
  health_monitor.py; Tray provisioning the box) → **#3 daily verification gate 🟢 DONE**
  (`daily_verify.py`: refreshes reconcile+shadow_pnl, checks parser-errors/exec-failures/
  position-sync/pnl-integrity, CLEAN/DIRTY verdict + streak N/30 to Telegram, Alpaca-calendar
  market days only) → **#4 Blockers 3 & 4 🟢 DONE (07-29).** Discipline: NO new analysts / NO Waxui
  execution during the streak; Waxui accrues an as-if-live record via shadow_pnl for the day-30
  all-3 production call. **The verify gate flags 07-28 DIRTY** (CSCO Δ−$30, old-code booking) — so
  the streak correctly starts at 0 and only counts clean, fixed-code days.
- **🟢 #4 Blockers 3 & 4 fixed (07-29, branch `fix/missed-exit-on-restart`, 427 tests):**
  **Blocker 3** — naked market escalation replaced with a bounded fill ladder on BOTH entry &
  exit paths. Entry: ask → capped `ask+max(5%,$0.03)` → SKIP+alert (never markets). Exit: bid →
  capped `bid−max(5%,$0.03)` → emergency `bid−20%` → true market + loud alert (guaranteed flat —
  Tray's call: an unfilled exit is worse than crossing the spread). Fill detection now polls every
  0.5s / ~3s per rung (was a single 15s wait) → worst case ~6s entry / ~9s exit. Escalated fills
  and skipped entries alert via `main._alert_escalation`. All caps/latency config-driven.
  **Blocker 4** — Gemini call carries a hard 10s timeout (`gemini_timeout_seconds` → genai
  `HttpOptions`), so a connect-hang fails fast to noise instead of freezing the loop. **Bonus:**
  fixed the double-teardown Telegram drop (`stop()` is now idempotent via `self._stopped`) — also
  kills the double `Bot stopped.` log. Tests: `tests/test_blocker34_fill_ladder.py` (11).
  **Still pending → 🟢:** observe a real escalation in a paper session; forced-hang check on Gemini.
- **Gemini key fixed (07-28)** — valid key in .env; health probe passes. Also added
  `tests/conftest.py` stubbing the live Gemini API off in the suite (a valid key made tests hit the
  real API — 14s + a flaky failure; now deterministic/offline again).
- **Next:** all four blockers are now 🟡/🟢 — remaining pre-live items are the two live-verify
  checks (observe a real fill escalation in paper; forced-hang check on Gemini) and Blocker 1's
  live restart-during-outage + default-stop backstop. Stand up the VPS (#2) when the box is ready.
  Keep banking clean days (**2/30 next**). Catch the **first Ace lifecycle** whenever Ace posts.

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
