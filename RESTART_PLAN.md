# Trading Bot Restart Plan — Simplest Analyst First

*Created: July 13, 2026*
*Strategy: Reverse the original approach. Instead of building the hardest parser first (Grizzlies), get ONE easy-to-parse analyst fully live, make money, then add harder analysts incrementally — funded by the bot's own track record.*

---

## Locked Decisions (July 13, 2026)

- **Asset scope:** ALL asset types — bot trades every signal from the chosen analyst (options, stocks, crypto)
- **Real capital at go-live:** $1K–$5K
- **PDT:** SEC eliminated the $25K PDT minimum June 4, 2026 (replaced by intraday margin system), but brokers have until Oct 20, 2027 to implement. **Verify Alpaca's current policy before live trading; keep the bot's PDT check until confirmed.**
- **Hosting:** Cloud VPS (always-on). Set up before/during paper validation.
- **Alerts:** Maximum verbosity — every signal, parse decision, order, and error to Telegram. Dial down after trust is built.
- **Paper first**, single analyst chosen by data (parse-ability ranking), evenings pace.

---

## Phase A: Revival & Recon (Week 1, ~2 evenings)

### Evening 1 — Systems Check
- [x] Launch Claude Code from `~/Desktop/trading` (file access)
- [x] Run existing startup health check
- [x] Verify Discord token with a real API call (viewing Discord in-app ≠ valid `.env` token)
- [x] Verify Alpaca paper API keys
- [x] Verify Telegram alerts
- [x] Run full test suite (176 tests) — see what rotted over a year
- **Deliverable:** Honest status report of what still works

### Evening 2 — Data Pull
- [x] Pull fresh message history from EVERY analyst channel (`pull_signals.py`), as far back as Discord allows
- **Deliverable:** Raw message archive per analyst — the corpus for everything that follows

---

## Phase B: Analyst Selection (Week 2, ~2 evenings)

### Evening 3 — Parse-ability Analysis
- [x] Rank each analyst on:
  - Format consistency
  - Signal density vs. noise
  - Ambiguity rate
  - Existing parser performance against fresh messages
- [x] Rough-estimate signal frequency and outcomes where messages contain results ("TP hit", "+40%")
- **Deliverable:** Ranked report with a recommendation

### Evening 4 — Commit & Simplify
- [x] Pick the starting analyst — **EVA** (99.5% deterministic)
- [x] Put bot in single-analyst mode (all others disabled in config)
- [x] Strip surface area down to just that pipeline

---

## Phase C: Hardening (Week 3, ~2 evenings)

### Evening 5 — Parser to 100%
- [x] Test suite for chosen analyst against the FRESH corpus (not just year-old validated set)
- [x] Every miss gets a fix or an explicit "skip this pattern" decision

### Evening 6 — Full Loop Dry Run
- [ ] Live poll → parse → paper trade on Alpaca → trim/exit → Telegram alert
- [ ] One complete lifecycle observed end to end

---

## Phase D: Paper Validation (Weeks 4–5, mostly passive)

Gate 1 criteria (from original roadmap):
- [ ] 5 clean market days
- [ ] 5+ complete trade lifecycles
- [ ] Zero parser errors
- [ ] Zero silent failures

Evenings = log review only. **Any parser error resets the clock.**

---

## Phase E: Go-Live (Week 6)

- [ ] Review paper P&L and parse accuracy
- [ ] If green: fund small, go live at $10/trade
- [ ] Ladder up via original gate system ($10 → $50 → $100 → prop firm)
- [ ] Add harder analysts ONE at a time, only after profit

---

## Explicitly Deferred

| Item | Revisit when |
|------|--------------|
| Kalshi bot | After first analyst is live and profitable |
| SPX / Tastytrade integration | Needed only for Waxui's SPX signals |
| Discord bot-account migration (OAuth2) | Only if the user token dies again |
| Nando parser | Indefinitely — only 20% validated, needs Whisper for voice |
| Grizzlies parser | After simpler analysts are live and earning |
