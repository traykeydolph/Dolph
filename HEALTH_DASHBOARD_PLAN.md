# Trading Bot — Health Dashboard & Surgical Instrumentation Plan

**Created:** 2026-06-04
**Goal:** Goal #3 of the year — make the bot fully functional: *get in on every trade, out on every TP.*
**Core problem:** No visibility. The bot records *that* a signal died but rarely *why*, so we can't act.
**Strategy:** Instrument the pipeline to record every signal's fate + reason → backfill history by replay → build a clickable Streamlit dashboard with a per-signal pressure-test loop.

**Decisions locked (2026-06-04):**
- Stack: **Streamlit** (Python-native, clickable, can call parsers directly for live re-test).
- Sequence: **Instrument first, then dashboard.**
- Scope: **Entry + exit/TP funnel together** in v1.

---

## Key reframe: the exit/TP side needs NO price history in v1
Analysts post their own exits ("STC ... @ 2.04", "SOLD AMZN 195P"). So "out on every TP" = *did we catch and act on every analyst exit/trim signal?* — the same funnel applied to exit-type messages. Price-based autonomous TP detection is a SEPARATE later effort (Phase 5, optional).

---

## The funnel + failure taxonomy (the schema everything hangs on)

**Entry path:**
`seen → classified(entry) → parsed → routed (not stale / not dup) → risk-passed → entry filled`

**Exit path (exit-type signals: STC, trim, close, "out here", "SOLD"):**
`seen → classified(exit) → parsed → matched to open position → trim/close executed`

**Drop reason codes (one per signal that doesn't complete its path):**
| code | meaning |
|---|---|
| `parse_miss` | required fields couldn't be extracted from the message |
| `unmatched_exit` | exit signal but no open position to act on |
| `stale` | signal too old when processed |
| `duplicate` | already seen / acted on |
| `risk_block` | blocked by risk rules (drawdown, size, etc.) |
| `exec_fail` | broker rejected / API error on entry |
| `exit_fail` | broker rejected / error on trim/close |
| `noise` | not a tradeable signal (commentary, etc.) |

**Capture rate (the headline metric):** completed paths ÷ tradeable signals, per analyst, entry and exit separately.

---

## Phase 1 — Map the real signal path (investigation, ~first session)
Before touching schema, trace exactly where rows get written and where reasons are lost. Files to walk:
`discord_poller.py` / `telegram_poller.py` → `classify_signals.py` → `signal_router.py` → `parsers/*` → `execution/*` → `storage/database.py`.

Output: a one-page map of "at stage X, row written to table Y, reason discarded here." Confirm:
- Do parse-misses ever reach the `trades` table, or only `message_log`? (488 trades incl. skipped/failed suggests most do — verify.)
- Why is `message_log.parsed_as` empty on 955 rows? Find the write path that's missing.
- How are exit signals currently classified vs entry? (`classify_signals.py`)

## Phase 2 — Schema + instrumentation (surgical edits)
Minimal additions (final shape TBD by Phase 1 — likely columns on `trades` + reliable `message_log.parsed_as`, or a dedicated `signal_audit` table if one-row-per-signal is cleaner):
- `signal_type` — entry / exit / update / noise
- `funnel_stage_reached` — last stage completed
- `drop_reason` — code from taxonomy (nullable)
Wire these at each decision point in `signal_router.py` + `execution/`. Every signal exits the pipeline with a recorded fate.

## Phase 3 — Backfill history by replay
Run all historical `raw_message`s (488 trades + message_log corpus) through the instrumented pipeline in **dry-run replay mode** to populate funnel_stage + drop_reason retroactively. Dashboard gets real history immediately. (Builds on existing `tests/test_replay.py` + `eval/`.)

## Phase 4 — Streamlit dashboard (`dashboard.py`)
Sections:
1. **Overview** — full funnel (entry + exit), aggregate capture rate, realized P&L, count of "missing" trades.
2. **Per-analyst scorecard** — table: signals | parse% | entry-capture% | exit-capture% | realized P&L | missing count. Rows clickable.
3. **Drill-down** — for a selected analyst + drop_reason: table of the exact raw messages that fell out, their parsed output (or error), and reason. *This is "which trades are missing," concretely.*
4. **Pressure-test panel** — pick a fallen-out signal → show stored `raw_message` → **"Re-run parser"** button calls the *current* `parsers/` code live → shows new parsed output + diff vs expected → mark fixed/green. The fix→re-run→confirm loop, built in.

**The surgical workflow this enables:**
`open dashboard → spot worst leak (e.g. waxui parse_miss) → drill into the exact failing messages → edit the parser → hit Re-run on those exact signals → confirm green → re-check capture rate.`

## Sequencing method — one analyst at a time, like SaaS integrations (decided 2026-06-04)
Treat each analyst as an "integration" certified to 100%, then move to the next. The rig (instrumentation + dashboard) is built ONCE; then walked across analysts one at a time.

**Certify on HISTORY first, top up with live signals.** Replay each analyst's stored `raw_message` corpus (138 grizzlies, 118 waxui, 102 enhanced_market, etc.) through the parser — fix → re-run → confirm green — instead of waiting for new signals to trickle in. Rationale: (1) instant feedback vs weeks of live accumulation; (2) every certified message becomes a **regression test** so parser fixes can't silently re-break a solved format; (3) per-analyst formats are stable, so historical messages are representative of future ones. New live signals are the ongoing top-up that catches long-tail new formats → added to the corpus as new tests → re-certify. NOT new-signals-only.

**Definition of "100% effective" per analyst (the done bar):** parse accuracy ~100% on the historical corpus AND the entry+exit funnel actually captures (executes) the tradeable ones AND it's covered by locked regression tests. Parsing alone ≠ done; full funnel green = done.

**Ordering — DECIDED 2026-06-04: waxui first.** Cashflow-led: certify the only profitable analyst (+$2,196), fix the SPX-conversion leak (21 silently-dropped signals), get real money flowing, and let that fund the work on the other 6. waxui → then the rest, ranked by P&L potential.

**Corpus note:** The DB only holds *logged* messages (e.g. 138 grizzlies); Trace has **years of message history** per analyst available going back (Discord/Telegram scroll-back / export). So corpus size is NOT a constraint — the bigger historical pull becomes the certification + regression test set per integration. Phase 3 logistics: figure out how to extract the full back-history per analyst (Discord export, etc.) when we start each integration. Caveat: very old messages may use retired formats — weight recent history, but old variants are still useful regression cases.

**7 analysts total.** Certify one at a time. waxui (1st) → 6 remaining by P&L potential.

**Cashflow dependency (flag):** "cashflow as we work the others" assumes the bot is live-trading real capital on certified analysts. Certifying waxui's parsing/funnel is necessary but not sufficient — it then needs to be running live (ties to Trace's plan to fund the bot with saved rent money once it's working). Confirm live-vs-paper status before counting on waxui cashflow.

## Phase 5 — (optional, later) Price-based autonomous TP
Capture price snapshots so the bot can hit TPs even when the analyst doesn't post an exit. Out of scope for v1.

---

## First leak to attack (from 2026-06-04 data)
**waxui** — the only profitable analyst (+$2,196), but only 13 of ~117 signals executed (66 skipped, 21 skipped_spx, 13 failed, 4 exit_failed). Highest-leverage fix in the whole book: stop the leak on the source that already prints. `skipped_spx` (21) suggests the SPX converter path is silently dropping waxui signals — prime first target.
