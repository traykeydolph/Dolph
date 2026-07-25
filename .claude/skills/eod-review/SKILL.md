---
name: eod-review
description: |
  End-of-day trading-bot review: stop the bot, audit the day's Eva/Ace execution
  and trade lifecycles, summarize the Waxui shadow log, scan for parser errors and
  silent failures, and give a Gate-1 verdict. Use when asked to "EOD review",
  "end of day review", "end of day", "daily review", or "wrap up for the day".
allowed-tools:
  - Bash
  - Read
  - Edit
---

# End-of-day review

Produce the daily Gate-1 audit. Work in `~/Desktop/trading`. The point is a
trustworthy, honest record of whether today advanced or reset the Gate-1 clock —
bias toward verification over speed.

## 0. Timezone convention (read first — easy to get wrong)
- `trading_bot.log` timestamps are **CT** (local).
- `trading_bot.db` timestamps are **UTC**. (e.g. an entry logged `08:39 CT` is
  stored `13:39 UTC` in the DB.)
Compute both dates up front and use the right one for each source:
```bash
cd ~/Desktop/trading
echo "CT  date (for log greps):  $(TZ=America/Chicago date +%Y-%m-%d)"
echo "UTC date (for DB queries): $(date -u +%Y-%m-%d)"
```
During market hours these match. **After ~7pm CT they differ** (UTC has rolled to
tomorrow) — pick the trading day deliberately. Below, substitute `<CT_DATE>` and
`<UTC_DATE>` accordingly.

## 1. Stop the bot cleanly first
Run the **stop-bot** skill (SIGTERM via pidfile; confirm `Bot stopped.` + pidfile
gone). The double `Bot stopped.` is the known harmless double-teardown.

## 2. Eva/Ace execution — today's trades & lifecycles
```bash
./venv/bin/python - <<'PY'
import sqlite3
c=sqlite3.connect('trading_bot.db'); c.row_factory=sqlite3.Row
D_UTC="<UTC_DATE>"
print("=== TRADES today ===")
for r in c.execute("SELECT * FROM trades WHERE date(created_at)=? OR date(executed_at)=? ORDER BY id",(D_UTC,D_UTC)):
    print(f"  #{r['id']} {r['analyst']} {r['action']} {r['ticker']} {r['direction']} "
          f"strike={r['strike']} exp={r['expiry']} qty={r['quantity']} "
          f"entry={r['entry_price']} exec={r['executed_price']} pnl={r['pnl']} "
          f"status={r['status']} conf={r['confidence']} msg={r['message_id']}")
print("=== POSITIONS opened/closed today ===")
for r in c.execute("SELECT * FROM positions WHERE date(opened_at)=? OR date(closed_at)=? ORDER BY id",(D_UTC,D_UTC)):
    print(f"  #{r['id']} {r['analyst']} {r['ticker']} {r['direction']} strike={r['strike']} "
          f"entry={r['entry_price']} qty={r['current_quantity']}/{r['original_quantity']} "
          f"trims={r['trim_count']} status={r['status']} pnl={r['total_pnl']} "
          f"opened={r['opened_at']} closed={r['closed_at']}")
PY
```
For any executed trade, verify the **full chain** in the log at the trade's CT
time (= DB UTC − 5h): parse → order submitted → fill → (for exits) post-exit
Alpaca verification → position row → journal/Sheets. Example window grep:
```bash
grep -E "<CT_DATE> HH:MM" trading_bot.log | grep -iE "order|fill|execut|verif|pnl|close"
```
**Call out explicitly whether a COMPLETE ACE LIFECYCLE happened** (Ace parse →
paper order → exit → DB row) — that's the milestone. If Ace produced 0 messages,
say so plainly (check: `message_log` count for channel `1478050123786485831`, and
whether Ace's seeded cursor moved vs prior sessions). Ace posts ~1.8 entries/wk,
so silence is expected, not a fault.

## 3. Parser-error / silent-failure scan (Gate-1 critical)
```bash
grep "<CT_DATE>" trading_bot.log | grep "\[ERROR\]"        # expect NONE
grep "<CT_DATE>" trading_bot.log | grep "\[WARNING\]"      # inspect each
grep "<CT_DATE>" trading_bot.log | grep -iE "alerts.telegram|Failed to send"  # telegram failures
```
Distinguish carefully:
- **Parser error / wrong parse** = resets the Gate-1 clock. Flag loudly.
- **Legitimate safety skip** = NOT an error. e.g. `No open position for trim`
  means an analyst trimmed/exited a contract the bot never held → the parser
  parsed fine, execution correctly declined, logged a WARNING, placed no order.
  That is the safety guard **working as designed**. A `skipped` trade row with
  high confidence is the same story.

## 4. Waxui shadow log — real-only breakdown
Exclude `replay_`-prefixed rows (verification artifacts, not real observations):
```bash
./venv/bin/python - <<'PY'
import json
from collections import Counter
D="<UTC_DATE>"
tiers=Counter(); execu=Counter(); total=0; ordered=0
for line in open('logs/waxui_shadow.jsonl'):
    s=line.strip()
    if not s: continue
    d=json.loads(s)
    if str(d.get('message_id','')).startswith('replay'): continue
    if D not in str(d.get('observed_at','')): continue
    total+=1
    tiers[d.get('tier','?')]+=1
    if d.get('executed'): ordered+=1
    if d.get('parsed'):
        if d.get('is_index'): execu['index/SPX (unexecutable)']+=1
        elif d.get('executable_on_alpaca'): execu['executable-on-alpaca']+=1
        else: execu['parsed-not-executable']+=1
print("observations:", total)
print("tiers:", dict(tiers), "  (unparsed = would-hit-Gemini)")
print("parsed split:", dict(execu))
print("orders fired on waxui path (must be 0):", ordered)
PY
```
Report: # observations, tier breakdown (regex-extract / unparsed=would-hit-Gemini
/ noise-skip), executable-vs-SPX(index) split, and **confirm 0 orders** on the
Waxui path. A high SPX/index share reconfirms why Waxui is shadow-only.

## 5. Verdict
State plainly whether today was a **CLEAN Gate-1 day**:
- CLEAN = zero parser errors AND zero silent failures → the streak **ADVANCES**.
- Any parser error → the streak **RESETS**. Say so.
Report Gate-1 progress as `Day N/5 clean · M/5 lifecycles`, and separately note
whether the **Ace milestone** is still pending. Gate 1 = 5 clean market days, 5+
complete lifecycles, zero parser errors, zero silent failures.

## 6. Update the record
Update `CURRENT_STATUS.md` (the "Where we are" TL;DR + "Open items"): today's
Gate-1 day count, any completed lifecycle, Ace status, shadow numbers, and any new
open item. Keep runtime data (logs, `*.db`, `*.jsonl`) OUT of git. Offer to commit
the doc update, but don't touch parse or order-execution logic during Gate-1.
