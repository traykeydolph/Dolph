# Current Status — Trading Bot

*Single orientation doc. Last updated: 2026-07-22 (EOD). Read this + `CLAUDE.md` +
`RESTART_PLAN.md` at the start of any session. Keep the "Where we are" and "Open items"
sections current — this file, not chat history, is the source of truth for continuity.*

---

## Where we are (TL;DR)
- **First real paper session ran 2026-07-22** (up 07:10 CT → clean shutdown). Three analysts
  live: Eva + Ace **execute** on Alpaca paper; Waxui runs **shadow / log-only** (never orders).
- **Gate 1: Day 1/5 clean · 1/5 lifecycles. Streak ADVANCES (not reset)** — zero parser errors,
  zero silent failures.
- **Eva IWM 300C 7/24 — full lifecycle verified.** Entry 08:39 → exit 09:56 CT, P&L **−$17**
  (a clean mechanical loss). Whole chain clean: regex parse (no Gemini), limit fill, no
  15s→market escalation, post-exit Alpaca verification, DB row, journal, Sheets. The
  position-aware **safety guard fired correctly** on 2 phantom-T trims (Eva trimmed a T call the
  bot never held → parsed fine, execution declined, logged `WARNING`, no order).
- **Ace: still pending its first live signal.** Silent today (0 messages); Ace posts ~1.8
  entries/wk so this is expected, not a fault. First Ace lifecycle remains the open milestone.
- **Waxui shadow: 17 real observations, 0 orders.** Tiers regex 6 / would-hit-Gemini 7 /
  noise 4; of the 6 parsed, **4 were SPX/index (unexecutable)** — reconfirms shadow-only.
- Account flat (0 in DB / 0 on Alpaca).
- **Committed** to branch `feat/ace-parser-and-waxui-shadow`. Push + PR pending a one-time
  `gh auth login` (gh not yet authenticated on this machine).
- **Next:** run day 2/5; catch the **first Ace lifecycle** end-to-end whenever Ace next posts.

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

## Open items / decisions (none block paper)
1. **LIVE-GATE:** `execute_entry_order()` escalates limit → **market** after 15s. On fast/0DTE
   options this bleeds edge; paper hides it (idealized fills). Before real money, switch to a
   **capped marketable-limit** (pay up to X% over signal, else skip).
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
