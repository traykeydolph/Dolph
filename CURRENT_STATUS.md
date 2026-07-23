# Current Status — Trading Bot

*Single orientation doc. Last updated: 2026-07-22 (morning). Read this + `CLAUDE.md` +
`RESTART_PLAN.md` at the start of any session. Keep the "Where we are" and "Open items"
sections current — this file, not chat history, is the source of truth for continuity.*

---

## Where we are (TL;DR)
- Bot was "down all week" — root cause was a **stale `trading_bot.pid`** (dead process blocking
  restart), not a bug. Cleared. 367 tests pass.
- **Three analysts wired and validated (2026-07-21 night):** Eva + Ace **execute** on Alpaca
  paper; Waxui runs **shadow / log-only** (parses, alerts `[SHADOW]`, logs — never orders).
- Account flat (0 in DB / 0 on Alpaca). Nothing committed to git yet.
- **Next:** run the first real paper session (premarket start), and watch the **first Ace
  lifecycle** end-to-end (parse → paper order → Telegram → position row).

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
2. **Double teardown on SIGTERM** — harmless/idempotent now; fix before unattended VPS.
3. **Startup Telegram banner** doesn't tag Waxui as `[SHADOW]` — add so execute/observe is
   visible at a glance ("Monitoring: Ace, Eva, Waxui / Ready to copy-trade" is misleading).
4. **Ace expiry-correction in a later message** (2/383): parser holds the first contract. Watch
   via max-verbosity alerts; future cross-message correction handler.
5. **No Ace signal library** in `obsidian_matcher` yet (harmless; regex covers it).
6. Two `replay_`-prefixed rows may sit in `waxui_shadow.jsonl` from verification — clear for a
   clean dataset.

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
