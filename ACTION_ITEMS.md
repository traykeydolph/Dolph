# Action Items — Bot Upgrades from Validation

Findings from the **2026-08-04 parse-validation pass** (Waxui + Enhanced Market) and
this session's diagnostics. Grouped by phase.

**Parser fixes below are Waxui-only (shadow)** unless noted — they do **not** touch
Eva/Ace execution or the 30-day streak, so they're safe to do mid-streak.

Legend: 🔧 near-term · 🚀 go-live/scale-up · 👤 needs Tray · 👀 watch · ✅ done this session

---

## 1. Waxui parser hardening 🔧 (shadow-only, one change + a regression test per case)

| # | Fluke (real message) | Current (wrong) | Fix |
|---|---|---|---|
| 1 | `Closed SPX @B/E`, `Closed SPY @2.50` | exit regex requires "here" → misses → Gemini → **info (missed exit, incl. executable tickers)** | broaden to `Closed {TICKER}` → **exit** regardless of trailing text (here / @price / @B/E / none) |
| 2 | `SPXXX … Holding 2/2!` | `HOLDING_RE` hardcodes `1/2` → **trim read as exit** | generalize to any `\d+/\d+` |
| 3 | `Reduced risk @X` (×3) | on the noise list → **noise** | it's a partial sell → **trim** (capture the price); remove from noise |
| 4 | `Using /ES 7630 as trail` | not in noise patterns → **Gemini → info** (wastes a scarce call) | broaden trail/stop noise pattern → caught by **regex** |
| 5 | `Added to SPY @X, New Avg Y` (×2) | parses `entry, strike=None` → could open a **2nd phantom position** | explicit **add/scale-in** handling; **never open a 2nd position** (skip at 1 contract; add size when quantity-aware) — ⚠️ SAFETY |

**Decision locked (trim behavior at 1 contract):** any trim = full close. Use **"A"
(exit at the first trim) now**; move to laddered/quantity-aware once multi-contract.
So #2/#3 correct the *classification*; execution stays "first trim closes" for now.

## 2. Reliability & data

- 👤 **Gemini API paid tier.** Free tier is throttled to **20 req/day** and is
  **non-deterministic on exits**. Waxui is ~38% Gemini-dependent and going live, so a
  reliable paid Gemini is effectively required. Enable billing on the API key's Google
  Cloud project (the Google One consumer plan does **not** count).
- 🔧 **Logging gap.** `message_log` stores only the `<@&…>` ping/content — **not the
  embed body** — for non-signal messages, so the validation sheet shows bare pings for
  commentary. Log the full post-embed content for **every** message so future data is
  complete (enrich historical rows from Discord at the weekly refresh).
- 👀 **Google Sheets "update open positions" failed once (08-04).** Watch; fix if it recurs.

## 3. Go-live / scale-up features 🚀 (post-streak)

- **Quantity-aware trim/exit (laddered).** Mirror the analyst's exact contract counts;
  distinguish all-out = exit vs partial = trim. Best performer in the shadow P&L on
  Waxui SPY: **laddered +$134** > first-trim (A) +$111 > hold-to-close (B) +$47.
  **Enhanced Market is the ideal pilot** — Alertsify states `Remaining: N` explicitly.
- **Mirror analyst stops (B/E).** Bot-side synthetic now (B/E doable; `/ES`-trail needs
  futures data → likely skip), or **native broker stops** once on an options-capable
  broker. Closes the Blocker-1 "no options auto-stop" gap.
- **Live-broker migration.** Choose a broker with **(a) native option stops** AND
  **(b) index/SPX options** — IBKR / Tastytrade / Schwab. Unlocks Waxui **SPX live**
  (not shadow) *and* native B/E stops in one move.
- **Enhanced Market onboarding.** Current `EnhancedMarketParser` is broken (0% action
  via `extract_embed_signals`; not wired into the regex tier). A clean regex hits **100%**
  on the Alertsify format (validated vs a 100-row labeled tab, offline). Rewrite + config
  toggle when we onboard him — re-subscribe needed only to poll him live.

## 4. Ongoing / process

- **Weekly validation-sheet refresh** (Fridays, **append-only — never wipe tabs**): add
  new rows, enrich commentary rows with embed text, and surface the A-vs-laddered P&L
  gap as more data accrues.

---

## ✅ Already fixed this session (context)

- Fill-ladder **double-fill race** (`_cancel_and_settle`) — validated live 08-04.
- Health-monitor **Gemini quota throttle** + made non-critical.
- **Obsidian journal path** portability (VPS).
- **requirements.txt** (alpaca-trade-api / telethon / coinbase / google-genai).
- **Signal Library path** portability (VPS).
