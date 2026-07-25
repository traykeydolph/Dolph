# Analyst Parse-ability Ranking — July 13, 2026

**Method:** Pulled fresh history from every analyst channel (11,901 messages total),
ran each corpus through the bot's real deterministic parsing tiers (noise
short-circuit → Obsidian signal library → regex extractors) with NO LLM involved.
"Deterministic %" = share of messages the bot fully classifies today without Gemini.

| Rank | Analyst | Deterministic | Actionable signals/wk | Corpus | Verdict |
|------|---------|--------------|----------------------|--------|---------|
| 🥇 1 | **Eva** | **99.5%** | 13.2 | 2,000 msgs / 6 mo | **Recommended starter** |
| 2 | ECS | 64.6%* | 12.1 | 2,000 msgs / 24 mo | *Entry template unhandled — fix would push ~95%+. Crypto only. |
| 3 | Ace | no parser yet | ~15 (est) | 377 msgs / 4.5 mo | Surprisingly clean format; strong candidate for analyst #2 |
| 4 | Waxui | 70.3% | 32.6 | 2,000 msgs / 6 mo | High volume, decent structure, heavy prose noise |
| 5 | Nando | no parser | low | 1,524 msgs / 35 mo | Structured entries but low activity; stays deprioritized |
| 6 | Grizzlies | 56.5% | 26.8 | 2,000 msgs / 8.5 mo | Messiest, as feared — the old "start here" plan was the hard mode |
| 7 | Zabes | 49.8% | 5.9 | 2,000 msgs / 26 mo | Casual prose ("24$", "boom!"), low volume, hard |
| — | Enhanced Market | n/a | n/a | 0 msgs | Paid subscription intentionally paused until the bot is running — resubscribe to evaluate later |

## Why Eva

- **Signals are bot-posted embeds with a rigid template:** `BTO ONDS 03/2026 12C @ 1.39 (day trade/possible swing), TP: 12, 13, 15 (SL: under 10.x)` / `STC ... (all out)`. Machine-written input = machine-readable input.
- **99.5% of 2,000 real messages** already classify correctly through the free deterministic tiers. Only 10 messages missed, and those are minor template edge cases (e.g. typo dates like `05/15/20026`, share adds "into IRA") — fixable to ~100%.
- **Mixed asset types** (options + shares + IBIT) — fits the "take every signal" mandate.
- **Sane volume** (13 actionable/wk) — enough lifecycles to hit Gate 1 in the 5-day paper window without drowning.
- Existing `eva.py` parser is already the best in the codebase.

## Eva caveats to handle in hardening (Evening 5)

1. Typo dates (`20026` → `2026`) need normalization, not rejection
2. Share-add signals ("Adding shares into IRA") — confirm execution path for equities
3. LEAPS entries (e.g. `01/15/27`) — long-dated options, verify Alpaca option symbol construction
4. The 10 unparseable messages become test cases

## Data

- Raw corpus: `data/history_20260713/<analyst>.json` (pull tool: `pull_history.py`)
- Full stats incl. per-analyst breakdowns: scratchpad `parseability_results.json`
  (regenerate anytime: `analyze_parseability.py` against the corpus)

---

# Addendum — Analyst #2 Bake-off (July 21, 2026)

**Task:** pick the parser to build *behind* Eva. Fresh 1,000-msg pull (383 for Ace —
whole channel) of the three candidates through the same deterministic tiers, NO LLM.
Corpus: `data/history_20260721/{waxui,ace,luigi}.json`. Tool: `analyze_parseability.py`
+ `scratchpad/diag.py` (ambiguous-bucket vocabulary probe).

**Metrics.** "Signals" = entries + trims + exits detected (struct + ambiguous prose).
- *Clean out-of-box* = signals a generic regex fully extracts today.
- *Achievable* = clean + prose signals reachable by a dedicated parser
  (instrument + a bounded action-verb table + price/% marker present).
- *Ambiguity rate* = trade-related messages not machine-clean (would hit Gemini).

| Rank | Analyst | Signals/wk | Clean out-of-box | Achievable w/ parser | Ambiguity | Executable on Alpaca? | Verdict |
|------|---------|-----------|------------------|----------------------|-----------|-----------------------|---------|
| 🥇 1 | **Waxui** | 27 | **93.3%** | 93.3% | 2.5% | **Partial** — SPY ok, SPX (its #2 name, ~148 mentions) blocked | Best numbers, wrong next build (see below) |
| 🥈 2 | **Ace** | 5.8 | 28.6% | **79.5%** | 20.9% | **Yes** — AAPL/AMZN/AMD/GOOGL equity opts | **Recommended #2.** Cleanest greenfield format |
| 3 | **Luigi** (blind) | 12.8 | 3.7% | 65.1% | **46.6%** | **Yes** — IWM/SPY/QQQ/META, no SPX | Richest volume, messiest signal layer — defer to #3 |

Existing Waxui parser on the FRESH pull (real bot code, `is_noise`+`extract_details`):
**65.8% deterministic** (238 noise-skip + 420 regex-extract, 342 → Gemini) — consistent
with the 70.3% from July 13 (format drift + newer window). The generic classifier's
93% overstates Waxui because it counts commentary as clean-noise and is generous on trim
lines the real parser drops; **65.8% is Waxui's ground truth.**

## Recommendation: build **Ace** as analyst #2

- **Parse-ability ranking is Waxui > Ace > Luigi**, but the #1 doesn't convert:
  Waxui is a *hardening* job (not greenfield), it's 0DTE SPX/SPY (fill-speed hostile to
  a poller), and SPX index options aren't executable on Alpaca (Tastytrade is deferred).
  Revisit Waxui when Tastytrade lands.
- **Ace is the cleanest greenfield build.** Entries are textbook and 100% machine-clean
  (`BTO $AAPL 267.5c 03/04 @0.11`); every name is an Alpaca-executable equity option.
  Its exits are prose (no STC) but a *bounded* vocabulary — ✅ / `up X%` / `all out` /
  `out on $TICKER @price` — and under the bot's 1-contract model they collapse to
  "flatten on any trim/exit marker," so you never need to extract fractions. That pushes
  achievable coverage to ~80% with a small parser. Only weakness: ~1.8 entries/wk — but
  low volume *suited* Eva's validation too (enough lifecycles without drowning).
- **Luigi is the tempting trap.** Clean BTO entries and a gift of a format —
  every management msg self-labels its contract (`$TICKER M/DD $STRIKEc - Update:`), so
  position mapping is trivial. But the *action* is prose across a wide vocabulary
  (adding / closing runners / selling 1/2 / "exit at open" / "SL @ entries"), giving the
  highest ambiguity of the three (46.6%) and only 3.7% clean out-of-box. It's the richest
  channel but the least *reliable* to parse deterministically. Good analyst #3 once Ace
  proves the greenfield playbook.

**Luigi (the unknown) result:** promising, not next. Textbook entries + self-labeling
update headers, undone by a prose-dominated management layer.

Data: `scratchpad/parseability_results.json` (regen: `analyze_parseability.py waxui ace luigi`).
