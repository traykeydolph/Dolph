# LIVE_SAFETY.md — Pre-Live Safety Gate

> **Purpose:** the go-live checklist. Every blocker below must be **fixed and verified**
> before this bot trades real money. **None may be waived.** Paper trading hides all of them —
> that is exactly why they are dangerous.
>
> Created 2026-07-24 (found during Gate-1 paper validation). Do **not** implement fixes while
> Gate 1 is running — execution/parser logic is frozen. This doc is the design + verification
> plan to execute at the paper→live transition.

**Status key:** 🔴 OPEN · 🟡 FIX WRITTEN (unverified) · 🟢 VERIFIED

| # | Blocker | Worst case | Status |
|---|---------|-----------|--------|
| 1 | Missed exit on restart | **Loss of principal** — position held open, no one watching | 🔴 OPEN |
| 2 | P&L recorded off signal, not fill | Corrupted track record (the thing we plan to monetize) | 🔴 OPEN |
| 3 | Limit→market escalation after 15s | Silent edge-bleed on fast fills | 🔴 OPEN |
| 4 | External calls have no timeout | A network blip can **hang message processing** indefinitely | 🔴 OPEN |

---

## BLOCKER 4 — External calls have no connection timeout 🔴

### Failure mode
The Gemini LLM fallback makes a **blocking network call with no connection timeout**. When the
network can resolve DNS but not complete the TCP/TLS connection (exactly the 07-23 blip
condition), the call **hangs indefinitely** instead of failing fast.

**Evidence (found 2026-07-24):** the test suite — normally 0.89s — hung for >2min on router
tests that reach the fallback. Stack: `signal_router.py:156` → `parsers/gemini_parser.py:79`
→ `google.genai` → `httpx` → `socket.create_connection` (blocked on connect). `gemini_parser`
catches *failures* (`:95` → returns `None`), so a fast refusal is harmless — but a **hang** is
not caught, because there is no timeout to convert it into a failure.

Production cousin: an analyst posts a message that needs the LLM fallback during a network
blip → `route_message` blocks → the processing path stalls. Unverified whether the 60s poll
watchdog (`main.py:277`) actually wraps this call; **must confirm** — if it doesn't, a single
message can freeze the bot.

### Fix
- Set an explicit **connect + read timeout** on the genai/httpx client (e.g. 10s), so a bad
  connection raises promptly and `gemini_parser`'s existing `except` returns `None`.
- Verify (or add) a hard timeout around `route_message`/message processing so no single message
  can block the loop, independent of which external call stalls.
- Make the test suite hermetic: mock the Gemini client in router-wiring tests so the suite never
  depends on live network (it currently makes real API calls).

### Verification
- With the network forced to hang on connect, `route_message` returns within the timeout (not
  indefinitely) and the message is handled/skipped, not blocked.
- Full test suite runs offline with no live network calls.

---

## BLOCKER 1 — Missed exit on restart 🔴
**Can lose principal. Highest priority.**

### Failure mode
An analyst posts a **close** for an open position while the bot is **down** (crash, machine
sleep/wake, manual restart — any of which a network outage can trigger). On restart the bot
**skips the close permanently** and the position sits open, unprotected.

There is exactly **one** path for an exit to reach a position — the poller seeing the Discord
message — and there is no backstop:
- **No broker-side stop.** Every Alpaca order is `type='limit'` / `type='market'`, never a stop
  or bracket (`execution/alpaca_client.py`). Nothing at the broker will auto-close you.
- **No internal auto-stop for options.** `_check_stop_losses` (`main.py:1149+`) only sends a
  *"50% drawdown — consider manual exit"* **alert** for options, then `continue`s; the actual
  auto-sell path runs for **crypto only**. And it is skipped entirely for any position with
  `stop_price = None` (`main.py:1158`).
- **Live example:** the currently open **USO put (pos #71)** has `stop_price = None` → **zero**
  automated monitoring right now.

### Why a restart loses the message
The poll cursor is **in-memory only** and reset to `None` every startup
(`discord_poller.py:40-42`, `self.last_message_id = {ch: None}`). On first poll per channel it
**seeds to the *latest* message** (`discord_poller.py:91-100`), i.e. it deliberately ignores
everything posted before boot. It only advances the cursor on a *successful* fetch
(`:154`) and fetches with `after=<cursor>` when one exists (`:89`).

- **Bot stays UP through the outage (07-23 case):** cursor preserved in memory → on recovery,
  `after=` refetches the backlog → exit fires **late but fires**. Bounded, survivable.
- **Bot RESTARTS during/after the outage:** cursor → `None` → reseed to latest → **the close
  posted during downtime is never seen.** Unbounded risk.

### Fix
1. **Persist the cursor** (per channel, to the DB) on every successful poll.
2. **On startup, replay — don't reseed.** Fetch messages `after` the last persisted cursor
   instead of seeding to latest; process them in order. Especially: reconcile any **exit/trim
   for a currently-open position** before resuming normal polling.
3. **Backstop (defense in depth):** attach a default stop/max-loss to every position so the
   drawdown monitor has something to act on; and/or a broker stop where the option chain
   supports it; and/or a watchdog alert when an open position exists and polling has been down
   > N minutes.

### Verification (must pass before live)
- **Restart-replay test:** open a paper position → stop the bot → post an exit in the channel
  while it is down → restart → **bot must fetch the missed exit and close the position**, not
  seed past it.
- **Outage-replay test:** simulate a poll outage spanning an exit message for an open position →
  on recovery the exit is processed exactly once.
- Cursor survives a kill -9 (persisted, not just graceful-shutdown state).

---

## BLOCKER 2 — P&L recorded off signal price, not actual fill 🔴

### Failure mode
Recorded P&L reflects the **parsed signal price**, not the **Alpaca fill** — so the track record
(the asset we intend to monetize) is wrong.

**Evidence (07-24 GOOGL 325C trim):**
```
Submitted option exit limit: sell 1 GOOGL...@ $0.79
Order ... filled: 1 shares @ $0.79          ← actual fill
Trimmed position: eva GOOGL -1 shares @ $0.70 (P&L: $22.00)   ← recorded off $0.70
```
Entry $0.48 → recorded +$22 (using $0.70); true on the fill ≈ +$31 (using $0.79). The DB
`trades.executed_price` also stored `0.70`.

### Fix
Source `executed_price` and all P&L from the **filled order** (fill price × qty from Alpaca),
never from the signal/limit. Audit trim **and** exit paths in
`execution/position_manager.py` / `execution/alpaca_client.py`.

### Verification
For every exit/trim: DB `executed_price` and computed `pnl` equal the actual Alpaca fill for
that order id (reconcile against the broker, not the message).

---

## BLOCKER 3 — Limit→market escalation after 15s 🔴

### Failure mode
`execute_entry_order()` replaces an unfilled limit with a **market** order after 15s
(`execution/alpaca_client.py:170-179`). On fast/0DTE options this pays through the spread;
paper's idealized fills hide the cost entirely.

### Fix
Replace the market fallback with a **capped marketable-limit**: pay up to X% over the signal
price, else **skip and alert** — never send a naked market order.

### Verification
On a fast mover, the order either fills within the cap or is skipped + alerted; assert no
`type='market'` fallback path is reachable for entries.

---

## Go-live rule
Live trading is gated on **all four blockers 🟢 VERIFIED** *and* Gate 1 passed (5 clean market
days, 5+ complete lifecycles, zero parser errors, zero silent failures). Blocker 1 is the hard
stop — it is the only one that can lose principal.
