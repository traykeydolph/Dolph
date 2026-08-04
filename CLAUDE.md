# Trading Bot — Project Context

> **Start here:** read `CURRENT_STATUS.md` first — it's the single orientation doc (current
> config, what's built, how to run, what to watch, open decisions). Keep it updated.

Discord-signal-following trading bot. Polls analyst channels (Zabes, Grizzlies, Waxui, ECS, Nando, Enhanced Market), parses calls into structured signals (entries/trims/exits) via per-analyst regex parsers with Gemini LLM fallback, executes via Alpaca. Kalshi prediction-market bot exists in parallel (currently deferred).

## Current Strategy (as of July 2026)

**Simplest analyst first.** After ~a year of stalled progress trying to build the hardest parser first (Grizzlies), the approach is reversed: get ONE easy-to-parse analyst fully live on paper trading, validate, go live small ($10/trade), then add harder analysts one at a time funded by real profits.

**The active plan lives in `RESTART_PLAN.md` — read it at the start of every session.** `ROADMAP.md` is the older phase history; its priority order is superseded by the restart plan, but its Gate criteria still apply.

## Working Rules

- **Safety first.** This bot spends real money when live. Bias toward correctness and verification over speed. Never weaken exit verification, duplicate-position guards, or timeout protection.
- **Paper before live, always.** Gate 1 = 5 clean market days, 5+ complete trade lifecycles, zero parser errors, zero silent failures.
- **Adding an analyst?** Follow `ANALYST_ONBOARDING.md` — the repeatable build→shadow→paper→live process. Analysts are a parser + tests + a config toggle, never a long-lived branch; runtime `ENABLED_ANALYSTS`/`SHADOW_ANALYSTS` is the on/off axis.
- Run tests with: `./venv/bin/python -m pytest tests/ -v`
- Position reconciliation tool: `./venv/bin/python sync_positions.py` (dry run; `--fix` to resolve)
- Discord auth uses a user token in `.env` (ToS-fragile; bot-account migration deferred). Verify the token with a real API call before trusting it.

## Evening 6 Dry-Run Runbook (July 2026)

State as of July 13 evening: Evenings 1-5 complete (see RESTART_PLAN.md). Eva parser at
100% corpus coverage, 197 tests passing, single-analyst gating live, Alpaca paper account
PA3OQ9Y8K2X7 ($250K, clean), DB reconciled to zero open positions, Telegram verified.

To run the dry run (market hours 6:30am-1:00pm PT):
1. Pre-flight: `./venv/bin/python -m pytest tests/ -q` (expect 197 passed) and confirm
   `.env` has `ENABLED_ANALYSTS=eva`
2. Start the bot in the background: `./venv/bin/python main.py` (startup health check
   runs first and refuses to start on critical failures — read its output)
3. Confirm from logs: watching exactly ONE channel (Eva's: 1035245170582626334)
4. When an Eva signal arrives, verify the full chain: parse -> Alpaca paper order ->
   Telegram alert -> position row in trading_bot.db
5. If no signal all day, replay a historical entry from data/history_20260713/eva.json
   with a fresh timestamp through the router (ask Tray first)
6. Report everything observed to Tray; kill the bot at market close unless told otherwise.
   Stop it via the pidfile — `kill -TERM $(cat trading_bot.pid)` — which triggers graceful
   shutdown and removes the pidfile. Do NOT use `pgrep -f "venv/bin/python main.py"`: the
   process shows in `ps` as `.../Python main.py`, so that pattern matches nothing and the
   bot keeps running. Confirm the stop with `cat trading_bot.pid` (should be gone) and the
   "Bot stopped." log line.

## Deferred (do not work on unless asked)

Kalshi, SPX/Tastytrade, Nando parser, Grizzlies parser, Discord OAuth2 migration.
