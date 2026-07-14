# Trading Bot — Project Context

Discord-signal-following trading bot. Polls analyst channels (Zabes, Grizzlies, Waxui, ECS, Nando, Enhanced Market), parses calls into structured signals (entries/trims/exits) via per-analyst regex parsers with Gemini LLM fallback, executes via Alpaca. Kalshi prediction-market bot exists in parallel (currently deferred).

## Current Strategy (as of July 2026)

**Simplest analyst first.** After ~a year of stalled progress trying to build the hardest parser first (Grizzlies), the approach is reversed: get ONE easy-to-parse analyst fully live on paper trading, validate, go live small ($10/trade), then add harder analysts one at a time funded by real profits.

**The active plan lives in `RESTART_PLAN.md` — read it at the start of every session.** `ROADMAP.md` is the older phase history; its priority order is superseded by the restart plan, but its Gate criteria still apply.

## Working Rules

- **Safety first.** This bot spends real money when live. Bias toward correctness and verification over speed. Never weaken exit verification, duplicate-position guards, or timeout protection.
- **Paper before live, always.** Gate 1 = 5 clean market days, 5+ complete trade lifecycles, zero parser errors, zero silent failures.
- Run tests with: `./venv/bin/python -m pytest tests/ -v`
- Position reconciliation tool: `./venv/bin/python sync_positions.py` (dry run; `--fix` to resolve)
- Discord auth uses a user token in `.env` (ToS-fragile; bot-account migration deferred). Verify the token with a real API call before trusting it.

## Deferred (do not work on unless asked)

Kalshi, SPX/Tastytrade, Nando parser, Grizzlies parser, Discord OAuth2 migration.
