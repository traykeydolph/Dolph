# Analyst Onboarding Runbook

> The repeatable process for adding a new analyst. Analysts are **not** separate
> codebases or branches — every analyst shares one execution engine, router, DB,
> alerting, and all four safety blockers. An analyst is just a **parser + tests +
> a config toggle**. This doc is the durable home for "the steps," so onboarding
> stays consistent as the core evolves. See `CLAUDE.md` for strategy and
> `LIVE_SAFETY.md` for the pre-live gate.

## The growth model (read once)

```
main  = trunk: shared core + safety + ALL validated parsers
  └── feat/<analyst>-parser   short-lived: build → shadow-validate → merge → delete
runtime toggle (.env):  ENABLED_ANALYSTS / SHADOW_ANALYSTS  ← who is live/observing
```

- **One process, one DB, one checkout.** The bot runs whatever is on the VPS
  checkout. You never run "two analyst branches" — you run `main` and pick who is
  active via config.
- **Branches are for *building* an analyst, not for *running* one.** Cut a
  short-lived branch off `main`, merge it when the parser is validated, delete it.
- **Config is the on/off axis, not git.** `SHADOW_ANALYSTS` = observe-only,
  `ENABLED_ANALYSTS` = execute. Shadow always wins if an analyst is in both.
- **One analyst at a time.** Do not start analyst N+1 until analyst N is at its
  target stage (live or deliberately parked). This is the core strategy in
  `CLAUDE.md` — simplest analyst first, funded by real profits.

---

## Stage 0 — Decide it's worth it

- Confirm the analyst posts a **parseable, repeatable** format (regex-friendly
  entries/trims/exits). If the format is prose-heavy chaos, it is a Gemini-fallback
  problem and belongs later, not now.
- Pull their history for a corpus: `pull_history.py` (see MEMORY — full per-analyst
  Discord history is available as training/validation data).
- Get the analyst's **Discord channel ID**.

## Stage 1 — Build the parser (on a branch)

```bash
git checkout main && git pull
git checkout -b feat/<analyst>-parser
```

Create `parsers/<analyst>.py`, modeled on `parsers/eva.py` (simplest) or
`parsers/ace.py`. A parser is a class exposing **static methods** the router calls:

| Method | Purpose |
|---|---|
| `is_noise(content) -> bool` | pre-filter: chat/GIFs/commentary that must never parse |
| `extract_details(content, message_id, timestamp) -> ParsedSignal \| None` | the regex extractor (Tier-2) |
| `enhance_prompt(base_prompt, message) -> str` | *(optional)* analyst-specific hints for the Gemini fallback |
| `post_process_signal(signal, message) -> ParsedSignal` | *(optional)* fix expiry years / trim fractions from Gemini output |

`ParsedSignal` fields are in `parsers/base.py`. Actions are `SignalAction`
(entry/trim/exit/stop_hit/info). Keep the safety instincts that already paid off:
- **Guard sole-position fallbacks by ticker** (Ace's `KNOWN_TICKERS` — never close a
  contract the message didn't name; see `parsers/ace.py`).
- A tickerless close resolving to the wrong open position is a money bug, not a
  parse nicety.

## Stage 2 — Wire it in (4 files)

1. **`config.py`** — add `discord_channel_<analyst>` field and append the pair to
   `_discord_channel_analyst_pairs`.
2. **`signal_router.py`** — `import` the parser, then register it in **both** dicts
   (the `is_noise` map ~L278 and the `extract_details` map ~L318) *and* the noise
   short-circuit checks (~L87–99).
3. **`parsers/gemini_parser.py`** — add the `enhance_prompt` branch in `_build_prompt`
   and the `post_process_signal` branch in `_extract_signal` (only if you wrote those
   optional hooks).
4. **`.env`** — add `DISCORD_CHANNEL_<ANALYST>=<channel_id>`. **Do not enable yet.**

## Stage 3 — Test to the bar the others cleared

Create `tests/test_<analyst>.py` (unit/edge cases) and
`tests/test_<analyst>_corpus.py` (replay the real history corpus). The bar set by
Eva/Ace:

- **100% corpus coverage** — every real historical message classifies correctly
  (entry/trim/exit/noise), zero wrong parses.
- Tests are **hermetic** — never hit live Gemini (the autouse fixture in
  `tests/conftest.py` stubs it; keep it that way).

```bash
./venv/bin/python -m pytest tests/ -q     # whole suite stays green
```

## Stage 4 — Shadow (observe-only, real market data, ZERO orders)

Add the analyst to `SHADOW_ANALYSTS` in `.env` (**not** `ENABLED_ANALYSTS`). Shadow:
- polls, parses, alerts, and logs to `logs/<analyst>_shadow.jsonl`
- **never** calls the execution path, **never** places an order (the `would_execute`
  verdict tells you what *would* have happened — see `shadow_logger.py`)

Run for enough sessions to trust it. Each day the **eod-review** skill's shadow
section (and `shadow_pnl.py`, if you build the price reconstruction) gives you the
tier breakdown (regex-extract / would-Gemini / noise), executable-vs-index split,
and a **hard confirmation of 0 orders**. Promotion criteria out of shadow:
- zero wrong parses across the shadow window
- the "would-execute" signals look correct by eye against the channel
- you understand the analyst's index/unexecutable share (e.g. Waxui's SPX)

## Stage 5 — Paper (execute on Alpaca paper)

Move the analyst from `SHADOW_ANALYSTS` → `ENABLED_ANALYSTS`. Now it executes on
the paper account (`PA3OQ9Y8K2X7`). Watch the **full chain** for the first live
signal: parse → Alpaca paper order → Telegram alert → DB row → journal/Sheets.

The daily gate applies unchanged:
- **eod-review** skill after each session (honest CLEAN/DIRTY, lifecycle count).
- `daily_verify.py` — objective CLEAN/DIRTY + streak; a wrong parse or a booked≠real
  fill trips it. The new analyst rides the same **30-day clean streak** clock.
- `reconcile_fills.py` — booked P&L must equal the real Alpaca fill (Blocker 2).

**Catch the first complete lifecycle** (entry → trim/exit → closed DB row) — that's
the milestone, same as the pending first-Ace lifecycle.

## Stage 6 — Merge

Once the parser is validated in shadow (and ideally has a clean paper lifecycle):

```bash
./venv/bin/python -m pytest tests/ -q     # green
git checkout main && git merge --no-ff feat/<analyst>-parser   # or squash
git branch -d feat/<analyst>-parser
```

The analyst now lives on the trunk; its live/shadow status is controlled entirely
by `.env` per environment. Delete the branch — its job is done.

## Stage 7 — Go live (real money) — only per LIVE_SAFETY

Live trading is gated on **all four blockers 🟢 VERIFIED** *and* Gate 1 passed
(the 30-day clean streak on fixed code, 5+ complete lifecycles, zero parser errors,
zero silent failures). Start small ($10/trade), funded by real profits, one analyst
at a time. See `LIVE_SAFETY.md` — none of those blockers may be waived.

---

## Anti-patterns (things we talked ourselves out of)

- ❌ **A long-lived branch per analyst.** They share the core — every future core
  fix would need merging into N branches (merge hell), and you can only run one
  checkout anyway. Isolation belongs in **runtime config**, not git topology.
- ❌ **Cloning a branch as an analyst "template."** A clone freezes a stale code
  snapshot. The reusable thing is *this runbook*, not a point-in-time tree.
- ❌ **Enabling execution before shadow.** Shadow is cheap insurance against a
  parser bug spending money. Always shadow first.
- ❌ **Starting analyst N+1 before N is done.** One at a time.
