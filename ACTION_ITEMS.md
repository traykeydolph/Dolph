# Action Items — Bot Upgrades from Validation

Findings from the **2026-08-04 parse-validation pass** (Waxui + Enhanced Market) and
this session's diagnostics. Grouped by phase.

**Parser fixes below are Waxui-only (shadow)** unless noted — they do **not** touch
Eva/Ace execution or the 30-day streak, so they're safe to do mid-streak.

Legend: 🔧 near-term · 🚀 go-live/scale-up · 👤 needs Tray · 👀 watch · ✅ done this session

---

## 1. Waxui parser hardening ✅ DONE (2026-08-04, `tests/test_waxui_hardening.py`, suite 479)

All 5 shipped in one Waxui change with a regression test per case (shadow-only):

| # | Fluke (real message) | Was | Now |
|---|---|---|---|
| 1 ✅ | `Closed SPX @B/E`, `Closed SPY @2.50` | needed "here" → missed → Gemini → info | `Closed {TICKER}` → **exit** (any trailing text) |
| 2 ✅ | `SPXXX … Holding 2/2!` | `1/2` hardcoded → trim read as exit | any `\d+/\d+` → **trim** |
| 3 ✅ | `Reduced risk @X` (×3) | on noise list → noise | `REDUCE_RE` → **trim** (captures price) |
| 4 ✅ | `Using /ES 7630 as trail` | fell to Gemini → info | `as trail` / `trailing stop` → **regex noise** |
| 5 ✅ | `Added to SPY @X` (×2) | `entry, strike=None` → phantom 2nd position | **info** (non-actionable; real add = quantity-aware phase) |

**Decision locked (trim behavior at 1 contract):** any trim = full close. Use **"A"
(exit at the first trim) now**; move to laddered/quantity-aware once multi-contract.
#2/#3 corrected the *classification*; execution stays "first trim closes" for now.
*Follow-up:* tickerless trims (e.g. `Reduced risk @X`) default the ticker via
`_extract_ticker` — add sole-open-position resolution before Waxui executes live.

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
