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
